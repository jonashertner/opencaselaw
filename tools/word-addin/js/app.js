/**
 * OpenCaseLaw Word Add-in — Main Application
 *
 * All dynamic content is escaped via escHtml() before insertion.
 * innerHTML is used only with pre-escaped strings — no raw user input.
 * Views: search (default), detail, laws, verify, settings
 */

// ── Live-update detection ──────────────────────────────────────────
// Polls /version.txt every 60 s. When the deployed version changes,
// shows a non-blocking banner inviting the user to refresh. The banner
// uses Office's display language (or the user's saved choice from
// roamingSettings if available) so it speaks the user's tongue.
//
// Server-side: a post-merge git hook on the VPS rsyncs the repo's
// tools/word-addin/ → /var/www/word-addin/ and writes
// /var/www/word-addin/version.txt with the current commit SHA.
(function autoUpdateBootstrap() {
  if (typeof window === 'undefined' || typeof fetch === 'undefined') return;
  var POLL_MS = 60 * 1000;
  var loaded = null;

  function fetchVersion() {
    return fetch('/version.txt', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (t) { return t ? t.trim() : null; })
      .catch(function () { return null; });
  }

  function detectBannerLang() {
    var raw = '';
    try {
      if (typeof Office !== 'undefined' && Office.context) {
        var rs = Office.context.roamingSettings;
        if (rs && rs.get) raw = rs.get('ocl_lang') || '';
        if (!raw) raw = Office.context.displayLanguage || Office.context.contentLanguage || '';
      }
    } catch (e) {}
    if (!raw && typeof navigator !== 'undefined') {
      raw = navigator.language || (navigator.languages && navigator.languages[0]) || '';
    }
    raw = (raw || 'de').toLowerCase();
    if (raw.indexOf('de') === 0) return 'de';
    if (raw.indexOf('fr') === 0) return 'fr';
    if (raw.indexOf('it') === 0) return 'it';
    if (raw.indexOf('en') === 0) return 'en';
    return 'de';
  }

  function showBanner() {
    if (document.getElementById('ocl-update-banner')) return;
    var msgs = {
      de: 'Neue Version verfügbar — klicken zum Aktualisieren',
      fr: 'Nouvelle version disponible — cliquer pour actualiser',
      it: 'Nuova versione disponibile — clicca per aggiornare',
      en: 'New version available — click to refresh'
    };
    var lang = detectBannerLang();
    var div = document.createElement('div');
    div.id = 'ocl-update-banner';
    div.setAttribute('role', 'status');
    div.style.cssText =
      'position:fixed;left:0;right:0;bottom:0;z-index:99999;' +
      'background:#0066cc;color:#fff;padding:10px 14px;' +
      'text-align:center;cursor:pointer;font-size:13px;' +
      'font-family:system-ui,-apple-system,Segoe UI,sans-serif;' +
      'box-shadow:0 -2px 6px rgba(0,0,0,0.15)';
    div.textContent = msgs[lang] || msgs.en;
    div.onclick = function () { location.reload(); };
    document.body.appendChild(div);
  }

  var pollingTimer = null;
  function startPolling() {
    /* Idempotent: a DOM-load race could otherwise call us twice
       (once from the readyState branch, once from the DOMContentLoaded
       handler) and leak a second setInterval forever. */
    if (pollingTimer !== null) return;
    pollingTimer = setInterval(function () {
      fetchVersion().then(function (latest) {
        if (latest && loaded && latest !== loaded) showBanner();
      });
    }, POLL_MS);
  }

  fetchVersion().then(function (v) {
    if (!v) return;  // version.txt not deployed yet — skip silently
    loaded = v;
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      startPolling();
    } else {
      window.addEventListener('DOMContentLoaded', startPolling, { once: true });
    }
  });
})();

// State
var state = {
  view: 'search',
  lang: 'de',
  query: '',
  results: [],
  total: 0,
  loading: false,
  error: null,
  detail: null,
  caseBrief: null,
  structure: null,  /* /api/structure response — gold-standard E. numbers */
  verifyResult: null,
  strengthenResult: null,
  strengthenText: '',
  verifyText: '',
  reflectResult: null,
  reflectRunning: false,
  courts: [],
  lawResult: null,
  supportText: '',
  supportResult: null,
  relatedRef: '',
  relatedResult: null,
  consent: false,
  previousView: null,
  recentLookups: [],
  scanResults: null,
  scanDocText: '',
  auditRunning: false,
  lastAuditAt: null,
  // Multi-select cluster — array of decision_id strings the user has
  // checked from the current results list. Cleared on new search,
  // view change away from search, or explicit "Clear" action.
  selectedIds: [],
  // Keyboard-traversed result index in the search view. -1 = no card focused.
  // Up/Down arrows move it; render() applies `.is-focused` to the matching
  // .result-card; Enter inserts the focused card's citation; Right Arrow
  // opens its detail. Reset to -1 on every new search / view change.
  focusedIdx: -1,
};

// In-memory prefetch cache for hovered result cards. Keys are decision_id;
// values are the resolved getDecisionDetail payload. Bounded by the size of
// the current results list so it never grows unbounded across searches.
var _prefetchCache = {};
var _prefetchTimers = {};

// SVG icon library — single source of truth for chrome-free, line-icon glyphs.
// Each function returns a string (escape-safe; no user input concatenated).
var ICONS = {
  lock: '<svg class="icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
  shieldCheck: '<svg class="icon icon-lg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>',
  link: '<svg class="icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1.5-1.5"/></svg>',
  insert: '<svg class="icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>',
  chevron: '<svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>',
  check: '<svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
};
function proLockSvg() { return localStorage.getItem('ocl_pro_key') ? '' : '<span class="pro-lock" title="Pro" aria-label="Pro">' + ICONS.lock + '</span>'; }

// Pattern to detect law article queries like "Art. 41 OR", "Art. 8 BV",
// "Art. 29 ERV", "OR 41", "ZGB 8".
//
// Forward pattern ("Art. <N> <ABBR>") accepts ANY 2-15 char alpha
// abbreviation — the backend's /laws/{abbr} endpoint validates against
// statutes.db, and on miss the frontend falls through to keyword search.
// This means new federal Verordnungen / Gesetze (e.g. ERV, FINMA-RS,
// HBEV-FINMA) work without code changes.
//
// Reverse pattern ("<ABBR> <N>") keeps the curated list because a generic
// match here would conflict with normal noun+number queries
// ("Vertrag 5", "Klage 2024" etc.).
var LAW_ABBREVS = 'OR|ZGB|StGB|BV|StPO|ZPO|SchKG|AHVG|AIG|AHVV|KVV|BGG|VwVG|ATSG|UVG|BVG|UWG|KG|MSchG|URG|PatG|DSG|FINMAG|GwG|BankG|FusG|IPRG|Lug\u00DC|KKG|CO|CC|CP|Cst\\.?|Cost\\.?|CPP|CPC|LP|LTF|LCD|LEtr|LDIP|LFus|LCart|LDA|LPM|LBI|LPD|LAJ|LAI|LATF|LPA|LPGA|LAA|LPP';
// First char is uppercase (case-sensitive) to avoid matching "Art. 5 vertrag"
// false-positives. Tail allows hyphen + Latin-1 supplement letters so
// "FINMA-RS", "LugÜ", "BG-NDB" etc. are captured. No \b at the end —
// JS's \b doesn't treat Ü/ä/ö as word chars without the `u` flag, which
// truncates unicode abbreviations. The character class already excludes
// whitespace, so greedy matching naturally stops at the next token break.
var GENERIC_LAW_ABBR = '[A-Z][A-Za-z\\-\\u00C0-\\u017F]{1,14}';
var LAW_PATTERN = new RegExp(
  '(?:[Aa]rt\\.?\\s*)' +
  '(\\d+[a-z]?(?:bis|ter|quater)?(?:\\s*(?:Abs|al|cpv|let|ch|lit)\\.?\\s*\\d*[a-z]?)?)' +
  '\\s+(' + GENERIC_LAW_ABBR + ')',
  ''
);
var LAW_PATTERN_REVERSE = new RegExp(
  '^(' + LAW_ABBREVS.replace(/\\\.\?/g, '\\.?') + ')\\s+(\\d+[a-z]?(?:bis|ter|quater)?)$',
  'i'
);

// French/Italian abbreviation → German canonical form for API lookup
var LAW_ALIAS = {
  'co': 'OR', 'cc': 'ZGB', 'cp': 'StGB', 'cst': 'BV', 'cst.': 'BV', 'cost': 'BV', 'cost.': 'BV',
  'cpp': 'StPO', 'cpc': 'ZPO', 'lp': 'SchKG', 'ltf': 'BGG', 'lcd': 'UWG', 'lcart': 'KG',
  'letr': 'AIG', 'ldip': 'IPRG', 'lfus': 'FusG',
  'lda': 'URG', 'lpm': 'MSchG', 'lbi': 'PatG', 'lpd': 'DSG',
  'laj': 'AHVG', 'lai': 'AIG', 'latf': 'BGG', 'lpa': 'VwVG', 'lpga': 'ATSG',
  'laa': 'UVG', 'lpp': 'BVG'
};

// Track whether we're running inside Word or standalone browser
var _insideWord = false;
// Debounce timer for live search-as-you-type.
var _liveSearchTimer = null;

// True for inputs that should require Enter (reference / law / docket lookups).
function _isReferencePattern(s) {
  if (LAW_PATTERN.test(s)) return true;
  if (LAW_PATTERN_REVERSE.test(s.trim())) return true;
  if (/^(BGE|ATF|DTF)\s+\d/i.test(s)) return true;
  if (/^\d[A-Z]_\d+\/\d{4}$/.test(s)) return true;
  if (/^[A-Z]-\d+\/\d{4}$/.test(s)) return true;
  return false;
}

// Initialize — works both inside Word (Office.onReady) and in plain browser
function initApp() {
  document.getElementById('lang-select').addEventListener('change', function (e) {
    state.lang = e.target.value;
    // Update gear button title
    document.getElementById('btn-settings').title = t('settings_gear_title', state.lang);
    document.getElementById('btn-settings').setAttribute('aria-label', t('settings_gear_title', state.lang));
    var brandSub = document.getElementById('brand-sub');
    if (brandSub) brandSub.textContent = t('brand_sub', state.lang);
    if (_insideWord && Office.context && Office.context.roamingSettings) {
      try {
        Office.context.roamingSettings.set('ocl_lang', state.lang);
        Office.context.roamingSettings.saveAsync();
      } catch (err) { /* roamingSettings not available */ }
    } else {
      try { localStorage.setItem('ocl_lang', state.lang); } catch (e) {}
    }
    render();
  });

  document.getElementById('btn-settings').addEventListener('click', function () {
    state.view = (state.view === 'settings') ? 'search' : 'settings';
    render();
  });

  // Load saved language. Order of preference:
  //   1. User's prior explicit choice (localStorage in browser, set
  //      via the language picker)
  //   2. Browser/Office display language (so first-launch users see
  //      their own locale without touching anything)
  //   3. DE fallback — matches the manifest's DefaultLocale.
  if (!_insideWord) {
    try {
      var stored = localStorage.getItem('ocl_lang');
      state.lang = stored || _detectInitialLang();
    } catch (e) { state.lang = _detectInitialLang(); }
  }
  document.getElementById('lang-select').value = state.lang;
  var brandSub = document.getElementById('brand-sub');
  if (brandSub) brandSub.textContent = t('brand_sub', state.lang);

  // Bind delegated click handler ONCE (never re-bind)
  var appEl = document.getElementById('app');
  appEl.addEventListener('click', handleAppClick);
  // Mirror Enter/Space activation onto every focusable [data-action] element
  // — without this, result cards (tabindex=0, role=button) and other
  // keyboard-focusable controls couldn't be activated via the keyboard,
  // breaking AT users and power users alike.
  appEl.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    var btn = e.target.closest('[data-action]');
    if (!btn) return;
    // Don't intercept native controls — buttons handle Enter/Space
    // themselves; <input>/<select>/<textarea> need normal typing.
    var tag = (btn.tagName || '').toLowerCase();
    if (tag === 'button' || tag === 'a' || tag === 'input' ||
        tag === 'select' || tag === 'textarea') return;
    e.preventDefault();
    handleAppClick(e);
  });

  // Global keyboard shortcuts — power-user productivity features for
  // legal practitioners who type far more than they click. Bound on
  // document so they fire from anywhere in the task pane.
  //
  //   Ctrl/Cmd + K     → focus the search input from any view
  //   Esc              → leave a sub-view (back to search) or close help
  //   ? (Shift + /)    → toggle the help overlay
  //   Ctrl/Cmd + Enter → insert the first result's citation directly
  //                      from the search view (no card click needed)
  //
  // The handler is intentionally narrow: it suppresses defaults only on
  // recognised shortcuts so typing in inputs / selects keeps working.
  document.addEventListener('keydown', function (e) {
    var meta = e.ctrlKey || e.metaKey;
    var inField = (function () {
      var t = document.activeElement;
      if (!t) return false;
      var tag = (t.tagName || '').toLowerCase();
      return tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable;
    })();

    // Ctrl/Cmd + K — focus search (works everywhere)
    if (meta && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (state.view !== 'search') { state.view = 'search'; render(); }
      var input = document.getElementById('search-input');
      if (input) { input.focus(); input.select(); }
      return;
    }

    // Esc — back / close help
    if (e.key === 'Escape') {
      if (state.view === 'help') {
        state.view = state.previousView || 'search';
        state.previousView = null;
        render();
        return;
      }
      var subViews = { detail: 1, verify: 1, support: 1, related: 1, guide: 1, settings: 1, scan: 1, reflect: 1, strengthen: 1 };
      if (subViews[state.view]) {
        // Reuse the existing 'back' action via the handleAppClick switch
        // so cleanup logic stays in one place.
        var fakeBtn = document.createElement('button');
        fakeBtn.dataset.action = 'back';
        handleAppClick({ target: fakeBtn });
      }
      return;
    }

    // ? — toggle help overlay (only when not typing into a field)
    if (!inField && !meta && e.key === '?') {
      e.preventDefault();
      if (state.view === 'help') {
        state.view = state.previousView || 'search';
        state.previousView = null;
      } else {
        state.previousView = state.view;
        state.view = 'help';
      }
      render();
      return;
    }

    // Ctrl/Cmd + Shift + Enter — insert all selected results as a
    // multi-citation. Checked BEFORE the single-result shortcut below
    // because Shift would otherwise be ignored.
    if (meta && e.shiftKey && e.key === 'Enter' && state.selectedIds.length > 0) {
      e.preventDefault();
      insertMultiCitation();
      return;
    }

    // Ctrl/Cmd + Enter on search view — insert top result's citation
    if (meta && !e.shiftKey && e.key === 'Enter' && state.view === 'search' && state.results.length > 0) {
      e.preventDefault();
      insertCitation(state.results[0]);
      return;
    }

    // Arrow-key navigation through result cards (search view only).
    // Up/Down move the focused index; Enter inserts the focused card;
    // Right Arrow opens detail; Left Arrow / Escape clears focus.
    // We intentionally avoid render() so navigation feels snappy — DOM
    // class flip + scrollIntoView only.
    if (!meta && state.view === 'search' && state.results.length > 0) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        // If user is mid-typing in the search bar with Shift held, leave
        // arrow keys to the input (selection extension etc.).
        if (inField && document.activeElement &&
            document.activeElement.id === 'search-input' && !e.shiftKey) {
          // Allow arrow nav to take over even from the input — most
          // search UIs (Spotlight, Quicksilver, Google) work this way.
        }
        e.preventDefault();
        var dir = (e.key === 'ArrowDown') ? 1 : -1;
        var nextIdx = state.focusedIdx + dir;
        if (nextIdx < 0) nextIdx = 0;
        if (nextIdx >= state.results.length) nextIdx = state.results.length - 1;
        _moveResultFocus(nextIdx);
        return;
      }
      if (state.focusedIdx >= 0 && state.focusedIdx < state.results.length) {
        if (!inField && e.key === 'Enter') {
          e.preventDefault();
          insertCitation(state.results[state.focusedIdx]);
          return;
        }
        if (e.key === 'ArrowRight') {
          e.preventDefault();
          showDetail(state.results[state.focusedIdx]);
          return;
        }
        if (e.key === 'ArrowLeft') {
          e.preventDefault();
          _moveResultFocus(-1);
          return;
        }
      }
    }
  });

  // Load recent lookups
  try { state.recentLookups = JSON.parse(localStorage.getItem('ocl_recent') || '[]'); } catch (e) { state.recentLookups = []; }

  // Check consent, then render
  try { state.consent = !!localStorage.getItem('ocl_consent'); } catch (e) {}
  render();
  // Background load of court list — used only by getCourtName() display
  // fallback. Failure is non-fatal (we degrade to raw court codes), but
  // log it so it shows up in the browser console if a user reports
  // garbled court names.
  listCourts().then(function (c) { state.courts = c; }).catch(function (err) {
    console.warn('listCourts failed:', err);
  });

  // Live corpus count for the welcome card. The hardcoded "969'000+" in
  // welcome_count would otherwise drift every few weeks as the daily
  // scrape adds decisions. Fetch the same stats.json the dashboard at
  // opencaselaw.ch consumes; fall back silently if unreachable.
  _loadCorpusCount();
}

function _loadCorpusCount() {
  try {
    fetch('https://opencaselaw.ch/stats.json', { cache: 'no-cache' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && data.total) {
          // de-CH apostrophe formatting matches the dashboard.
          var n = Number(data.total);
          if (n > 0) {
            state.totalDecisions = n.toLocaleString('de-CH').replace(/,/g, '\u2019') + '+';
            // Re-render only if we're currently on the welcome screen
            // (cheap, idempotent — escHtml + render use no I/O).
            if (state.view === 'search' && state.results.length === 0 && !state.query) {
              render();
            }
          }
        }
      })
      .catch(function () {});
  } catch (e) {}
}

// Map Office.context.displayLanguage / browser navigator.language to one
// of our four supported codes. Anything outside DE/FR/IT/EN falls back
// to EN — that matches the AppSource locale fallback chain.
function _detectInitialLang() {
  var raw = '';
  try {
    if (typeof Office !== 'undefined' && Office.context) {
      raw = (Office.context.displayLanguage || Office.context.contentLanguage || '');
    }
  } catch (e) {}
  if (!raw && typeof navigator !== 'undefined') {
    raw = navigator.language || (navigator.languages && navigator.languages[0]) || '';
  }
  raw = (raw || '').toLowerCase();
  if (raw.indexOf('de') === 0) return 'de';
  if (raw.indexOf('fr') === 0) return 'fr';
  if (raw.indexOf('it') === 0) return 'it';
  if (raw.indexOf('en') === 0) return 'en';
  return 'de';  // Swiss-default — mirrors manifest DefaultLocale
}

