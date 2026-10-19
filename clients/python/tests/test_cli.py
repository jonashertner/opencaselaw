import io
import json

import pytest
from opencaselaw_cli import cli
from opencaselaw_cli.client import APIError


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        item = self.pages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def page(ids, *, offset=0, total=10, lower=True, more=True):
    return {"results": [{"decision_id": id_, "court": "bger"} for id_ in ids],
            "returned": len(ids), "total": total, "total_is_lower_bound": lower,
            "has_more": more, "next_offset": offset + len(ids) if more else None,
            "offset": offset, "limit": len(ids), "result_set_id": "rs_fixture"}


def invoke(monkeypatch, capsys, argv, responses, stdin=""):
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "create_client", lambda args: client)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(stdin))
    monkeypatch.setenv("OCL_JOBS", "1")  # the fake answers in order; batches would interleave
    monkeypatch.setenv("OCL_CONFIG", "/nonexistent/ocl-config")
    code = cli.main(argv)
    out = capsys.readouterr()
    return client, code, out


def test_bounded_pagination_keeps_lower_bound_and_server_page_metadata():
    client = FakeClient([page(["a", "b"]), page(["c"], offset=2, lower=False)])
    result, code = cli.search(client, {"court": "bger", "offset": 0}, 3, 2)
    assert code == 0
    assert [row["decision_id"] for row in result["results"]] == ["a", "b", "c"]
    assert result["total_is_lower_bound"] is True
    assert result["has_more"] is True and result["next_offset"] == 3
    assert result["_client"]["max_results_reached"] is True
    assert result["_client"]["retrieval_complete"] is False
    assert [call[1]["limit"] for call in client.calls] == [2, 1]
    assert [call[1]["offset"] for call in client.calls] == [0, 2]
    assert result["_client"]["pages"][0]["result_set_id"] == "rs_fixture"


def test_text_query_is_one_ranked_request_within_the_server_cap():
    # Ranked pages are not composable: the candidate pool depends on the window.
    client = FakeClient([page([f"d{i}" for i in range(7)], more=True, total=500)])
    result, code = cli.search(client, {"query": "Rachekündigung"}, 7, 2)
    assert code == 0 and len(client.calls) == 1
    assert client.calls[0][1]["limit"] == 7 and client.calls[0][1]["offset"] == 0
    assert result["returned"] == 7 and result["has_more"] is True
    assert result["_client"]["ranked_single_request"] is True
    assert result["_client"]["retrieval_complete"] is False
    with pytest.raises(ValueError, match="800"):
        cli.search(FakeClient([]), {"query": "Rachekündigung"}, 801, 50)
    assert cli.search(FakeClient([page(["a"], more=False)]), {"court": "bge"}, 801, 50)[1] == 0


def test_filter_enumeration_drops_duplicate_rows_across_pages():
    client = FakeClient([page(["a", "b"]), page(["b", "c"], offset=2, more=False)])
    result, code = cli.search(client, {"court": "bge"}, 10, 2)
    assert code == 0
    assert [row["decision_id"] for row in result["results"]] == ["a", "b", "c"]
    assert result["returned"] == 3 and result["_client"]["duplicates_dropped"] == 1
    assert result["_client"]["ranked_single_request"] is False


def test_partial_search_failure_returns_evidence_and_retry_offset():
    client = FakeClient([page(["a", "b"]), APIError(503, "busy")])
    result, code = cli.search(client, {}, 4, 2)
    assert code == 4 and len(result["results"]) == 2
    assert result["has_more"] is True and result["next_offset"] == 2
    assert result["_client"]["errors"] == [{"offset": 2, "status": 503, "message": "busy"}]


def test_bounded_candidate_pool_does_not_claim_exhaustive_match_count():
    client = FakeClient([page(["a"], more=False, total=2000, lower=True)])
    result, code = cli.search(client, {}, 100)
    assert code == 0
    assert result["total"] == 2000 and result["total_is_lower_bound"] is True
    assert result["_client"]["retrieval_complete"] is True
    assert "exhaustive" not in result


