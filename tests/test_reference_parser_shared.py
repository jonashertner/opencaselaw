"""The server's reference parser is the client's, byte for byte (below the docstring)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _body(text: str) -> str:
    # drop the module docstring, which names the copy
    end = text.index('"""', 3) + 3
    return text[end:]


def test_parser_evg_chambers_are_decision_refs():
    import decision_ref
    import reference_parser
    assert frozenset(reference_parser._EVG_CHAMBERS) == decision_ref.EVG_CHAMBERS


def test_server_parser_is_the_client_parser():
    server = (ROOT / "reference_parser.py").read_text(encoding="utf-8")
    client = (ROOT / "clients/python/src/opencaselaw_cli/references.py").read_text(encoding="utf-8")
    assert _body(server) == _body(client)
