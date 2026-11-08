"""scripts/parity_audit.py: the weekly entscheidsuche parity audit (offline).

Fixture sitemaps and fixture HF rows only; no network. The rules under test are
the per-court key quirks from the 2026-11-08 comparison, the allow-list, the
two-run confirmation and the one-message-per-run ntfy contract."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import parity_audit as pa  # noqa: E402


def sitemap(*ids: str) -> str:
    locs = "".join(f"<url><loc>https://entscheidsuche.ch/view/{i}</loc></url>" for i in ids)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset>{locs}</urlset>'


SITEMAPS = {
    "CH_BGer_1.xml": sitemap(
        "CH_BGer_002_2C-1023-2018_2018-11-23",           # ours: 2C_1023/2018
        "CH_BGer_009_9C-806-2018_2018-12-13",            # missing
    ),
    "CH_BGE_1.xml": sitemap(
        "CH_BGE_006_BGE-89-IV-26_1963-01-30",            # bge_reference
        "CH_BGE_012_20160823-53377-11_2016-08-23",       # ECtHR row, canton CE
    ),
    "CH_UNIBE_1.xml": sitemap("CH_BGB_001_BGE-52-I-1"),  # allow-listed
    "TG_OG_1.xml": sitemap("TG_OG_001_RBOG-2025-Nr--01_2025"),   # zero padding
    "AR_Gerichte_1.xml": sitemap("AR_OG_003_OG-O3V-23-24_nodate"),  # OG prefix
    "GE_Gerichte_1.xml": sitemap("GE_CJ_007_A-97-2020_2022-06-21"),  # case number
    "ZH_Baurekurs_1.xml": sitemap(
        "ZH_BRK_001_entscheidauszug-aus-brge-i-nr--0030-2019-vom-15--maerz-2019"),
    "NW_Gerichte_1.xml": sitemap("NW_OG_001_42172_2026-06-23"),    # publication id
    "SG_Gerichte_1.xml": sitemap(
        "SG_DEP_001_25-6932_2026-03-31",                  # missing, same date held
        "SG_VSG_001_B-2023-123456789012345_2024-01-05",   # truncated, prefix
    ),
    "VD_FindInfo_1.xml": sitemap("VD_TC_013_d41d8cd98f00b204e980_2026-01-06"),
    "SH_OG_1.xml": sitemap("SH_OG_001_-div-Nr--50-2020-2--_2026-04-17"),  # HTML slug
}

OURS = [
    {"court": "bger", "canton": "CH", "docket_number": "2C_1023/2018",
     "decision_date": "2018-11-23"},
    {"court": "bge", "canton": "CH", "docket_number": "6S.1/1962",
     "bge_reference": "BGE 89 IV 26", "decision_date": "1963-01-30"},
    {"court": "bge_egmr", "canton": "CE", "docket_number": "20160823_53377_11",
     "decision_date": "2026-01-01"},
    {"court": "tg_gerichte", "canton": "TG", "docket_number": "RBOG-2025-1",
     "decision_date": "2025-01-01"},
    {"court": "ar_gerichte", "canton": "AR", "docket_number": "O3V-23-24"},
    {"court": "ge_gerichte", "canton": "GE", "docket_number": "ATAS/573/2022",
     "decision_date": "2022-06-21",
     "pdf_url": "https://justice.ge.ch/apps/decis/fr/atas/file/2022/0005/ATAS_000573_2022_A_97_2020.pdf"},
    {"court": "zh_baurekursgericht", "canton": "ZH", "docket_number": "BRGE I Nr. 0030/2019",
     "decision_date": "2019-03-15",
     "pdf_url": "https://www.baurekursgericht-zh.ch/media/entscheidauszug_aus_brge_i_nr._0030-2019_vom_15._maerz_2019.pdf"},
    {"court": "nw_gerichte", "canton": "NW", "docket_number": "VA 25 26",
     "source_url": "https://www.nw.ch/_rte/publikation/42172"},
    {"court": "sg_publikationen", "canton": "SG", "docket_number": "B 2023/123456789012345678",
     "decision_date": "2024-01-05"},
    {"court": "sg_publikationen", "canton": "SG", "docket_number": "IV 2025/1",
     "decision_date": "2026-03-31"},
    {"court": "vd_gerichte", "canton": "VD", "docket_number": "ZA19.041001",
     "pdf_url": "https://prestations.vd.ch/pub/101623/api/decision/download/bf13818e-ff60-4cf5-909a-629a5bdd67fb"},
    {"court": "sh_gerichte", "canton": "SH", "docket_number": "<div><span style='x'>Nr. 50/2020/2"},
]

ALLOW = {"entries": [{
    "spider": "CH_UNIBE", "ids": ["CH_BGB_001_BGE-52-I-1", "CH_BGB_001_BGE-99-I-9"],
    "reason": "BGE before 1954, Teil I: scan mislabelled, text held back on purpose",
    "added": "2026-11-08"}]}


@pytest.fixture
def ours_dir(tmp_path):
    """Fixture HF rows written as a real parquet, read by the mirror code path."""
    pq = pytest.importorskip("pyarrow.parquet")
    pa_ = pytest.importorskip("pyarrow")
    d = tmp_path / "hf"
    d.mkdir()
    by_court: dict[str, list[dict]] = {}
    for r in OURS:
        full = {c: r.get(c) for c in pa.OUR_COLUMNS}
        full["full_text"] = "x" * 50          # a heavy column the audit must not need
        by_court.setdefault(r["court"], []).append(full)
    for court, rows in by_court.items():
        pq.write_table(pa_.Table.from_pylist(rows), d / f"{court}.parquet")
    # The nightly delta parquet has another vocabulary and must be skipped.
    pq.write_table(pa_.Table.from_pylist([{"id": "x", "content_text": "y"}]),
                   d / "delta-2026-11-08.parquet")
    return d


@pytest.fixture
def env(tmp_path, ours_dir, monkeypatch):
    sm = tmp_path / "sm"
    sm.mkdir()
    for name, text in SITEMAPS.items():
        (sm / name).write_text(text, encoding="utf-8")
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps(ALLOW), encoding="utf-8")
    sent = []
    monkeypatch.setattr(pa, "send_ntfy", lambda t, b, url=None: sent.append((t, b)) or True)
    return {"tmp": tmp_path, "sm": sm, "allow": allow, "ours": ours_dir,
            "out": tmp_path / "logs" / "parity_audit.json", "sent": sent}


def run(env, today: str, extra=()):
    args = ["--out", str(env["out"]), "--history-dir", str(env["tmp"] / "hist"),
            "--cache-dir", str(env["tmp"] / "cache"), "--allowlist", str(env["allow"]),
            "--ours-dir", str(env["ours"]), "--sitemaps-dir", str(env["sm"]),
            "--offline", "--no-enum", "--today", today, *extra]
    assert pa.main(args) == 0
    return json.loads(env["out"].read_text())


# ── keys ────────────────────────────────────────────────────────────────

def test_keys_cover_the_per_court_quirks():
    assert set(pa.keys("RBOG-2025-Nr--01")) & set(pa.keys("RBOG-2025-1"))      # TG
    assert set(pa.keys("OG-O3V-23-24")) & set(pa.keys("O3V-23-24"))           # AR
    assert set(pa.keys("BGE-89-IV-26")) & set(pa.keys("BGE 89 IV 26"))
    assert set(pa.keys("2C-1023-2018")) & set(pa.keys("2C_1023/2018"))
    assert set(pa.keys("I-601-2006")) & set(pa.keys("I 601/06"))                    # EVG
    assert not set(pa.keys("I-601-2006")) & set(pa.keys("I 601/07"))
    assert pa.their_dockets("UR_Gerichte", "2026-OG-V-25-16-Leis")[0] == "OG-V-25-16"
    assert set(pa.keys(pa.their_dockets("UR_Gerichte", "2026-OG-V-25-16-Leis")[0])) & set(
        pa.keys("OG V 25 16"))
    zh = pa.their_dockets("ZH_Baurekurs", "BRGE-I-0165-0167-2012-664")[0]
    assert set(pa.keys(zh)) & set(pa.keys("BRGE I Nrn. 0165-0167/2012"))
    assert pa.es_canton("XX_Upload", "ZG_UPL_001") == "ZG" and pa.es_canton("TA_SST") == "CH"
    assert pa.unkeyable("765a98dd7955fc96f5cc") and pa.unkeyable("-div--span-style--co")
    assert not pa.unkeyable("2C-1023-2018")
    assert set(pa.keys("-div-Nr--50-2020-2--")) & set(pa.keys("<div>Nr. 50/2020/2</div>"))
    assert pa.keys("unknown") == [] and pa.keys("") == [] and pa.keys(None) == []
    # Different case numbers must stay different.
    assert not set(pa.keys("C-5183-2023")) & set(pa.keys("P-5183-2023"))


def test_ge_case_number_and_url_stems():
    url = "https://justice.ge.ch/apps/decis/fr/atas/file/2022/0005/ATAS_000573_2022_A_97_2020.pdf"
    assert pa.ge_case_number(url) == "A/97/2020"
    assert pa.ge_case_number("https://justice.ge.ch/x/show/3030513?doc=") is None
    assert pa.url_stem("https://www.nw.ch/_rte/publikation/42172") == "42172"
    assert pa.url_stem("https://x.ch/media/2025-09_1.pdf") == "2025-09_1"
    assert pa.url_stem("https://x.ch/a/download") is None             # no digit
    assert pa.url_stem("https://p.vd.ch/d/bf13818e-ff60-4cf5-909a-629a5bdd67fb") is None


def test_parse_ids_and_sitemaps():
    assert pa.parse_es_id("CH_BGer_002_2C-1023-2018_2018-11-23") == {
        "sig": "CH_BGer_002", "docket": "2C-1023-2018", "date": "2018-11-23"}
    assert pa.parse_es_id("CH_EDÖB_002_Urteil-des-Bundesger_2012-05-31")["sig"] == "CH_EDÖB_002"
    assert pa.parse_es_id("CH_BGB_001_BGE-52-I-1")["date"] is None
    assert pa.parse_es_id("TG_OG_001_TVR-2000-Nr--39_2000")["docket"] == "TVR-2000-Nr--39"
    assert pa.parse_es_id("AR_OG_003_O3V-23-24_nodate")["docket"] == "O3V-23-24"
    xml = sitemap("A_B_001_x&amp;y_2020-01-01", "A_B_001_x&amp;y_2020-01-01")
    listing = pa.build_listing({"A_B_1.xml": xml, "A_B_2.xml": xml})
    assert list(listing) == ["A_B_001_x&y_2020-01-01"]          # deduplicated by ID
    assert listing["A_B_001_x&y_2020-01-01"]["spider"] == "A_B"


def test_enumeration_targets_and_unlisted_entries():
    listing = pa.build_listing({"GR_Gerichte_1.xml": sitemap("GR_VG_002_A-2008-31_2008-09-05")})
    snap = {"total": {"GR": 3, "GR_VG": 3, "GR_VG_002": 3, "GR_VG_003": 0}}
    assert pa.enumeration_targets(listing, snap) == [("GR_VG_002", 3, 1)]
    n = pa.add_unlisted(listing, {"GR_VG_002": [
        {"id": "GR_VG_002_A-2008-31_2008-09-05", "date": "2008-09-05", "reference": ["A 2008 31"]},
        {"id": "GR_VG_002_A-2009-1_2009-01-02", "date": "2009-01-02", "reference": ["A 2009 1"]},
    ]})
    assert n == 1
    e = listing["GR_VG_002_A-2009-1_2009-01-02"]
    assert e["spider"] == "GR_Gerichte" and e["listed"] is False
    assert "A 2009 1" in e["dockets"]


def test_enumerate_signature_pages_politely():
    pages = [[{"_source": {"id": f"X_Y_001_{i}"}, "sort": [f"{i}"]} for i in range(1000)],
             [{"_source": {"id": "X_Y_001_last", "date": "2020-01-01", "reference": ["R 1"]}}]]
    bodies = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        bodies.append(json.loads(req.data))
        return Resp(json.dumps({"hits": {"hits": pages[len(bodies) - 1]}}).encode())

    slept = []
    http = pa.Http(opener=opener, sleep=slept.append)
    hits = pa.enumerate_signature(http, "X_Y_001", max_pages=5)
    assert len(hits) == 1001 and hits[-1]["reference"] == ["R 1"]
    assert bodies[0]["_source"] == ["id", "date", "reference"]      # never text
    assert bodies[1]["search_after"] == ["999"]
    assert http.requests == 2


# ── end to end ──────────────────────────────────────────────────────────

def test_matching_reproduces_the_quirk_rules(env):
    rep = run(env, "2026-11-08")
    sp = rep["spiders"]
    residual = {r["id"] for r in rep["residuals"]}
    assert residual == {
        "CH_BGer_009_9C-806-2018_2018-12-13",
        "SG_DEP_001_25-6932_2026-03-31",          # no same-date fallback for SG
        "VD_TC_013_d41d8cd98f00b204e980_2026-01-06",   # no uuid without network
    }
    assert sp["CH_BGE"]["matched"] == 2                          # incl. the CE row
    assert sp["TG_OG"]["matched"] == sp["AR_Gerichte"]["matched"] == 1
    assert sp["GE_Gerichte"]["matched_by"] == {"exact": 1}       # by case number
    assert sp["ZH_Baurekurs"]["matched"] == sp["NW_Gerichte"]["matched"] == 1
    assert sp["SH_OG"]["matched"] == 1
    assert sp["SG_Gerichte"]["matched_by"] == {"prefix": 1}
    assert sp["CH_UNIBE"]["allowlisted"] == 1 and sp["CH_UNIBE"]["residual"] == 0
    assert rep["allowlisted"][0]["reason"].startswith("BGE before 1954")
    assert rep["stale_allowlist"] == ["CH_BGB_001_BGE-99-I-9"]
    r = next(x for x in rep["residuals"] if x["spider"] == "CH_BGer")
    assert r["url"] == "https://entscheidsuche.ch/view/CH_BGer_009_9C-806-2018_2018-12-13"
    assert r["docket"] == "9C-806-2018" and r["date"] == "2018-12-13"
    assert (env["tmp"] / "hist" / "2026-11-08.json").exists()


def test_doc_resolution_uses_the_cache(env):
    cache = env["tmp"] / "cache"
    cache.mkdir()
    (cache / "doc_cache.json").write_text(json.dumps({
        # VD hash ID -> the portal uuid in their PDF URL
        "VD_TC_013_d41d8cd98f00b204e980_2026-01-06": {"num": [], "urls": [
            "https://prestations.vd.ch/pub/101623/api/decision/download/"
            "BF13818E-ff60-4cf5-909a-629a5bdd67fb"]},
        # a docket named in Num
        "CH_BGer_009_9C-806-2018_2018-12-13": {"num": ["2C 1023/2018"], "urls": []},
    }))
    rep = run(env, "2026-11-08")
    assert rep["spiders"]["VD_FindInfo"]["matched_by"] == {"doc": 1}
    assert rep["spiders"]["CH_BGer"]["matched_by"] == {"exact": 1, "doc": 1}
    assert {r["id"] for r in rep["residuals"]} == {"SG_DEP_001_25-6932_2026-03-31"}


def test_fetch_doc_reads_num_and_urls(monkeypatch):
    docs = {
        "ZH_BRK_001_2021-15": {"Num": ["BRGE III Nr. 0006/2021"],
                               "PDF": {"URL": "https://www.baurekursgericht-zh.ch/media/2021-15.pdf"}},
        "TA_DZK_001_CD-2023-DO-12_2024-06-13": {
            "Num": ["CD 2023/DO/12"],
            "PDF": "{'Datei': 'x.pdf', 'URL': 'https://www.sportstribunal.ch/f/CD-2023-DO-12.pdf'}"},
    }
    urls = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        urls.append(req.full_url)
        key = req.full_url.rsplit("/", 1)[-1][:-5]
        if key not in docs:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return Resp(json.dumps(docs[key]).encode())

    http = pa.Http(opener=opener, sleep=lambda s: None)
    zh = pa.fetch_doc(http, {"spider": "ZH_Baurekurs", "id": "ZH_BRK_001_2021-15"})
    assert zh == {"num": ["BRGE III Nr. 0006/2021"],
                  "urls": ["https://www.baurekursgericht-zh.ch/media/2021-15.pdf"]}
    ta = pa.fetch_doc(http, {"spider": "TA_SST", "id": "TA_DZK_001_CD-2023-DO-12_2024-06-13"})
    assert ta["urls"] == ["https://www.sportstribunal.ch/f/CD-2023-DO-12.pdf"]
    assert pa.fetch_doc(http, {"spider": "X", "id": "nope"})["missing"] is True
    assert urls[0] == "https://entscheidsuche.ch/docs/ZH_Baurekurs/ZH_BRK_001_2021-15.json"
    idx = pa.OurIndex([{"court": "zh_baurekursgericht", "canton": "ZH",
                        "docket_number": "BRGE III Nr. 0006/2021"}])
    assert idx.resolve(("ZH",), zh) == 0


def test_resolver_respects_the_fetch_budget(monkeypatch):
    import collections
    unmatched = [{"id": f"X_Y_001_{i}", "spider": "X", "sig": "X_Y_001", "docket": f"A-{i}"}
                 for i in range(5)]
    calls = []
    monkeypatch.setattr(pa, "fetch_doc",
                        lambda http, e: calls.append(e["id"]) or {"num": [], "urls": []})
    cache, stats = {}, collections.defaultdict(collections.Counter)
    left, fetched = pa.resolve_docs(unmatched, pa.OurIndex([]), object(), cache, stats, 2)
    assert fetched == 2 and len(calls) == 2 and len(left) == 5 and len(cache) == 2


def test_date_fallback_only_for_listed_spiders(env):
    # SG_DEP shares its date with an unclaimed SG row, but SG is keyable:
    # a date match there would hide the gap.
    rep = run(env, "2026-11-08")
    assert "date" not in rep["spiders"]["SG_Gerichte"]["matched_by"]
    listing = pa.build_listing({"GE_Gerichte_1.xml": sitemap("GE_CJ_002_C-21839-2025_2026-01-05")})
    idx = pa.OurIndex([{"court": "ge_gerichte", "canton": "GE",
                        "docket_number": "ACJC/1/2026", "decision_date": "2026-01-05"}])
    stats, unmatched = pa.match(listing, idx)
    assert stats["GE_Gerichte"]["date"] == 1 and unmatched == []


def test_first_run_is_a_baseline_then_two_run_confirmation(env):
    rep1 = run(env, "2026-11-08")
    assert rep1["totals"]["newly_confirmed"] == 0 and env["sent"] == []
    assert {r["notified"] for r in rep1["residuals"]} == {"baseline"}

    # A new gap appears the next week: open, not yet confirmed (could be lag).
    env["sm"].joinpath("BE_BVD_1.xml").write_text(
        sitemap("BE_VB_001_120-2026-13_2026-08-12"), encoding="utf-8")
    rep2 = run(env, "2026-11-15")
    new = next(r for r in rep2["residuals"] if r["spider"] == "BE_BVD")
    assert new["first_seen"] == "2026-11-15" and new["notified"] is None
    assert env["sent"] == []

    # Still open a week later: confirmed, announced once.
    rep3 = run(env, "2026-11-22")
    assert rep3["newly_confirmed"] == ["BE_VB_001_120-2026-13_2026-08-12"]
    assert len(env["sent"]) == 1
    title, body = env["sent"][0]
    assert "1 new residual" in title and "BE_BVD: 1" in body
    assert "https://entscheidsuche.ch/view/BE_VB_001_120-2026-13_2026-08-12" in body

    # And never again.
    rep4 = run(env, "2026-11-29")
    assert rep4["newly_confirmed"] == [] and len(env["sent"]) == 1


def test_a_residual_that_is_fixed_drops_out(env):
    run(env, "2026-11-08")
    rows = OURS + [{"court": "bger", "canton": "CH", "docket_number": "9C_806/2018"}]
    import pyarrow as pa_
    import pyarrow.parquet as pq
    pq.write_table(pa_.Table.from_pylist(
        [{c: r.get(c) for c in pa.OUR_COLUMNS} for r in rows if r["court"] == "bger"]),
        env["ours"] / "bger.parquet")
    rep = run(env, "2026-11-15")
    assert "CH_BGer_009_9C-806-2018_2018-12-13" not in {r["id"] for r in rep["residuals"]}


def test_no_ntfy_flag_and_empty_inputs(env, tmp_path):
    run(env, "2026-11-08")
    env["sm"].joinpath("BE_BVD_1.xml").write_text(
        sitemap("BE_VB_001_120-2026-13_2026-08-12"), encoding="utf-8")
    run(env, "2026-11-09")
    run(env, "2026-11-20", extra=["--no-ntfy"])
    assert env["sent"] == []
    empty = tmp_path / "empty"
    empty.mkdir()
    args = ["--out", str(tmp_path / "o.json"), "--cache-dir", str(tmp_path / "c"),
            "--ours-dir", str(empty), "--sitemaps-dir", str(env["sm"]), "--offline",
            "--no-enum", "--history-dir", ""]
    assert pa.main(args) == 2                     # never "everything is missing"
    assert not (tmp_path / "o.json").exists()


def test_allowlist_entries_need_a_reason(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"entries": [{"spider": "X", "ids": ["a"]}]}))
    with pytest.raises(ValueError):
        pa.load_allowlist(p)


def test_shipped_allowlist_is_valid():
    entries = pa.load_allowlist(pa.DEFAULT_ALLOWLIST)
    assert entries, "the shipped allow-list should hold the explained residuals"
    for a in entries:
        assert a["reason"] and a.get("added") and (a["_ids"] or a["_rx"])


def test_ntfy_topic_from_env_only(monkeypatch):
    import importlib
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    m = importlib.reload(pa)
    assert m.NTFY_URL == ""
    assert m.send_ntfy("t", "b") is False
    monkeypatch.setenv("NTFY_TOPIC", "ops-test-topic")
    m = importlib.reload(pa)
    assert m.NTFY_URL == "https://ntfy.sh/ops-test-topic"
    monkeypatch.delenv("NTFY_TOPIC")
    importlib.reload(pa)


# ── systemd units ───────────────────────────────────────────────────────

def test_units_are_low_priority_and_outside_the_build_window():
    sd = REPO / "systemd"
    svc = (sd / "opencaselaw-parity-audit.service").read_text()
    timer = (sd / "opencaselaw-parity-audit.timer").read_text()
    assert "ExecStart=/usr/bin/python3 scripts/parity_audit.py" in svc
    for line in ("Type=oneshot", "Nice=19", "CPUWeight=10", "IOWeight=10",
                 "IOSchedulingClass=idle", "OnFailure=ntfy-alert@%n.service"):
        assert line in svc
    assert "decisions.db" in svc and "never opened" in svc
    assert "OnCalendar=Sun *-*-* 20:15:00 UTC" in timer
    assert "Persistent=false" in timer
    dropin = (sd / "opencaselaw-parity-audit.service.d" / "ntfy.conf").read_text()
    assert "EnvironmentFile=-/opt/caselaw/ops.env" in dropin
