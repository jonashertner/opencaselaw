"""The MCP App widget must speak every host dialect (2026-07-29).

Verified empirically with a Playwright iframe harness
(scripts/widget_dialect_harness.py), not by reading the code:

  INBOUND  — window.openai.toolOutput, MCP-UI postMessage, and the official
             ui/ JSON-RPC notification all render results correctly.
  OUTBOUND — before this was fixed, a click against an official MCP Apps
             host emitted only {type:"tool"}, which such a host ignores.
             The widget rendered, showed results, and every button was
             inert — worse for the user than showing no widget at all.

The three outbound shapes are mutually unrecognisable (no `type` vs no
`jsonrpc`), so a host acts on exactly one and ignores the others.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import law_widget  # noqa: E402

HTML = law_widget.LAW_SEARCH_WIDGET_HTML


def test_outbound_speaks_all_three_dialects():
    assert "window.openai.callTool" in HTML, "OpenAI/ChatGPT hosts"
    assert '"tools/call"' in HTML or "method:\"tools/call\"" in HTML, \
        "official MCP Apps hosts (Claude, VS Code, M365)"
    assert 'type:"tool"' in HTML, "MCP-UI hosts"


def test_outbound_goes_through_one_helper():
    """Every call site must use callServerTool — a bespoke postMessage at a
    new call site is how one dialect silently gets dropped again."""
    assert "function callServerTool(" in HTML
    assert HTML.count("callServerTool(") >= 3          # definition + 2 uses
    # no call site may hand-roll the legacy shape any more
    assert HTML.count('postMessage({ type:"tool"') <= 1


def test_jsonrpc_envelope_is_well_formed():
    # anchor on the CODE form; the explanatory comment also says "tools/call"
    i = HTML.index('method:"tools/call"')
    frag = HTML[max(0, i - 200):i + 200]
    assert 'jsonrpc:"2.0"' in frag
    assert "params:{ name:name, arguments:args }" in frag
    assert "id:(++__rpcId)" in frag, "each request needs a distinct id"


def test_inbound_still_accepts_every_shape():
    assert "window.openai" in HTML                      # global / set_globals
    assert "openai:set_globals" in HTML
    assert "addEventListener" in HTML and "message" in HTML


def test_widget_stays_self_contained():
    """Apps run under a deny-by-default CSP; external refs would need an
    explicit _meta.ui.csp entry."""
    import re
    assert not re.findall(r'(?:src|href)=["\']https?://', HTML)


# ── MCP Apps lifecycle (2026-11-10) ─────────────────────────────────────────
# Spec 2026-01-26: "The Host MUST NOT send any request or notification to the
# View before it receives an `initialized` notification." Both widgets lacked
# the handshake, so in Claude (or any spec host) they never got their tool
# result and stayed on the loading line. The string checks pin the wire shape;
# the node test drives the real runtime through the sequence a host runs.

import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

import decision_widget  # noqa: E402
from widget_runtime import runtime_js  # noqa: E402


@pytest.mark.parametrize("html", [HTML, decision_widget.widget_html()],
                         ids=["law", "decision"])
def test_widget_opens_an_mcp_apps_session(html):
    assert 'method:"ui/initialize"' in html
    assert 'protocolVersion:"2026-01-26"' in html
    assert 'method:"ui/notifications/initialized"' in html
    assert 'method:"ui/notifications/size-changed"' in html


_NODE_DRIVER = r"""
const sent = [], listeners = {}, applied = [];
let height = 480, resize = null;
global.setTimeout = () => 0;          // skip the 8 s globals poll
global.window = {
  parent: { postMessage: (m) => sent.push(JSON.parse(JSON.stringify(m))) },
  addEventListener: (t, f) => (listeners[t] = listeners[t] || []).push(f),
};
global.document = {
  addEventListener: () => {},
  documentElement: { clientWidth: 700, getBoundingClientRect: () => ({ height }) },
};
global.ResizeObserver = class { constructor(cb) { resize = cb; } observe() {} };
const deliver = (d) => listeners.message.forEach((f) => f({ data: d }));
const snap = (label) => ({ label, sent: sent.splice(0) });
const steps = [];

eval(process.argv[1]);
mountWidget((p) => { if (p) applied.push(p); return !!p; });
steps.push(snap("mount"));
resize();
steps.push(snap("resize before initialized"));
const initId = steps[0].sent.find((m) => m.method === "ui/initialize").id;
deliver({ jsonrpc: "2.0", id: initId + 99, result: {} });
steps.push(snap("answer with a foreign id"));
deliver({ jsonrpc: "2.0", id: initId, result: { protocolVersion: "2026-01-26" } });
steps.push(snap("host answers ui/initialize"));
deliver({ jsonrpc: "2.0", method: "ui/notifications/tool-result",
          params: { structuredContent: { __PAYLOAD_KEY__: [1, 2] } } });
height = 900; resize(); resize();
steps.push(snap("tool result, then two resizes"));
process.stdout.write(JSON.stringify({ steps, applied }));
"""


def _drive(payload_key: str, script: str = _NODE_DRIVER) -> dict:
    proc = subprocess.run(
        ["node", "-e", script.replace("__PAYLOAD_KEY__", payload_key), runtime_js(payload_key)],
        capture_output=True, text=True, timeout=30, check=True)
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("payload_key", ["hits", "decisions"])
def test_handshake_runs_in_spec_order(payload_key):
    out = _drive(payload_key)
    steps = {s["label"]: s["sent"] for s in out["steps"]}

    init = [m for m in steps["mount"] if m.get("method") == "ui/initialize"]
    assert len(init) == 1
    assert init[0]["params"]["protocolVersion"] == "2026-01-26"
    assert init[0]["params"]["appInfo"]["name"]
    assert isinstance(init[0]["id"], int)
    # nothing JSON-RPC but the request itself before the host has answered
    assert [m.get("method") for m in steps["mount"] if m.get("jsonrpc")] == ["ui/initialize"]
    assert steps["resize before initialized"] == []
    assert steps["answer with a foreign id"] == []

    after = steps["host answers ui/initialize"]
    assert [m["method"] for m in after] == [
        "ui/notifications/initialized", "ui/notifications/size-changed"]
    assert after[1]["params"] == {"width": 700, "height": 480}

    # the tool result renders; a resize reports once, a no-op resize not at all
    assert out["applied"] and out["applied"][-1][payload_key] == [1, 2]
    sizes = [m for m in steps["tool result, then two resizes"]
             if m.get("method") == "ui/notifications/size-changed"]
    assert [m["params"]["height"] for m in sizes] == [900]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_error_answer_leaves_no_session():
    """A host that answers ui/initialize with an error has no MCP Apps
    session: the widget must not claim one or start reporting sizes."""
    script = _NODE_DRIVER.replace(
        'result: { protocolVersion: "2026-01-26" } }',
        'error: { code: -32601, message: "no" } }')
    out = _drive("hits", script)
    steps = {s["label"]: s["sent"] for s in out["steps"]}
    assert steps["host answers ui/initialize"] == []
    assert not [m for m in steps["tool result, then two resizes"]
                if m.get("method") == "ui/notifications/size-changed"]