// Try Office.js initialization (fires if inside Word)
if (typeof Office !== 'undefined' && Office.onReady) {
  Office.onReady(async function (info) {
    if (info.host === Office.HostType.Word) {
      _insideWord = true;
      try {
        var settings = Office.context.roamingSettings;
        // Order: explicit user choice (roamingSettings) → Office display
        // language → DE default. The display-language fallback means a
        // French Word user sees French UI on first launch without
        // touching the language picker.
        state.lang = settings.get('ocl_lang') || _detectInitialLang();
        /* License-key roaming: roamingSettings is the only Office store
           that follows a user across Word for Mac / Win / Online, so a
           lawyer who activated Pro once shouldn't have to paste the key
           again on a second device. Mirror into localStorage on first
           sight so the rest of the code (which reads localStorage)
           continues to work without a new code path. */
        var roamingKey = settings.get('ocl_pro_key');
        if (roamingKey && !localStorage.getItem('ocl_pro_key')) {
          try { localStorage.setItem('ocl_pro_key', roamingKey); } catch (_e) {}
        }
      } catch (e) {
        state.lang = _detectInitialLang();
      }
      _restoreAuditState();
    }
    initApp();
  });
} else {
  // Standalone browser — init on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', function () { initApp(); });
}

// Persistent audit status strip — replaces the old two-tab nav.
// States: idle (never audited) / running / clean / has-issues / error.
// Clicking either runs the audit (idle), shows summary (completed), or
// routes to settings (when Pro is locked).
function renderAuditStrip() {
  var lang = state.lang;
  var hasPro = !!localStorage.getItem('ocl_pro_key');

  if (state.auditRunning) {
    return '<div class="audit-strip audit-strip-running" aria-busy="true">' +
      '<div class="audit-strip-spinner"></div>' +
      '<span class="audit-strip-label">' + escHtml(t('audit_running', lang)) + '</span>' +
      '</div>';
  }

  var r = state.scanResults;
  if (r && r.total !== undefined) {
    var bad = (r.issues || []).length;
    var ago = _relativeTime(state.lastAuditAt, lang);
    if (bad === 0) {
      return '<button class="audit-strip audit-strip-clean" data-action="audit-open-summary">' +
        '<span class="audit-strip-icon">' + ICONS.check + '</span>' +
        '<span class="audit-strip-label">' + escHtml(t('strip_clean', lang, { n: r.total })) + '</span>' +
        '<span class="audit-strip-meta">' + escHtml(ago) + '</span>' +
        '</button>';
    }
    return '<button class="audit-strip audit-strip-issues" data-action="audit-open-summary">' +
      '<span class="audit-strip-icon">\u26A0</span>' +
      '<span class="audit-strip-label">' + escHtml(t('strip_issues', lang, { n: bad })) + '</span>' +
      '<span class="audit-strip-meta">' + escHtml(ago) + '</span>' +
      '</button>';
  }

  if (state.error && state.view === 'scan') {
    // Differentiate precondition errors (empty doc, not in Word) from
    // genuine audit failures. The former are NOT failures — the user
    // just needs to add content first — so the strip stays in the
    // neutral idle state and lets the inline body message do the
    // explaining. The red "Audit failed — try again" strip is reserved
    // for actual server / network / parser failures.
    var precondition = state.error.type === 'doc_empty' ||
                       state.error.type === 'not_in_word' ||
                       state.error.type === 'no_selection';
    if (!precondition) {
      return '<button class="audit-strip audit-strip-error" data-action="scan-doc">' +
        '<span class="audit-strip-icon">\u2717</span>' +
        '<span class="audit-strip-label">' + escHtml(t('strip_error', lang)) + '</span>' +
        '</button>';
    }
    // fall through to idle rendering below
  }

  // Idle (never audited this session).
  var action = hasPro ? 'scan-doc' : 'open-settings';
  return '<button class="audit-strip audit-strip-idle" data-action="' + action + '">' +
    '<span class="audit-strip-icon">' + ICONS.shieldCheck + '</span>' +
    '<span class="audit-strip-label">' + escHtml(t('strip_idle', lang)) + '</span>' +
    (hasPro ? '' : proLockSvg()) +
    '<span class="audit-strip-cta">' + ICONS.chevron + '</span>' +
    '</button>';
}

// Reflect strip — second whole-document Pro feature, visible on the
// search view alongside the audit strip so the user sees more than
// one Pro action at a glance. Selection-based Pro tools (Verify /
// Strengthen / Find-support / Find-related) stay inside the
// scan-results view because they only make sense when there's a
// Word selection or cursor context to operate on.
function renderReflectStrip() {
  var lang = state.lang;
  var hasPro = !!localStorage.getItem('ocl_pro_key');
  var action = hasPro ? 'reflect' : 'open-settings';
  return '<button class="audit-strip audit-strip-idle reflect-strip" data-action="' + action + '">' +
    '<span class="audit-strip-icon">' + (ICONS.sparkles || ICONS.shieldCheck) + '</span>' +
    '<span class="audit-strip-label">' + escHtml(t('reflect_strip_idle', lang)) + '</span>' +
    (hasPro ? '' : proLockSvg()) +
    '<span class="audit-strip-cta">' + ICONS.chevron + '</span>' +
    '</button>';
}

// Format a past timestamp as "just now" / "N min ago" / "HH:mm" / date.
function _relativeTime(iso, lang) {
  if (!iso) return '';
  var then = new Date(iso).getTime();
  var now = Date.now();
  var secs = Math.max(0, Math.floor((now - then) / 1000));
  var LABELS = {
    now:  { de: 'gerade eben',  fr: 'à l\'instant', it: 'adesso',    en: 'just now' },
    min:  { de: 'vor {n} Min',  fr: 'il y a {n} min', it: '{n} min fa', en: '{n} min ago' },
    hour: { de: 'vor {n} Std',  fr: 'il y a {n} h', it: '{n} h fa',  en: '{n} h ago' },
    day:  { de: 'vor {n} T',    fr: 'il y a {n} j', it: '{n} g fa',  en: '{n} d ago' },
  };
  function pick(k, n) { var s = LABELS[k][lang] || LABELS[k].de; return n !== undefined ? s.replace('{n}', n) : s; }
  if (secs < 30) return pick('now');
  if (secs < 3600) return pick('min', Math.round(secs / 60));
  if (secs < 86400) return pick('hour', Math.round(secs / 3600));
  return pick('day', Math.round(secs / 86400));
}

// Rendering — all values passed through escHtml() for XSS safety
// Safe: all dynamic content is escaped via escHtml before concatenation
function render() {
  var app = document.getElementById('app');
  try {
    if (!state.consent) { app.innerHTML = renderConsent(); bindEvents(); return; }

    // Audit strip mounts above the primary surfaces (search + audit summary).
    // Sub-views (detail/verify/support/related/settings/guide) get a back-link
    // and use the full panel.
    var showStrip = (state.view === 'search' || state.view === 'scan');
    var html = showStrip ? renderAuditStrip() : '';
    // The Reflect strip is a sibling whole-document Pro action; only
    // shown on the main search view, not inside the scan-results
    // view (where it would compete for attention with the audit
    // outcome). Adding it here makes the Pro tier visibly more than
    // a single button without requiring users to first run an audit.
    if (state.view === 'search') html += renderReflectStrip();
    var view = state.view;
    if (view === 'search') html += renderSearch();
    else if (view === 'detail') html += renderDetail();
    else if (view === 'verify') html += renderVerify();
    else if (view === 'support') html += renderSupport();
    else if (view === 'related') html += renderRelated();
    else if (view === 'scan') html += renderScan();
    else if (view === 'strengthen') html += renderStrengthen();
    else if (view === 'reflect') html += renderReflect();
    else if (view === 'guide') html += renderGuide();
    else if (view === 'help') html += renderHelp();
    else if (view === 'settings') html += renderSettings();

    // The multi-select bar floats above the main panel when ≥1
    // result is selected; it's part of the search-view experience.
    if (view === 'search') html += renderMultiBar();

    // Preserve the live search input NODE across the re-render. A live
    // search-as-you-type fires doSearch() -> render(), which rebuilds
    // #app.innerHTML — destroying and recreating the <input> the user is typing
    // in. On Mac Word's WKWebView, replacing a focused input via innerHTML drops
    // focus, caret, AND the in-progress text, so the field appears to clear after
    // a typing pause (the reported bug). Instead of recreating it, we keep the
    // ACTUAL node: detach it before the swap, then re-insert it in place of the
    // freshly rendered placeholder and restore focus + caret. The node's own
    // event listeners travel with it, so typing keeps working uninterrupted.
    var _keptInput = document.getElementById('search-input');
    var _keptFocused = !!_keptInput && (document.activeElement === _keptInput);
    var _keptSel = null;
    if (_keptInput) {
      try { _keptSel = { s: _keptInput.selectionStart, e: _keptInput.selectionEnd }; } catch (e) {}
      _keptInput.remove(); // detach so the innerHTML swap can't destroy it
    }

    app.innerHTML = html; // eslint-disable-line no-unsanitized/property -- all values pre-escaped via escHtml()
    bindEvents();

    if (_keptInput) {
      var _placeholder = document.getElementById('search-input');
      if (_placeholder && _placeholder.parentNode) {
        // If the user is NOT actively typing (e.g. a suggestion click or the
        // clear button just set state.query), adopt the rendered value so those
        // paths still update the field. If they ARE typing, keep their text.
        if (!_keptFocused) _keptInput.value = _placeholder.value;
        _placeholder.parentNode.replaceChild(_keptInput, _placeholder);
        if (_keptFocused) {
          _keptInput.focus();
          if (_keptSel) {
            try { _keptInput.setSelectionRange(_keptSel.s, _keptSel.e); } catch (e) {}
          }
        }
      }
    }

    if (state.lawResult) resolveAmendmentRefs();
    // Announce dynamic state changes to assistive tech. Counts, errors,
    // and loading transitions all flow through render(), so a single
    // aria-live region beats sprinkling them in each renderer.
    _announceState();
  } catch (renderErr) {
    // Never let a render exception kill the whole task pane. Log, then
    // show a recoverable error surface so the user can still navigate
    // back to a known-good view (settings) or retry.
    console.error('render() failed:', renderErr);
    var lang = state.lang || 'de';
    var msg = (lang === 'fr') ? 'Erreur d\u2019affichage. Veuillez réessayer.' :
              (lang === 'it') ? 'Errore di visualizzazione. Riprovare.' :
              (lang === 'en') ? 'Display error. Please try again.' :
                                'Anzeigefehler. Bitte erneut versuchen.';
    app.innerHTML = '<div class="state-message" role="alert">' + escHtml(msg) +
      '<button class="retry-btn" data-action="back">\u2190 ' + escHtml(t('back', lang)) + '</button></div>';
  }
}

/* Render the "PII redacted before send" trust banner for any Pro view
   whose response carries a `_pii_summary`. The Word channel's whole
   reason for existing is the structural redaction layer — show what
   it caught on every view that called a Pro endpoint, not just Scan.
   Returns '' when nothing was redacted (server saw no PII), which is
   itself a meaningful signal: don't clutter the panel for clean text. */
function _renderPIIBanner(piiSummary, lang) {
  if (!piiSummary || !piiSummary.total || typeof window.formatPIISummary !== 'function') return '';
  var details = window.formatPIISummary(piiSummary, lang);
  var label = t('audit_pii_redacted', lang, { n: piiSummary.total });
  return '<div class="pii-banner" title="' + escHtml(details) + '">' +
    '<span class="pii-banner-icon" aria-hidden="true">\uD83D\uDD12</span>' +
    '<span class="pii-banner-text">' +
      '<span class="pii-banner-label">' + escHtml(label) + '</span>' +
      ' &mdash; ' + escHtml(details) +
    '</span>' +
  '</div>';
}

// Announce key state to screen readers via a polite aria-live region in
// index.html. Kept terse so AT doesn't read the whole panel on every
// keystroke; only the count / error / loading line is announced.
function _announceState() {
  var live = document.getElementById('a11y-live');
  if (!live) return;
  var lang = state.lang;
  var msg = '';
  if (state.loading) {
    msg = t('loading', lang);
  } else if (state.error && state.error.message) {
    msg = state.error.message;
  } else if (state.view === 'search' && state.results.length > 0 && state.total > 0) {
    msg = t('results_count', lang, { n: state.total });
  } else if (state.view === 'search' && state.results.length === 0 && state.query && !state.lawResult) {
    msg = t('no_results', lang);
  }
  if (live.textContent !== msg) live.textContent = msg;
}

// Format law article text into styled paragraphs with superscript numbers.
// Each paragraph carries TWO insert affordances:
//   - the small superscript number → inserts the reference only
//   - a hover-revealed "Wortlaut" button → inserts verbatim paragraph + ref
function formatLawParagraphs(text, artNum, abbrev) {
  if (!text) return '';
  var lines = text.split('\n');
  var html = '';
  var lang = state.lang;
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    if (!line) continue;
    var m = line.match(/^(\d+[a-z]?(?:bis|ter|quater)?)\s+(.*)$/);
    if (m) {
      // Locale-correct paragraph abbreviation: DE Abs., FR al., IT cpv., EN para.
      var paraRef = 'Art. ' + artNum + ' ' + paragraphLabel(lang) + ' ' + m[1] + ' ' + abbrev;
      var paraBody = formatLawFootnotes(m[2]);
      // Strip footnote markers from the verbatim text we insert (no inline HTML).
      // Patterns cover the three languages of the official statute publications
      // (DE Fedlex, FR RS, IT RS) so French/Italian users also get clean inserts.
      var verbatim = m[2].replace(/\s*(Fassung gem\u00e4ss|Eingef\u00fcgt durch|Aufgehoben durch|Berichtigung der|Bereinigt gem\u00e4ss|Strafdrohungen?\s+gem\u00e4ss|Nouvelle teneur selon|Introduit par|Abrog\u00e9 par|Erratum|Mis \u00e0 jour selon|Nuovo testo giusta|Introdotto dal|Abrogato dal|Aggiornato giusta|SR\s+\d+[\.\d]*).*$/i, '').trim();
      html += '<div class="law-para">' +
        '<span class="law-para-num" data-action="insert-law-para" data-ref="' + escHtml(paraRef) + '" title="' + escHtml(t('btn_insert_ref', lang)) + ': ' + escHtml(paraRef) + '">' + escHtml(m[1]) + '</span> ' +
        paraBody +
        ' <button class="law-para-verbatim" data-action="insert-law-verbatim" data-ref="' + escHtml(paraRef) + '" data-text="' + escHtml(verbatim) + '" title="' + escHtml(t('btn_insert_verbatim', lang)) + '" aria-label="' + escHtml(t('btn_insert_verbatim', lang)) + '">' + ICONS.insert + '</button>' +
        '</div>';
    } else {
      // Check if entire line is a footnote/amendment note
      if (/^(Fassung|Eingef|Aufgehoben|Strafdrohung|Berichtigung|Version|Introdotto|Abrogato|Nuovo|Introd)/i.test(line)) {
        html += '<div class="law-footnote">' + escHtml(line) + '</div>';
      } else {
        html += '<div class="law-para">' + formatLawFootnotes(line) + '</div>';
      }
    }
  }
  return html;
}

function formatLawFootnotes(text) {
  // Split inline footnotes (e.g. "Fassung gemäss...", "Nouvelle teneur selon...",
  // "Nuovo testo giusta...", "SR 281.1") from the main text. Patterns cover the
  // three languages of the official Swiss statute publications so French and
  // Italian law-text retains its rendering quality.
  var footnotePatterns = [
    // German (Fedlex DE)
    /(\.\s*)(Fassung gem\u00e4ss.+)$/,
    /(\.\s*)(Strafdrohungen?.+gem\u00e4ss.+)$/,
    /(\.\s*)(Eingef\u00fcgt durch.+)$/,
    /(\.\s*)(Aufgehoben durch.+)$/,
    /(\.\s*)(Berichtigung der.+)$/,
    /(\.\s*)(Bereinigt gem\u00e4ss.+)$/,
    // French (Fedlex FR / RS)
    /(\.\s*)(Nouvelle teneur selon.+)$/,
    /(\.\s*)(Introduit par.+)$/,
    /(\.\s*)(Abrog\u00e9 par.+)$/,
    /(\.\s*)(Erratum.+)$/,
    /(\.\s*)(Mis \u00e0 jour selon.+)$/,
    // Italian (Fedlex IT / RS)
    /(\.\s*)(Nuovo testo giusta.+)$/,
    /(\.\s*)(Introdotto dal.+)$/,
    /(\.\s*)(Abrogato dal.+)$/,
    /(\.\s*)(Aggiornato giusta.+)$/,
    // Universal
    /(\.\s*)(SR \d+[\.\d]*)$/,
  ];
  for (var i = 0; i < footnotePatterns.length; i++) {
    var fm = text.match(footnotePatterns[i]);
    if (fm) {
      return escHtml(text.substring(0, text.length - fm[2].length)) +
        '<span class="law-footnote-inline">' + escHtml(fm[2]) + '</span>';
    }
  }
  return escHtml(text);
}

// Async: resolve AS/BBl refs in footnotes to clickable Fedlex links (called after render)
//
// URL validation uses the URL constructor + protocol comparison, not a
// string-prefix check, so a server response like "https:javascript:..."
// (no double-slash) or whitespace-prefixed scheme cannot bypass the guard.
function resolveAmendmentRefs() {
  var footnotes = document.querySelectorAll('.law-footnote, .law-footnote-inline');
  footnotes.forEach(function (el) {
    var text = el.textContent;
    var refs = text.match(/(AS|BBl|RO|RU|FF)\s+(\d{4})\s+(\d+)/g);
    if (!refs) return;
    refs.forEach(function (ref) {
      var m = ref.match(/(AS|BBl|RO|RU|FF)\s+(\d{4})\s+(\d+)/);
      if (!m) return;
      apiFetch('/amendment-ref', { ref_type: m[1], year: m[2], page: m[3] }).then(function (data) {
        if (!data || !data.url || typeof data.url !== 'string') return;
        var safe = false;
        try {
          var parsed = new URL(data.url);
          safe = (parsed.protocol === 'https:' && parsed.hostname.length > 0);
        } catch (_e) { safe = false; }
        if (!safe) return;
        var link = '<a href="' + escHtml(safeHref(data.url)) + '" target="_blank" rel="noopener noreferrer" class="law-ref-link">' + escHtml(ref) + '</a>';
        el.innerHTML = el.innerHTML.replace(escHtml(ref), link); // eslint-disable-line no-unsanitized/property
      }).catch(function () {});
    });
  });
}