def test_nonadvancing_pagination_stops_and_reports_partial():
    response = page(["a"])
    response["next_offset"] = 0
    client = FakeClient([response])
    result, code = cli.search(client, {}, 100)
    assert code == 4 and len(client.calls) == 1
    assert result["results"][0]["decision_id"] == "a"
    assert result["_client"]["errors"]


def test_jsonl_search_pipes_into_get_without_losing_error_metadata(monkeypatch, capsys):
    _, code, output = invoke(monkeypatch, capsys,
        ["decisions", "search", "OR", "--format", "jsonl", "--fields", "decision_id", "--max-results", "2"],
        [page(["a", "b"])])
    assert code == 0 and not output.err
    lines = [json.loads(line) for line in output.out.splitlines()]
    assert lines[:2] == [{"decision_id": "a"}, {"decision_id": "b"}]
    assert lines[2]["_type"] == "pagination" and lines[2]["total_is_lower_bound"] is True
    client, code, output = invoke(monkeypatch, capsys,
        ["decisions", "get", "--stdin", "--format", "jsonl"],
        [{"decision_id": "a", "full_text": "exact source"}, APIError(404, "missing")], output.out)
    assert code == 4 and "failed" in output.err
    lines = [json.loads(line) for line in output.out.splitlines()]
    assert lines[0]["full_text"] == "exact source"
    assert lines[1]["errors"][0]["decision_id"] == "b"
    assert len(client.calls) == 2


def test_invalid_batch_is_rejected_before_any_requests(monkeypatch, capsys):
    client, code, output = invoke(monkeypatch, capsys, ["decisions", "get", "--stdin"], [], 'valid\n{"wrong":"id"}\n')
    assert code == 2 and not client.calls and not output.out
    assert "stdin:2" in output.err


def test_cite_per_reference_pinpoint_overrides_common_default(monkeypatch, capsys):
    # Every reference is identified (cite, then the decision record); the
    # pinpoint is formatted only after the passage was found.
    hit86 = {"exists": True, "decision_id": "bge_BGE_140_III_86", "citation_string": "ATF 140 III 86", "citation_string_de": "BGE 140 III 86"}
    hit513 = {"exists": True, "decision_id": "bge_BGE_136_III_513", "citation_string": "ATF 136 III 513", "citation_string_de": "BGE 136 III 513"}
    client, code, output = invoke(monkeypatch, capsys, ["cite", "--stdin", "--pinpoint", "1", "--language", "fr"],
        [dict(hit86), {"decision_id": "bge_BGE_140_III_86", "citation_string_de": "BGE 140 III 86"},
         {"decision_id": "bge_BGE_140_III_86", "e_number": "2.3", "text": "served"}, {"citation_string": "ATF 140 III 86, consid. 2.3"},
         dict(hit513), {"decision_id": "bge_BGE_136_III_513", "citation_string_de": "BGE 136 III 513"},
         {"decision_id": "bge_BGE_136_III_513", "e_number": "1", "text": "served"}, {"citation_string": "ATF 136 III 513, consid. 1"}],
        '{"reference":"BGE 140 III 86","pinpoint":"2.3"}\n{"reference":"BGE 136 III 513","pinpoint":null}\n')
    assert code == 0
    assert [c[0] for c in client.calls] == ["/api/cite", "/api/decisions/bge_BGE_140_III_86", "/api/erwaegung/bge_BGE_140_III_86/2.3", "/api/cite",
                                            "/api/cite", "/api/decisions/bge_BGE_136_III_513", "/api/erwaegung/bge_BGE_136_III_513/1", "/api/cite"]
    assert client.calls[0] == ("/api/cite", {"reference": "BGE 140 III 86", "language": "fr"})
    assert client.calls[3] == ("/api/cite", {"reference": "bge_BGE_140_III_86", "pinpoint": "2.3", "language": "fr"})
    rows = json.loads(output.out)["results"]
    assert rows[0]["citation_string"] == "ATF 140 III 86, consid. 2.3" and rows[0]["identity_check"]["method"] == "exact_server_citation"
    assert rows[1]["citation_string"] == "ATF 136 III 513, consid. 1"
    # one row with an invalid pinpoint fails alone; the batch goes on
    client, code, output = invoke(monkeypatch, capsys, ["cite", "--stdin", "--format", "json"],
        [dict(hit513), {"decision_id": "bge_BGE_136_III_513", "citation_string_de": "BGE 136 III 513"}],
        '{"reference":"BGE 140 III 86","pinpoint":"foo"}\n{"reference":"BGE 136 III 513"}\n')
    out = json.loads(output.out)
    assert code == 4 and out["errors"][0]["status"] == 400 and "foo" in out["errors"][0]["message"] and out["results"][0]["decision_id"] == "bge_BGE_136_III_513"


