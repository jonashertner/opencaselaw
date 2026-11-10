"""Shared JS runtime for the ui:// widgets (law search, decision search).

Everything host-facing lives here — outbound tool calls, inbound payload
discovery, the ready handshake, escaping — so a dialect fix lands in every
widget at once. The alternative (a copy per widget) is how the outbound bug
of 2026-07-29 would have survived in half the surfaces.

Host dialects, empirically (scripts/widget_dialect_harness.py renders the real
HTML in a sandboxed iframe and captures what a host actually receives):

  outbound   window.openai.callTool        OpenAI / ChatGPT Apps SDK
             JSON-RPC "tools/call"         official MCP Apps (Claude, VS Code,
                                           M365 Copilot)
             {type:"tool"}                 MCP-UI hosts
  inbound    window.openai.toolOutput      + the openai:set_globals event
             postMessage                   MCP-UI payload or a JSON-RPC
                                           ui/notifications/tool-result
  lifecycle  ui/initialize → response →    official MCP Apps (spec 2026-01-26)
             ui/notifications/initialized
             ui/notifications/size-changed after initialized, on every resize

The lifecycle row is not optional. The spec says "The Host MUST NOT send any
request or notification to the View before it receives an `initialized`
notification", so a widget that never sends ui/initialize gets no tool result
from a spec host and sits on its loading line forever. That was the state of
both widgets until 2026-11-10: the July harness host pushed data without a
handshake, so it could not see the gap (its strict-host case now does).

No external resources anywhere (sandbox / CSP safe).
"""


def runtime_js(payload_key: str) -> str:
    """Widget runtime JS. `payload_key` is the array field that identifies this
    widget's structuredContent (`hits` for laws, `decisions` for decisions) —
    the payload is located by shape, not by position, because hosts wrap it
    differently."""
    return _RUNTIME_JS.replace("__PAYLOAD_KEY__", payload_key)