function renderConsent() {
  var lang = state.lang;
  return '<div class="consent">' +
    '<div class="consent-title">' + escHtml(t('consent_title', lang)) + '</div>' +
    '<div class="consent-text">' + escHtml(t('consent_text', lang)) + '</div>' +
    '<div class="consent-links">' +
    '<a href="https://word.opencaselaw.ch/terms.html" target="_blank">' + escHtml(t('consent_terms', lang)) + '</a>' +
    '<span class="pro-sep">\u00B7</span>' +
    '<a href="https://word.opencaselaw.ch/privacy.html" target="_blank">' + escHtml(t('consent_privacy', lang)) + '</a>' +
    '</div>' +
    '<button class="btn btn-full" data-action="accept-consent">' + escHtml(t('consent_accept', lang)) + '</button>' +
    '</div>';
}

// Search View
function renderSearch() {
  var lang = state.lang;
  var html =
    '<div class="search-wrap">' +
    '<svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>' +
    '<input class="search-bar" id="search-input" type="text" ' +
    'placeholder="' + escHtml(t('search_placeholder', lang)) + '" ' +
    'value="' + escHtml(state.query) + '">' +
    (state.query ? '<button class="search-clear" data-action="clear-search" aria-label="Clear">\u00D7</button>' : '') +
    '</div>';

  // Show law article card if a law result exists
  if (state.lawResult && state.lawResult.articles && state.lawResult.articles.length > 0) {
    var lawArt = state.lawResult.articles[0];
    var abbrev = state.lawResult.abbreviation || '';
    var artNum = lawArt.article_num || lawArt.article || '';
    var heading = lawArt.heading ? ' \u2014 ' + lawArt.heading : '';
    var lawTitle = 'Art. ' + artNum + ' ' + abbrev + heading;
    var lawSubtitle = state.lawResult.title || '';
    var lawText = lawArt.text || lawArt.content || '';
    // Format paragraphs: split on lines starting with a number
    var lawParas = formatLawParagraphs(lawText, artNum, abbrev);
    html += '<div class="law-card">' +
      '<div class="law-card-header"><div>' +
      '<div class="law-card-title">' + escHtml(lawTitle) + '</div>' +
      '<div class="law-card-subtitle">' + escHtml(lawSubtitle) + '</div>' +
      '</div></div>' +
      '<div class="law-paras">' + lawParas + '</div>' +
      '<div class="law-card-actions">' +
      '<button class="btn btn-insert" data-action="insert-law-ref">' + escHtml(t('btn_insert_ref', lang)) + '</button>' +
      '</div></div>';
  }

  if (state.loading) {
    // Skeleton screens (CSS already shipping) replace the bare spinner so the
    // layout doesn't jump when results land — keeps perceived response fast.
    // The text is for screen readers only via the polite live region.
    html += renderResultsSkeleton();
  } else if (state.error) {
    html += renderError();
  } else if (state.results.length === 0 && state.query && !state.lawResult) {
    html += '<div class="state-message">' + escHtml(t('no_results', lang)) +
      '<br><span style="font-size:12px;">' + escHtml(t('no_results_hint', lang)) + '</span></div>';
  } else if (state.results.length === 0 && !state.query) {
    html += renderWelcome(lang);
  } else {
    html += '<div class="results">';
    if (state.total > 0) {
      html += '<div class="results-count">' + escHtml(t('results_count', lang, { n: state.total })) + '</div>';
    }
    for (var i = 0; i < state.results.length; i++) {
      html += renderResultCard(state.results[i], i);
    }
    html += '</div>';
  }
  return html;
}

function renderWelcome(lang) {
  var html = '<div class="welcome">';

  // First-run hint — single dismissible banner the very first time a user
  // sees the welcome screen. Dismissed forever via localStorage flag.
  var tourSeen = false;
  try { tourSeen = localStorage.getItem('ocl_hint_seen') === '1'; } catch (e) {}
  if (!tourSeen && state.recentLookups.length === 0) {
    html += '<div class="welcome-hint">' +
      '<div class="welcome-hint-text">' + escHtml(t('hint_welcome', lang)) + '</div>' +
      '<button class="btn btn-detail welcome-hint-dismiss" data-action="dismiss-hint">' +
      escHtml(t('hint_dismiss', lang)) + '</button>' +
      '</div>';
  }

  // Recent lookups (when user has history) — surfaces the user's own context first.
  if (state.recentLookups.length > 0) {
    html += '<div class="quick-label">' + escHtml(t('recent_label', lang)) + '</div>';
    html += '<div class="quick-chips">';
    for (var r = 0; r < state.recentLookups.length; r++) {
      html += '<button class="quick-chip" data-action="quick-search" data-query="' + escHtml(state.recentLookups[r]) + '">' + escHtml(state.recentLookups[r]) + '</button>';
    }
    html += '</div>';
  }

  // Quick search suggestions
  html += '<div class="quick-label">' + escHtml(t('quick_try', lang)) + '</div>';
  html += '<div class="quick-chips">';
  var examples = t('quick_examples', lang).split('|');
  for (var i = 0; i < examples.length; i++) {
    html += '<button class="quick-chip" data-action="quick-search" data-query="' + escHtml(examples[i]) + '">' + escHtml(examples[i]) + '</button>';
  }
  html += '</div>';

  // Footer — coverage stats + how-it-works link + keyboard-shortcut hint.
  // The "?" affordance is the only entry-point UI for the help overlay
  // (the keyboard shortcut still works from anywhere); kept whisper-quiet.
  html += '<div class="welcome-footer">' +
    '<a class="welcome-link" data-action="show-guide">' + escHtml(t('how_it_works', lang)) + '</a>' +
    '<a class="welcome-link" data-action="show-help" title="?" style="margin-left:12px;">' + escHtml(t('help_title', lang)) + '</a>' +
    '<span class="welcome-count-small">' + escHtml(t('welcome_count', lang, { n: state.totalDecisions || '969\u2009000+' })) + '</span>' +
    '</div>';

  html += '</div>';
  return html;
}

function renderResultCard(r, idx) {
  var lang = state.lang;
  var badges = '';
  if (r.is_leading_case) badges += '<span class="badge badge-leading">\u2605 ' + escHtml(t('badge_leading', lang)) + '</span> ';
  if (r.citation_count > 0) badges += '<span class="badge badge-citations">' + escHtml(t('badge_citations', lang, { n: r.citation_count })) + '</span> ';
  if (r.legal_area) badges += '<span class="badge badge-area">' + escHtml(r.legal_area) + '</span>';

  // Sentence-aware truncation: prefer cutting at ". " over mid-word.
  var regeste = r.regeste || stripHtml(r.snippet) || r.title || '';
  regeste = _truncateSentence(regeste, 200);
  var courtName = r.court ? getCourtName(r.court, lang) : (r.court_name || '');
  var date = r.decision_date || r.date || '';
  var docket = r.citation_string_de || r.docket_number || r.decision_id;

  // Multi-select checkbox — id-keyed so re-renders preserve selection
  // even if result ordering changes. The checkbox sits inline with the
  // docket; data-action='toggle-select' is intercepted before the card's
  // outer 'detail' click via a stopPropagation in handleAppClick.
  var did = r.decision_id || ('idx-' + idx);
  var selected = state.selectedIds.indexOf(did) >= 0;
  var checkAria = t(selected ? 'multi_deselect_aria' : 'multi_select_aria', lang);

  var focused = (state.focusedIdx === idx);
  return '<div class="result-card' + (selected ? ' result-card-selected' : '') +
    (focused ? ' is-focused' : '') +
    '" data-action="detail" data-idx="' + idx + '" data-decision-id="' +
    escHtml(did) + '" tabindex="0" role="button">' +
    '<div class="result-header">' +
    '<input type="checkbox" class="result-select" data-action="toggle-select" ' +
    'data-id="' + escHtml(did) + '" data-idx="' + idx + '" ' +
    'aria-label="' + escHtml(checkAria) + '"' + (selected ? ' checked' : '') + '>' +
    '<div class="result-header-text"><div>' +
    '<div class="result-docket">' + escHtml(docket) + '</div>' +
    '<div class="result-meta">' + escHtml(date) + ' \u00B7 ' + escHtml(courtName) + '</div>' +
    '</div><div>' + badges + '</div></div></div>' +
    (regeste ? '<div class="result-regeste">' + escHtml(regeste) + '</div>' : '') +
    pinpointChipHtml(r.pinpoint, lang) +
    '<div class="result-actions">' +
    '<button class="btn btn-insert btn-insert-card" data-action="insert" data-idx="' + idx + '" title="' + escHtml(t('btn_insert', lang)) + '">' +
    ICONS.insert + ' ' + escHtml(t('btn_insert', lang)) + '</button>' +
    '</div></div>';
}

// Render the pinpoint chip for a result card. Returns '' when no
// pinpoint present (graceful no-op for cantonal decisions without
// decision_structure entries, OR for results where the lexical
// resolver couldn't find a confident anchor — these are still
// valid search hits, the card just doesn't get the chip).
//
// The chip's link uses pp.url which now includes the hash fragment
// (#e-X-Y) so the browser auto-scrolls to the right Erwägung on
// landing — verified end-to-end via Playwright 2026-05-10. Click
// stops propagation to avoid the card's outer "detail" handler also
// firing (which would open the in-pane detail view instead of the
// external SEO page where the highlight markup lives).
function pinpointChipHtml(pp, lang) {
  if (!pp || !pp.e_number) return '';
  var label = pinpointLabel(lang);
  var url = pp.url || '';
  var sentence = pp.matched_sentence || '';
  var conf = pp.confidence || '';
  var source = pp.source || 'lexical';
  // Truncate matched_sentence for compact display; full text is on
  // the linked SEO page.
  var sentenceTrim = sentence.length > 180 ? sentence.slice(0, 177) + '\u2026' : sentence;
  var sourceClass = source === 'semantic' ? ' result-pinpoint--semantic' : '';
  return '<a href="' + escHtml(safeHref(url)) + '" target="_blank" rel="noopener" ' +
    'class="result-pinpoint' + sourceClass + '" ' +
    'title="\u00d6ffnet die Erw\u00e4gung in einem neuen Tab" ' +
    'onclick="event.stopPropagation();">' +
    '<span class="result-pinpoint-icon" aria-hidden="true">\uD83D\uDCCD</span>' +
    '<span class="result-pinpoint-loc">' + escHtml(label + ' ' + pp.e_number) + '</span>' +
    (conf ? '<span class="result-pinpoint-conf">(' + escHtml(conf) + ')</span>' : '') +
    (sentenceTrim ? '<span class="result-pinpoint-text">' + escHtml(sentenceTrim) + '</span>' : '') +
    '</a>';
}

// Floating multi-select bar — only rendered when ≥1 card is selected.
// Sticky at the bottom of the panel so it's reachable without scrolling
// back to the top, and stays out of the way until the user opts in.
function renderMultiBar() {
  if (!state.selectedIds.length) return '';
  var lang = state.lang;
  var n = state.selectedIds.length;
  return '<div class="multi-bar" role="region" aria-label="' + escHtml(t('multi_aria_region', lang)) + '">' +
    '<span class="multi-bar-count">' + escHtml(t('multi_n_selected', lang, { n: n })) + '</span>' +
    '<button class="btn btn-detail multi-bar-clear" data-action="multi-clear">' +
    escHtml(t('multi_clear', lang)) + '</button>' +
    '<button class="btn btn-insert multi-bar-insert" data-action="multi-insert" ' +
    'title="' + escHtml(t('help_insert_multi', lang)) + '">' +
    ICONS.insert + ' ' + escHtml(t('multi_insert', lang)) + '</button>' +
    '</div>';
}

// Truncate at the last sentence boundary within `max` chars; fall back to
// last word boundary; only resort to mid-word cut if no whitespace at all.
function _truncateSentence(s, max) {
  s = String(s || '').replace(/\s+/g, ' ').trim();
  if (s.length <= max) return s;
  var win = s.substring(0, max);
  var lastSentence = Math.max(win.lastIndexOf('. '), win.lastIndexOf('? '), win.lastIndexOf('! '));
  if (lastSentence > max * 0.5) return s.substring(0, lastSentence + 1).trim();
  var lastSpace = win.lastIndexOf(' ');
  if (lastSpace > max * 0.6) return s.substring(0, lastSpace).trim() + '\u2026';
  return win + '\u2026';
}

/**
 * Split an Erwägung into sub-sections based on numbering patterns (3.1, 3.2, 3.2.1).
 * Uses RegExp.prototype to find sub-section markers in the text.
 */
function splitErwaegung(mainNum, text) {
  if (!text) return [{ num: mainNum, text: '' }];

  // Defensive: the case-brief extractor sometimes returns Erwägungen
  // with `number: null` (notably for older BGE entries whose structure
  // couldn't be parsed). In that case there's nothing to anchor a
  // regex against — building one from `?` or `null` produced an
  // invalid regex (`/(?(?:\.\d+)+)/`) that threw mid-render and was
  // caught by the global render() error boundary, surfacing as a
  // generic "display error" to the user. Treat any non-numeric mainNum
  // as "render the body as a single block, no sub-section splitting".
  if (typeof mainNum !== 'string' || !/^\d+(?:\.\d+)*$/.test(mainNum)) {
    return [{ num: mainNum || '?', text: text }];
  }

  var escaped = mainNum.replace(/\./g, '\\.');
  var re = new RegExp('(?:\\. |\\n)(' + escaped + '(?:\\.\\d+)+)\\s', 'g');
  var parts = [];
  var lastIdx = 0;
  var m;
  while ((m = re.exec(text)) !== null) {
    var prevText = text.substring(lastIdx, m.index).trim();
    if (parts.length === 0 && prevText) {
      parts.push({ num: mainNum, text: prevText });
    } else if (prevText && parts.length > 0) {
      parts[parts.length - 1].text += ' ' + prevText;
    }
    lastIdx = m.index + m[0].length;
    parts.push({ num: m[1], text: '' });
  }
  var remaining = text.substring(lastIdx).trim();
  if (parts.length === 0) {
    return [{ num: mainNum, text: text }];
  }
  if (remaining) {
    parts[parts.length - 1].text = remaining;
  }
  if (parts.every(function (p) { return !p.text; })) {
    return [{ num: mainNum, text: text }];
  }
  return parts;
}

