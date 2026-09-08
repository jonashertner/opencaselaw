/**
 * REST API client for mcp.opencaselaw.ch.
 * Handles fetch, error states, retry, rate limiting.
 *
 * Privacy: optionally sends X-Install-Cohort, an 8-hex-char monthly hash
 * SHA-256(install_id + YYYY-MM)[:8]. See buildClientHeaders() below.
 * See docs/datenschutz/ for the full privacy contract.
 */

const API_BASE = 'https://mcp.opencaselaw.ch/api';

// ─── Install cohort (privacy-respecting usage signal) ────────────────
// One random UUID per install is stored locally. Every request derives
// a fresh hash per month, so cross-month tracking is cryptographically
// infeasible. The user can disable it in settings at any time via the
// 'ocl_usage_signal_optout' localStorage key.

function oclGetInstallId() {
  try {
    var id = localStorage.getItem('ocl_install_id');
    if (!id) {
      if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        id = crypto.randomUUID();
      } else {
        // Fallback: 16 random bytes as hex
        var buf = new Uint8Array(16);
        crypto.getRandomValues(buf);
        id = Array.from(buf, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
      }
      localStorage.setItem('ocl_install_id', id);
    }
    return id;
  } catch (e) {
    return null;  // localStorage unavailable — skip the header entirely
  }
}

async function oclComputeCohort() {
  // Opt-out check
  try {
    if (localStorage.getItem('ocl_usage_signal_optout') === '1') return null;
  } catch (e) { return null; }

  var id = oclGetInstallId();
  if (!id) return null;
  var now = new Date();
  var month = now.getUTCFullYear() + '-' + String(now.getUTCMonth() + 1).padStart(2, '0');
  var input = id + '|' + month;
  try {
    var buf = new TextEncoder().encode(input);
    var hashBuf = await crypto.subtle.digest('SHA-256', buf);
    var hex = Array.from(new Uint8Array(hashBuf), function (b) {
      return b.toString(16).padStart(2, '0');
    }).join('');
    return hex.slice(0, 8);
  } catch (e) {
    return null;
  }
}

async function oclBuildHeaders(extra) {
  var headers = { 'X-Client': 'word-addin' };
  var cohort = await oclComputeCohort();
  if (cohort) headers['X-Install-Cohort'] = cohort;
  if (extra) {
    Object.keys(extra).forEach(function (k) { headers[k] = extra[k]; });
  }
  return headers;
}

// A GET has no side effect, so a transient failure is safe to repeat.
// Without this a single blip surfaced as an error in the task pane: the
// header claimed retry and rate limiting, but nothing retried anything.
var RETRY_STATUSES = [502, 503, 504];
var MAX_ATTEMPTS = 3;

// Nothing bounded how long a request could hang. fetch() has no default
// timeout, so a slow or stalled call left the pane waiting indefinitely
// with no way back — and server-side tools genuinely do run for tens of
// seconds. 45s is comfortably past a normal call and well short of a
// user concluding the add-in is broken.
var REQUEST_TIMEOUT_MS = 45000;

function _timeoutSignal(ms) {
  // AbortSignal.timeout is not in every Office webview yet.
  if (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) {
    return AbortSignal.timeout(ms);
  }
  if (typeof AbortController === 'undefined') return null;
  var ctl = new AbortController();
  setTimeout(function () { ctl.abort(); }, ms);
  return ctl.signal;
}

function _sleep(ms) {
  return new Promise(function (r) { setTimeout(r, ms); });
}

async function _fetchWithTimeout(url, opts) {
  opts = opts || {};
  var signal = _timeoutSignal(REQUEST_TIMEOUT_MS);
  if (signal) opts.signal = signal;
  var attempt = fetch(url, opts);
  // Where the webview has no AbortController the signal above is null and
  // aborting is impossible, so racing a timer is the only way the timeout
  // means anything. Without this the guard silently did nothing in exactly
  // the old webviews it exists to protect: the request could not be
  // cancelled AND the wait was unbounded.
  if (!signal) {
    attempt = Promise.race([
      attempt,
      _sleep(REQUEST_TIMEOUT_MS).then(function () {
        var e = new Error('timeout');
        e.name = 'TimeoutError';
        throw e;
      }),
    ]);
  }
  try {
    return await attempt;
  } catch (e) {
    // An abort is indistinguishable from a network drop to the caller
    // otherwise, and the two want different messages.
    if (e && (e.name === 'AbortError' || e.name === 'TimeoutError')) {
      throw { type: 'timeout', message: 'Request timed out.' };
    }
    throw { type: 'network_error', message: (e && e.message) || 'Network error' };
  }
}

