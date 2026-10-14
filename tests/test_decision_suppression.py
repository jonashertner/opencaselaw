"""Host-local decision suppression (privacy takedowns). Offline; the ids are
made up and follow the corpus id shapes."""
import asyncio
import contextvars
import json
from dataclasses import dataclass

import pytest

import decision_suppression as ds

IDS = ["bvger_D-1111_2023", "bvger_D-222_2026", "bger_9X_77_2026"]


@dataclass
class TC:  # stands in for mcp.types.TextContent
    type: str
    text: str


@pytest.fixture
def s():
    return ds.Suppression.from_ids(IDS)


@pytest.fixture(autouse=True)
def _reset():
    ds._reset_for_tests()
    yield
    ds._reset_for_tests()


@pytest.mark.parametrize("text", [
    "bvger_D-1111_2023", "D-1111/2023", "d-1111/2023", "D-1111_2023",
    "BVGer D-1111/2023 vom 1. Januar 2024", "D-222 / 2026", "9X_77/2026",
    "/entscheid/bvger_D-222_2026#e-3",
])
def test_mentions_ids_and_dockets(s, text):
    assert s.mentions(text)


@pytest.mark.parametrize("text", [
    "D-11110/2023", "D-1111/20230", "ED-1111/2023", "D-2220/2026",
    "D-1111/2024", "bvger_D-1111_20231", "Abteilung IV", "",
])
def test_neighbouring_dockets_are_not_matched(s, text):
    assert not s.mentions(text)


def test_args_hit_reads_every_argument(s):
    assert s.args_hit({"decision_id": "bvger_D-1111_2023"})
    assert s.args_hit({"decision_ids": ["x", "D-222/2026"]})
    assert s.args_hit({"query": "Asyl D-1111/2023"})
    assert not s.args_hit({"query": "Asyl Wegweisung", "court": "bvger"})


def test_empty_list_is_a_no_op():
    s = ds.Suppression.from_ids([])
    payload = {"results": [{"decision_id": "bvger_D-1111_2023"}]}
    assert not s.mentions("D-1111/2023")
    assert s.filter_obj(payload) is payload
    assert s.scrub_text("x bvger_D-1111_2023") == "x bvger_D-1111_2023"


def test_json_results_drop_suppressed_records(s):
    text = json.dumps({"total": 3, "results": [
        {"decision_id": "bvger_D-1111_2023", "snippet": "secret"},
        {"decision_id": "bvger_D-5_2020", "snippet": "keep"},
        {"decision": {"decision_id": "bger_9X_77_2026"}, "snippet": "nested secret"},
        {"id": "x", "url": "https://mcp.opencaselaw.ch/entscheid/bvger_D-222_2026"},
    ]}, separators=(",", ":"))
    out = json.loads(s.scrub_text(text))
    assert [r.get("snippet") for r in out["results"]] == ["keep"]
    assert "secret" not in s.scrub_text(text)


def test_json_top_level_record_becomes_not_available(s):
    out = json.loads(s.scrub_text(json.dumps({"decision_id": "bvger_D-1111_2023",
                                              "full_text": "secret"})))
    assert out == ds.NOT_AVAILABLE


def test_unlink_keeps_the_court_text(s):
    text = ("vgl. Urteil [BVGer D-1111/2023](https://mcp.opencaselaw.ch/entscheid/"
            "bvger_D-1111_2023#e-6-1) E. 6.1 und [BVGer D-5/2020]"
            "(https://mcp.opencaselaw.ch/entscheid/bvger_D-5_2020)")
    out = s.scrub_text(text)
    assert out.startswith("vgl. Urteil BVGer D-1111/2023 E. 6.1 und [BVGer D-5/2020](")
    assert "bvger_D-1111_2023" not in out


def test_markdown_blocks_headed_by_a_suppressed_decision_are_dropped(s):
    text = "\n".join([
        "Retrieved 2 decisions.", "",
        "## [D-1111/2023](https://mcp.opencaselaw.ch/entscheid/bvger_D-1111_2023)",
        "court: bvger", "**Text**", "secret facts", "", "---", "",
        "## [D-5/2020](https://mcp.opencaselaw.ch/entscheid/bvger_D-5_2020)",
        "court: bvger", "kept facts",
    ])
    out = s.scrub_text(text)
    assert "secret facts" not in out and "D-1111" not in out
    assert "kept facts" in out and "Retrieved 2 decisions." in out