// Detail View
function renderDetail() {
  var lang = state.lang;
  var d = state.detail;
  var cb = state.caseBrief;

  if (!d) return renderDetailSkeleton();

  var html = '<a class="back-link" data-action="back">\u2190 ' + escHtml(t('back', lang)) + '</a>';

  // Sticky insert bar — primary "Insert" + secondary "Insert with link"
  var citationText = formatCitation(d, lang);
  html += '<div class="sticky-insert">' +
    '<span class="sticky-citation">' + escHtml(citationText) + '</span>' +
    '<div class="sticky-insert-actions">' +
    '<button class="btn btn-insert" data-action="insert-main" title="' + escHtml(t('btn_insert', lang)) + '">' +
    ICONS.insert + escHtml(t('btn_insert', lang)) + '</button>' +
    '<button class="btn btn-insert btn-icon-only" data-action="insert-main-link" title="' + escHtml(t('btn_insert_link', lang)) + '" aria-label="' + escHtml(t('btn_insert_link', lang)) + '">' +
    ICONS.link + '</button>' +
    '</div></div>';

  html += '<h2 class="detail-title">' + escHtml(d.docket_number || d.decision_id) + '</h2>';
  if (d.title) html += '<div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">' + escHtml(d.title) + '</div>';
  var courtName = d.court ? getCourtName(d.court, lang) : (d.court_name || '');
  html += '<div class="detail-meta">' + escHtml(d.decision_date || d.date || '') + ' \u00B7 ' + escHtml(courtName) + '</div>';

  var badges = '';
  if (d.is_leading_case) badges += '<span class="badge badge-leading">\u2605 ' + escHtml(t('badge_leading', lang)) + '</span> ';
  if (d.citation_count) badges += '<span class="badge badge-citations">' + escHtml(String(d.citation_count)) + ' ' + escHtml(t('citations_label', lang)) + '</span> ';
  if (d.legal_area) badges += '<span class="badge badge-area">' + escHtml(d.legal_area) + '</span>';
  if (badges) html += '<div class="badges-row">' + badges + '</div>';

  if (d.source_url) {
    html += '<a class="source-link" href="' + escHtml(safeHref(d.source_url)) + '" target="_blank">' + escHtml(t('source_link', lang)) + ' \u2192</a>';
  }

  // ── Regeste ──
  if (d.regeste) {
    var needsCollapse = d.regeste.length > 300;
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('section_regeste', lang)) + '</div>' +
      '<div class="section-body' + (needsCollapse ? ' collapsible' : '') + '" id="regeste-body">' + escHtml(d.regeste) + '</div>' +
      (needsCollapse ? '<button class="expand-btn" data-action="toggle-expand" data-target="regeste-body">' + escHtml(t('btn_show_more', lang)) + '</button>' : '') +
      '</div>';
  }

  // ── Sachverhalt (from case brief) ──
  if (cb && cb.sachverhalt) {
    var svCollapse = cb.sachverhalt.length > 200;
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('section_sachverhalt', lang)) + '</div>' +
      '<div class="section-body' + (svCollapse ? ' collapsible' : '') + '" id="sv-body">' + escHtml(cb.sachverhalt) + '</div>' +
      (svCollapse ? '<button class="expand-btn" data-action="toggle-expand" data-target="sv-body">' + escHtml(t('btn_show_more', lang)) + '</button>' : '') +
      '</div>';
  }

  // ── Erwägungen (accordion with sub-sections) ──
  // For some decisions (notably older BGE entries) the case-brief
  // extractor cannot recover Erwägung numbers. In that case we render
  // the body as a flat block and skip the pinpoint affordances —
  // showing "E. ?" with a non-functional insert button would be a
  // regression vs. just showing the text.
  /* Prefer /api/structure (dedicated decision_structure.db, federal courts):
     it returns one entry per Erwägung paragraph with `e_number`, `depth`,
     `parent`, and `text_excerpt`/`text` — gold-standard source. Fall back
     to the case-brief `key_erwaegungen` (which also exposes `e_number`,
     historically also `number` from older API versions). */
  var ewList = null;
  if (state.structure && Array.isArray(state.structure.erwaegungen_paragraphs)
      && state.structure.erwaegungen_paragraphs.length) {
    /* Normalise structure rows to the same shape the renderer below
       expects (number + text). We use text_excerpt because the full
       text isn't returned by /api/structure to keep the payload small;
       a future iteration can lazily fetch /api/erwaegung/{id}/{e} on
       expand for the verbatim full body. */
    ewList = state.structure.erwaegungen_paragraphs.map(function (p) {
      return { e_number: p.e_number, depth: p.depth, text: p.text || p.text_excerpt || '' };
    });
  } else if (cb) {
    ewList = cb.key_erwaegungen || cb.erwaegungen || null;
  }
  if (ewList && ewList.length) {
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('section_erwaegungen', lang)) + '</div>';
    for (var i = 0; i < ewList.length; i++) {
      var e = ewList[i];
      /* The API returns `e_number` (snake_case) — older add-in code
         only checked `.number` which silently always failed. Accept
         both so we work against any vintage of the server response. */
      var rawNum = e.e_number || e.number;
      var hasRealNum = (typeof rawNum === 'string' && /^\d+(?:\.\d+)*$/.test(rawNum)) ||
                       (typeof rawNum === 'number' && isFinite(rawNum));
      var num = hasRealNum ? String(rawNum) : '';
      var ewText = e.text || '';
      // Without a real number, splitting is meaningless — emit one block.
      var subSections = hasRealNum
        ? splitErwaegung(num, ewText)
        : [{ num: '', text: ewText }];

      html += '<div class="erwaegung-block">';
      for (var si = 0; si < subSections.length; si++) {
        var sub = subSections[si];
        var subNeedsCollapse = sub.text.length > 200;
        var subId = 'ew-' + i + '-' + si;
        var isMain = (si === 0);
        var subHasNum = (typeof sub.num === 'string' && sub.num.length > 0);

        html += '<div class="ew-sub' + (isMain ? ' ew-main' : '') + '">';
        if (subHasNum) {
          // Pinpoint label + insert affordance — only when we have a
          // citable sub-number to insert.
          html += '<div class="erwaegung-header">' +
            '<span class="erwaegung-num' + (isMain ? '' : ' ew-sub-num') + '">' +
            escHtml(pinpointLabel(lang) + ' ' + sub.num) + '</span>' +
            '<button class="btn btn-insert erwaegung-insert" data-action="insert-ew" data-ew="' +
            escHtml(sub.num) + '" title="' + escHtml(formatCitation(d, lang, sub.num)) + '">' +
            escHtml(pinpointLabel(lang) + ' ' + sub.num) + '</button>' +
            '</div>';
        }
        html += '<div class="erwaegung-body' + (subNeedsCollapse ? ' collapsible' : '') +
          '" id="' + subId + '">' + escHtml(sub.text) + '</div>' +
          (subNeedsCollapse
            ? '<button class="expand-btn" data-action="toggle-expand" data-target="' + subId + '">' +
              escHtml(t('btn_show_more', lang)) + '</button>'
            : '') +
          '</div>';
      }
      html += '</div>';
    }
    html += '</div>';
  } else if (state.loading) {
    html += '<div class="section-card">' + renderSkeletonLines(4) + '</div>';
  }

  // ── Dispositiv (from case brief) ──
  if (cb && cb.dispositiv) {
    var dpCollapse = cb.dispositiv.length > 200;
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('section_dispositiv', lang)) + '</div>' +
      '<div class="section-body' + (dpCollapse ? ' collapsible' : '') + '" id="dp-body">' + escHtml(cb.dispositiv) + '</div>' +
      (dpCollapse ? '<button class="expand-btn" data-action="toggle-expand" data-target="dp-body">' + escHtml(t('btn_show_more', lang)) + '</button>' : '') +
      '</div>';
  }

  // ── Statutes ──
  if (cb && cb.statutes && cb.statutes.length) {
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('section_statutes', lang)) + '</div><div class="pills">';
    for (var j = 0; j < cb.statutes.length; j++) {
      var st = cb.statutes[j];
      var stLabel = typeof st === 'string' ? st : ('Art. ' + (st.article || '?') + ' ' + (st.law_code || ''));
      html += '<span class="pill pill-static">' + escHtml(stLabel) + '</span>';
    }
    html += '</div></div>';
  }

  // ── Full text: fallback when no Erwägungen were extracted ──
  var hasErwaegungen = ewList && ewList.length;
  if (d.full_text && !hasErwaegungen) {
    // No structured sections — show truncated full text as fallback
    var ftLen = d.full_text.length;
    var ftCollapse = ftLen > 3000;
    var ftDisplay = ftCollapse ? d.full_text.substring(0, 3000) : d.full_text;
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('section_fulltext', lang)) + '</div>' +
      '<div class="fulltext-body collapsible" id="fulltext-body">' + escHtml(ftDisplay) + '</div>';
    if (ftCollapse) {
      html += '<button class="expand-btn" data-action="expand-fulltext">' + escHtml(t('btn_show_more', lang)) + '</button>';
    }
    html += '</div>';
  }

  // Link to full text on opencaselaw.ch
  var entscheidUrl = 'https://mcp.opencaselaw.ch/entscheid/' + encodeURIComponent(d.decision_id || '');
  html += '<a class="fulltext-link" data-action="open-external" data-url="' + escHtml(entscheidUrl) + '">' +
    escHtml(t('fulltext_link', lang)) + ' \u2192</a>';

  return html;
}

// Help / keyboard shortcuts overlay. Triggered by '?' or via the
// gear-menu link. Lists every shortcut with both the platform-neutral
// label (Ctrl) and Mac-friendly fallback (Cmd) — Word for Mac is a
// real share of Swiss legal users.
function renderHelp() {
  var lang = state.lang;
  var isMac = (typeof navigator !== 'undefined' && /Mac/.test(navigator.platform || ''));
  var meta = isMac ? '\u2318' : 'Ctrl';   // ⌘ vs Ctrl
  function row(combo, label) {
    return '<tr><td class="kbd-cell"><kbd>' + escHtml(combo) + '</kbd></td>' +
      '<td>' + escHtml(label) + '</td></tr>';
  }
  var html = '<a class="back-link" data-action="back">\u2190 ' + escHtml(t('back_short', lang)) + '</a>';
  html += '<h3 class="settings-heading">' + escHtml(t('help_title', lang)) + '</h3>';
  html += '<p class="help-intro">' + escHtml(t('help_intro', lang)) + '</p>';
  html += '<table class="kbd-table"><tbody>';
  html += row(meta + ' + K',                t('help_focus_search', lang));
  html += row('Esc',                         t('help_back',         lang));
  html += row('?',                           t('help_toggle_help',  lang));
  html += row(meta + ' + Enter',             t('help_insert_top',   lang));
  html += row(meta + ' + Shift + Enter',     t('help_insert_multi', lang));
  html += row('Enter',                       t('help_open_first',   lang));
  html += row('Tab / Shift+Tab',             t('help_navigate',     lang));
  html += '</tbody></table>';
  html += '<div class="help-footer">' +
    '<a class="welcome-link" data-action="show-guide">' + escHtml(t('how_it_works', lang)) + '</a>' +
    '</div>';
  return html;
}

// Guide View
function renderGuide() {
  var lang = state.lang;
  var html = '<a class="back-link" data-action="back">\u2190 ' + escHtml(t('back_short', lang)) + '</a>';
  html += '<h3 class="settings-heading">' + escHtml(t('guide_title', lang)) + '</h3>';

  // Step 1: Search
  html += '<div class="guide-step">' +
    '<div class="guide-step-num">1</div>' +
    '<div class="guide-step-body">' +
    '<div class="guide-step-title">' + escHtml(t('guide_step1_title', lang)) + '</div>' +
    '<div class="guide-step-desc">' + escHtml(t('guide_step1_desc', lang)) + '</div>' +
    '</div></div>';

  // Step 2: Insert
  html += '<div class="guide-step">' +
    '<div class="guide-step-num">2</div>' +
    '<div class="guide-step-body">' +
    '<div class="guide-step-title">' + escHtml(t('guide_step2_title', lang)) + '</div>' +
    '<div class="guide-step-desc">' + escHtml(t('guide_step2_desc', lang)) + '</div>' +
    '</div></div>';

  // Step 3: Verify
  html += '<div class="guide-step">' +
    '<div class="guide-step-num">3</div>' +
    '<div class="guide-step-body">' +
    '<div class="guide-step-title">' + escHtml(t('guide_step3_title', lang)) + '</div>' +
    '<div class="guide-step-desc">' + escHtml(t('guide_step3_desc', lang)) + '</div>' +
    '</div></div>';

  // Coverage info
  html += '<div class="section-card" style="margin-top:16px;">' +
    '<div class="section-label">' + escHtml(t('guide_coverage_title', lang)) + '</div>' +
    '<div class="section-body">' + escHtml(t('guide_coverage_desc', lang)) + '</div>' +
    '</div>';

  html += '<button class="btn btn-insert btn-full" data-action="back" style="margin-top:16px;">' + escHtml(t('guide_start', lang)) + '</button>';
  return html;
}

// Verify View
function renderVerify() {
  var lang = state.lang;
  var html = '<a class="back-link" data-action="back">\u2190 ' + escHtml(t('back_short', lang)) + '</a>';
  html += '<h3 class="verify-heading">' + escHtml(t('verify_title', lang)) + '</h3>';

  if (state.verifyText) {
    html += '<div class="verify-selected"><div class="section-label">' + escHtml(t('verify_selected', lang)) + '</div>' +
      '<div class="section-body">' + escHtml(state.verifyText) + '</div></div>';
  }

  if (state.loading) {
    html += '<div class="verify-loading"><div class="spinner"></div><span>' + escHtml(t('verify_checking', lang)) + '</span></div>';
  } else if (state.verifyResult) {
    var v = state.verifyResult;
    /* PII trust banner — proves to the lawyer (and AppSource reviewers)
       that names / IBAN / AHV etc. never crossed the network. Total=0
       means clean input; helper returns '' so we don't clutter. */
    html += _renderPIIBanner(v._pii_summary, lang);
    var verdictLabels = {
      supports: t('verdict_supports', lang),
      partial: t('verdict_partial', lang),
      contradicts: t('verdict_contradicts', lang),
    };
    var icons = { supports: '\u2713', partial: '\u26A0', contradicts: '\u2717' };

    html += '<div class="verdict-card ' + escHtml(v.verdict) + '">' +
      '<div class="verdict-header">' +
      '<div class="verdict-icon ' + escHtml(v.verdict) + '">' + (icons[v.verdict] || '?') + '</div>' +
      '<div class="verdict-label">' + escHtml(verdictLabels[v.verdict] || v.verdict) + '</div></div>' +
      '<div class="verdict-explanation">' + escHtml(v.explanation || '') + '</div></div>';

    if (v.quote) {
      html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('relevant_ew', lang)) +
        (v.relevant_erwaegung ? ' (' + escHtml(pinpointLabel(lang) + ' ' + v.relevant_erwaegung) + ')' : '') +
        '</div><div class="verdict-quote">\u00AB' + escHtml(v.quote) + '\u00BB</div></div>';
    }

    var commentBtn = supportsComments()
      ? '<button class="btn btn-insert" style="flex:1;" data-action="insert-comment">' + escHtml(t('btn_insert_comment', lang)) + '</button>'
      : '<button class="btn btn-insert" style="flex:1;" data-action="insert-verdict-text">' + escHtml(t('btn_insert_result', lang)) + '</button>';

    html += '<div class="verdict-actions">' + commentBtn +
      '<button class="btn btn-detail" style="flex:1;" data-action="verify-fulltext">' + escHtml(t('btn_fulltext', lang)) + '</button></div>';
    var footerKey = localStorage.getItem('ocl_pro_key') ? 'verify_footer_pro' : 'verify_footer';
    html += '<div class="verdict-footer">' + escHtml(t(footerKey, lang)) + '</div>';
  } else if (state.error) {
    html += renderError();
  }

  return html;
}

// Support View
function renderSupport() {
  var lang = state.lang;
  var html = '<a class="back-link" data-action="back">\u2190 ' + escHtml(t('back', lang)) + '</a>';
  html += '<h3 class="verify-heading">' + escHtml(t('tool_support', lang)) + '</h3>';

  if (state.supportText) {
    html += '<div class="verify-selected"><div class="section-label">' + escHtml(t('support_your_statement', lang)) + '</div>' +
      '<div class="section-body">' + escHtml(state.supportText) + '</div></div>';
  }

  if (state.loading) {
    html += '<div class="verify-loading"><div class="spinner"></div><span>' + escHtml(t('support_searching', lang)) + '</span></div>';
  } else if (state.supportResult) {
    var sr = state.supportResult;
    html += _renderPIIBanner(sr._pii_summary, lang);

    // Show parsed claim + legal area
    if (sr.claim || sr.legal_area) {
      html += '<div class="support-meta">';
      if (sr.legal_area) html += '<span class="badge badge-area">' + escHtml(sr.legal_area) + '</span> ';
      if (sr.statutes) {
        for (var s = 0; s < sr.statutes.length && s < 3; s++) {
          html += '<span class="pill" style="margin:2px;">' + escHtml(sr.statutes[s]) + '</span>';
        }
      }
      html += '</div>';
    }

    // Results
    var results = sr.results || [];
    if (results.length === 0) {
      html += '<div class="state-message">' + escHtml(t('support_no_results', lang)) + '</div>';
    } else {
      html += '<div class="results">';
      html += '<div class="results-count">' + escHtml(t('support_found', lang, { n: results.length })) + '</div>';
      for (var i = 0; i < results.length; i++) {
        html += renderSupportCard(results[i], i);
      }
      html += '</div>';
    }

    html += '<div class="verdict-footer">' + escHtml(t('verify_footer_pro', lang)) + '</div>';
  } else if (state.error) {
    html += renderError();
  }
  return html;
}

function renderSupportCard(r, idx) {
  var lang = state.lang;
  var docket = r.docket_number || r.decision_id || '';
  var courtName = r.court ? getCourtName(r.court, lang) : (r.court_name || '');
  var date = r.decision_date || r.date || '';
  var relevance = r._relevance || 0;
  var supports = r._supports;
  var explanation = r._explanation || '';
  var keyPassage = r._key_passage || '';
  var regeste = r.regeste || stripHtml(r.snippet) || r.title || '';
  if (regeste.length > 150) regeste = regeste.substring(0, 150) + '\u2026';

  var supportClass = supports ? 'support-yes' : 'support-maybe';

  var html = '<div class="result-card ' + supportClass + '">' +
    '<div class="result-header"><div>' +
    '<div class="result-docket">' + escHtml(docket) + '</div>' +
    '<div class="result-meta">' + escHtml(date) + ' \u00B7 ' + escHtml(courtName) + '</div>' +
    '</div>';

  // Relevance indicator
  if (relevance > 0) {
    html += '<div class="relevance-bar" title="' + relevance + '% relevant">' +
      '<div class="relevance-fill" style="width:' + Math.min(relevance, 100) + '%;"></div></div>';
  }
  html += '</div>';

  // AI explanation
  if (explanation) {
    html += '<div class="support-explanation">' + escHtml(explanation) + '</div>';
  }

  // Key passage quote
  if (keyPassage) {
    html += '<div class="support-quote">\u00AB' + escHtml(keyPassage) + '\u00BB</div>';
  }

  // Regeste fallback
  if (!explanation && regeste) {
    html += '<div class="result-regeste">' + escHtml(regeste) + '</div>';
  }

  html += '<div class="result-actions">' +
    '<button class="btn btn-insert" data-action="insert-support" data-idx="' + idx + '">' + escHtml(t('btn_insert', lang)) + '</button>' +
    '<button class="btn btn-detail" data-action="detail-support" data-idx="' + idx + '">' + escHtml(t('btn_fulltext', lang)) + '</button>' +
    '</div></div>';
  return html;
}

// Related View
function renderRelated() {
  var lang = state.lang;
  var html = '<a class="back-link" data-action="back">\u2190 ' + escHtml(t('back', lang)) + '</a>';
  html += '<h3 class="verify-heading">' + escHtml(t('btn_find_related', lang)) + '</h3>';

  if (state.relatedRef) {
    html += '<div class="verify-selected"><div class="detail-label">' + escHtml(t('related_for', lang)) + '</div>' +
      '<div class="section-body" style="font-weight:600;">' + escHtml(state.relatedRef) + '</div></div>';
  }

  if (state.loading) {
    html += '<div class="verify-loading"><div class="spinner"></div><span>' + escHtml(t('loading', lang)) + '</span></div>';
  } else if (state.relatedResult) {
    var r = state.relatedResult;
    var incoming = r.incoming || [];
    var outgoing = r.outgoing || [];

    // Put all related results into state.results so insert/detail handlers work
    state.results = incoming.concat(outgoing);

    if (incoming.length > 0) {
      html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('related_cited_by', lang)) + '</div>';
      for (var i = 0; i < incoming.length; i++) {
        html += renderResultCard(incoming[i], i);
      }
      html += '</div>';
    }

    if (outgoing.length > 0) {
      html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('related_cites', lang)) + '</div>';
      for (var j = 0; j < outgoing.length; j++) {
        html += renderResultCard(outgoing[j], incoming.length + j);
      }
      html += '</div>';
    }

    if (incoming.length === 0 && outgoing.length === 0) {
      html += '<div class="state-message">' + escHtml(t('related_none', lang)) + '</div>';
    }

    html += '<div class="verdict-footer">' + escHtml(t('verify_footer_pro', lang)) + '</div>';
  } else if (state.error) {
    html += renderError();
  }
  return html;
}

