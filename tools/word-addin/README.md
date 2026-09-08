# OpenCaseLaw Word Plugin

Search 971,000+ Swiss court decisions and insert citations directly in Microsoft Word.

## Installation (Sideload)

### Word for Windows / Mac

1. Download [manifest.xml](https://word.opencaselaw.ch/manifest.xml)
2. Open Word → **File** → **Get Add-ins** → **Upload My Add-in**
3. Select the downloaded `manifest.xml`
4. The OpenCaseLaw sidebar appears on the right

### Word Online

1. Open a document at office.com
2. Click **Insert** → **Add-ins** → **Upload My Add-in**
3. Enter URL: `https://word.opencaselaw.ch/manifest.xml`

## Features

### Free

- Search court decisions across all Swiss courts
- Insert formatted citations at cursor: `(BGE 125 III 231, E. 3)`
- Language-aware formatting (DE/FR/IT/EN)
- Look up federal statute articles
- View decision details with structured Sachverhalt / Erwägungen / Dispositiv,
  including correct **E. / consid. / para. numbers** (sourced from
  `decision_structure.db` for federal courts)

### Pro (CHF 5/month, license-based)

Two distinct workflows, two buttons:

#### 1. Verify Citations
*The "I'm about to file this — tell me nothing is wrong" workflow.*

- Click **Verify Citations** with cursor in any paragraph (selection
  optional — paragraph at cursor is auto-detected)
- Server runs Sonnet to check that each cited BGE/BGer/etc. exists,
  matches the assertion, and the pinpoint is correct
- Returns inline issues with one-click fix suggestions

Whole-document scan: `attestDocument` → `POST /api/attest`. Per-citation
verify: `verifyReferencePro` → `POST /api/billing/verify`.

#### 2. Verify & Strengthen
*The "this paragraph builds my argument — make it bulletproof" workflow.*

- Click **Strengthen** with cursor in the paragraph you're developing
- Returns a structured "junior associate review":
  - **Verified citations** — each ref cross-checked against the corpus
    with court, date, and citation count
  - **Suggested leading cases** — top 5 leading cases for each cited
    statute that you didn't cite, ranked by citation-graph centrality,
    one-click insert at cursor
  - **Commentary excerpts** — relevant OnlineKommentar.ch passages
  - **Argument-strength signal** — coarse {strong, medium, weak}
- Paragraph-only by design (multi-paragraph dilutes ranking)

`verifyAndStrengthenPro` → `POST /api/billing/strengthen`. Shared 25/day
cap with Verify.

### Privacy — structural PII redaction

The Pro endpoints **never receive un-redacted PII**. This is a class
invariant of the API client, not a configurable option:

- **Client side** (`js/redact.js`): every Pro request (Verify, Strengthen,
  Ground, Audit, Mirror) runs through `_requireRedact()` which replaces
  nine categories with typed placeholders (`[NAME_1]`, `[AHV_1]`, …):
  e-mail, AHV/AVS, CH IBAN, CHE/UID, Swiss phone, date of birth, street
  address, PLZ + place, and personal names. Pattern-based and bounded by
  design: names are caught only after a title (Herr/Herrn/Frau/Dr./M./
  Monsieur/Mme/Madame/Me/Sig./Signor(a)/Avv./avv./Dott., upper-case
  surnames included), dates of birth only after a birth formula. A bare
  "Max Müller" is **not** detected — the privacy page says so. The same
  original always gets the same placeholder within one request. Trailing
  citation vocabulary is never swallowed into a name ("Herr Müller Art.
  41 OR" keeps the article). Legal citations (BGE, BGer, dockets, Art. X)
  are explicitly **preserved** so verification still works.
- **No opt-out**: there is no setting to disable redaction. Even setting
  a legacy localStorage flag is ignored. If `redact.js` somehow fails
  to load, the function throws `redact_unavailable` and the request is
  refused — fail-closed instead of silent leak. This is locked by a
  STRUCTURAL test that fails CI if anyone re-introduces an opt-out.
- **Server-side hard guard** (`quality/redact.py`): every Pro endpoint
  re-runs the patterns against the supposedly-redacted text. If it
  finds any AHV/IBAN/CHE/EMAIL/PHONE pattern, it returns
  `400 client_redaction_incomplete` with the type labels (never the
  matched substring). Defense-in-depth re-redact runs before any LLM call.
- **Un-redacted in the user's UI only**: when the server returns
  annotated text or issue messages containing placeholders, the client
  un-redacts them locally before display, so the lawyer sees their
  own document text in the UI — the original PII never crossed the network.
  Every Pro result view shows a banner with the count per category.
- **Prompts know about placeholders**: the Verify / Ground prompts in
  `stripe_billing.py` tell the model that `[NAME_1]`-style tokens are
  redacted personal data to be treated as opaque proper nouns.
- **Deploy skew to watch**: the code above is v4 (working tree). The live
  add-in serves v3, and `privacy.html` §5 deliberately describes **v3**, so
  the legal pages can ship on their own without over-promising. After a v4
  deploy, update §5 to add: upper-case surnames, the Herrn / Monsieur /
  Madame / Signor(a) / avv. / dott. titles, the `geb.` / `Geburtsdatum` /
  `nata il` / slash-date anchors, lower-case `rue`, one placeholder per
  distinct original, the Mirror banner, and the sentence that the Verify
  and Ground prompts describe placeholders to the model.
- **Disclosed on every platform**: `privacy.html` §5 and `terms.html` §5,
  the opencaselaw.ch/word landing page, opencaselaw.ch/datenschutz, the
  AppSource listing text and the in-app settings note. Keep them in sync
  with `PATTERNS` when the redactor changes.

The earlier "Tier B" flow that called `https://api.anthropic.com/v1/messages`
directly from the add-in with a user-supplied Anthropic key was removed
on 2026-04-25 (see `tests/web/test_word_addin_no_browser_anthropic.py`
for the regression guard).

## Citation Formats

| Language | Example |
|----------|---------|
| DE | (BGer 4A_747/2012 vom 5. April 2013, E. 2) |
| FR | (TF 4A_747/2012 du 5 avril 2013, consid. 2) |
| IT | (TF 4A_747/2012 del 5 aprile 2013, consid. 2) |
| EN | (BGer 4A_747/2012 of 5 April 2013, para. 2) |

## Architecture

```
   Word/Office.js task pane                Server (FastAPI on Hetzner)
   ┌─────────────────────────┐             ┌─────────────────────────┐
   │ js/redact.js            │             │ quality/redact.py       │
   │  STRUCTURAL guard       │             │  HARD guard (mirror)    │
   │  ↓                      │             │  ↓                      │
   │ js/api.js               │ HTTPS POST  │ mcp_server.py           │
   │  _requireRedact()       │ ──────────→ │  /api/billing/verify    │
   │  redacted_text only     │             │  /api/billing/strengthen│
   │  ↓                      │             │  /api/billing/find-support
   │                         │             │  /api/billing/reflect   │
   │                         │             │  /api/attest            │
   │ js/app.js               │ ←────────── │  ↓                      │
   │  un-redact response     │  JSON       │  Anthropic Sonnet/Haiku │
   │  for user-visible UI    │             │  (caching disabled)     │
   └─────────────────────────┘             └─────────────────────────┘
```

Test suites:
- `tests/redact.test.js` — base PII patterns (37 tests)
- `tests/redact_extended.test.js` — adversarial + structural (49 tests)
- `tests/redact_polish.test.js` — title forms, trailing-token guard,
  lower-case rue, DOB anchors, consistent placeholders, portability (30 tests)
- `tests/citation.test.js` — citation formatter (87 tests)
- `tests/i18n.test.js` — language coverage (60 tests)
- `tests/test_redact_mirror.py` — Python guard + scrub (19 tests)
- `tests/test_redact_js_py_parity.py` — runs one fixture through node and
  Python, requires identical output (skipped without node)
- `tests/test_pro_redaction_guard.py` — FastAPI integration (14 tests)
- `tests/test_strengthen.py` — Strengthen handler (9 tests)

Run `make test-addin` for the JS suites and `pytest tests/test_redact_*.py
tests/test_pro_redaction_guard.py` for the server side.

## License

Code: MIT · Data: CC0 1.0 · Source: [OpenCaseLaw.ch](https://opencaselaw.ch)
