"""Suggestions sent from opencaselaw.ch/vorschlag/.

One SQLite file beside metrics.db (``$SWISS_CASELAW_DIR/suggestions.db``, or
``OCL_SUGGESTIONS_DB``), written by the REST workers and never by the build:
it is not part of the corpus and not swapped. WAL mode, so eight workers can
append at once.

What is kept, and why (the Datenschutz page says the same):
- the text, redacted a second time here with quality.redact (the page already
  redacts in the browser; this catches what a script-less client sends);
- the e-mail address, only if the person gave one, to write once when the status changes;
- a hash of the IP address keyed to the day, only to rate-limit; it cannot be
  linked across days and is never shown.

Each suggestion has a public number (0007) and a secret token: the status link
``/vorschlag/#<token>`` reads it back without exposing anyone else's.

Kill switch: ``OCL_SUGGESTIONS=0`` turns both endpoints into 503s; the page then
offers a prepared e-mail instead.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KINDS = ("source", "feature", "error", "other")
STATUSES = ("received", "read", "planned", "declined", "shipped")
LANGS = ("de", "fr", "it", "rm", "en")
CONTEXT_TYPES = ("search", "decision", "law", "scholarship", "page")

MIN_LEN = 12
MAX_LEN = 4000
MAX_LINKS = 3
PER_HOUR = 5
PER_DAY = 20
GLOBAL_PER_DAY = 500

_TOKEN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
TOKEN_RE = re.compile(r"^[a-z2-9]{10}$")
_EMAIL_RE = re.compile(r"^[^@\s<>\"]{1,64}@[^@\s<>\"]{1,190}\.[A-Za-z]{2,24}$")
_KIND_LABEL = {"source": "Quelle", "feature": "Funktion", "error": "Fehler", "other": "Anderes"}

_init_lock = threading.Lock()
_initialised: set[str] = set()


def enabled() -> bool:
    return os.environ.get("OCL_SUGGESTIONS", "1") != "0"


def db_path() -> Path:
    explicit = os.environ.get("OCL_SUGGESTIONS_DB")
    if explicit:
        return Path(explicit)
    base = os.environ.get("SWISS_CASELAW_DIR", str(Path.home() / ".swiss-caselaw"))
    return Path(base) / "suggestions.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or db_path())
    conn = sqlite3.connect(str(path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    key = str(path)
    if key not in _initialised:
        with _init_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS suggestions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    token         TEXT UNIQUE NOT NULL,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    kind          TEXT NOT NULL,
                    lang          TEXT NOT NULL,
                    text          TEXT NOT NULL,
                    redactions    INTEGER NOT NULL DEFAULT 0,
                    context_type  TEXT,
                    context_value TEXT,
                    page          TEXT,
                    email         TEXT,
                    ip_hash       TEXT,
                    status        TEXT NOT NULL DEFAULT 'received',
                    note          TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_suggestions_ip ON suggestions(ip_hash, created_at);
                CREATE INDEX IF NOT EXISTS ix_suggestions_created ON suggestions(created_at);
                """
            )
            _initialised.add(key)
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _salt(path: Path) -> bytes:
    """A random secret beside the DB, made once: without it the day hash of an
    IPv4 address could be reversed by trying all four billion."""
    f = path.with_suffix(".salt")
    try:
        return f.read_bytes()
    except FileNotFoundError:
        s = secrets.token_bytes(32)
        try:
            fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(s)
            return s
        except FileExistsError:  # another worker won the race
            return f.read_bytes()


def ip_hash(ip: str, when: datetime, path: Path) -> str:
    h = hashlib.sha256(_salt(path) + when.strftime("%Y-%m-%d").encode() + (ip or "").encode())
    return h.hexdigest()[:20]


def number(row_id: int) -> str:
    return f"{row_id:04d}"


def _token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(10))


def _clip(value, n: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:n] or None


def validate(payload) -> tuple[dict | None, str | None]:
    """Clean a submitted payload, or name what is wrong with it."""
    if not isinstance(payload, dict):
        return None, "invalid_json"
    text = payload.get("text")
    if not isinstance(text, str):
        return None, "text_required"
    text = text.replace("\r\n", "\n").strip()
    if len(text) < MIN_LEN:
        return None, "text_too_short"
    if len(text) > MAX_LEN:
        return None, "text_too_long"
    if len(re.findall(r"https?://", text, re.I)) > MAX_LINKS:
        return None, "too_many_links"
    kind = payload.get("kind") if payload.get("kind") in KINDS else "other"
    lang = payload.get("lang") if payload.get("lang") in LANGS else "de"
    email = payload.get("email")
    if email in (None, ""):
        email = None
    elif not isinstance(email, str) or not _EMAIL_RE.match(email.strip()) or len(email.strip()) > 254:
        return None, "email_invalid"
    else:
        email = email.strip()
    ctx_type = ctx_value = None
    ctx = payload.get("context")
    if isinstance(ctx, dict) and ctx.get("type") in CONTEXT_TYPES:
        ctx_type, ctx_value = ctx["type"], _clip(ctx.get("value"), 300)
    return {
        "text": text, "kind": kind, "lang": lang, "email": email,
        "context_type": ctx_type, "context_value": ctx_value,
        "page": _clip(payload.get("page"), 300),
    }, None