def test_plain_input_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / "ids.txt"
    path.write_text("\ufeffa\nb\n", encoding="utf-8")  # editors may prepend a BOM
    client, code, _output = invoke(monkeypatch, capsys, ["decisions", "get", "--input", str(path), "--no-full-text"],
        [{"decision_id": "a"}, {"decision_id": "b"}])
    assert code == 0 and len(client.calls) == 2
    assert all(call[1] == {"full_text": False} for call in client.calls)


def test_top_level_error_is_stderr_only(monkeypatch, capsys):
    # A passage the service does not have is an answer (exit 4), reported on stdout.
    _, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "a", "2.3"], [APIError(404, "no passage")])
    assert code == 4 and json.loads(output.out)["error"]["status"] == 404 and "ocl:" in output.err
    _, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "a", "2.3"], [APIError(None, "Request failed: refused")])
    assert code == 3 and not output.out and json.loads(output.err)["error"]["status"] is None


def test_http_200_error_is_retained_with_failure_exit(monkeypatch, capsys):
    _, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "a", "2.3", "--fields", "text"],
                            [{"error": "no structured passage", "hint": "retrieve full decision"}])
    assert code == 4
    out = json.loads(output.out)
    assert out["error"] == {"status": 200, "message": "no structured passage"} and out["hint"] == "retrieve full decision"
    assert out["requested_e_number"] == "2.3"


def test_missing_citation_remains_explicit_under_field_projection(monkeypatch, capsys):
    _, code, output = invoke(monkeypatch, capsys, ["cite", "unknown", "--fields", "citation_string"],
                            [{"exists": False, "close_matches": ["candidate"], "warning": "unresolved"}])
    assert code == 4
    assert json.loads(output.out) == {"exists": False, "close_matches": ["candidate"], "warning": "unresolved"}


def test_citation_graph_jsonl_keeps_direction_and_pagination(monkeypatch, capsys):
    _, code, output = invoke(monkeypatch, capsys,
        ["citations", "list", "a", "--format", "jsonl", "--fields", "source_decision_id"],
        [{"incoming": [{"source_decision_id": "b", "confidence_score": 0.8}], "outgoing": [],
          "incoming_has_more": True, "outgoing_has_more": False, "next_offset": 1}])
    assert code == 0
    rows = [json.loads(line) for line in output.out.splitlines()]
    assert rows[0] == {"source_decision_id": "b", "_direction": "incoming"}
    assert rows[1]["incoming_has_more"] is True and rows[1]["next_offset"] == 1


@pytest.mark.parametrize("argv", [
    ["--format", "jsonl", "decisions", "search", "OR"],
    ["decisions", "--format", "jsonl", "search", "OR"],
    ["decisions", "search", "OR", "--format", "jsonl"],
])
def test_common_flags_work_at_each_level(argv):
    args = cli.build_parser().parse_args(argv)
    assert args.format == "jsonl" and args.base_url == "https://mcp.opencaselaw.ch"


def test_workflow_parser_contract():
    args = cli.build_parser().parse_args(["bundle", "create", "Art. 41 OR", "--out", "evidence", "--law", "OR:41", "--passage", "2.3", "--resume"])
    assert (args.command, args.action, args.query, args.out) == ("bundle", "create", "Art. 41 OR", "evidence")
    assert args.law == ["OR:41"] and args.passage == ["2.3"] and args.resume
    args = cli.build_parser().parse_args(["citations", "resolve", "BGE 140 III 86", "--language", "fr"])
    assert args.references == ["BGE 140 III 86"] and args.language == "fr"