// Settings View
function renderSettings() {
  var lang = state.lang;
  var proKey = localStorage.getItem('ocl_pro_key') || '';

  var html = '<a class="back-link" data-action="back">\u2190 ' + escHtml(t('back_short', lang)) + '</a>';
  html += '<h3 class="settings-heading">' + escHtml(t('settings_title', lang)) + '</h3>';

  // Pro subscription section
  html += '<div class="section-card">';
  if (proKey) {
    // Pro active
    html += '<div class="pro-badge-row"><span class="pro-badge">PRO</span> ' + escHtml(t('pro_active', lang)) + '</div>' +
      '<div class="settings-field"><label class="settings-label">' + escHtml(t('pro_license_key', lang)) + '</label>' +
      '<input type="text" class="settings-input" id="pro-key-input" value="' + escHtml(proKey) + '" readonly style="font-size:11px;color:var(--text-secondary);">' +
      '</div>' +
      '<div class="settings-actions">' +
      '<button class="btn btn-detail" data-action="manage-subscription">' + escHtml(t('btn_manage_sub', lang)) + '</button>' +
      '<button class="btn btn-detail" data-action="remove-pro" style="color:var(--red);">' + escHtml(t('btn_remove_license', lang)) + '</button>' +
      '</div>';
  } else {
    // Upgrade CTA — hero pitch on top, features as support, pricing on the button.
    html += '<div class="pro-hero">' +
      '<div class="pro-hero-icon">' + ICONS.shieldCheck + '</div>' +
      '<div class="pro-hero-title">' + escHtml(t('pro_hero_title', lang)) + '</div>' +
      '<div class="pro-hero-sub">' + escHtml(t('pro_hero_sub', lang)) + '</div>' +
      '</div>' +
      '<ul class="pro-features">' +
      '<li class="pro-feature"><span class="pro-feature-check">' + ICONS.check + '</span>' + escHtml(t('pro_feature_verify_new', lang)) + '</li>' +
      '<li class="pro-feature"><span class="pro-feature-check">' + ICONS.check + '</span>' + escHtml(t('pro_feature_support', lang)) + '</li>' +
      '<li class="pro-feature"><span class="pro-feature-check">' + ICONS.check + '</span>' + escHtml(t('pro_feature_related', lang)) + '</li>' +
      '<li class="pro-feature"><span class="pro-feature-check">' + ICONS.check + '</span>' + escHtml(t('pro_feature_limit', lang)) + '</li>' +
      '</ul>' +
      '<div class="pro-consent">' +
      '<label class="pro-consent-label"><input type="checkbox" id="pro-consent-check" class="pro-consent-check"> ' +
      escHtml(t('pro_consent_text', lang)) + ' ' +
      '<a href="https://word.opencaselaw.ch/terms.html" target="_blank">' + escHtml(t('consent_terms', lang)) + '</a> ' +
      escHtml(t('pro_consent_and', lang)) + ' ' +
      '<a href="https://word.opencaselaw.ch/privacy.html" target="_blank">' + escHtml(t('consent_privacy', lang)) + '</a>.' +
      '</label></div>' +
      '<button class="btn btn-pro" data-action="upgrade-pro" id="btn-upgrade-pro" disabled>' +
      escHtml(t('btn_upgrade_price', lang)) + '</button>' +
      '<div class="pro-cancel-note">' + escHtml(t('pro_cancel_note', lang)) + '</div>' +
      '<div class="pro-divider"><span>' + escHtml(t('pro_or_key', lang)) + '</span></div>' +
      '<div class="settings-field"><label class="settings-label">' + escHtml(t('pro_license_key', lang)) + '</label>' +
      '<input type="text" class="settings-input" id="pro-key-input" value="" placeholder="ocl_pro_...">' +
      '</div>' +
      '<div class="settings-actions">' +
      '<button class="btn btn-detail" data-action="activate-pro">' + escHtml(t('btn_activate', lang)) + '</button>' +
      '</div>';
  }
  html += '</div>';

  // Citation style picker — Swiss legal practice has several conventions;
  // expose them rather than dictating one. Live preview reflects each
  // choice using a fixed sample decision (BGer 4A_747/2012).
  var currentStyle = (function () {
    try { return localStorage.getItem('ocl_citation_style') || 'parenthesised'; }
    catch (e) { return 'parenthesised'; }
  })();
  var sample = {
    docket_number: '4A_747/2012',
    decision_date: '2013-04-05',
    court: 'bger',
  };
  function previewFor(s) {
    try { return formatCitation(sample, lang, '2.1', s); }
    catch (e) { return ''; }
  }
  html += '<div class="section-card" style="margin-top:12px;">' +
    '<div class="section-label">' + escHtml(t('cite_style_title', lang)) + '</div>' +
    '<div class="cite-style-list">';
  // Statically-keyed lookup keeps the i18n parity linter happy and
  // makes the four expected strings findable via grep.
  var STYLE_LABELS = {
    parenthesised: t('cite_style_parenthesised', lang),
    footnote:      t('cite_style_footnote',      lang),
    brief:         t('cite_style_brief',         lang),
    long:          t('cite_style_long',          lang),
  };
  ['parenthesised', 'footnote', 'brief', 'long'].forEach(function (s) {
    var checked = (s === currentStyle) ? ' checked' : '';
    html += '<label class="cite-style-row">' +
      '<input type="radio" name="cite-style" value="' + s + '" class="cite-style-radio"' + checked + '>' +
      '<div class="cite-style-meta">' +
      '<div class="cite-style-name">' + escHtml(STYLE_LABELS[s]) + '</div>' +
      '<div class="cite-style-preview">' + escHtml(previewFor(s)) + '</div>' +
      '</div></label>';
  });
  html += '</div></div>';

  // Privacy / telemetry section — opt-out toggle for the install cohort header
  var optedOut = localStorage.getItem('ocl_usage_signal_optout') === '1';
  html += '<div class="section-card" style="margin-top:12px;">' +
    '<div class="section-label">' + escHtml(t('priv_signal_title', lang)) + '</div>' +
    '<label class="pro-consent-label" style="align-items:flex-start; gap:8px;">' +
    '<input type="checkbox" class="pro-consent-check" id="priv-signal-toggle"' +
    (optedOut ? '' : ' checked') + '> ' +
    '<span>' + escHtml(t('priv_signal_body', lang)) + '</span>' +
    '</label>' +
    '<div style="font-size:11px; color:var(--text-secondary); margin-top:8px; line-height:1.5;">' +
    escHtml(t('priv_signal_note', lang)) + ' ' +
    '<a href="https://opencaselaw.ch/datenschutz/" target="_blank" style="color:var(--blue);">' + escHtml(t('priv_signal_more', lang)) + '</a>' +
    '</div>' +
    '<div style="font-size:11px; color:var(--text-secondary); margin-top:8px; line-height:1.5;">' +
    '\uD83D\uDD12 ' + escHtml(t('priv_redact_note', lang)) + ' ' +
    '<a href="https://word.opencaselaw.ch/privacy.html" target="_blank" style="color:var(--blue);">' + escHtml(t('priv_redact_more', lang)) + '</a>' +
    '</div>' +
    '</div>';

  html += '<div class="settings-footer">' +
    '<a href="https://opencaselaw.ch" target="_blank" style="color:var(--blue);">opencaselaw.ch</a> \u00B7 ' +
    '<a href="https://opencaselaw.ch/datenschutz/" target="_blank" style="color:var(--blue);">' + escHtml(t('priv_signal_privacy_link', lang)) + '</a> \u00B7 ' +
    '<a href="https://github.com/jonashertner/opencaselaw" target="_blank" style="color:var(--blue);">GitHub</a><br>' +
    'Code: MIT \u00B7 Daten: CC0 1.0</div>';

  return html;
}

// Error rendering
function renderError() {
  var lang = state.lang;
  if (!state.error) return '';
  if (state.error.type === 'daily_limit') {
    return '<div class="state-message">' + escHtml(t('error_daily_limit', lang)) + '</div>';
  }
  if (state.error.type === 'rate_limit') {
    return '<div class="state-message">' + escHtml(t('error_rate_limit', lang)) + '<br>' +
      escHtml(t('error_rate_wait', lang, { n: state.error.retryAfter })) +
      '<button class="retry-btn" data-action="retry">' + escHtml(t('btn_retry', lang)) + '</button></div>';
  }
  if (state.error.type === 'use_search') {
    return '<div class="state-message">' + escHtml(state.error.message) + '<br><a href="https://opencaselaw.ch" target="_blank" style="color:var(--text);font-weight:600;">opencaselaw.ch</a></div>';
  }
  if (state.error.type === 'not_found' || state.error.type === 'no_citation' || state.error.type === 'no_selection' || state.error.type === 'decision_not_found') {
    return '<div class="state-message">' + escHtml(state.error.message) + '</div>';
  }
  return '<div class="state-message">' + escHtml(t('error_connection', lang)) +
    '<button class="retry-btn" data-action="retry">' + escHtml(t('btn_retry', lang)) + '</button></div>';
}

// Skeleton helpers
function renderSkeletonLines(n) {
  var html = '';
  for (var i = 0; i < n; i++) {
    html += '<div class="skeleton-line" style="width:' + (70 + Math.round(Math.random() * 25)) + '%;"></div>';
  }
  return html;
}

// Search-results skeleton — three result-card-shaped placeholders.
// Replaces the bare spinner so the layout doesn't jump when results
// arrive (CLS = 0) and the user feels a faster perceived response.
function renderResultsSkeleton() {
  function card() {
    return '<div class="skeleton-card">' +
      '<div class="skeleton-line" style="width:35%;height:14px;"></div>' +
      '<div class="skeleton-line" style="width:55%;height:11px;margin-top:6px;"></div>' +
      '<div class="skeleton-line" style="margin-top:12px;"></div>' +
      '<div class="skeleton-line short"></div>' +
    '</div>';
  }
  return '<div class="results-skeleton" aria-hidden="true">' +
    card() + card() + card() +
  '</div>';
}

// Move the keyboard focus to a result card. -1 clears focus.
// Doesn't trigger a full render — just swaps the .is-focused class
// and scrolls the new card into view. State stays in sync so any
// subsequent render() picks up the right card.
function _moveResultFocus(newIdx) {
  var prev = document.querySelector('.result-card.is-focused');
  if (prev) prev.classList.remove('is-focused');
  state.focusedIdx = newIdx;
  if (newIdx < 0) return;
  var next = document.querySelector('.result-card[data-idx="' + newIdx + '"]');
  if (!next) return;
  next.classList.add('is-focused');
  if (typeof next.scrollIntoView === 'function') {
    try { next.scrollIntoView({ block: 'nearest' }); } catch (e) {}
  }
}

// Lightweight bottom-edge toast for action confirmations (e.g. after a
// successful citation insert into Word). Fades in 200 ms, holds 2.5 s,
// fades out 200 ms. Single-instance: subsequent calls replace the
// previous message rather than stacking. Live-region attribute makes
// AT users hear the confirmation too.
var _toastEl = null;
var _toastHideTimer = null;
function showToast(message, opts) {
  opts = opts || {};
  if (!_toastEl) {
    _toastEl = document.createElement('div');
    _toastEl.className = 'ocl-toast';
    _toastEl.setAttribute('role', 'status');
    _toastEl.setAttribute('aria-live', 'polite');
    document.body.appendChild(_toastEl);
  }
  if (_toastHideTimer) clearTimeout(_toastHideTimer);
  // Use textContent to avoid any HTML-injection surface (the toast is
  // called from places that pass raw decision dockets / titles).
  _toastEl.textContent = '';
  var iconMark = document.createElement('span');
  iconMark.className = 'ocl-toast-icon';
  iconMark.textContent = opts.icon || '\u2713';
  _toastEl.appendChild(iconMark);
  var msgSpan = document.createElement('span');
  msgSpan.className = 'ocl-toast-msg';
  msgSpan.textContent = message;
  _toastEl.appendChild(msgSpan);
  _toastEl.classList.add('is-visible');
  var holdMs = (opts.duration != null) ? opts.duration : 2500;
  _toastHideTimer = setTimeout(function () {
    if (_toastEl) _toastEl.classList.remove('is-visible');
  }, holdMs);
}

// Pre-fetch the detail payload for a result card the user is hovering.
// 200 ms debounce so flick-overs (cursor passing through the list to
// reach a button) don't burn API calls. Cached by decision_id; when
// the user actually clicks "detail", showDetail can hit the cache and
// render instantly. Failures are silent (no UI surface).
function _prefetchDetail(decisionId) {
  if (!decisionId) return;
  if (_prefetchCache[decisionId]) return;          // already cached
  if (_prefetchTimers[decisionId]) return;         // already pending
  _prefetchTimers[decisionId] = setTimeout(function () {
    delete _prefetchTimers[decisionId];
    if (_prefetchCache[decisionId]) return;
    if (typeof getDecisionDetail !== 'function') return;
    try {
      Promise.resolve(getDecisionDetail(decisionId))
        .then(function (data) {
          if (data) _prefetchCache[decisionId] = data;
        })
        .catch(function () {});
    } catch (e) { /* swallow */ }
  }, 200);
}
function _cancelPrefetch(decisionId) {
  if (_prefetchTimers[decisionId]) {
    clearTimeout(_prefetchTimers[decisionId]);
    delete _prefetchTimers[decisionId];
  }
}

function renderDetailSkeleton() {
  return '<a class="back-link" data-action="back">\u2190 ' + escHtml(t('back', state.lang)) + '</a>' +
    '<div class="skeleton-card" style="margin-top:8px;">' +
    '<div class="skeleton-line" style="width:50%;height:18px;"></div>' +
    '<div class="skeleton-line short" style="margin-top:6px;"></div>' +
    '</div>' +
    '<div class="skeleton-card">' + renderSkeletonLines(5) + '</div>' +
    '<div class="skeleton-card">' + renderSkeletonLines(3) + '</div>';
}

// Post-render setup (input focus + keydown only — click is bound once in initApp)
function bindEvents() {
  var searchInput = document.getElementById('search-input');
  if (searchInput) {
    searchInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        if (e.shiftKey) {
          doQuickInsert(searchInput.value);
        } else {
          // Cancel any pending live-search and run immediately.
          if (_liveSearchTimer) clearTimeout(_liveSearchTimer);
          doSearch(searchInput.value);
        }
      }
    });
    // Debounced live search — 350ms after the last keystroke. Skips empty,
    // skips reference patterns (those need Enter to confirm intent and
    // jump to detail). Min 3 chars to avoid scanning the whole corpus.
    searchInput.addEventListener('input', function (e) {
      var value = e.target.value;
      if (_liveSearchTimer) clearTimeout(_liveSearchTimer);
      var trimmed = (value || '').trim();
      if (trimmed.length < 3) return;
      if (_isReferencePattern(trimmed)) return;
      // 500ms (was 350ms): a legal query is typed deliberately with natural
      // pauses between words, and 350ms fired mid-thought ("search starts too
      // quickly"). 500ms still feels live but waits out a normal typing pause.
      _liveSearchTimer = setTimeout(function () { doSearch(value); }, 500);
    });
    searchInput.focus();
  }

  // Hover-prefetch on every visible result card. 200 ms debounce inside
  // _prefetchDetail so flick-overs don't burn API calls. When the user
  // actually clicks "detail", showDetail can hit _prefetchCache and
  // render instantly. Cleared (cancelled) when the cursor leaves before
  // the debounce fires.
  var resultCards = document.querySelectorAll('.result-card[data-decision-id]');
  for (var rci = 0; rci < resultCards.length; rci++) {
    (function (card) {
      var did = card.getAttribute('data-decision-id');
      if (!did) return;
      card.addEventListener('mouseenter', function () { _prefetchDetail(did); });
      card.addEventListener('mouseleave', function () { _cancelPrefetch(did); });
      card.addEventListener('focus', function () { _prefetchDetail(did); });
    })(resultCards[rci]);
  }

  var proCheck = document.getElementById('pro-consent-check');
  var proBtn = document.getElementById('btn-upgrade-pro');
  if (proCheck && proBtn) {
    proCheck.addEventListener('change', function () {
      proBtn.disabled = !proCheck.checked;
    });
  }

  // Privacy / usage signal toggle — persist opt-out state in localStorage
  var privToggle = document.getElementById('priv-signal-toggle');
  if (privToggle) {
    privToggle.addEventListener('change', function () {
      if (privToggle.checked) {
        localStorage.removeItem('ocl_usage_signal_optout');
      } else {
        localStorage.setItem('ocl_usage_signal_optout', '1');
      }
    });
  }

  // Citation-style radio group — persist user's pick and re-render so
  // any previewed citations refresh immediately.
  var styleRadios = document.querySelectorAll('input[name="cite-style"]');
  if (styleRadios && styleRadios.length) {
    styleRadios.forEach(function (radio) {
      radio.addEventListener('change', function () {
        if (!radio.checked) return;
        try { localStorage.setItem('ocl_citation_style', radio.value); } catch (e) {}
        render();
      });
    });
  }
}