def test_numbered_and_bulleted_result_items_are_dropped(s):
    url = "https://mcp.opencaselaw.ch/entscheid/"
    text = "\n".join([
        "Found 3 decisions (showing 1–3):", "",
        f"**1.** [D-1111/2023]({url}bvger_D-1111_2023) (2024-01-18) [bvger] [de]",
        "   Title: D-1111/2023", "   ...secret snippet...", "",
        f"**2.** [D-5/2020]({url}bvger_D-5_2020) (2020-01-01) [bvger] [de]",
        "   ...kept snippet...", "",
        "Prior instances:",
        f"- [D-222/2026]({url}bvger_D-222_2026) (2026-03-04) [bvger] conf=0.90",
        f"- [D-5/2020]({url}bvger_D-5_2020) (2020-01-01) [bvger] conf=0.80",
    ])
    out = s.scrub_text(text)
    assert "secret" not in out and "D-1111" not in out and "D-222" not in out
    assert "kept snippet" in out and out.count("D-5/2020") == 2


def test_court_text_bullet_citing_a_suppressed_docket_stays_verbatim(s):
    url = "https://mcp.opencaselaw.ch/entscheid/bvger_D-1111_2023"
    text = (f"- vgl. Urteil [BVGer D-1111/2023]({url}) E. 6.1;\n"
            "  zweite Zeile des Gerichtstexts\n- nächster Punkt")
    assert s.scrub_text(text) == ("- vgl. Urteil BVGer D-1111/2023 E. 6.1;\n"
                                  "  zweite Zeile des Gerichtstexts\n- nächster Punkt")


def test_current_reads_and_reloads_the_file(tmp_path, monkeypatch):
    f = tmp_path / "suppressed.txt"
    monkeypatch.setenv("OCL_SUPPRESSED_DECISIONS_FILE", str(f))
    monkeypatch.setattr(ds, "RELOAD_S", 0.0)
    assert not ds.current().ids                      # missing file
    f.write_text("# takedowns\nbvger_D-1111_2023  # reason kept elsewhere\n\n")
    assert ds.current().ids == {"bvger_D-1111_2023"}
    f.write_text("")
    import os
    os.utime(f, ns=(1, 1))                           # force a new mtime
    assert not ds.current().ids


def _run(coro):
    return asyncio.run(coro)


def test_wrapper_refuses_and_filters(tmp_path, monkeypatch):
    f = tmp_path / "suppressed.txt"
    f.write_text("\n".join(IDS))
    monkeypatch.setenv("OCL_SUPPRESSED_DECISIONS_FILE", str(f))
    payload_var = contextvars.ContextVar("p", default=None)
    calls = []

    async def inner(name, arguments):
        calls.append(name)
        payload_var.set({"results": [{"decision_id": "bvger_D-1111_2023"},
                                     {"decision_id": "ok"}]})
        return [TC("text", json.dumps({"results": [
            {"decision_id": "bvger_D-1111_2023"}, {"decision_id": "ok"}]}))]

    wrapped = ds.wrap_tool_handler(inner, payload_var, TC)

    refused = _run(wrapped("get_decision", {"decision_id": "D-1111/2023"}))
    assert json.loads(refused[0].text) == ds.NOT_AVAILABLE and calls == []

    async def search():
        out = await wrapped("search_decisions", {"query": "Asyl"})
        return out, payload_var.get()

    out, payload = _run(search())
    assert json.loads(out[0].text) == {"results": [{"decision_id": "ok"}]}
    assert payload == {"results": [{"decision_id": "ok"}]}


def test_wrapper_filters_tuple_results(s):
    content = [TC("text", json.dumps([{"id": "bger_9X_77_2026"}, {"id": "ok"}]))]
    structured = {"results": [{"id": "bger_9X_77_2026"}, {"id": "ok"}]}
    c, st = ds.filter_tool_result(s, (content, structured), TC)
    assert json.loads(c[0].text) == [{"id": "ok"}]
    assert st == {"results": [{"id": "ok"}]}