def test_help_has_no_network_side_effect(monkeypatch, capsys):
    monkeypatch.setattr(cli, "create_client", lambda args: pytest.fail("help must not create a client"))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["decisions", "search", "--help"])
    assert exit_info.value.code == 0
    assert "--max-results" in capsys.readouterr().out


def test_every_command_and_argument_explains_itself():
    parser = cli.build_parser()
    missing = []

    def walk(p, path):
        for action in p._actions:
            if isinstance(action, cli.argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    if not sub.description:
                        missing.append(path + [name, "<description>"])
                    walk(sub, path + [name])
            elif action.dest not in ("help", "version") and not (action.help or "").strip():
                missing.append(path + [action.dest])

    walk(parser, ["ocl"])
    assert not missing, missing
    assert "examples:" in parser.format_help()


def test_closed_pipe_exits_cleanly(monkeypatch):
    class ClosedPipe:
        def write(self, _):
            raise BrokenPipeError()

    monkeypatch.setattr(cli, "create_client", lambda args: FakeClient([{"decision_id": "a"}]))
    monkeypatch.setattr(cli.sys, "stdout", ClosedPipe())
    assert cli.main(["decisions", "get", "a"]) == 0


def test_projection_keeps_server_qualification():
    payload = {"citation_string": "verbatim citation", "decision_date_warning": "source discrepancy",
               "rule_statement_note": "quotation withheld"}
    assert cli._project(payload, ["citation_string"]) == payload


@pytest.mark.parametrize("argv,path", [
    (["decisions", "get", "4A_747/2012"], "/api/decisions/bger_4A_747_2012"),
    (["decisions", "passage", "4A_747/2012", "2.3"], "/api/erwaegung/bger_4A_747_2012/2.3"),
    (["citations", "list", "4A_747/2012"], "/api/citations/bger_4A_747_2012"),
])
def test_docket_slashes_resolve_via_server_before_path_request(monkeypatch, capsys, argv, path):
    client, code, _output = invoke(monkeypatch, capsys, argv,
        [{"exists": True, "decision_id": "bger_4A_747_2012"},
         {"decision_id": "bger_4A_747_2012", "docket_number": "4A 747/2012"},
         {"is_case_number": True, "exact": True, "results": [{"decision_id": "bger_4A_747_2012", "docket_number": "4A 747/2012"}]},
         {"decision_id": "bger_4A_747_2012", "e_number": "2.3", "text": "served"}])
    assert code == 0
    assert client.calls[0] == ("/api/cite", {"reference": "4A_747/2012", "language": "de"})
    assert client.calls[1] == ("/api/decisions/bger_4A_747_2012", {"full_text": False})
    assert client.calls[2][0] == "/api/lookup" and client.calls[2][1]["exact"] is True
    assert client.calls[3][0] == path


def test_docket_fragment_matched_by_substring_is_rejected(monkeypatch, capsys):
    # The service resolves "247/2020" to the newest decision whose docket
    # contains it; the client must not print another chamber's passage.
    client, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "247/2020", "2"],
        [{"exists": True, "decision_id": "bger_6B_1247_2020", "citation_string_de": "BGer 6B_1247/2020 vom 7. Oktober 2021"},
         {"decision_id": "bger_6B_1247_2020", "docket_number": "6B_1247/2020"},
         {"is_case_number": True, "exact": True, "results": []}])
    assert code == 4 and len(client.calls) == 3 and not output.out
    assert "6B_1247/2020" in json.loads(output.err)["error"]["message"]
    assert json.loads(output.err)["error"]["kind"] == "resolution"