async function apiFetch(path, params) {
  params = params || {};
  var url = new URL(API_BASE + path);
  Object.keys(params).forEach(function (k) {
    var v = params[k];
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  });

  var headers = await oclBuildHeaders();
  var resp;
  for (var attempt = 1; ; attempt++) {
    resp = await _fetchWithTimeout(url.toString(), { headers: headers });
    if (RETRY_STATUSES.indexOf(resp.status) === -1 || attempt >= MAX_ATTEMPTS) break;
    await _sleep(300 * attempt);   // 300ms, then 600ms
  }

  if (resp.status === 429) {
    var retryAfter = parseInt(resp.headers.get('Retry-After') || '30', 10);
    throw { type: 'rate_limit', retryAfter: retryAfter };
  }
  if (!resp.ok) {
    throw { type: 'http_error', status: resp.status, message: resp.statusText };
  }
  return resp.json();
}

async function searchDecisions(query, filters) {
  filters = filters || {};
  return apiFetch('/decisions', {
    query: query,
    court: filters.court,
    canton: filters.canton,
    language: filters.language,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    limit: filters.limit || 20,
    offset: filters.offset || 0,
    sort: filters.sort || 'relevance',
  });
}

async function getDecision(decisionId) {
  return apiFetch('/decisions/' + encodeURIComponent(decisionId), { full_text: true });
}

async function getCaseBrief(caseRef) {
  return apiFetch('/case-brief/' + encodeURIComponent(caseRef));
}

/* Structured Sachverhalt + Erwägungen + Dispositiv from the dedicated
   decision_structure.db. Federal courts only (BGE/BGer/BVGer/BStGer).
   Returns null on 404 so callers can degrade gracefully for cantonal
   decisions where structure isn't extracted. The renderer prefers this
   source over case-brief because each Erwägung paragraph comes with a
   verified `e_number` ("3", "3.1", "3.2.1") suitable for inline pinpoint
   citations and one-click insert. */
async function getDecisionStructure(decisionId) {
  return apiFetch('/structure/' + encodeURIComponent(decisionId));
}

async function listCourts() {
  return apiFetch('/courts');
}

async function searchLaws(query, opts) {
  opts = opts || {};
  return apiFetch('/laws/search', {
    query: query,
    language: opts.language || 'de',
    limit: opts.limit || 10,
  });
}

async function getLaw(abbreviation, article, language) {
  return apiFetch('/laws/' + encodeURIComponent(abbreviation), {
    article: article,
    language: language,
  });
}

async function getLeadingCases(query, lawCode, article) {
  return apiFetch('/leading-cases', {
    query: query,
    law_code: lawCode,
    article: article,
  });
}

async function getDoctrine(query) {
  return apiFetch('/doctrine', { query: query });
}

// Canonical citation lookup — returns citation_string_{de,fr,it} + canonical_url + close_matches.
async function citeReference(reference, lang) {
  return apiFetch('/cite', { reference: reference, lang: lang || 'de' });
}

// Document audit — single POST that finds every citation, validates existence
// and pinpoints, and returns annotated_text + structured issues with positions.
//
// Privacy contract (STRUCTURAL — not a user-toggle):
// PII redaction is unconditional. There is no opt-out. The function will
// throw `redact_unavailable` if the redactor module isn't loaded — Pro
// requests fail loud rather than silently leak. Field name `redacted_text`
// (not `draft_text`) documents the contract on the wire so AppSource
// reviewers, security auditors and server-side guards can verify it.
// On success, the server's annotated_text + issue messages are un-redacted
// LOCALLY so the user sees their own document text, not [NAME_1] — but
// the original PII never crossed the network.
async function attestDocument(draftText, lang) {
  var redaction = _requireRedact(draftText);
  var report = await apiPost('/attest', {
    redacted_text: redaction.redacted,
    lang: lang || 'de',
    client_redactor_version: REDACTOR_VERSION,
    client_redactor_summary: redaction.summary,
  });
  if (redaction.summary.total > 0 && report) {
    if (typeof report.annotated_text === 'string') {
      report.annotated_text = unredact(report.annotated_text, redaction.replacements);
    }
    if (Array.isArray(report.issues)) {
      report.issues.forEach(function (iss) {
        if (typeof iss.context === 'string') iss.context = unredact(iss.context, redaction.replacements);
        if (typeof iss.message === 'string') iss.message = unredact(iss.message, redaction.replacements);
      });
    }
    report._pii_summary = redaction.summary;
  }
  return report;
}

