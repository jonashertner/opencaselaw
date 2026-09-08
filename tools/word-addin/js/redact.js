/**
 * Client-side PII redaction for the Pro check-cites flow.
 *
 * The Pro endpoints — POST /attest, /billing/verify, /billing/strengthen,
 * /billing/find-support and /billing/reflect — receive selected text,
 * a paragraph or the full document. Law-firm documents routinely
 * contain client names, AHV/AVS numbers, IBANs, addresses, etc. that
 * must never leave the lawyer's machine in the clear.
 *
 * `redactPII(text)` runs Swiss-aware regex matching, replaces every
 * detected PII span with a typed placeholder ([NAME_1], [AHV_1], …)
 * and returns the redacted text + a per-call replacement map. The
 * same original string always maps to the same placeholder within
 * one call, so a party that appears five times is one entity to the
 * model, not five. The server only ever sees the placeholders; legal
 * citations (BGE, BGer, statute refs, dockets) are NOT touched, so
 * citation-checking is unaffected. `unredact(text, replacements)`
 * reverses the mapping — used when displaying server-returned text
 * back to the user with the original wording restored.
 *
 * Scope is deliberately pattern-based and bounded. Nine categories,
 * documented (with their limits) at
 * https://word.opencaselaw.ch/privacy.html — keep that page in sync
 * with PATTERNS below. Personal names are caught only after a title
 * (Herr, Frau, Dr., M., Mme, Me, Sig., Avv., …); dates of birth only
 * after a birth formula. A bare "Max Müller" is not detected.
 *
 * Privacy invariant: this module is the ONLY place that defines what
 * counts as PII. If you add a new field to a Pro-bound payload,
 * pipe it through redactPII first.
 *
 * Portability: no lookbehind, no named groups, no /u flag. Older
 * Office webviews reject those at parse time, which would take the
 * whole module down — and api.js then refuses every Pro call.
 *
 * Tests: tests/redact.test.js, tests/redact_extended.test.js,
 * tests/redact_polish.test.js (run each with `node tests/<file>`).
 */

'use strict';

/* Letter classes shared by the name and address patterns. Upper-case
   letters are allowed inside a token so that "M. Jean DUPONT" — the
   usual form in French and Italian pleadings — is caught whole. */
var L_UPPER = 'A-ZÄÖÜÉÀÈÇÔÎÛÊÑ';
var L_ANY = 'A-Za-zäöüéàèçôîûêñÄÖÜÉÀÈÇÔÎÛÊÑ';
var NAME_TOKEN = '[' + L_UPPER + '][' + L_ANY + '\\-\'\u2019]+';

/* Tokens that can directly follow a name and must never be swallowed
   into it: the citation vocabulary the Pro tools exist to verify.
   "Herr Müller Art. 41 OR" must redact "Müller" and leave the article
   reference intact. Trailing tokens in this list are trimmed off a
   NAME match; if nothing is left, the match is dropped. */
var NAME_STOP = {};
['Art', 'Abs', 'Ziff', 'Ziffer', 'Rz', 'Bst', 'Lit', 'Anm', 'Vgl', 'BGE', 'BGer',
 'BVGer', 'BStGer', 'BPatGer', 'ATF', 'TF', 'TAF', 'TPF', 'TFB', 'DTF', 'Urteil',
 'Entscheid', 'Arrêt', 'Sentenza', 'Verfügung', 'Erw', 'SR', 'RS', 'OR', 'ZGB',
 'StGB', 'BV', 'ZPO', 'StPO', 'SchKG', 'BGG', 'VwVG', 'CO', 'CC', 'CP', 'CPC',
 'CPP', 'LP', 'LTF', 'Cst', 'Und', 'Oder', 'Sowie', 'Et', 'Ou', 'Ed', 'Con'
].forEach(function (t) { NAME_STOP[t] = true; });

/* Titles that anchor a personal name. Longer forms precede their
   prefixes (Herrn before Herr, Signora before Signor) so the
   alternation takes the whole word. Lower-case avv./dott. follow the
   Italian convention of writing the title in lower case mid-sentence. */
var NAME_TITLES = '(?:Herrn|Herr|Frau|Hr\\.|Fr\\.|Mademoiselle|Madame|Monsieur|' +
  'Maître|Me\\.?|Mme|Mlle|M\\.|Signora|Signor|Sig\\.(?:ra)?|Avvocato|Avv\\.|avv\\.|' +
  'Dott\\.(?:ssa)?|dott\\.(?:ssa)?|Dr\\.|Prof\\.)';