def test_unresolved_docket_never_fabricates_a_canonical_id(monkeypatch, capsys):
    miss = {"exists": False, "close_matches": [{"decision_id": "different"}]}
    client, code, output = invoke(monkeypatch, capsys, ["decisions", "get", "4A_00000/2012"], [dict(miss), dict(miss)])
    # the underscore form, then the pre-2007 dot form; the close match is never taken
    assert code == 4 and [c[1]["reference"] for c in client.calls] == ["4A_00000/2012", "4A.00000/2012"]
    assert json.loads(output.out)["errors"][0]["decision_id"] == "4A_00000/2012"


def test_projection_keeps_workflow_completeness_and_artifact_pointer():
    payload = {"status": "partial", "bundle": "/tmp/evidence", "manifest": "/tmp/evidence/manifest.json",
               "completeness": {"selected_evidence_retrieved": False}, "scope": "bounded selection"}
    assert cli._project(payload, ["status"]) == payload


def test_law_projection_retains_edition_and_original_source():
    payload = {"articles": [{"article_num": "41", "text": "source"}], "version": "historical",
               "snapshot_date": "2017-04-01", "source_url": "https://www.fedlex.admin.ch/fixture"}
    assert cli._project(payload, ["articles"]) == payload


def test_projection_retains_citation_identity_evidence():
    payload = {"decision_id": "a", "status": "resolution_incomplete",
               "identity_check": {"method": "exact_candidate_label", "candidate_window_may_be_capped": True}}
    assert cli._project(payload, ["decision_id"]) == payload