// ── Billing / Pro ───────────────────────────────────────────

async function createCheckout(successUrl, cancelUrl) {
  return apiPost('/billing/checkout?success_url=' + encodeURIComponent(successUrl) +
    '&cancel_url=' + encodeURIComponent(cancelUrl));
}

async function validateLicense(key) {
  return apiFetch('/billing/validate', { key: key });
}

async function verifyReferencePro(licenseKey, selectedText, caseRef, lang) {
  var redaction = _requireRedact(selectedText);
  var resp = await apiPost('/billing/verify', {
    license_key: licenseKey,
    redacted_text: redaction.redacted,
    case_ref: caseRef,
    lang: lang || 'de',
    client_redactor_version: REDACTOR_VERSION,
    client_redactor_summary: redaction.summary,
  });
  if (redaction.summary.total > 0 && resp) {
    ['explanation', 'evidence', 'context', 'comment'].forEach(function (k) {
      if (typeof resp[k] === 'string') resp[k] = unredact(resp[k], redaction.replacements);
    });
    resp._pii_summary = redaction.summary;
  }
  return resp;
}

/* Day-3 (planned): Strengthen — paragraph-only deep review. Soft-capped
   server-side at 10/day per license; client just calls and renders. */
async function verifyAndStrengthenPro(licenseKey, paragraphText, lang) {
  var redaction = _requireRedact(paragraphText);
  var resp = await apiPost('/billing/strengthen', {
    license_key: licenseKey,
    redacted_text: redaction.redacted,
    lang: lang || 'de',
    client_redactor_version: REDACTOR_VERSION,
    client_redactor_summary: redaction.summary,
  });
  if (redaction.summary.total > 0 && resp) {
    /* Un-redact every user-visible string the deeper response may echo back. */
    ['summary', 'argument_strength_explanation'].forEach(function (k) {
      if (typeof resp[k] === 'string') resp[k] = unredact(resp[k], redaction.replacements);
    });
    ['suggested_citations', 'counter_authorities', 'commentary_excerpts', 'verified_citations'].forEach(function (k) {
      if (Array.isArray(resp[k])) {
        resp[k].forEach(function (item) {
          ['rationale', 'excerpt', 'context', 'why_relevant'].forEach(function (f) {
            if (typeof item[f] === 'string') item[f] = unredact(item[f], redaction.replacements);
          });
        });
      }
    });
    resp._pii_summary = redaction.summary;
  }
  return resp;
}

/* Reflect — Pro literary-mirror on the whole document.
   Whole-document scope: the add-in reads the current draft, runs
   js/redact.js to strip PII, and sends the redacted text to
   POST /billing/reflect. The server identifies the central legal
   issue and draws ONE literary parallel (Shakespeare / Dürrenmatt /
   Frisch / Goethe / Kafka / etc.) that mirrors the same human
   dilemma, returning a 200-400 word markdown summary plus a
   single question for the lawyer to bring back to the case.
   Cap: 25/day per license (shared with verify + strengthen).
   The summary is purposely reflective — explicitly NOT legal advice. */
async function reflectOnDocumentPro(licenseKey, documentText, lang) {
  var redaction = _requireRedact(documentText);
  var resp = await apiPost('/billing/reflect', {
    license_key: licenseKey,
    redacted_text: redaction.redacted,
    lang: lang || 'de',
    client_redactor_version: REDACTOR_VERSION,
    client_redactor_summary: redaction.summary,
  });
  if (redaction.summary.total > 0 && resp) {
    /* Un-redact the user-visible strings the LLM may have echoed back.
       The Reflect prompt instructs the model to phrase the issue
       generically (no party names), but redacted tokens like
       __DOCKET_N__ can still appear if the LLM quotes the document
       verbatim. Reversing the redaction client-side restores the
       original text for the lawyer's own viewing. */
    ['legal_issue', 'summary_markdown', 'question_for_reflection'].forEach(
      function (k) {
        if (typeof resp[k] === 'string') {
          resp[k] = unredact(resp[k], redaction.replacements);
        }
      }
    );
    resp._pii_summary = redaction.summary;
  }
  return resp;
}

