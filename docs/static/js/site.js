/* ============================================================================
   OpenCaseLaw — shared chrome + i18n (v3)
   Renders the nav + footer into [data-nav] / [data-foot] placeholders from one
   source, holds the shared nav/footer strings (de/fr/it/rm/en), and exposes
   OCL.init({active, i18n, onReady}) for per-page strings and data wiring.
   ========================================================================= */
(function () {
  var SHARED = {
    de:{skip:'Zum Inhalt springen',nav_search:'Suche',nav_courts:'Gerichte',nav_laws:'Gesetze',nav_connect:'Verbinden →',nav_quality:'Qualität',
      foot_browse:'Durchsuchen',foot_build:'Entwickeln',foot_trust:'Vertrauen',foot_coverage:'Abdeckung',foot_scholarship:'Literatur',foot_about:'Über uns',
      foot_tag:'Das vollständige offene Verzeichnis der Schweizer Rechtsprechung. CC0-Datensatz, MIT-Code.',foot_legal:'Korpus CC0 · Code MIT'},
    fr:{skip:'Aller au contenu',nav_search:'Recherche',nav_courts:'Tribunaux',nav_laws:'Lois',nav_connect:'Connexion →',nav_quality:'Qualité',
      foot_browse:'Parcourir',foot_build:'Développer',foot_trust:'Confiance',foot_coverage:'Couverture',foot_scholarship:'Doctrine',foot_about:'À propos',
      foot_tag:'Le registre ouvert complet de la jurisprudence suisse. Jeu de données CC0, code MIT.',foot_legal:'Corpus CC0 · Code MIT'},
    it:{skip:'Vai al contenuto',nav_search:'Ricerca',nav_courts:'Tribunali',nav_laws:'Leggi',nav_connect:'Collega →',nav_quality:'Qualità',
      foot_browse:'Sfoglia',foot_build:'Sviluppa',foot_trust:'Fiducia',foot_coverage:'Copertura',foot_scholarship:'Dottrina',foot_about:'Chi siamo',
      foot_tag:'Il registro aperto completo della giurisprudenza svizzera. Dataset CC0, codice MIT.',foot_legal:'Corpus CC0 · Codice MIT'},
    rm:{skip:'Ir al cuntegn',nav_search:'Tschertga',nav_courts:'Dretgiras',nav_laws:'Leschas',nav_connect:'Connectar →',nav_quality:'Qualitad',
      foot_browse:'Tschertgar',foot_build:'Sviluppar',foot_trust:'Confidenza',foot_coverage:'Cuvertura',foot_scholarship:'Litteratura',foot_about:'Davart nus',
      foot_tag:'Il register avert cumplet da la giurisprudenza svizra. Set da datas CC0, code MIT.',foot_legal:'Corpus CC0 · Code MIT'},
    en:{skip:'Skip to content',nav_search:'Search',nav_courts:'Courts',nav_laws:'Laws',nav_connect:'Connect →',nav_quality:'Quality',
      foot_browse:'Browse',foot_build:'Build',foot_trust:'Trust',foot_coverage:'Coverage',foot_scholarship:'Scholarship',foot_about:'About',
      foot_tag:'The complete open record of Swiss case law. CC0 dataset, MIT code.',foot_legal:'Corpus CC0 · Code MIT'}
  };
  var LANGS = ['de','fr','it','rm','en'];
  var qp = new URLSearchParams(location.search).get('lang');
  var stored = null; try { stored = localStorage.getItem('lang'); } catch (e) {}
  var lang = (qp && SHARED[qp]) ? qp : ((stored && SHARED[stored]) ? stored : 'de');
  if (qp && SHARED[qp]) { try { localStorage.setItem('lang', qp); } catch (e) {} }

  var DICT = {};
  function setDict(extra) { LANGS.forEach(function (l) { DICT[l] = Object.assign({}, SHARED[l] || {}, (extra && extra[l]) || {}); }); }
  setDict();
  function t(k) { return (DICT[lang] || DICT.de)[k] || DICT.de[k] || k; }
  function fmt(n) { return n == null ? '—' : n.toLocaleString('de-CH'); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' })[c]; }); }

  function cur(active, key) { return active === key ? ' aria-current="page"' : ''; }
  function navHTML(active) {
    return '<header class="nav"><div class="wrap">' +
      '<a class="brand" href="/"><span class="glyph">+</span> opencaselaw.ch</a>' +
      '<button class="nav-burger" aria-label="Menu" aria-expanded="false" onclick="document.body.classList.toggle(\'menu\')">☰</button>' +
      '<nav class="nav-links" aria-label="Primary">' +
        '<a href="/search/" data-t="nav_search"' + cur(active, 'search') + '>Search</a>' +
        '<a href="/courts/" data-t="nav_courts"' + cur(active, 'courts') + '>Courts</a>' +
        '<a href="/laws/" data-t="nav_laws"' + cur(active, 'laws') + '>Laws</a>' +
        '<a href="/#connect" class="nav-cta" data-t="nav_connect">Connect →</a>' +
        '<span class="langs" id="langs"></span>' +
      '</nav></div></header>';
  }
  function footHTML() {
    return '<footer class="foot"><div class="wrap"><div class="foot-grid">' +
      '<div><a class="brand" href="/"><span class="glyph">+</span> opencaselaw.ch</a>' +
        '<p class="foot-tag" style="margin-top:14px" data-t="foot_tag"></p>' +
        '<ul style="margin-top:14px"><li><a href="https://www.linkedin.com/company/opencaselaw-ch/" rel="noopener">LinkedIn</a></li></ul></div>' +
      '<div><h4 data-t="foot_browse">Browse</h4><ul>' +
        '<li><a href="/search/" data-t="nav_search">Search</a></li>' +
        '<li><a href="/courts/" data-t="nav_courts">Courts</a></li>' +
        '<li><a href="/laws/" data-t="nav_laws">Laws</a></li>' +
        '<li><a href="/scholarship/" data-t="foot_scholarship">Scholarship</a></li>' +
        '<li><a href="/coverage/" data-t="foot_coverage">Coverage</a></li></ul></div>' +
      '<div><h4 data-t="foot_build">Build</h4><ul>' +
        '<li><a href="/mcp/">MCP server</a></li>' +
        '<li><a href="/api/">REST API</a></li>' +
        '<li><a href="/cli/">Research CLI</a></li>' +
        '<li><a href="https://word.opencaselaw.ch/">Word add-in</a></li>' +
        '<li><a href="https://huggingface.co/datasets/voilaj/swiss-caselaw" rel="noopener">HuggingFace</a></li></ul></div>' +
      '<div><h4 data-t="foot_trust">Trust</h4><ul>' +
        '<li><a href="/integrity/">Integrity</a></li>' +
        '<li><a href="/standards/">Standards</a></li>' +
        '<li><a href="/quality.html" data-t="nav_quality">Quality</a></li>' +
        '<li><a href="/methodology.html">Methodology</a></li>' +
        '<li><a href="/ueber/" data-t="foot_about">About</a></li></ul></div>' +
      '</div><p class="foot-legal">© 2026 Jonas Hertner / OpenCaseLaw · <span data-t="foot_legal"></span></p></div></footer>';
  }

  function applyI18n(root) {
    (root || document).querySelectorAll('[data-t]').forEach(function (el) {
      var v = t(el.dataset.t); if (/<[a-z]/i.test(v)) el.innerHTML = v; else el.textContent = v;
    });
    document.documentElement.lang = lang;
  }
  function swap(sel, html) {
    var ph = document.querySelector(sel); if (!ph) return;
    var box = document.createElement('div'); box.innerHTML = html;
    ph.replaceWith(box.firstElementChild);
  }
  function renderChrome(active) {
    swap('[data-nav]', navHTML(active));
    swap('[data-foot]', footHTML());
    var lw = document.getElementById('langs');
    if (lw) lw.innerHTML = LANGS.map(function (l) {
      var u = new URLSearchParams(location.search); u.set('lang', l);
      return '<a href="?' + u.toString() + '" class="' + (l === lang ? 'on' : '') + '" hreflang="' + l + '">' + l + '</a>';
    }).join('');
  }
  function reveal() {
    if (!('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px' });
    document.querySelectorAll('.rv').forEach(function (el) { io.observe(el); });
  }

  window.OCL = {
    lang: lang, t: t, fmt: fmt, esc: esc, applyI18n: applyI18n,
    init: function (opts) {
      opts = opts || {};
      if (opts.i18n) setDict(opts.i18n);
      renderChrome(opts.active);
      applyI18n();
      reveal();
      if (opts.onReady) opts.onReady(window.OCL);
    }
  };
})();