def test_config_file_and_environment_set_defaults(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text("base_url = https://research.example\nlanguage = fr\njobs = 2\n# comment\nformat = jsonl\n")
    monkeypatch.setenv("OCL_CONFIG", str(config))
    monkeypatch.delenv("OCL_LANGUAGE", raising=False)
    args = cli.build_parser().parse_args(["laws", "get", "OR"])
    assert (args.base_url, args.language, args.jobs, args.format) == ("https://research.example", "fr", 2, "jsonl")
    monkeypatch.setenv("OCL_LANGUAGE", "it")
    monkeypatch.setenv("OCL_JOBS", "5")
    args = cli.build_parser().parse_args(["laws", "get", "OR", "--jobs", "1"])
    assert (args.language, args.jobs) == ("it", 1)  # environment beats file; flag beats both
    config.write_text("colour = red\n")
    with pytest.raises(ValueError, match="config line 1"):
        cli.load_config()
    config.write_text("format = yaml\n")
    with pytest.raises(ValueError, match="format must be one of"):
        cli.load_config()


def test_completion_candidates_follow_the_parser():
    parser = cli.build_parser(config={})
    assert "decisions" in cli.complete(parser, [""]) and "__complete" not in cli.complete(parser, [""])
    assert cli.complete(parser, ["dec"]) == ["decisions"]
    level = cli.complete(parser, ["decisions", ""])
    assert {"get", "passage", "search", "--help", "--format"} <= set(level) and "__complete" not in level
    assert "--max-results" in cli.complete(parser, ["decisions", "search", "--"])
    assert cli.complete(parser, ["decisions", "search", "--sort", ""]) == ["date_asc", "date_desc", "published_desc", "relevance"]
    assert cli.complete(parser, ["decisions", "search", "--sort", "date_desc", "--m"]) == ["--marked-for-publication", "--max-results"]
    assert "--sort" not in cli.complete(parser, ["decisions", "search", "--sort", "date_desc", "--"])
    for shell in ("bash", "zsh", "fish"):
        script = cli._COMPLETION_SCRIPTS[shell]
        assert "ocl __complete" in script


def test_completion_and_complete_commands_print_scripts(monkeypatch, capsys):
    monkeypatch.setenv("OCL_CONFIG", "/nonexistent/ocl-config")
    assert cli.main(["completion", "zsh"]) == 0
    assert capsys.readouterr().out.startswith("#compdef ocl")
    assert cli.main(["__complete", "--", "laws", "get", "OR", "--lang"]) == 0
    assert capsys.readouterr().out.strip() == "--language"


def test_cite_pinpoint_is_verified_before_it_is_formatted(monkeypatch, capsys):
    hit = {"exists": True, "decision_id": "bge_BGE_140_III_86", "citation_string": "BGE 140 III 86, E. 2.3"}
    record = {"decision_id": "bge_BGE_140_III_86", "citation_string_de": "BGE 140 III 86"}
    client, code, output = invoke(monkeypatch, capsys, ["cite", "BGE 140 III 86", "--pinpoint", "2.3", "--format", "json"],
                                  [dict(hit), dict(record), {"error": "E. '2.3' not found", "available_e_numbers": ["2", "4.1"]}])
    assert code == 4 and client.calls[2][0] == "/api/erwaegung/bge_BGE_140_III_86/2.3" and len(client.calls) == 3
    out = json.loads(output.out)
    assert out["pinpoint_exists"] is False and out["available_e_numbers"] == ["2", "4.1"] and "not in the structure index" in out["pinpoint_note"]
    assert out["citation_string"] == "BGE 140 III 86, E. 2.3"  # the fake's decision-level answer; never reformatted with the missing pinpoint
    client, code, output = invoke(monkeypatch, capsys, ["cite", "BGE 140 III 86", "--pinpoint", "2.3", "--format", "json"],
                                  [dict(hit), dict(record), {"decision_id": "bge_BGE_140_III_86", "e_number": "2.3", "text": "served"},
                                   {"citation_string": "BGE 140 III 86, E. 2.3 (formatted)"}])
    assert code == 0 and json.loads(output.out)["pinpoint_status"] == "retrieved"
    assert json.loads(output.out)["citation_string"] == "BGE 140 III 86, E. 2.3 (formatted)"
    client, code, _ = invoke(monkeypatch, capsys, ["cite", "BGE 140 III 86", "--pinpoint", "2.3", "--no-verify-pinpoint", "--format", "json"],
                             [dict(hit), dict(record), {"citation_string": "BGE 140 III 86, E. 2.3"}])
    assert code == 0 and len(client.calls) == 3 and client.calls[2][1]["pinpoint"] == "2.3"
    # a transport failure on the passage fetch is exit 3, not "pinpoint unavailable"
    client, code, output = invoke(monkeypatch, capsys, ["cite", "BGE 140 III 86", "--pinpoint", "2.3", "--format", "json"],
                                  [dict(hit), dict(record), APIError(None, "Request failed: reset")])
    assert code == 3 and json.loads(output.out)["errors"][0]["status"] is None
    # a docket fragment the service matches by substring is never cited (blocker from the review)
    client, code, output = invoke(monkeypatch, capsys, ["cite", "247/2020", "--format", "json"],
                                  [{"exists": True, "decision_id": "bger_6B_1247_2020", "citation_string": "BGer 6B_1247/2020 vom 7. Oktober 2021"},
                                   {"decision_id": "bger_6B_1247_2020", "docket_number": "6B_1247/2020", "court": "bger"},
                                   {"is_case_number": True, "exact": True, "results": []}])
    assert code == 4 and json.loads(output.out)["errors"][0]["kind"] == "resolution" and "6B_1247/2020" in json.loads(output.out)["errors"][0]["message"]
    # a missing reference still returns the service's answer with its close matches
    client, code, output = invoke(monkeypatch, capsys, ["cite", "4A_00000/2012", "--format", "json"],
                                  [{"exists": False, "close_matches": [{"decision_id": "x"}]}, {"exists": False, "close_matches": [{"decision_id": "x"}]}])
    assert code == 4 and json.loads(output.out)["exists"] is False and json.loads(output.out)["close_matches"] == [{"decision_id": "x"}]
    # a rate limit is a transport failure (3), a 404 is "not there" (4)
    client, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "a", "2"], [APIError(429, "slow down")])
    assert code == 3
    client, code, output = invoke(monkeypatch, capsys, ["decisions", "get", "a", "b", "--no-full-text"], [{"decision_id": "a"}, APIError(429, "slow down")])
    assert code == 3
    # an inline pinpoint is read from the reference and verified the same way
    client, code, output = invoke(monkeypatch, capsys, ["cite", "BGE 140 III 86 E. 99", "--format", "json"],
                                  [dict(hit), {"decision_id": "bge_BGE_140_III_86", "citation_string_de": "BGE 140 III 86"},
                                   {"error": "E. '99' not found", "available_e_numbers": ["2"]}])
    out = json.loads(output.out)
    assert code == 4 and out["pinpoint"] == "99" and out["pinpoint_source"] == "reference" and out["pinpoint_exists"] is False
    assert len(client.calls) == 3  # the identifying cite answer is reused; no second decision-level request