// Delegated click handler — bound ONCE in initApp, never re-added
async function handleAppClick(e) {
    var btn = e.target.closest('[data-action]');
    if (!btn) return;
    _lastClickedBtn = btn;
    var action = btn.dataset.action;
    var idx = parseInt(btn.dataset.idx, 10);

    // The multi-select checkbox lives inside a result-card (whose
    // outer click opens the detail view). Stop propagation so a
    // checkbox toggle doesn't double-fire as a card click.
    if (action === 'toggle-select') {
      e.stopPropagation();
      if (typeof e.preventDefault === 'function') e.preventDefault();
    }

    switch (action) {
      case 'insert':       await insertCitation(state.results[idx]); break;
      case 'detail':       await showDetail(state.results[idx]); break;
      case 'toggle-select': {
        var did = btn.dataset.id;
        if (!did) break;
        var pos = state.selectedIds.indexOf(did);
        if (pos >= 0) state.selectedIds.splice(pos, 1);
        else state.selectedIds.push(did);
        render();
        break;
      }
      case 'multi-clear':
        state.selectedIds = [];
        render();
        break;
      case 'multi-insert':
        await insertMultiCitation();
        break;
      case 'insert-main':  await insertCitation(state.detail); break;
      case 'insert-main-link': await insertCitation(state.detail, null, { asLink: true }); break;
      case 'insert-ew':    await insertCitation(state.detail, btn.dataset.ew); break;
      case 'back': {
        // Sub-views (detail/verify/support/related/guide) return to the tab
        // they were launched from. Falls back to the search tab.
        var subViews = { detail: 1, verify: 1, support: 1, related: 1, guide: 1 };
        if (subViews[state.view] && state.previousView) {
          state.view = state.previousView;
          state.detail = null;
          state.caseBrief = null;
          state.structure = null;
          state.error = null;
        } else {
          state.view = 'search';
          state.detail = null;
          state.caseBrief = null;
          state.structure = null;
          state.verifyResult = null;
          state.supportResult = null;
          state.supportText = '';
          state.relatedResult = null;
          state.relatedRef = '';
          state.error = null;
        }
        render();
        break;
      }
      case 'retry':
        state.error = null;
        if (state.view === 'search') doSearch(state.query);
        render();
        break;
      case 'find-related':  await findRelated(); break;
      case 'find-support': await findSupport(); break;
      case 'scan-doc':     await scanDocument(); break;
      case 'reflect':      await reflectOnDocument(); break;
      case 'audit-open-summary':
        state.view = 'scan';
        state.error = null;
        render();
        break;
      case 'open-settings':
        state.view = 'settings';
        render();
        break;
      case 'audit-reset':
        state.scanResults = null;
        state.scanDocText = '';
        state.error = null;
        render();
        break;
      case 'audit-comment-all': await commentAllAuditIssues(); break;
      case 'audit-insert-bibliography': await insertBibliographyAtCursor(); break;
      case 'dismiss-hint':
        try { localStorage.setItem('ocl_hint_seen', '1'); } catch (_e) {}
        render();
        break;
      case 'audit-jump': {
        var jIss = state.scanResults && state.scanResults.issues && state.scanResults.issues[idx];
        if (jIss) {
          var jOk = await selectInDocument(jIss.citation, jIss._nth || 0);
          if (jOk && btn) {
            var jOrig = btn.textContent; btn.textContent = '\u2713';
            setTimeout(function () { btn.textContent = jOrig; }, 800);
          }
        }
        break;
      }
      case 'audit-comment': {
        var cIss = state.scanResults && state.scanResults.issues && state.scanResults.issues[idx];
        if (cIss) {
          var cText = _formatIssueComment(cIss, state.lang);
          var cOk = await commentOnSubstring(cIss.citation, cText, cIss._nth || 0);
          if (btn) {
            btn.textContent = cOk ? '\u2713' : '\u2717';
            setTimeout(function () { render(); }, 1000);
          }
        }
        break;
      }
      case 'audit-fix-pinpoint': {
        var fIss = state.scanResults && state.scanResults.issues && state.scanResults.issues[idx];
        var newPp = btn.dataset.pp;
        if (fIss && newPp) {
          // Strip old E./consid./para. suffix and re-attach the suggested
          // one in user-language (centralised via pinpointLabel()).
          var stripped = fIss.citation.replace(/\s*(?:E\.|consid\.|para\.|cons\.)\s*[\d.]+\s*$/i, '').trim();
          var fixed = stripped + ' ' + pinpointLabel(state.lang) + ' ' + newPp;
          var fOk = await replaceInDocument(fIss.citation, fixed, fIss._nth || 0);
          if (btn) {
            btn.textContent = fOk ? '\u2713' : '\u2717';
            setTimeout(function () { render(); }, 1000);
          }
        }
        break;
      }
      case 'insert-support':
        if (state.supportResult && state.supportResult.results) {
          await insertCitation(state.supportResult.results[idx]);
        }
        break;
      case 'detail-support':
        if (state.supportResult && state.supportResult.results) {
          await showDetail(state.supportResult.results[idx]);
        }
        break;
      case 'insert-law-para':
        await insertTextAtCursor(btn.dataset.ref || '');
        if (btn) { var _o = btn.textContent; btn.textContent = '\u2713'; setTimeout(function () { btn.textContent = _o; }, 600); }
        break;
      case 'insert-law-verbatim': {
        var lvText = btn.dataset.text || '';
        var lvRef = btn.dataset.ref || '';
        if (lvText && lvRef) {
          // German guillemets for quote; legal style is «...» (Art. X Abs. N AB)
          await insertTextAtCursor('\u00AB' + lvText + '\u00BB (' + lvRef + ')');
          if (btn) { btn.classList.add('btn-success'); setTimeout(function () { btn.classList.remove('btn-success'); }, 900); }
        }
        break;
      }
      case 'insert-law-ref':
        if (state.lawResult && state.lawResult.articles && state.lawResult.articles.length > 0) {
          var la = state.lawResult.articles[0];
          var laRef = 'Art. ' + (la.article_num || la.article || '') + ' ' + (state.lawResult.abbreviation || '');
          await insertTextAtCursor(laRef);
        }
        break;
      case 'accept-consent':
        state.consent = true;
        try { localStorage.setItem('ocl_consent', '1'); } catch (e) {}
        render();
        break;
      case 'quick-search':
        doSearch(btn.dataset.query || '');
        break;
      case 'toggle-expand':
        var target = document.getElementById(btn.dataset.target);
        if (target) {
          var isExpanded = target.classList.toggle('expanded');
          btn.textContent = isExpanded ? t('btn_show_less', state.lang) : t('btn_show_more', state.lang);
        }
        break;
      case 'expand-fulltext':
        var ftEl = document.getElementById('fulltext-body');
        if (ftEl && state.detail && state.detail.full_text) {
          var MAX_DOM_CHARS = 50000;
          var ft = state.detail.full_text;
          if (ft.length > MAX_DOM_CHARS) {
            ftEl.textContent = ft.substring(0, MAX_DOM_CHARS);
            // Add "continue on opencaselaw.ch" link after the text
            var contLink = document.createElement('a');
            contLink.className = 'fulltext-link';
            contLink.dataset.action = 'open-external';
            contLink.dataset.url = 'https://mcp.opencaselaw.ch/entscheid/' + encodeURIComponent(state.detail.decision_id || '');
            contLink.textContent = t('fulltext_continue', state.lang);
            ftEl.parentNode.insertBefore(contLink, ftEl.nextSibling.nextSibling || null);
          } else {
            ftEl.textContent = ft;
          }
          ftEl.classList.add('expanded');
          btn.textContent = t('btn_show_less', state.lang);
          btn.dataset.action = 'collapse-fulltext';
        }
        break;
      case 'collapse-fulltext':
        var ftEl2 = document.getElementById('fulltext-body');
        if (ftEl2 && state.detail && state.detail.full_text) {
          ftEl2.textContent = state.detail.full_text.substring(0, 3000);
          ftEl2.classList.remove('expanded');
          btn.textContent = t('btn_show_more', state.lang);
          btn.dataset.action = 'expand-fulltext';
          // Remove "continue" link if added
          var contLinks = ftEl2.parentNode.querySelectorAll('.fulltext-link');
          contLinks.forEach(function(el) { el.remove(); });
        }
        break;
      case 'open-external':
        var extUrl = btn.dataset.url;
        if (extUrl) openExternal(extUrl);
        break;
      case 'clear-search':
        state.query = '';
        state.results = [];
        state.total = 0;
        state.lawResult = null;
        state.error = null;
        render();
        break;
      case 'show-guide':
        state.previousView = state.view;
        state.view = 'guide';
        render();
        break;
      case 'show-help':
        state.previousView = state.view;
        state.view = 'help';
        render();
        break;
      case 'verify-ref':   await startVerify(); break;
      case 'strengthen':   await startStrengthen(); break;
      case 'strengthen-insert-citation': {
        var sIdx = parseInt(btn.dataset.idx, 10);
        var s = state.strengthenResult && state.strengthenResult.suggested_citations &&
                state.strengthenResult.suggested_citations[sIdx];
        if (s) {
          /* Insert citation_string verbatim — server returns canonical
             "BGE 132 III 222" / "BGer 4A_747/2012 vom 5. April 2013" forms */
          await insertTextAtCursor(' (' + (s.citation || '') + ')');
        }
        break;
      }
      case 'insert-comment': await doInsertComment(); break;
      case 'insert-verdict-text': await doInsertVerdictText(); break;
      case 'verify-fulltext':
        if (state.verifyResult && state.verifyResult._decision) {
          await showDetail(state.verifyResult._decision);
        }
        break;
      case 'manage-subscription':
        var mKey = localStorage.getItem('ocl_pro_key');
        if (mKey) {
          try {
            var portal = await apiPost('/billing/portal?key=' + encodeURIComponent(mKey));
            if (portal.portal_url) openExternal(portal.portal_url);
          } catch (e) { console.error('Portal error:', e); }
        }
        break;
      case 'upgrade-pro':  await doUpgradePro(); break;
      case 'activate-pro': await doActivatePro(); break;
      case 'remove-pro':
        _clearProKey();
        render();
        break;
    }
}

// Actions — Free tier: direct lookup only (no keyword search)
async function doSearch(query) {
  query = (query || '').trim();
  if (!query) return;

  state.query = query;
  state.results = [];
  state.lawResult = null;
  state.loading = true;
  state.error = null;
  // Clear keyboard focus + prefetch cache on every new search so we don't
  // dangle a focused-card ref to a stale result list and don't grow the
  // cache unbounded across queries.
  state.focusedIdx = -1;
  _prefetchCache = {};
  render();

  // 1. Detect law article pattern → direct law lookup
  // Matches "Art. 41 OR" and "OR 41" / "BV 8" etc.
  var lawMatch = query.match(LAW_PATTERN);
  var lawReverse = !lawMatch && query.trim().match(LAW_PATTERN_REVERSE);
  var lawAbbrev = lawMatch ? lawMatch[2] : (lawReverse ? lawReverse[1] : null);
  var lawArticle = lawMatch ? lawMatch[1] : (lawReverse ? lawReverse[2] : null);
  if (lawAbbrev) {
    var normalized = LAW_ALIAS[lawAbbrev.toLowerCase()];
    if (normalized) lawAbbrev = normalized;
    try {
      var lawData = await getLaw(lawAbbrev, lawArticle, state.lang);
      if (lawData && lawData.articles && lawData.articles.length > 0) {
        state.lawResult = lawData;
        addRecentLookup(query);
        state.loading = false;
        render();
        return;
      }
      // Empty result: with the generic LAW_PATTERN we accept any
      // uppercase-leading short token, so non-law false-positives
      // ("Art. 5 Vertrag") will land here. Fall through to keyword
      // search instead of showing "not found".
    } catch (e) {
      // 404 / network error → fall through to keyword search.
    }
  }

  // 2. Detect decision reference → direct decision lookup
  var isDecisionRef = /^(BGE|ATF|DTF)\s+\d+\s+[IVX]+\s+\d+$/i.test(query) ||
                      /^\d[A-Z]_\d+\/\d{4}$/.test(query) ||
                      /^[A-Z]-\d+\/\d{4}$/.test(query) ||
                      /^[A-Z]{2,}\.\d{4}\.\d+$/.test(query);

  if (isDecisionRef) {
    try {
      var decision = await getDecision(query);
      if (decision && decision.decision_id) {
        state.loading = false;
        addRecentLookup(query);
        await showDetail(decision);
        return;
      }
    } catch (e) {
      // Not found by direct lookup — fall through to search
    }
  }

  // 3. Keyword search — full-text search across the corpus, all languages.
  // Result objects already include citation_string_{de,fr,it} + canonical_url.
  try {
    var data = await searchDecisions(query, { limit: 20 });
    var prevResults = state.results;
    state.results = (data && data.results) || [];
    state.total = (data && data.total) || 0;
    if (state.results.length > 0) addRecentLookup(query);
    // Drop any previously-selected ids that aren't in the new result
    // set — the multi-bar must only reference visible decisions.
    if (state.selectedIds.length && state.results !== prevResults) {
      var stillVisible = {};
      for (var i = 0; i < state.results.length; i++) {
        var rid = state.results[i].decision_id;
        if (rid) stillVisible[rid] = 1;
      }
      state.selectedIds = state.selectedIds.filter(function (id) {
        return !!stillVisible[id];
      });
    }
  } catch (e) {
    state.error = e && e.type ? e : { type: 'http_error' };
  }
  state.loading = false;
  render();
}

async function doQuickInsert(query) {
  query = (query || '').trim();
  if (!query) return;
  // Try to format and insert citation directly
  var lawMatch = query.match(LAW_PATTERN);
  var lawReverse = !lawMatch && query.trim().match(LAW_PATTERN_REVERSE);
  var lawAbbrev = lawMatch ? lawMatch[2] : (lawReverse ? lawReverse[1] : null);
  var lawArticle = lawMatch ? lawMatch[1] : (lawReverse ? lawReverse[2] : null);
  if (lawAbbrev) {
    await insertTextAtCursor('Art. ' + lawArticle + ' ' + lawAbbrev);
    return;
  }
  // Decision ref — build citation and insert
  var citation = '(' + query + ')';
  await insertTextAtCursor(citation);
}

async function showDetail(decision) {
  state.previousView = state.view;
  state.view = 'detail';
  state.detail = decision;
  state.caseBrief = null;
  state.structure = null;
  var id = decision.decision_id || decision.docket_number;
  // Hover-prefetch hit: if the user paused over this card before
  // clicking, we already have the detail payload in memory. Skip the
  // network round-trip entirely and render synchronously.
  var cached = id ? _prefetchCache[id] : null;
  if (cached && cached.regeste) {
    state.detail = Object.assign({}, decision, cached);
    state.loading = false;
    render();
    // Still fetch case-brief AND the structured-paragraphs sidecar
    // in the background. The structure DB has gold-standard E. numbers
    // for federal decisions; case-brief has Sachverhalt / statutes.
    try {
      var pair = await Promise.all([
        getCaseBrief(id).catch(function () { return null; }),
        getDecisionStructure(id).catch(function () { return null; }),
      ]);
      if (pair[0]) state.caseBrief = pair[0];
      if (pair[1]) state.structure = pair[1];
      if (pair[0] || pair[1]) render();
    } catch (e) { /* silent */ }
    return;
  }
  state.loading = true;
  render();
  try {
    var results = await Promise.all([
      getDecision(id).catch(function () { return null; }),
      getCaseBrief(id).catch(function () { return null; }),
      getDecisionStructure(id).catch(function () { return null; }),
    ]);
    if (results[0]) state.detail = Object.assign({}, decision, results[0]);
    state.caseBrief = results[1];
    state.structure = results[2];
  } catch (e) {
    state.error = e;
  }
  state.loading = false;
  render();
}

var _lastClickedBtn = null;
async function insertCitation(decision, erwaegung, opts) {
  if (!decision) return;
  opts = opts || {};
  var text = formatCitation(decision, state.lang, erwaegung);
  var btn = _lastClickedBtn;

  // Default insert mode = PLAIN TEXT. Swiss legal practice expects
  // citations in briefs / memoranda to be plain text references —
  // hyperlinks are unconventional in court submissions and look
  // amateurish in printed-on-paper documents. Users who explicitly
  // want a link click the dedicated "Insert with link" button (the
  // only path that sets opts.asLink = true).
  var asLink = opts.asLink === true;
  var url = decision.canonical_url || (decision.decision_id ? 'https://mcp.opencaselaw.ch/entscheid/' + encodeURIComponent(decision.decision_id) : null);

  try {
    if (asLink && url) {
      await insertHyperlinkAtCursor(text, url);
    } else {
      await insertTextAtCursor(text);
    }
    if (btn) {
      var orig = btn.innerHTML;
      btn.innerHTML = ICONS.check;
      btn.classList.add('btn-success');
      setTimeout(function () {
        btn.innerHTML = orig;
        btn.classList.remove('btn-success');
      }, 900);
    }
    // Bottom-edge toast confirms the insert visibly even when the user's
    // eyes are on Word (most cases). The button micro-feedback above
    // covers the local "I clicked the right thing" affordance; the toast
    // covers the remote "did it actually land in my document" affordance.
    var docket = decision.citation_string_de || decision.docket_number ||
                 decision.decision_id || '';
    showToast(t('toast_inserted', state.lang) + (docket ? ' \u00B7 ' + docket : ''));
  } catch (e) {
    if (btn) {
      btn.textContent = '\u2717';
      btn.style.background = 'var(--red)';
      setTimeout(function () { btn.textContent = t('btn_insert', state.lang); btn.style.background = ''; }, 2000);
    }
    showToast(t('toast_insert_failed', state.lang), { icon: '\u2717', duration: 4000 });
    console.error('Insert failed:', e);
  }
}

/**
 * Insert all currently selected results as one Swiss-convention
 * multi-citation. Called from the floating multi-bar's "Insert N
 * selected" button and from the Ctrl/Cmd + Shift + Enter shortcut.
 *
 * Always inserts plain text (not a hyperlink) — there isn't a single
 * meaningful URL for a cluster of authorities, and Word's
 * insertHyperlink can only target one URL per range.
 *
 * Visual feedback mirrors single-insert: success tick on the button,
 * 900 ms revert. Selection is cleared on success so the bar dismisses
 * itself; failure preserves the selection so the user can retry.
 */
async function insertMultiCitation() {
  if (!state.selectedIds.length) return;
  var lang = state.lang;
  var btn = _lastClickedBtn;

  // Map ids back to decision objects, in the order they were clicked
  // (which matches the order the user selected them — preserves intent).
  var byId = {};
  for (var i = 0; i < state.results.length; i++) {
    var r = state.results[i];
    var did = r.decision_id || ('idx-' + i);
    byId[did] = r;
  }
  var decisions = state.selectedIds
    .map(function (id) { return byId[id]; })
    .filter(function (d) { return !!d; });
  if (!decisions.length) return;

  var text = formatMultiCitation(decisions, lang);

  try {
    await insertTextAtCursor(text);
    state.selectedIds = [];
    if (btn) {
      var orig = btn.innerHTML;
      btn.innerHTML = ICONS.check + ' ' + escHtml(t('multi_inserted', lang, { n: decisions.length }));
      btn.classList.add('btn-success');
      // Re-render after the tick so the bar dismisses cleanly.
      setTimeout(function () {
        btn.classList.remove('btn-success');
        render();
      }, 900);
    } else {
      render();
    }
  } catch (e) {
    if (btn) {
      btn.textContent = '\u2717';
      btn.style.background = 'var(--red)';
      setTimeout(function () { render(); }, 2000);
    }
    console.error('Multi-insert failed:', e);
  }
}

async function startVerify() {
  var lang = state.lang;
  var proKey = localStorage.getItem('ocl_pro_key');

  if (!proKey) {
    state.view = 'settings';
    render();
    return;
  }
  state.previousView = state.view;
  try {
    /* Selection-or-paragraph fallback: lawyers don't pre-select. */
    var selected = await getSelectionOrParagraph();
    if (!selected || selected.trim().length < 10) {
      state.error = { type: 'no_selection', message: t('no_selection', lang) };
      state.view = 'verify';
      render();
      return;
    }
    state.verifyText = selected;
    state.view = 'verify';
    state.loading = true;
    state.verifyResult = null;
    state.error = null;
    render();

    var refs = extractCitations(selected);
    var caseRef = refs.length > 0 ? refs[0] : selected.trim().substring(0, 50);
    state.verifyResult = await verifyReferencePro(proKey, selected, caseRef, lang);
  } catch (e) {
    if (e.type === 'invalid_license') {
      _clearProKey();
      state.error = { type: 'no_selection', message: t('pro_key_invalid', lang) };
    } else if (e.type === 'redact_unavailable') {
      /* Structural-redaction guard fired client-side. Show a clean
         message; don't silently fall back to sending un-redacted. */
      state.error = { type: 'redact_unavailable', message: t('redact_unavailable', lang) };
    } else if (e.type === 'http_error' && e.status === 400 &&
               (e.message || '').indexOf('client_redaction_incomplete') >= 0) {
      state.error = { type: 'redact_server_reject', message: t('redact_server_reject', lang) };
    } else {
      state.error = e;
    }
  }
  state.loading = false;
  render();
}

/* Verify-and-Strengthen — Pro feature #2.
   Paragraph-only: lawyers run this on individual argument paragraphs
   they want to bulletproof. Selection-or-paragraph fallback identical
   to startVerify so behaviour stays predictable across both buttons. */