/* Trim trailing citation vocabulary off a title-anchored name match.
   The first whitespace-separated token is always the title; academic
   prefixes (Dr., med., …) and the name follow. Returns the shortened
   match, or null when no name token survives. */
function trimNameMatch(match) {
  var tokens = match.split(/\s+/);
  var keep = tokens.length;
  while (keep > 1 && NAME_STOP[tokens[keep - 1].replace(/[.\-'\u2019]+$/, '')]) keep--;
  if (keep <= 1) return null;
  /* Rebuild from the original string so inner whitespace is preserved. */
  var out = match;
  for (var i = tokens.length; i > keep; i--) {
    var idx = out.lastIndexOf(tokens[i - 1]);
    out = out.slice(0, idx).replace(/\s+$/, '');
  }
  /* A title alone, or a title plus academic prefix only, is not a name. */
  var rest = out.split(/\s+/).slice(1).filter(function (t) {
    return !/^(?:Dr\.|Prof\.|med\.|iur\.)$/.test(t);
  });
  return rest.length ? out : null;
}

/* Patterns ordered by specificity. Earlier patterns "win" overlapping
   regions because we de-overlap left-to-right after sorting. Keep
   structured-ID patterns (AHV, IBAN, CHE) before free-text ones
   (NAME, ADDRESS) so an AHV inside a sentence doesn't get partially
   eaten by the address pattern. */
var PATTERNS = [
  {
    /* TLD constrained to LOWERCASE letters: the case-sensitivity of
       [a-z]{2,} makes "info@x.chHerr" stop at ".ch" because the next
       character (uppercase 'H') simply doesn't match the class. \b
       handles plain end-of-word/string for normal cases. */
    type: 'EMAIL',
    regex: /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[a-z]{2,}\b/g,
  },
  {
    /* Swiss social-security number: 756.XXXX.XXXX.XX (or with spaces). */
    type: 'AHV',
    regex: /\b756[.\s]\d{4}[.\s]\d{4}[.\s]\d{2}\b/g,
  },
  {
    /* Swiss IBAN: CH + 2 check digits + 17 alphanumerics, formatted in
       4-digit groups. Always 21 chars total without spaces. */
    type: 'IBAN',
    regex: /\bCH\d{2}(?:\s?[A-Z0-9]{4}){4}\s?[A-Z0-9]\b/g,
  },
  {
    /* Swiss company UID: CHE-XXX.XXX.XXX (also accept hyphenated). */
    type: 'CHE',
    regex: /\bCHE[\-\s]?\d{3}[.\-\s]?\d{3}[.\-\s]?\d{3}\b/g,
  },
  {
    /* Swiss phone: +41 79 123 45 67  /  +41-79-123-45-67  /  079 123 45 67.
       Boundary keeps it from eating into adjacent digits. */
    type: 'PHONE',
    regex: /(?:\+41[\s\-]?\(?0?\)?[\s\-]?\d{2}|\b0\d{2})[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b/g,
  },
  {
    /* DOB only when anchored to a birth formula or asterisk — avoids
       redacting random dates that might be filing dates etc. Anchors:
       "geboren am", "geb. am", "geb." + date, "Geburtsdatum:",
       "né(e) le", "date de naissance:", "nato/nata il",
       "data di nascita:", "*". Date as d.m.yyyy or d/m/yyyy. */
    type: 'DOB',
    regex: /(?:\bgeb(?:oren)?\.?\s+am\s+|\bgeb\.\s*|\bGeburtsdatum\s*:?\s*|\bnée?\s+le\s+|\bdate\s+de\s+naissance\s*:?\s*|\bnat[oa]\s+il\s+|\bdata\s+di\s+nascita\s*:?\s*|\*\s*)\d{1,2}[./]\d{1,2}[./](?:19|20)\d{2}/gi,
  },
  {
    /* Street + number, all four official languages.
       Matches "Bahnhofstrasse 12", "Rue du Rhône 65", "rue de la Gare 12",
       "Via Pretorio 7", "Place de la Gare 4". Swiss-French addresses
       are commonly written with a lower-case street word, so rue /
       avenue / chemin / boulevard are accepted in either case — but
       then the first word after the particle must be capitalised, so
       "en place depuis 3" or "via e-mail" never match. The charset
       covers every Swiss-French and Swiss-Italian accented letter. */
    type: 'ADDRESS',
    regex: new RegExp(
      '\\b(?:[' + L_UPPER + '][' + L_ANY + '\\-]{2,}(?:strasse|gasse|weg|platz|allee|str\\.)' +
      '|(?:[Rr]ue|[Aa]venue|[Bb]oulevard|[Cc]hemin|Place|Route|Via|Piazza|Viale|Vicolo)' +
      '(?:\\s+(?:de\\s+la|de\\s+l[\'\u2019]|de|du|des|del|della|delle|dei|d[\'\u2019]))?' +
      '\\s*' + NAME_TOKEN + '(?:\\s+[' + L_ANY + '\\-]+){0,3})' +
      '\\s+\\d+[a-z]?\\b', 'g'),
  },
  {
    /* PLZ + city: CH-8001 Zürich  /  8001 Zürich  /  1003 Lausanne. */
    type: 'POSTAL',
    regex: /\b(?:CH[\-\s])?\d{4}\s+[A-ZÄÖÜ][A-Za-zäöüéàèç\-]+(?:[\s\-][A-ZÄÖÜ][A-Za-zäöüéàèç\-]+){0,2}\b/g,
  },
  {
    /* Title-anchored personal names. Avoids false positives by
       requiring a leading honorific or professional title.

       Title list covers all four official languages:
         DE: Herr, Herrn, Frau, Hr., Fr., Dr., Prof.
         FR: M., Monsieur, Mme, Madame, Mlle, Mademoiselle, Me, Maître
         IT: Sig., Signor, Sig.ra, Signora, Avv./avv., Avvocato, Dott./dott.
       Counter `{0,3}` lets a single surname after the title match —
       common form in pleadings ("Hr. Müller"). Trailing citation
       tokens are trimmed by trimNameMatch(). */
    type: 'NAME',
    regex: new RegExp(
      NAME_TITLES + '\\s+(?:Dr\\.\\s+|Prof\\.\\s+|med\\.\\s+|iur\\.\\s+)?' +
      NAME_TOKEN + '(?:\\s+' + NAME_TOKEN + '){0,3}\\b', 'g'),
    trim: trimNameMatch,
  },
];

var PII_TYPES = (function () {
  var t = {};
  for (var i = 0; i < PATTERNS.length; i++) t[PATTERNS[i].type] = PATTERNS[i].type;
  return t;
})();

/* Collect every match for one pattern using replace-callback. The
   callback returns the original substring (string is left unchanged)
   while we record (start, end, original) into `out`. We use
   replace-callback rather than the regex iterator method to keep the
   code straightforward across older WebView versions. */
function collectMatches(text, spec, sink) {
  var rx = new RegExp(spec.regex.source, spec.regex.flags);
  text.replace(rx, function (match, /* …captures, */ offset) {
    /* `offset` is always the second-to-last argument when there are
       no capture groups, but defensive — in our patterns the only
       capture-bearing one is DOB, where offset is still computable
       via arguments[arguments.length - 2]. */
    var off = (typeof offset === 'number') ? offset : arguments[arguments.length - 2];
    var kept = spec.trim ? spec.trim(match) : match;
    if (kept === null) return match;
    sink.push({
      type: spec.type,
      start: off,
      end: off + kept.length,
      original: kept,
      priority: spec.__priority,
    });
    return match;
  });
}

/* Shape: { redacted, replacements, summary }. Empty input returns the
   empty result. `replacements` carries one entry per occurrence;
   occurrences of the same original share a placeholder. */
function redactPII(text /*, options */) {
  if (text == null || text === '') {
    return { redacted: '', replacements: [], summary: { byType: {}, total: 0 } };
  }

  var allMatches = [];
  for (var p = 0; p < PATTERNS.length; p++) {
    PATTERNS[p].__priority = p;
    collectMatches(text, PATTERNS[p], allMatches);
  }

  /* Resolve overlaps: sort by start ascending, then by priority (lower
     = more specific = wins). Keep a running "lastEnd" cursor and drop
     any match that begins before it. */
  allMatches.sort(function (a, b) {
    if (a.start !== b.start) return a.start - b.start;
    if (a.priority !== b.priority) return a.priority - b.priority;
    return b.end - a.end; /* prefer the longer span at same start */
  });

  var keep = [];
  var lastEnd = -1;
  for (var i = 0; i < allMatches.length; i++) {
    if (allMatches[i].start >= lastEnd) {
      keep.push(allMatches[i]);
      lastEnd = allMatches[i].end;
    }
  }

  var replacements = [];
  var counters = {};
  var byOriginal = {};
  var out = '';
  var pos = 0;
  for (var k = 0; k < keep.length; k++) {
    var hit = keep[k];
    var key = hit.type + '\u0000' + hit.original;
    var placeholder = byOriginal[key];
    if (!placeholder) {
      counters[hit.type] = (counters[hit.type] || 0) + 1;
      placeholder = '[' + hit.type + '_' + counters[hit.type] + ']';
      byOriginal[key] = placeholder;
    }
    out += text.slice(pos, hit.start) + placeholder;
    replacements.push({
      type: hit.type,
      original: hit.original,
      placeholder: placeholder,
      start: hit.start,
      end: hit.end,
    });
    pos = hit.end;
  }
  out += text.slice(pos);

  /* byType counts occurrences (what the banner reports), so the
     lawyer sees "5× Name" when a party appears five times, even
     though those five share one placeholder. */
  var byType = {};
  for (var r = 0; r < replacements.length; r++) {
    byType[replacements[r].type] = (byType[replacements[r].type] || 0) + 1;
  }

  return {
    redacted: out,
    replacements: replacements,
    summary: { byType: byType, total: replacements.length },
  };
}

/* Reverse the mapping for displaying server output to the user. */
function unredact(text, replacements) {
  if (!text || !replacements || replacements.length === 0) return text || '';
  /* Sort by placeholder length DESC so [NAME_10] is replaced before
     [NAME_1] (otherwise "[NAME_1]0" would result). */
  var sorted = replacements.slice().sort(function (a, b) {
    return b.placeholder.length - a.placeholder.length;
  });
  var out = text;
  for (var i = 0; i < sorted.length; i++) {
    var r = sorted[i];
    out = out.split(r.placeholder).join(r.original);
  }
  return out;
}

/* Human-readable summary for the UI: "3 names, 1 AHV, 2 emails redacted". */
function formatSummary(summary, lang) {
  if (!summary || !summary.total) return '';
  lang = lang || 'de';
  var labels = {
    de: { EMAIL: 'E-Mail', AHV: 'AHV-Nr.', IBAN: 'IBAN', CHE: 'UID', PHONE: 'Telefon', DOB: 'Geburtsdatum', ADDRESS: 'Adresse', POSTAL: 'Ort/PLZ', NAME: 'Name' },
    fr: { EMAIL: 'e-mail', AHV: 'no AVS',  IBAN: 'IBAN', CHE: 'IDE', PHONE: 'téléphone', DOB: 'date de naissance', ADDRESS: 'adresse', POSTAL: 'NPA/lieu', NAME: 'nom' },
    it: { EMAIL: 'e-mail', AHV: 'no AVS',  IBAN: 'IBAN', CHE: 'IDI', PHONE: 'telefono',  DOB: 'data di nascita',   ADDRESS: 'indirizzo', POSTAL: 'NAP/località', NAME: 'nome' },
    en: { EMAIL: 'email',  AHV: 'AVS no.', IBAN: 'IBAN', CHE: 'UID', PHONE: 'phone',     DOB: 'date of birth',     ADDRESS: 'address', POSTAL: 'PC/city', NAME: 'name' },
  };
  var dict = labels[lang] || labels.en;
  var parts = [];
  for (var key in summary.byType) {
    if (Object.prototype.hasOwnProperty.call(summary.byType, key)) {
      var n = summary.byType[key];
      var lbl = dict[key] || key;
      parts.push(n + '× ' + lbl);
    }
  }
  return parts.join(', ');
}

/* Browser global. */
if (typeof window !== 'undefined') {
  window.redactPII = redactPII;
  window.unredactPII = unredact;
  window.PII_TYPES = PII_TYPES;
  window.formatPIISummary = formatSummary;
}

/* Node test runner. */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    redactPII: redactPII,
    unredact: unredact,
    PII_TYPES: PII_TYPES,
    formatSummary: formatSummary,
    PATTERNS: PATTERNS,
  };
}