def test_batch_runs_concurrently_and_keeps_input_order(monkeypatch):
    import threading, time
    class OrderedClient:
        def __init__(self):
            self.lock = threading.Lock(); self.active = 0; self.peak = 0
        def get(self, path, params=None):
            with self.lock:
                self.active += 1; self.peak = max(self.peak, self.active)
            time.sleep(0.02)
            with self.lock:
                self.active -= 1
            return {"decision_id": path.rsplit("/", 1)[-1]}
    client = OrderedClient()
    args = cli.build_parser(config={"jobs": 4}).parse_args(["decisions", "get", "a", "b", "c", "d", "e", "f"])
    result, code = cli._batch(args, client, "get")
    assert code == 0 and [r["decision_id"] for r in result["results"]] == list("abcdef") and client.peak > 1


def test_batch_breaker_stops_after_repeated_transport_failures(monkeypatch, capsys):
    responses = [APIError(None, "Request failed: refused")] * 5
    client, code, output = invoke(monkeypatch, capsys, ["decisions", "get"] + [f"id{i}" for i in range(9)] + ["--format", "json"], responses)
    assert code == 3 and len(client.calls) == 5
    errors = json.loads(output.out)["errors"]
    assert len(errors) == 9 and "skipped after 5 consecutive" in errors[-1]["message"]


def test_table_csv_and_md_formats(monkeypatch, capsys):
    rows = [page(["a", "b"], more=False, lower=False, total=2)]
    rows[0]["results"][0]["citation_string_de"] = "BGE 1 I 1"; rows[0]["results"][0]["decision_date"] = "2020-01-01"
    _, code, output = invoke(monkeypatch, capsys, ["decisions", "search", "x", "--max-results", "2", "--format", "table"], [json.loads(json.dumps(rows[0]))])
    assert code == 0 and output.out.splitlines()[0].startswith("decision_id  citation") and "2 shown of 2 matching" in output.out
    _, code, output = invoke(monkeypatch, capsys, ["decisions", "search", "x", "--max-results", "2", "--format", "csv"], [json.loads(json.dumps(rows[0]))])
    assert code == 0 and output.out.splitlines()[0] == "decision_id,citation,court,decision_date,docket_number,title" and "a,BGE 1 I 1,bger,2020-01-01,," in output.out
    _, code, output = invoke(monkeypatch, capsys, ["decisions", "search", "x", "--max-results", "2", "--format", "md"], [json.loads(json.dumps(rows[0]))])
    assert code == 0 and output.out.startswith("| decision_id | citation | court | decision_date |") and "| a | BGE 1 I 1 |" in output.out
    _, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "a", "2", "--format", "md"],
                             [{"decision_id": "a", "e_number": "2", "citation_string_de": "BGE 1 I 1, E. 2", "text": "Quoted [law](https://x) text", "canonical_url": "https://u"}])
    assert code == 0 and output.out.startswith("**BGE 1 I 1, E. 2** (https://u)") and "> Quoted law text" in output.out
    _, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "a", "2", "--format", "csv"], [{"decision_id": "a", "e_number": "2", "text": "t"}])
    assert code == 2 and "needs a list result" in output.err