async function startStrengthen() {
  var lang = state.lang;
  var proKey = localStorage.getItem('ocl_pro_key');
  if (!proKey) {
    state.view = 'settings';
    render();
    return;
  }
  state.previousView = state.view;
  try {
    var paragraph = await getSelectionOrParagraph();
    if (!paragraph || paragraph.trim().length < 20) {
      state.error = { type: 'no_selection', message: t('strengthen_no_text', lang) };
      state.view = 'strengthen';
      render();
      return;
    }
    state.strengthenText = paragraph;
    state.view = 'strengthen';
    state.loading = true;
    state.strengthenResult = null;
    state.error = null;
    render();
    state.strengthenResult = await verifyAndStrengthenPro(proKey, paragraph, lang);
  } catch (e) {
    if (e.type === 'invalid_license') {
      _clearProKey();
      state.error = { type: 'no_selection', message: t('pro_key_invalid', lang) };
    } else if (e.type === 'redact_unavailable') {
      state.error = { type: 'redact_unavailable', message: t('redact_unavailable', lang) };
    } else if (e.type === 'http_error' && e.status === 400 &&
               (e.message || '').indexOf('client_redaction_incomplete') >= 0) {
      state.error = { type: 'redact_server_reject', message: t('redact_server_reject', lang) };
    } else {
      state.error = e;
    }
  }
  state.loading = false;
  render();
}

async function findSupport() {
  var lang = state.lang;
  var proKey = localStorage.getItem('ocl_pro_key');
  if (!proKey) { state.view = 'settings'; render(); return; }
  state.previousView = state.view;
  try {
    var selected = await getSelectedText();
    if (!selected || selected.trim().length < 15) {
      state.error = { type: 'no_selection', message: t('no_selection_support', lang) };
      state.view = 'support';
      render();
      return;
    }
    state.supportText = selected;
    state.view = 'support';
    state.loading = true;
    state.supportResult = null;
    state.error = null;
    render();
    state.supportResult = await findSupportingDecisions(proKey, selected, lang);
  } catch (e) {
    if (e.type === 'invalid_license') {
      _clearProKey();
      state.error = { type: 'no_selection', message: t('pro_key_invalid', lang) };
    } else {
      state.error = e;
    }
  }
  state.loading = false;
  render();
}

async function doInsertComment() {
  if (!state.verifyResult) return;
  var v = state.verifyResult;
  var lang = state.lang;
  var verdictLabels = {
    supports: '\u2713 ' + t('verdict_supports', lang),
    partial: '\u26A0 ' + t('verdict_partial', lang),
    contradicts: '\u2717 ' + t('verdict_contradicts', lang),
  };
  var text = (verdictLabels[v.verdict] || v.verdict) + ': ' + (v.explanation || '');
  var inserted = await insertComment(text);
  if (!inserted) {
    await insertTextAtCursor(' [' + text + ']');
  }
}

async function doInsertVerdictText() {
  if (!state.verifyResult) return;
  var v = state.verifyResult;
  var icons = { supports: '\u2713', partial: '\u26A0', contradicts: '\u2717' };
  await insertTextAtCursor(' [' + (icons[v.verdict] || '?') + ' ' + (v.explanation || '') + ']');
}

async function findRelated() {
  var lang = state.lang;
  var proKey = localStorage.getItem('ocl_pro_key');
  if (!proKey) { state.view = 'settings'; render(); return; }
  state.previousView = state.view;
  try {
    var selected = await getSelectedText();
    if (!selected || selected.trim().length < 3) {
      state.error = { type: 'no_selection', message: t('no_selection_related', lang) };
      render();
      return;
    }
    var refs = extractCitations(selected);
    if (refs.length === 0) {
      state.error = { type: 'no_citation', message: t('no_selection_related', lang) };
      render();
      return;
    }
    // Resolve the citation to a decision ID
    state.loading = true;
    state.view = 'related';
    state.relatedRef = refs[0];
    state.relatedResult = null;
    state.error = null;
    render();

    // Get the decision first to resolve the ID
    var decision = null;
    try { decision = await getDecision(refs[0]); } catch (e) {}
    var decisionId = decision ? decision.decision_id : refs[0];

    // Fetch citation graph
    var citations = await findCitations(decisionId, 'both', 10);

    // Enrich results with metadata by looking up each one
    var incoming = citations.incoming || [];
    var outgoing = citations.outgoing || [];

    // Fetch regeste for top results
    async function enrichResults(list) {
      var refs = list
        .map(function (c) { return c.docket_number || c.bge_ref || c.decision_id || ''; })
        .filter(function (r) { return r && r !== 'None'; })
        .slice(0, 5);
      // Sequential to avoid overwhelming the connection
      var enriched = [];
      for (var i = 0; i < refs.length && enriched.length < 3; i++) {
        try {
          var data = await searchDecisions(refs[i], { limit: 1 });
          if (data.results && data.results.length > 0) {
            var d = data.results[0];
            if (d.regeste || d.title || stripHtml(d.snippet)) enriched.push(d);
          }
        } catch (e) {}
      }
      return enriched;
    }

    var enrichedIncoming = await enrichResults(incoming);
    var enrichedOutgoing = await enrichResults(outgoing);

    state.relatedResult = {
      ref: refs[0],
      decision_id: decisionId,
      incoming: enrichedIncoming,
      outgoing: enrichedOutgoing,
    };
  } catch (e) {
    state.error = e;
  }
  state.loading = false;
  render();
}

async function doUpgradePro() {
  try {
    var successUrl = 'https://word.opencaselaw.ch/pro-success.html?session_id={CHECKOUT_SESSION_ID}';
    var cancelUrl = 'https://word.opencaselaw.ch/';
    var localeMap = { de: 'de', fr: 'fr', it: 'it', en: 'en' };
    var locale = localeMap[state.lang] || 'de';
    var data = await createCheckout(successUrl, cancelUrl, locale);
    if (data.checkout_url) {
      openExternal(data.checkout_url);
    }
  } catch (e) {
    console.error('Checkout error:', e);
  }
}

/* Clear the Pro license from BOTH localStorage and roamingSettings.
   Called from every site that handled an invalid_license error or an
   explicit user "remove key" action — without the roaming side, an
   invalidated key would silently re-appear on next launch. */
function _clearProKey() {
  try { localStorage.removeItem('ocl_pro_key'); } catch (_e) {}
  if (_insideWord && Office.context && Office.context.roamingSettings) {
    try {
      Office.context.roamingSettings.remove('ocl_pro_key');
      Office.context.roamingSettings.saveAsync();
    } catch (_e) {}
  }
}

async function doActivatePro() {
  var keyInput = document.getElementById('pro-key-input');
  if (!keyInput) return;
  var key = keyInput.value.trim();
  if (!key || !key.startsWith('ocl_pro_')) {
    state.error = { type: 'invalid_license', message: t('pro_key_invalid', state.lang) };
    render();
    return;
  }
  try {
    var data = await validateLicense(key);
    if (data && data.valid) {
      localStorage.setItem('ocl_pro_key', key);
      state.error = null;
      /* Mirror into roamingSettings so the activation follows the user
         across Word for Mac / Win / Online — they paste once, not once
         per device. saveAsync is fire-and-forget; failure here only
         affects roaming, not the local activation. */
      if (_insideWord && Office.context && Office.context.roamingSettings) {
        try {
          Office.context.roamingSettings.set('ocl_pro_key', key);
          Office.context.roamingSettings.saveAsync();
        } catch (_e) { /* roaming unavailable — local activation still works */ }
      }
      render();
    } else {
      /* Server returned valid:false (or empty body). Show user-facing
         feedback so the input doesn't appear to silently swallow the
         click — previously this branch returned with no UI signal. */
      state.error = { type: 'invalid_license', message: t('pro_key_invalid', state.lang) };
      render();
    }
  } catch (e) {
    console.error('License validation error:', e);
    state.error = { type: 'invalid_license', message: t('pro_key_invalid', state.lang) };
    render();
  }
}

// Recent lookups helper
function addRecentLookup(query) {
  query = query.trim();
  if (!query) return;
  // Remove if already exists, add to front
  state.recentLookups = state.recentLookups.filter(function (r) { return r !== query; });
  state.recentLookups.unshift(query);
  if (state.recentLookups.length > 8) state.recentLookups = state.recentLookups.slice(0, 8);
  try { localStorage.setItem('ocl_recent', JSON.stringify(state.recentLookups)); } catch (e) {}
}

// Audit Document (Pro feature) — single /api/attest call validates every
// citation in the document for existence + pinpoint correctness, returning
// structured issues with positions for in-document jump + comment.
//
// On success: auto-inserts a Word comment on each issue (when the platform
// supports comments + the user hasn't opted out via "ocl_no_auto_comment").
// The lawyer reviews issues inline in their document, not in a sidebar list.
async function scanDocument() {
  var lang = state.lang;
  var proKey = localStorage.getItem('ocl_pro_key');
  if (!proKey) { state.view = 'settings'; render(); return; }

  var docText = await getDocumentText();
  // Two distinct failure modes — surface them separately so the user
  // gets actionable feedback instead of the previous catch-all
  // "only available inside Word" message that fired even when they
  // *were* in Word but the document happened to be empty.
  if (!docText) {
    if (!_insideWord) {
      state.error = { type: 'not_in_word', message: t('audit_no_word', lang) };
    } else {
      state.error = { type: 'doc_empty', message: t('audit_doc_empty', lang) };
    }
    state.view = 'scan';
    render();
    return;
  }

  // Stay on whatever view the user is on; the strip shows progress.
  state.auditRunning = true;
  state.scanResults = null;
  state.scanDocText = docText;
  state.error = null;
  render();

  try {
    var report = await attestDocument(docText, lang);
    var seen = {};
    var issues = (report.issues || []).map(function (issue) {
      var cite = issue.citation || '';
      var n = seen[cite] || 0; seen[cite] = n + 1;
      return Object.assign({}, issue, { _nth: n });
    });
    state.scanResults = {
      ok: !!report.ok,
      total: report.citations_found || 0,
      passed: report.citations_ok || 0,
      issues: issues,
      annotated: report.annotated_text || '',
      autoCommentedCount: 0,
    };
    state.lastAuditAt = new Date().toISOString();
    _persistAuditState();

    // Auto-insert Word comments for each issue unless the user opted out.
    var autoComment = supportsComments() && localStorage.getItem('ocl_no_auto_comment') !== '1';
    if (autoComment && issues.length > 0) {
      var inserted = 0;
      for (var i = 0; i < issues.length; i++) {
        var iss = issues[i];
        var msg = _formatIssueComment(iss, lang);
        var ok = await commentOnSubstring(iss.citation, msg, iss._nth || 0);
        if (ok) inserted += 1;
      }
      state.scanResults.autoCommentedCount = inserted;
    }

    // After success, surface the summary view.
    state.view = 'scan';
  } catch (e) {
    if (e && e.type === 'invalid_license') {
      _clearProKey();
      state.error = { type: 'no_selection', message: t('pro_key_invalid', lang) };
    } else {
      state.error = e;
    }
    state.view = 'scan';
  }
  state.auditRunning = false;
  render();
}

// Persist audit state per-document so it survives Word reopen.
function _persistAuditState() {
  if (!_insideWord || !Office.context || !Office.context.document || !Office.context.document.settings) return;
  try {
    var s = Office.context.document.settings;
    s.set('ocl_audit_total', (state.scanResults && state.scanResults.total) || 0);
    s.set('ocl_audit_issues', (state.scanResults && state.scanResults.issues && state.scanResults.issues.length) || 0);
    s.set('ocl_audit_at', state.lastAuditAt || '');
    s.saveAsync();
  } catch (e) { /* settings unavailable on this host */ }
}
function _restoreAuditState() {
  if (!_insideWord || !Office.context || !Office.context.document || !Office.context.document.settings) return;
  try {
    var s = Office.context.document.settings;
    var at = s.get('ocl_audit_at');
    var total = s.get('ocl_audit_total');
    var issues = s.get('ocl_audit_issues');
    if (at && total !== null) {
      state.lastAuditAt = at;
      // Restore a stub state so the strip shows "Last check: N min ago"
      // without re-running. Full issue details are gone (we'd need to
      // re-audit to rebuild them) — that's intentional, prevents stale
      // jump-to-position pointers if the document changed.
      state.scanResults = { total: total, passed: total - issues, issues: [], annotated: '', _restored: true };
    }
  } catch (e) { /* ignore */ }
}

// Insert a Word comment for every issue, anchored on the original citation text.
async function commentAllAuditIssues() {
  var lang = state.lang;
  if (!state.scanResults || !state.scanResults.issues) return;
  var inserted = 0;
  for (var i = 0; i < state.scanResults.issues.length; i++) {
    var iss = state.scanResults.issues[i];
    var commentText = _formatIssueComment(iss, lang);
    var ok = await commentOnSubstring(iss.citation, commentText, iss._nth || 0);
    if (ok) inserted += 1;
  }
  if (_lastClickedBtn) {
    _lastClickedBtn.textContent = '\u2713 ' + inserted;
    setTimeout(function () { render(); }, 1200);
  }
}

// Build a deduped, alphabetically-sorted bibliography of validated citations
// from the current audit report and insert it at the cursor as a heading + list.
async function insertBibliographyAtCursor() {
  var lang = state.lang;
  if (!state.scanResults || !state.scanResults.annotated) return;
  var cites = _extractPassedCitations(state.scanResults.annotated);
  if (cites.length === 0) return;
  // Sort: BGE-style refs first (by volume), then docket-style alphabetical.
  cites.sort(function (a, b) { return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }); });
  var heading = t('bibliography_heading', lang);
  var body = '\n' + heading + '\n';
  for (var i = 0; i < cites.length; i++) body += '\u2014 ' + cites[i] + '\n';
  await insertTextAtCursor(body);
  if (_lastClickedBtn) {
    var orig = _lastClickedBtn.innerHTML;
    _lastClickedBtn.innerHTML = ICONS.check + ' ' + cites.length;
    _lastClickedBtn.classList.add('btn-success');
    setTimeout(function () {
      if (_lastClickedBtn) {
        _lastClickedBtn.innerHTML = orig;
        _lastClickedBtn.classList.remove('btn-success');
      }
    }, 1200);
  }
}

function _formatIssueComment(issue, lang) {
  var label;
  if (issue.problem === 'decision_not_in_corpus') label = t('audit_issue_not_found', lang);
  else if (issue.problem === 'pinpoint_not_in_decision') label = t('audit_issue_pinpoint', lang);
  else label = t('audit_issue_unknown', lang);
  var msg = '\u26a0 ' + label;
  if (issue.suggestion) msg += ' \u2014 ' + issue.suggestion;
  if (issue.valid_pinpoints && issue.valid_pinpoints.length) {
    msg += ' (' + t('audit_valid_pinpoints', lang) + ' ' + issue.valid_pinpoints.slice(0, 8).join(', ') + ')';
  }
  return msg;
}

// Audit View — summary + actions. The strip handles the headline; this view
// is the expanded detail page.
function renderScan() {
  var lang = state.lang;
  var html = '<button class="back-link" data-action="back">\u2190 ' + escHtml(t('back', lang)) + '</button>';

  if (state.error && !state.scanResults) {
    html += '<div class="audit-inline-error">' + escHtml(state.error.message || t('error_connection', lang)) + '</div>';
    html += '<button class="btn btn-pro-primary btn-full" data-action="scan-doc">' + escHtml(t('audit_start_button', lang)) + '</button>';
    return html;
  }
  if (!state.scanResults) {
    // Should not normally happen — strip's idle button calls scan-doc directly.
    return html + '<button class="btn btn-pro-primary btn-full" data-action="scan-doc">' + escHtml(t('audit_start_button', lang)) + '</button>';
  }

  // Re-run button (top-right of results)
  html += '<div class="audit-result-header">' +
    '<button class="btn btn-detail audit-rerun" data-action="scan-doc">\u21BB ' + escHtml(t('btn_audit_again', lang)) + '</button>' +
    '</div>';

  // If issues were auto-commented in the document, surface that prominently.
  var r0 = state.scanResults;
  if (r0.autoCommentedCount > 0) {
    html += '<div class="audit-comments-banner">' +
      '<span class="audit-comments-icon">' + ICONS.check + '</span>' +
      '<span>' + escHtml(t('audit_comments_inserted', lang, { n: r0.autoCommentedCount })) + '</span>' +
      '</div>';
  }

  var report = state.scanResults;
  var total = report.total || 0;
  var passed = report.passed || 0;
  var bad = (report.issues && report.issues.length) || 0;

  if (total === 0) {
    html += '<div class="state-message">' + escHtml(t('audit_summary_none', lang)) + '</div>';
    return html;
  }

  // Summary banner
  var summaryClass = bad === 0 ? 'audit-summary-ok' : 'audit-summary-mixed';
  var summaryText = bad === 0
    ? t('audit_summary_ok', lang, { n: total })
    : t('audit_summary_mixed', lang, { n: total, ok: passed, bad: bad });
  html += '<div class="audit-summary ' + summaryClass + '">' +
    '<span class="audit-summary-icon">' + (bad === 0 ? '\u2713' : '\u26a0') + '</span>' +
    '<span class="audit-summary-text">' + escHtml(summaryText) + '</span>' +
    '</div>';

  // Privacy banner — same shared helper as Verify/Strengthen/Support.
  html += _renderPIIBanner(report._pii_summary, lang);

  // Bulk actions: re-comment-all (only useful if user dismissed the auto
  // comments) + insert-bibliography
  var bulkActions = '';
  if (bad > 0 && supportsComments() && !r0.autoCommentedCount) {
    bulkActions += '<button class="btn btn-detail" data-action="audit-comment-all">' +
      escHtml(t('audit_comment_all', lang)) + '</button>';
  }
  if (passed > 0) {
    bulkActions += '<button class="btn btn-detail" data-action="audit-insert-bibliography">' +
      escHtml(t('audit_insert_bibliography', lang)) + '</button>';
  }
  if (bulkActions) {
    html += '<div class="audit-bulk-actions">' + bulkActions + '</div>';
  }

  // Issues list — each card shows what's wrong + suggested fix + jump-to + comment
  for (var i = 0; i < (report.issues || []).length; i++) {
    var iss = report.issues[i];
    html += renderAuditIssueCard(iss, i, lang);
  }

  // Passed citations — collapsible reveal so users see exactly what was validated.
  if (passed > 0) {
    var passedCites = _extractPassedCitations(report.annotated || '');
    html += '<details class="audit-passed">' +
      '<summary class="audit-passed-summary">' +
      '<span class="audit-pp-icon ok">' + ICONS.check + '</span>' +
      '<span>' + escHtml(t('audit_passed_label', lang, { n: passed })) + '</span>' +
      ICONS.chevron +
      '</summary>' +
      '<ul class="audit-passed-list">';
    for (var p = 0; p < passedCites.length; p++) {
      html += '<li>' + escHtml(passedCites[p]) + '</li>';
    }
    html += '</ul></details>';
  }

  // Selection-based tools — reachable from the audit context. Subtle.
  html += '<div class="audit-secondary">' +
    '<div class="audit-secondary-title">' + escHtml(t('audit_secondary_title', lang)) + '</div>' +
    '<button class="btn btn-detail audit-secondary-btn" data-action="verify-ref">' +
    '<span class="audit-secondary-label">' + escHtml(t('btn_verify_pro', lang)) + '</span></button>' +
    '<button class="btn btn-detail audit-secondary-btn" data-action="strengthen">' +
    '<span class="audit-secondary-label">' + escHtml(t('btn_strengthen', lang)) + '</span></button>' +
    '<button class="btn btn-detail audit-secondary-btn" data-action="find-support">' +
    '<span class="audit-secondary-label">' + escHtml(t('tool_support', lang)) + '</span></button>' +
    '<button class="btn btn-detail audit-secondary-btn" data-action="find-related">' +
    '<span class="audit-secondary-label">' + escHtml(t('btn_find_related', lang)) + '</span></button>' +
    '<button class="btn btn-detail audit-secondary-btn" data-action="reflect">' +
    '<span class="audit-secondary-label">' + escHtml(t('btn_reflect', lang)) + '</span></button>' +
    '</div>';

  return html;
}