def _redact(text: str) -> tuple[str, int]:
    try:
        from quality.redact import redact
    except Exception:  # pragma: no cover - the module ships with the repo
        return text, 0
    r = redact(text)
    return r.redacted, r.total


def submit(payload, ip: str, *, path: Path | None = None) -> tuple[int, dict]:
    """Store one suggestion. Returns (HTTP status, body)."""
    if not enabled():
        return 503, {"error": "suggestions_disabled"}
    # The page's hidden field: a person never fills it. Answer like a success so a
    # bot learns nothing, and keep nothing.
    if isinstance(payload, dict) and payload.get("website"):
        return 201, {"no": "0000", "token": _token(), "status": "received"}
    clean, err = validate(payload)
    if err:
        return 400, {"error": err}
    path = Path(path or db_path())
    now = _now()
    hashed = ip_hash(ip, now, path)
    text, n_redacted = _redact(clean["text"])
    conn = connect(path)
    try:
        hour_ago = _iso(now - timedelta(hours=1))
        day_start = now.strftime("%Y-%m-%dT00:00:00Z")
        mine_hour = conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE ip_hash=? AND created_at>=?", (hashed, hour_ago)).fetchone()[0]
        mine_day = conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE ip_hash=? AND created_at>=?", (hashed, day_start)).fetchone()[0]
        all_day = conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE created_at>=?", (day_start,)).fetchone()[0]
        if mine_hour >= PER_HOUR or mine_day >= PER_DAY or all_day >= GLOBAL_PER_DAY:
            return 429, {"error": "rate_limited"}
        for _ in range(5):
            token = _token()
            try:
                cur = conn.execute(
                    "INSERT INTO suggestions (token, created_at, updated_at, kind, lang, text, redactions,"
                    " context_type, context_value, page, email, ip_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (token, _iso(now), _iso(now), clean["kind"], clean["lang"], text, n_redacted,
                     clean["context_type"], clean["context_value"], clean["page"], clean["email"], hashed))
                conn.commit()
                break
            except sqlite3.IntegrityError:  # token collision: 31^10, but cheap to handle
                continue
        else:  # pragma: no cover
            return 500, {"error": "token"}
        row_id = cur.lastrowid
    finally:
        conn.close()
    _notify_async(f"Vorschlag {number(row_id)} · {_KIND_LABEL[clean['kind']]} · "
                  f"{len(text)} Zeichen · {clean['lang']}" + (" · mit E-Mail" if clean["email"] else ""))
    return 201, {"no": number(row_id), "token": token, "status": "received", "created_at": _iso(now)}


def status(token: str, *, path: Path | None = None) -> tuple[int, dict]:
    """What the person who holds the token may see: never the e-mail or the IP hash."""
    if not enabled():
        return 503, {"error": "suggestions_disabled"}
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        return 404, {"error": "not_found"}
    path = Path(path or db_path())
    if not path.exists():
        return 404, {"error": "not_found"}
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT id, kind, lang, text, status, note, created_at, updated_at FROM suggestions WHERE token=?",
            (token,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return 404, {"error": "not_found"}
    return 200, {"no": number(row["id"]), "kind": row["kind"], "lang": row["lang"], "text": row["text"],
                 "status": row["status"], "note": row["note"],
                 "created_at": row["created_at"], "updated_at": row["updated_at"]}


def set_status(row_id: int, new_status: str, note: str | None = None, *, path: Path | None = None) -> sqlite3.Row:
    """Operator side (scripts/suggestions_admin.py)."""
    if new_status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    conn = connect(path)
    try:
        sets, args = ["status=?", "updated_at=?"], [new_status, _iso(_now())]
        if note is not None:
            sets.append("note=?")
            args.append(note.strip() or None)
        args.append(row_id)
        if conn.execute(f"UPDATE suggestions SET {', '.join(sets)} WHERE id=?", args).rowcount != 1:
            raise KeyError(row_id)
        conn.commit()
        return conn.execute("SELECT * FROM suggestions WHERE id=?", (row_id,)).fetchone()
    finally:
        conn.close()


# ── operator alert ───────────────────────────────────────────────────────────
# ntfy.sh topics are public to anyone who knows the name, so the alert carries the
# number and the kind, never the text.
def _notify(message: str) -> None:
    topic = os.environ.get("OCL_SUGGEST_NTFY_TOPIC", "opencaselaw-publish")
    if not topic or os.environ.get("OCL_SUGGEST_NTFY", "1") == "0":
        return
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
            headers={"Title": "OpenCaseLaw: neuer Vorschlag", "Tags": "bulb", "Priority": "default"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _notify_async(message: str) -> None:
    threading.Thread(target=_notify, args=(message,), daemon=True).start()