def test_long_form_docket_reference_in_get_and_passage(monkeypatch, capsys):
    client, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "BGer 4A_747/2012 vom 5. April 2013", "1", "--format", "json", "--fields", "e_number"],
        [{"exists": True, "decision_id": "bger_4A_747_2012"},
         {"decision_id": "bger_4A_747_2012", "docket_number": "4A 747/2012", "court": "bger"},
         {"is_case_number": True, "exact": True, "results": [{"decision_id": "bger_4A_747_2012", "docket_number": "4A 747/2012", "court": "bger"}]},
         {"decision_id": "bger_4A_747_2012", "e_number": "1", "text": "served"}])
    assert code == 0 and json.loads(output.out)["e_number"] == "1"
    assert client.calls[0][1]["reference"] == "4A_747/2012"  # the docket inside the long form is what is queried
    # a lettered sub-number the index lacks returns its parent with a note and exit 4
    client, code, output = invoke(monkeypatch, capsys, ["decisions", "passage", "bge_BGE_125_II_633", "2a", "--format", "json"],
        [{"error": "E. '2a' not found", "available_e_numbers": ["1", "2", "3"]},
         {"decision_id": "bge_BGE_125_II_633", "e_number": "2", "text": "a) Das Rekursgericht [BGE 1 I 1](https://x/1) hat"}])
    out = json.loads(output.out)
    assert code == 4 and out["e_number"] == "2" and out["requested_e_number"] == "2a" and "not indexed as such" in out["note"]
    assert out["text_plain"] == "a) Das Rekursgericht BGE 1 I 1 hat"


def test_verbose_logs_requests_and_counts_them(monkeypatch, capsys):
    from opencaselaw_cli.client import Client
    import io as _io
    lines = []
    client = Client(opener=lambda request, timeout: _io.BytesIO(b'{"results": [], "total": 0, "total_is_lower_bound": false, "has_more": false, "next_offset": null}'),
                    sleep=lambda s: None, log=lines.append)
    result, code = cli.search(client, {"court": "bge"}, 5, 5)
    assert code == 0 and result["_client"]["requests"] == 1 and client.requests == 1
    assert lines and lines[0].startswith("GET https://mcp.opencaselaw.ch/api/decisions?") and " 200 " in lines[0]
    args = cli.build_parser(config={}).parse_args(["decisions", "search", "--verbose"])
    assert args.verbose is True


def test_heading_only_statute_text_is_unresolved(monkeypatch, capsys):
    flagged = {"abbreviation": "OR", "sr_number": "220", "as_of": "2010-01-01", "text_source": "fedlex_pdf", "text_status": "heading_only",
               "note": "UNRESOLVED: no article text recovered", "articles": [{"article_num": "336c", "text": "Art. 336c", "text_status": "heading_only"}]}
    _, code, output = invoke(monkeypatch, capsys, ["laws", "get", "OR", "--article", "336c", "--as-of", "2010-01-01", "--format", "json"], [dict(flagged)])
    assert code == 4 and json.loads(output.out)["text_status"] == "heading_only"
    ok = {**flagged, "text_status": "ok", "articles": [{"article_num": "336c", "text": "1 Nach Ablauf der Probezeit ...", "text_status": "ok"}]}
    _, code, _ = invoke(monkeypatch, capsys, ["laws", "get", "OR", "--article", "336c", "--as-of", "2010-01-01", "--format", "json"], [dict(ok)])
    assert code == 0


def test_search_passes_publication_filters_and_sort(monkeypatch, capsys):
    client, code, _ = invoke(monkeypatch, capsys, [
        "decisions", "search", "--court", "bger", "--published-from", "2026-10-13",
        "--published-to", "2026-10-16", "--sort", "published_desc", "--format", "jsonl"],
        [page(["a"], more=False, total=1, lower=False)])
    assert code == 0
    path, params = client.calls[0]
    assert path == "/api/decisions"
    assert params["published_from"] == "2026-10-13" and params["published_to"] == "2026-10-16"
    assert params["sort"] == "published_desc"


def test_search_without_publication_filters_sends_none(monkeypatch, capsys):
    client, _, _ = invoke(monkeypatch, capsys, ["decisions", "search", "--court", "bger"],
                          [page(["a"], more=False, total=1, lower=False)])
    params = client.calls[0][1]
    assert params["published_from"] is None and params["published_to"] is None  # dropped by Client.get