// ── Reflect view (Pro feature #4) ────────────────────────────────────
// Whole-document literary mirror. Renders the JSON response from
// /api/billing/reflect (legal_issue + literary_reference + summary
// markdown + question_for_reflection + disclaimer) as a single
// readable card the lawyer can skim in 30-60 seconds.
function renderReflect() {
  var lang = state.lang;
  // Back-link at the top — mirrors the pattern used by every other
  // sub-view (renderSettings, renderVerify, renderStrengthen, …).
  // Without this the user has no obvious way back to the main view
  // once the literary mirror is on screen.
  var html = '<a class="back-link" data-action="back">\u2190 ' +
    escHtml(t('back_short', lang)) + '</a>';
  html += '<h3 class="verify-heading">' + escHtml(t('reflect_title', lang)) + '</h3>';

  if (state.reflectRunning) {
    html += '<div class="verify-loading"><div class="spinner"></div>' +
      '<span>' + escHtml(t('reflect_running', lang)) + '</span></div>';
    return html;
  }

  if (state.error && state.view === 'reflect') {
    html += '<div class="verify-error">' + escHtml(state.error.message || t('reflect_error', lang)) + '</div>' +
      '<button class="btn btn-detail" data-action="reflect">' + escHtml(t('reflect_retry', lang)) + '</button>';
    return html;
  }

  var r = state.reflectResult;
  if (!r) {
    html += '<div class="verify-help">' + escHtml(t('reflect_intro', lang)) + '</div>' +
      '<button class="btn btn-pro-primary btn-full" data-action="reflect">' +
      escHtml(t('reflect_start', lang)) + '</button>';
    return html;
  }

  // PII trust banner — same element the other four Pro views show, so
  // the lawyer sees what was redacted before the whole document left Word.
  html += _renderPIIBanner(r._pii_summary, lang);

  // Result card.
  if (r.legal_issue) {
    html += '<div class="reflect-issue">' +
      '<div class="section-label">' + escHtml(t('reflect_issue_label', lang)) + '</div>' +
      '<div class="section-body">' + escHtml(r.legal_issue) + '</div></div>';
  }
  if (r.literary_reference && (r.literary_reference.work || r.literary_reference.author)) {
    var lit = r.literary_reference;
    html += '<div class="reflect-literary">' +
      '<div class="section-label">' + escHtml(t('reflect_lit_label', lang)) + '</div>' +
      '<div class="section-body"><em>' + escHtml(lit.work || '') + '</em>' +
      (lit.author ? ' &middot; ' + escHtml(lit.author) : '') +
      (lit.scene_or_theme ? '<br><span class="text-muted">' + escHtml(lit.scene_or_theme) + '</span>' : '') +
      '</div></div>';
  }
  if (r.summary_markdown) {
    // Light markdown→HTML: headers, paragraphs, emphasis. Anything else
    // gets escaped. Reflect output is short, controlled by our prompt;
    // a full markdown renderer is overkill.
    html += '<div class="reflect-summary">' + _renderLightMarkdown(r.summary_markdown) + '</div>';
  }
  if (r.question_for_reflection) {
    html += '<div class="reflect-question">' +
      '<div class="section-label">' + escHtml(t('reflect_question_label', lang)) + '</div>' +
      '<div class="section-body"><strong>' + escHtml(r.question_for_reflection) + '</strong></div></div>';
  }
  if (r.disclaimer) {
    html += '<div class="reflect-disclaimer">' + escHtml(r.disclaimer.replace(/[*]/g, '')) + '</div>';
  }
  html += '<div style="display:flex;gap:6px;margin-top:12px;">' +
    '<button class="btn btn-detail" data-action="reflect">\u21BB ' + escHtml(t('reflect_again', lang)) + '</button></div>';
  return html;
}

// Minimal markdown renderer used by renderReflect — handles headers
// (## / ###), paragraphs, emphasis (*x* and **x**), and inline code.
// Anything outside this list passes through as plain text (escaped).
function _renderLightMarkdown(md) {
  if (!md) return '';
  var lines = String(md).split(/\r?\n/);
  var out = [];
  var paraBuf = [];
  function flushPara() {
    if (paraBuf.length === 0) return;
    var p = paraBuf.join(' ');
    out.push('<p>' + _renderInlineMd(p) + '</p>');
    paraBuf = [];
  }
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    if (/^\s*$/.test(line)) { flushPara(); continue; }
    var h = /^(#{2,3})\s+(.+)$/.exec(line);
    if (h) {
      flushPara();
      var level = h[1].length;
      out.push('<h' + level + '>' + _renderInlineMd(h[2]) + '</h' + level + '>');
      continue;
    }
    paraBuf.push(line.trim());
  }
  flushPara();
  return out.join('');
}

function _renderInlineMd(s) {
  // Escape first, then re-introduce only the inline marks we allow.
  var esc = escHtml(s);
  // **bold** before *italic* to avoid greedy collision.
  esc = esc.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  esc = esc.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  esc = esc.replace(/`([^`]+)`/g, '<code>$1</code>');
  return esc;
}

// Reflect — Pro feature. Runs against the WHOLE document, redacts
// client-side, then calls /api/billing/reflect. The redact path is
// the same as Strengthen + Verify; un-redaction happens inside
// api.js::reflectOnDocumentPro before the result reaches here.
async function reflectOnDocument() {
  var lang = state.lang;
  var proKey = localStorage.getItem('ocl_pro_key');
  if (!proKey) { state.view = 'settings'; render(); return; }

  state.view = 'reflect';
  state.error = null;
  state.reflectResult = null;
  state.reflectRunning = true;
  render();

  var docText;
  try {
    docText = await getDocumentText();
  } catch (e) {
    docText = '';
  }
  // Differentiate the three failure modes so the user knows what to
  // fix: not running inside Word vs. empty document vs. too short
  // for a meaningful reflection.
  if (!docText) {
    state.reflectRunning = false;
    if (!_insideWord) {
      state.error = { type: 'not_in_word', message: t('audit_no_word', lang) };
    } else {
      state.error = { type: 'doc_empty', message: t('audit_doc_empty', lang) };
    }
    render();
    return;
  }
  if (docText.trim().length < 80) {
    state.reflectRunning = false;
    state.error = { type: 'too_short', message: t('reflect_too_short', lang) };
    render();
    return;
  }
  // Server cap is 60 000 chars on the redacted payload (mcp_server.py
  // ReflectRequest). Redaction usually shrinks text, but check raw
  // length first so we never burn an LLM call we know will 422.
  if (docText.length > 60000) {
    state.reflectRunning = false;
    state.error = { type: 'too_long', message: t('reflect_too_long', lang) };
    render();
    return;
  }

  try {
    var data = await reflectOnDocumentPro(proKey, docText, lang);
    state.reflectRunning = false;
    if (data && data.error && !data.summary_markdown) {
      state.error = { type: data.error, message: data.message || t('reflect_error', lang) };
    } else {
      state.reflectResult = data;
    }
    render();
  } catch (e) {
    state.reflectRunning = false;
    var msg = (e && e.message) ? e.message : t('reflect_error', lang);
    // Server-side cap (60k after redaction) — surface the same
    // explanatory message as the pre-check above so the user knows
    // what to fix instead of seeing the generic "Reflection failed".
    var errType = 'reflect_failed';
    if (e && (e.code === 'too_long' || (e.message && /at most 60000/.test(e.message)))) {
      msg = t('reflect_too_long', lang);
      errType = 'too_long';
    }
    state.error = { type: errType, message: msg };
    render();
  }
}

// ── Verify-and-Strengthen view (Pro feature #2) ──────────────────────
// Renders the structured response from /api/billing/strengthen as a
// scrollable "junior associate review" card: verified citations,
// suggested leading cases (with one-click insert), commentary excerpts,
// and an argument-strength signal. Counter-authorities + summary fields
// are present in the API response but empty in v1; renderer skips them.
function renderStrengthen() {
  var lang = state.lang;
  var html = '<button class="back-link" data-action="back">\u2190 ' + escHtml(t('back', lang)) + '</button>';
  html += '<h2 class="detail-title">' + escHtml(t('strengthen_title', lang)) + '</h2>';
  html += '<div class="detail-meta" style="margin-bottom:12px;">' + escHtml(t('strengthen_subtitle', lang)) + '</div>';

  if (state.loading) {
    html += renderSkeletonLines(8);
    return html;
  }
  if (state.error) {
    var em = (state.error && state.error.message) || t('error_connection', lang);
    html += '<div class="audit-inline-error">' + escHtml(em) + '</div>';
    html += '<button class="btn btn-pro-primary btn-full" data-action="strengthen">' + escHtml(t('strengthen_retry', lang)) + '</button>';
    return html;
  }
  var r = state.strengthenResult;
  if (!r) {
    html += '<button class="btn btn-pro-primary btn-full" data-action="strengthen">' + escHtml(t('strengthen_start', lang)) + '</button>';
    return html;
  }
  if (r.error) {
    html += '<div class="audit-inline-error">' + escHtml(r.message || r.error) + '</div>';
    return html;
  }

  html += _renderPIIBanner(r._pii_summary, lang);

  /* ── Argument-strength banner ── */
  var strengthClass = 'audit-summary-' + (r.argument_strength === 'strong' ? 'ok'
                       : r.argument_strength === 'medium' ? 'mixed' : 'mixed');
  // Explicit key map (rather than concatenated keys) keeps the i18n
  // static lint useful — see tests/web/test_word_addin_i18n_parity.py.
  var strengthKey = r.argument_strength === 'strong' ? 'strength_strong'
                  : r.argument_strength === 'medium' ? 'strength_medium'
                  : 'strength_weak';
  var strengthLabel = t(strengthKey, lang) || r.argument_strength || '';
  html += '<div class="audit-summary ' + strengthClass + '">' +
    '<span class="audit-summary-icon">\u2696</span>' +
    '<span class="audit-summary-text"><b>' + escHtml(strengthLabel) + '</b> &mdash; ' +
    escHtml(r.argument_strength_explanation || '') + '</span></div>';

  /* ── Verified citations ── */
  if (Array.isArray(r.verified_citations) && r.verified_citations.length) {
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('strengthen_verified', lang)) + '</div>';
    for (var i = 0; i < r.verified_citations.length; i++) {
      var v = r.verified_citations[i];
      var ok = v.verified ? '\u2713' : '\u2717';
      var cls = v.verified ? 'badge-leading' : 'badge-area';
      html += '<div class="erwaegung-block" style="border-left:3px solid var(--' +
              (v.verified ? 'green' : 'accent') + ');padding-left:8px;">' +
        '<div><span class="badge ' + cls + '">' + ok + '</span> <b>' +
        escHtml(v.citation) + '</b>' +
        (v.pinpoint ? ' <span style="color:var(--text-secondary);">(' + escHtml(v.pinpoint) + ')</span>' : '') +
        '</div>' +
        '<div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">' +
          escHtml(v.court || '') + (v.date ? ' &middot; ' + escHtml(v.date) : '') +
          (v.citation_count ? ' &middot; ' + v.citation_count + '\u00d7 ' + escHtml(t('citations_label', lang)) : '') +
        '</div>' +
      '</div>';
    }
    html += '</div>';
  }

  /* ── Suggested citations (the headline value) ── */
  if (Array.isArray(r.suggested_citations) && r.suggested_citations.length) {
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('strengthen_suggested', lang)) + '</div>';
    for (var s = 0; s < r.suggested_citations.length; s++) {
      var sc = r.suggested_citations[s];
      html += '<div class="erwaegung-block" style="border-left:3px solid var(--blue);padding-left:8px;">' +
        '<div><b>' + escHtml(sc.citation || '') + '</b>' +
        (sc.citation_count ? ' <span class="badge badge-citations">' + sc.citation_count + '</span>' : '') + '</div>' +
        '<div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">' +
          escHtml(sc.court || '') + (sc.date ? ' &middot; ' + escHtml(sc.date) : '') +
          (sc.related_statute ? ' &middot; ' + escHtml(sc.related_statute) : '') +
        '</div>' +
        (sc.regeste_excerpt ? '<div style="font-size:11px;margin-top:4px;color:var(--text);">' + escHtml(sc.regeste_excerpt) + '</div>' : '') +
        '<div style="margin-top:6px;display:flex;gap:6px;">' +
          '<button class="btn btn-detail" data-action="strengthen-insert-citation" data-idx="' + s + '">' +
            ICONS.insert + escHtml(t('strengthen_insert_btn', lang)) + '</button>' +
          (sc.url ? '<a class="btn btn-detail" href="' + escHtml(sc.url) + '" target="_blank">' + ICONS.link + ' ' + escHtml(t('source_link', lang)) + '</a>' : '') +
        '</div>' +
      '</div>';
    }
    html += '</div>';
  }

  /* ── Commentary excerpts ── */
  if (Array.isArray(r.commentary_excerpts) && r.commentary_excerpts.length) {
    html += '<div class="detail-section"><div class="detail-label">' + escHtml(t('strengthen_commentary', lang)) + '</div>';
    for (var k = 0; k < r.commentary_excerpts.length; k++) {
      var cx = r.commentary_excerpts[k];
      html += '<div class="erwaegung-block">' +
        '<div><b>' + escHtml(cx.title || cx.law_abbreviation + ' Art. ' + cx.article_number) + '</b></div>' +
        (cx.authors ? '<div style="font-size:11px;color:var(--text-secondary);">' + escHtml(cx.authors) + '</div>' : '') +
        (cx.snippet ? '<div style="font-size:11px;margin-top:4px;color:var(--text);">' + escHtml(cx.snippet) + '</div>' : '') +
        '<div style="font-size:10px;color:var(--text-muted);margin-top:4px;">' + escHtml(cx.source || '') + '</div>' +
      '</div>';
    }
    html += '</div>';
  }

  /* ── Empty state ── */
  if ((!r.verified_citations || !r.verified_citations.length) &&
      (!r.suggested_citations || !r.suggested_citations.length)) {
    html += '<div class="state-message">' + escHtml(t('strengthen_no_results', lang)) + '</div>';
  }

  return html;
}

// Walk the server-annotated text, pulling out citations marked with ✓.
// The /api/attest contract appends ✓ to each validated citation in
// document order; we capture the preceding token group as the citation.
function _extractPassedCitations(annotated) {
  if (!annotated) return [];
  var re = /([A-Z][A-Za-zÀ-ÿ.\-_/\d\s]{2,80}?)\s\u2713/g;
  var seen = {};
  var out = [];
  var match;
  while ((match = re.exec(annotated)) !== null) {
    var c = match[1].trim().replace(/\s+/g, ' ');
    if (!c || seen[c]) continue;
    seen[c] = true;
    out.push(c);
  }
  return out;
}

function renderAuditIssueCard(iss, idx, lang) {
  var problemLabel;
  var problemClass;
  if (iss.problem === 'decision_not_in_corpus') {
    problemLabel = t('audit_issue_not_found', lang);
    problemClass = 'audit-issue-missing';
  } else if (iss.problem === 'pinpoint_not_in_decision') {
    problemLabel = t('audit_issue_pinpoint', lang);
    problemClass = 'audit-issue-pinpoint';
  } else {
    problemLabel = t('audit_issue_unknown', lang);
    problemClass = 'audit-issue-other';
  }

  var html = '<div class="audit-issue ' + problemClass + '">' +
    '<div class="audit-issue-header">' +
    '<span class="audit-issue-icon">\u26a0</span>' +
    '<span class="audit-issue-citation">' + escHtml(iss.citation || '') + '</span>' +
    '</div>' +
    '<div class="audit-issue-label">' + escHtml(problemLabel) + '</div>';

  if (iss.suggestion) {
    html += '<div class="audit-issue-suggestion">' + escHtml(iss.suggestion) + '</div>';
  }

  if (iss.valid_pinpoints && iss.valid_pinpoints.length) {
    html += '<div class="audit-issue-pinpoints">' +
      '<span class="audit-pinpoints-label">' + escHtml(t('audit_valid_pinpoints', lang)) + '</span> ';
    for (var k = 0; k < Math.min(iss.valid_pinpoints.length, 8); k++) {
      var pp = iss.valid_pinpoints[k];
      html += '<button class="pill audit-pinpoint-pill" data-action="audit-fix-pinpoint" ' +
        'data-idx="' + idx + '" data-pp="' + escHtml(pp) + '">' + escHtml(pinpointLabel(lang) + ' ' + pp) + '</button>';
    }
    html += '</div>';
  }

  html += '<div class="audit-issue-actions">';
  html += '<button class="btn btn-detail" data-action="audit-jump" data-idx="' + idx + '">' +
    escHtml(t('audit_jump_to', lang)) + '</button>';
  if (supportsComments()) {
    html += '<button class="btn btn-detail" data-action="audit-comment" data-idx="' + idx + '">' +
      escHtml(t('audit_insert_comment', lang)) + '</button>';
  }
  html += '</div></div>';
  return html;
}

// Utilities — HTML entity escaping for XSS prevention
function escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// escHtml stops attribute breakout but NOT dangerous URL schemes: an href of
// "javascript:..." or "data:..." (from a decision source_url / amendment-ref
// URL) is a valid attribute value that executes on click. safeHref returns the
// URL only when it is http(s), else '#', so dynamic hrefs can never carry an
// executable scheme (Codex review P1.6).
function safeHref(url) {
  var u = String(url || '').trim();
  return /^https?:\/\//i.test(u) ? u : '#';
}

function openExternal(url) {
  // Office add-ins: use a temporary <a> click (bypasses popup blocker)
  var a = document.createElement('a');
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function stripHtml(s) {
  if (!s) return '';
  return s.replace(/<[^>]*>/g, '');
}