_RUNTIME_JS = r"""
  // ---- OpenCaseLaw widget runtime (widget_runtime.py) ----------------
  // Outbound tool call. Hosts disagree on dialect, so emit every shape we
  // know and let the host recognise its own:
  //   1. window.openai.callTool  — OpenAI / ChatGPT hosts
  //   2. JSON-RPC "tools/call"   — official MCP Apps (Claude, VS Code, M365)
  //   3. {type:"tool"}           — MCP-UI hosts
  // Verified 2026-07-29 with a Playwright iframe harness: before (2) was
  // added the widget rendered correctly in an official host but every
  // button was inert, because only shape (3) was ever sent.
  // Shapes are mutually unrecognisable (no `type` vs no `jsonrpc`), so a
  // host acts on exactly one and ignores the rest.
  // Returns false when there is no host at all (widget opened standalone) —
  // callers fall back to opening the canonical URL.
  var __rpcId = 0;
  function callServerTool(name, args) {
    try { if (window.openai && window.openai.callTool) { window.openai.callTool(name, args); return true; } } catch (e) {}
    if (window.parent && window.parent !== window) {
      var sent = false;
      try { window.parent.postMessage({ jsonrpc:"2.0", id:(++__rpcId), method:"tools/call", params:{ name:name, arguments:args } }, "*"); sent = true; } catch (e) {}
      try { window.parent.postMessage({ type:"tool", payload:{ toolName:name, params:args } }, "*"); sent = true; } catch (e) {}
      return sent;
    }
    return false;
  }

  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }

  // Snippets arrive as text carrying <mark> highlights and nothing else.
  // Split on the one tag we allow and escape every other fragment.
  //
  // The obvious implementation — escape everything, then turn &lt;mark&gt;
  // back into <mark> — is wrong here: a host that injects the widget through
  // an iframe srcdoc attribute HTML-decodes the document before parsing it,
  // so those &lt; entities in this source become bare < and the replace no
  // longer matches. Highlights then render as literal "<mark>" text in every
  // result. Caught 2026-07-29 by the iframe harness, which uses srcdoc
  // exactly as such a host does. This form contains no entities at all, so
  // it survives that decoding, and it also refuses to promote a literal
  // "&lt;mark&gt;" appearing in source data into real markup.
  function safeSnippet(html) {
    return String(html == null ? "" : html).split(/(<\/?mark>)/).map(function (p) {
      return (p === "<mark>" || p === "</mark>") ? p : esc(p);
    }).join("");
  }

  // Copy to clipboard. The async Clipboard API needs a permission the host
  // may not have granted the sandbox, so fall back to execCommand on a
  // detached textarea. cb(ok) so the caller can show feedback either way.
  function copyText(text, cb) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { cb(true); }, function () { cb(legacyCopy(text)); });
        return;
      }
    } catch (e) {}
    cb(legacyCopy(text));
  }
  function legacyCopy(text) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", "");
      ta.style.position = "fixed"; ta.style.top = "-1000px";
      document.body.appendChild(ta); ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return !!ok;
    } catch (e) { return false; }
  }

  // Locate the structuredContent (the object carrying the payload array)
  // anywhere inside a global or a host message, whatever the wrapper shape.
  function findPayload(o, depth) {
    if (!o || typeof o !== "object" || depth > 5) return null;
    if (Array.isArray(o.__PAYLOAD_KEY__)) return o;
    for (var k in o) { try { var r = findPayload(o[k], depth + 1); if (r) return r; } catch (e) {} }
    return null;
  }
  function fromGlobals() {
    var srcs = [];
    try { if (window.openai && window.openai.toolOutput) srcs.push(window.openai.toolOutput); } catch (e) {}
    srcs.push(window.__TOOL_OUTPUT__, window.structuredContent, window.toolOutput);
    for (var i = 0; i < srcs.length; i++) { var h = findPayload(srcs[i], 0); if (h) return h; }
    return null;
  }

  // Inbound data arrives as a global, the openai:set_globals event, or a
  // postMessage. `apply(payload)` returns truthy once it has rendered.
  // MCP Apps lifecycle (spec 2026-01-26). A spec host sends nothing — no
  // tool-input, no tool-result — until the view has sent ui/initialize, had
  // it answered, and confirmed with ui/notifications/initialized. Height is
  // reported only after that, since the host has no session to size before.
  var __mcpInitId = null, __mcpReady = false, __lastSize = "";
  function postToHost(msg) {
    try { if (window.parent && window.parent !== window) window.parent.postMessage(msg, "*"); } catch (e) {}
  }
  function reportSize() {
    if (!__mcpReady) return;
    var el = document.documentElement;
    var w = Math.ceil(el.clientWidth || 0);
    var h = Math.ceil(el.getBoundingClientRect().height || 0);
    var key = w + "x" + h;
    if (key === __lastSize) return;
    __lastSize = key;
    postToHost({ jsonrpc:"2.0", method:"ui/notifications/size-changed", params:{ width:w, height:h } });
  }
  function startMcpApps() {
    if (!(window.parent && window.parent !== window)) return;
    __mcpInitId = ++__rpcId;
    postToHost({ jsonrpc:"2.0", id:__mcpInitId, method:"ui/initialize", params:{
      protocolVersion:"2026-01-26",
      appInfo:{ name:"opencaselaw-widget", version:"1" },
      appCapabilities:{} } });
    try { new ResizeObserver(reportSize).observe(document.documentElement); } catch (e) {}
  }
  // True when `d` was the host's answer to our ui/initialize (and is consumed).
  function handleMcpLifecycle(d) {
    if (__mcpInitId === null || !d || d.jsonrpc !== "2.0" || d.id !== __mcpInitId) return false;
    __mcpInitId = null;
    if (d.result) {   // an error answer means no MCP Apps session: stay on the other dialects
      postToHost({ jsonrpc:"2.0", method:"ui/notifications/initialized", params:{} });
      __mcpReady = true;
      reportSize();
    }
    return true;
  }

  function mountWidget(apply) {
    function boot() { return apply(fromGlobals()); }
    window.addEventListener("message", function (e) {
      var d = e && e.data; if (d == null) return;
      if (typeof d === "string") { try { d = JSON.parse(d); } catch (x) { return; } }
      if (handleMcpLifecycle(d)) return;
      apply(findPayload(d, 0));
    });
    startMcpApps();
    window.addEventListener("openai:set_globals", boot);
    document.addEventListener("DOMContentLoaded", boot);
    // Some MCP-UI hosts only send the render data after the iframe announces
    // it is ready; post a few common ready signals (harmless if unused).
    ["ui-lifecycle-iframe-ready", "iframe-ready", "mcp-ui-ready", "ready"].forEach(function (t) {
      try { if (window.parent && window.parent !== window) window.parent.postMessage({ type: t }, "*"); } catch (e) {}
    });
    // Poll briefly for late-injected globals (host may set window.openai
    // after load).
    var _tries = 0;
    (function poll() { if (boot()) return; if (_tries++ < 40) setTimeout(poll, 200); })();
  }
"""