/* Find decisions that support a legal statement. Pro feature.
   Server (POST /billing/find-support) parses the statement, runs
   citation-graph + statute searches, and scores each candidate
   decision by how well it supports the claim. Capped server-side
   at 25/day per license. Like verify/strengthen, we redact PII
   client-side before transmission and unredact response strings
   client-side. */
async function findSupportingDecisions(licenseKey, statementText, lang) {
  var redaction = _requireRedact(statementText);
  var resp = await apiPost('/billing/find-support', {
    license_key: licenseKey,
    statement: redaction.redacted,
    lang: lang || 'de',
    client_redactor_version: REDACTOR_VERSION,
    client_redactor_summary: redaction.summary,
  });
  if (redaction.summary.total > 0 && resp) {
    /* Un-redact known string fields in the response. */
    ['claim', 'summary', 'analysis', 'explanation', 'legal_area'].forEach(function (k) {
      if (typeof resp[k] === 'string') resp[k] = unredact(resp[k], redaction.replacements);
    });
    if (Array.isArray(resp.results)) {
      resp.results.forEach(function (item) {
        if (!item) return;
        ['rationale', 'support_quote', 'relevance_explanation', 'excerpt'].forEach(function (f) {
          if (typeof item[f] === 'string') item[f] = unredact(item[f], redaction.replacements);
        });
      });
    }
    resp._pii_summary = redaction.summary;
  }
  return resp;
}

/* Structural redaction — no opt-out. Throws if the redactor module
   isn't loaded so a Pro request can never silently leak un-redacted
   PII. The thrown error is shaped like other api errors so the existing
   try/catch in app.js handles it cleanly with a user-facing message. */
var REDACTOR_VERSION = 'redact.js@v4';

function _requireRedact(text) {
  if (typeof window === 'undefined' || typeof window.redactPII !== 'function') {
    throw {
      type: 'redact_unavailable',
      message: 'PII-Redaktion nicht geladen — Pro-Aufruf wurde abgebrochen, um Datenleck zu vermeiden. Bitte das Add-in neu laden.',
    };
  }
  return window.redactPII(text || '');
}

/* Browser-side helper: unredact server-returned strings. */
function unredact(text, replacements) {
  if (typeof window !== 'undefined' && typeof window.unredactPII === 'function') {
    return window.unredactPII(text, replacements);
  }
  return text;
}

async function apiPost(path, body) {
  var url = API_BASE + path;
  var headers = await oclBuildHeaders();
  var opts = { method: 'POST', headers: headers };
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  // Timed out, but never retried: a POST bills a Pro call and may have
  // been applied server-side, so repeating one is not safe the way a
  // GET is.
  var resp = await _fetchWithTimeout(url, opts);
  if (resp.status === 429) {
    throw { type: 'rate_limit', retryAfter: parseInt(resp.headers.get('Retry-After') || '30', 10) };
  }
  if (resp.status === 401) {
    throw { type: 'invalid_license', message: 'License key invalid or expired.' };
  }
  if (!resp.ok) {
    var errData = {};
    try { errData = await resp.json(); } catch (e) {}
    // FastAPI validation errors (422) return {detail: [{type, loc, msg, ...}]}
    // rather than {error: "..."}; surface the first detail message and the
    // structured `type` so the caller can map to a localized hint
    // (e.g. string_too_long → reflect_too_long).
    var msg = errData.error || resp.statusText;
    var code = null;
    if (Array.isArray(errData.detail) && errData.detail.length) {
      var d0 = errData.detail[0];
      if (d0 && d0.msg) msg = d0.msg;
      if (d0 && d0.type) code = d0.type === 'string_too_long' ? 'too_long' : d0.type;
    } else if (typeof errData.detail === 'string') {
      msg = errData.detail;
    }
    throw { type: 'http_error', status: resp.status, message: msg, code: code };
  }
  return resp.json();
}
