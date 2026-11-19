"""Date-aware decision identity: keep same-docket second rulings.

Courts issue several rulings under one docket (interim measures, referral back
to the prosecutor, final judgment, revision). ``decision_id`` is docket-keyed,
so once a docket is held, a scraper that skips ``state.is_known(docket_id)``
never fetches a later ruling under it. Audit 2026-11-16
(docs/claude/same-docket-gap-2026-11-16/): BVGer 186, BStGer 16 and BPatGer 36
such rulings were missing, e.g. BStGer SK.2019.12 (referral 2019-09-25 held,
merits judgment 2021-04-23 missing).

This is the BGer mechanism of 2026-09-03 (e555b27f, scrapers/bger.py), made
court-generic:

* Sidecar ``state/<court>.dates.txt``, one ``<base_id>\\t<YYYY-MM-DD>\\t<id>``
  line per held (docket, decision date) pair, validated against state at load
  (a crash between fetch and durable write re-yields the ruling).
* A listed ruling whose docket is held only under OTHER dates gets the id
  ``<court>_<docket>-D<YYYYMMDD>``; ``docket_number`` stays the real docket
  (citations resolve; build_fts5's content-aware dedup keeps same-docket
  different-date rows; get_decision by docket returns the newest).
* Legacy mode: sidecar absent, empty, unreadable or half-seeded → the old
  docket-keyed behaviour, byte for byte, and the sidecar is never written.
  Seed with ``backfill_docket_collisions.py --court <court> --seed-only``.

Usage in a scraper: call ``_load_known_dates()`` at the start of discovery,
pass every listed stub through ``_stub_filter()`` (None = skip), build the
Decision with ``stub["decision_id"]`` and call ``_mark_date()`` once the
Decision is built.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

from models import make_decision_id

logger = logging.getLogger(__name__)

# The earlier BVGer recovery (scripts/recover_bvger_docket_collisions.py) and
# build_fts5's insert-collision re-id hold second rulings under
# ``<base_id>_d<YYYYMMDD>``. Those rows are in the corpus but never in
# state/<court>.jsonl, so the seed must take them as held pairs and the loader
# must accept them without the state check (the scrapers never write such ids,
# so a sidecar line naming one can only come from the corpus seed).
_CORPUS_SUFFIX_RE = re.compile(r"_d(\d{4})(\d{2})(\d{2})$")


def corpus_suffix_date(decision_id: str, base_id: str) -> str | None:
    """ISO date of a ``<base_id>_dYYYYMMDD`` id, else None."""
    if not decision_id.startswith(base_id):
        return None
    m = _CORPUS_SUFFIX_RE.fullmatch(decision_id, len(base_id))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


class DateAwareIdentityMixin:
    _known_dates: set | None = None
    _dated_ids: set = frozenset()

    # ── helpers ──────────────────────────────────────────────────────────
    def _dates_sidecar(self) -> Path:
        return Path(self.state.state_file).with_name(
            f"{self.court_code}.dates.txt")

    @staticmethod
    def _date_iso(value) -> str | None:
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            v = value.strip()[:10]
            if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                return v
        return None

    def _base_id(self, docket: str) -> str:
        return make_decision_id(self.court_code, docket)

    def _legacy(self) -> None:
        self._known_dates = None
        self._dated_ids = set()

    # ── load ─────────────────────────────────────────────────────────────
    def _load_known_dates(self) -> None:
        cc = self.court_code
        self._claimed_ids = set()
        self._claimed_dates = set()
        if getattr(self, "state", None) is None:
            self._legacy()       # partially constructed instance (tests, tooling)
            return
        p = self._dates_sidecar()
        if not p.exists() or p.stat().st_size == 0:
            logger.info(
                f"[{cc}] date sidecar missing/empty — legacy docket-keyed "
                "discovery (same-docket second rulings stay hidden; run "
                f"backfill_docket_collisions.py --court {cc} --seed-only)")
            self._legacy()
            return
        try:
            text = p.read_text()
        except (OSError, UnicodeDecodeError) as e:
            logger.error(f"[{cc}] unreadable date sidecar ({e}) — falling "
                         "back to legacy; reseed to recover")
            self._legacy()
            return
        known: set = set()
        dated: set = set()
        dropped = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                dropped += 1
                continue
            base_id, d_iso, did = parts
            # A pair counts only when its id is in state (marked after the
            # durable corpus write) — or is a corpus `_dYYYYMMDD` id, which
            # only the seed can have written.
            if (not base_id or not did or not self._date_iso(d_iso)
                    or not (self.state.is_known(did)
                            or corpus_suffix_date(did, base_id))):
                dropped += 1
                continue
            known.add(f"{base_id}|{d_iso}")
            dated.add(base_id)
        if dropped:
            logger.info(f"[{cc}] {dropped} date-sidecar pairs not in state / "
                        "malformed — their rulings will be retried")
        # Half-seed guard: a sidecar far smaller than the held corpus can only
        # be a partial/corrupt seed — legacy rather than re-fetching every
        # held docket under a -D id.
        state_n = len(getattr(self.state, "_seen", []) or [])
        if state_n > 100 and len(dated) < state_n // 2:
            logger.error(
                f"[{cc}] date sidecar covers {len(dated)} dockets vs "
                f"{state_n} known ids — refusing half-seeded mode, falling "
                f"back to legacy; reseed with backfill_docket_collisions.py "
                f"--court {cc} --reseed")
            self._legacy()
            return
        self._known_dates = known
        self._dated_ids = dated
        logger.info(f"[{cc}] date sidecar: {len(known)} (docket, date) pairs "
                    f"over {len(dated)} dockets")

    # ── write ────────────────────────────────────────────────────────────
    def _mark_date(self, base_id: str, d_iso: str | None,
                   decision_id: str) -> None:
        if self._known_dates is None:
            return                         # legacy mode never writes
        if not base_id or not d_iso or not decision_id:
            return
        key = f"{base_id}|{d_iso}"
        if key in self._known_dates:
            return
        self._known_dates.add(key)
        self._dated_ids.add(base_id)
        with open(self._dates_sidecar(), "a") as f:
            f.write(f"{base_id}\t{d_iso}\t{decision_id}\n")

    def _mark_decision_date(self, decision) -> None:
        """Record a built Decision's (docket, date) pair (no-op in legacy)."""
        if decision is None:
            return
        self._mark_date(self._base_id(decision.docket_number),
                        self._date_iso(decision.decision_date),
                        decision.decision_id)

    # ── filter ───────────────────────────────────────────────────────────
    def _stub_filter(self, stub: dict) -> dict | None:
        """None = skip; else the stub to yield, its ``decision_id`` set
        (plain, or ``-D<YYYYMMDD>`` for a same-docket second ruling)."""
        cc = self.court_code
        did = stub.get("decision_id") or self._base_id(stub["docket_number"])
        stub = dict(stub, decision_id=did)
        if self._known_dates is None:
            return None if self.state.is_known(did) else stub        # legacy
        claimed = getattr(self, "_claimed_ids", None)
        if claimed is None:
            claimed = self._claimed_ids = set()
            self._claimed_dates = set()
        d_iso = self._date_iso(stub.get("decision_date"))
        key = f"{did}|{d_iso}" if d_iso else None
        # A new docket's ruling takes the plain id and claims it for the rest
        # of the run (batch callers mark state only after the run).
        if not self.state.is_known(did) and did not in claimed:
            claimed.add(did)
            if key:
                self._claimed_dates.add(key)
            return stub
        if not key:
            return None          # held docket, undated listing: cannot classify
        if key in self._known_dates or key in self._claimed_dates:
            return None          # this exact ruling is held / claimed this run
        if did not in self._dated_ids and did not in claimed:
            logger.warning(
                f"[{cc}] {stub.get('docket_number')} is held without date "
                f"info — listing dated {d_iso} cannot be classified, skipped "
                "(reseed the date sidecar)")
            return None
        alt_id = self._base_id(
            f"{stub['docket_number']}-D{d_iso.replace('-', '')}")
        if self.state.is_known(alt_id) or alt_id in claimed:
            return None
        claimed.add(alt_id)
        self._claimed_dates.add(key)
        logger.info(f"[{cc}] same-docket second ruling: "
                    f"{stub.get('docket_number')} dated {d_iso} → {alt_id}")
        return dict(stub, decision_id=alt_id)
