# AppSource Listing — OpenCaseLaw

## App name
OpenCaseLaw - Swiss Case Law

## Provider Name (must match manifest <ProviderName>)
Jonas Hertner

## Short description (100 chars max)
Search 1,050,000+ Swiss court decisions and insert correct citations directly in Word.

## Long description (4000 chars max)

OpenCaseLaw gives lawyers, judges, legal scholars, and students instant access to Swiss case law and legislation — directly inside Microsoft Word.

**Pricing — disclosed up front (Microsoft Marketplace policy 1100.1.3)**

- **Core features are free**: search across 1,050,000+ decisions, statute lookup, citation insertion. No account, no card, no trial expiry.
- **Optional Pro subscription — CHF 5/month**, billed via **Stripe**. Cancelable at any time from your Stripe billing portal. Adds AI reference verification, argument search, and document scanning. The first 100 verifications per day are included; no overage charges. No other in-app purchases or hidden fees.

**1,050,000+ court decisions. 5,525 federal laws. 15,600 cantonal laws. One search.**

Search the full text of decisions from the Federal Supreme Court (BGer/TF), Federal Administrative Court (BVGer/TAF), Federal Criminal Court (BStGer/TPF), Federal Patent Court (BPatGer/TFB), FINMA, WEKO/COMCO, and all 26 cantonal courts. The database is updated daily (BGer decisions within 15 minutes of publication) and covers decisions from 1875 to today.

**Insert correctly formatted citations in one click**

Find a decision, click "Insert" — the correctly formatted citation appears at your cursor position. Citations follow Swiss legal citation standards and adapt to your chosen language:
- German: (BGE 133 III 121, E. 3)
- French: (ATF 133 III 121, consid. 3)
- Italian: (DTF 133 III 121, consid. 3)

No more manual formatting. No more typos in docket numbers.

**Look up federal and cantonal statutes inline**

Search for "Art. 41 OR" or "Art. 8 BV" — the full article text appears directly in the panel. Covers 5,525 Swiss federal laws (133,359 articles in DE/FR/IT) and 15,600 cantonal laws across all 26 cantons. The current consolidated text is shown alongside the Federal Council Botschaft reference (legislative intent).

**Find related decisions**

Select a citation in your document and click "Find related" to discover decisions on the same topic. Built on a citation graph with 8.65 million cross-references between decisions (8.03 million resolved).

**Free core features, optional Pro subscription**

Search, statute lookup, and citation insertion are completely free, with no account required.

Pro subscription (CHF 5/month via Stripe, cancelable anytime) adds AI-powered features:
- Reference verification: select a passage citing a court decision, click "Verify" — AI reads the cited decision and checks whether it supports your claim
- Argument search: find cases that support or contradict a legal statement
- Document scan: scan an entire document for legal references and verify them
- 100 verifications per day

**Multilingual**

Full interface in German, French, Italian, and English. Court names, button labels, error messages — everything adapts to your language choice. Citations are automatically formatted in the selected language.

**Privacy**

No cookies. No user accounts for free features. No logging of search queries. The court decision data is CC0 1.0 (public domain). The add-in code is MIT licensed. Before any Pro request leaves Word, the add-in replaces e-mail addresses, AHV numbers, IBANs, UID, phone numbers, dates of birth, addresses, postal codes and titled names with placeholders; this is always on, pattern-based and not a guarantee of completeness (scope and limits: https://word.opencaselaw.ch/privacy.html). Privacy policy: https://word.opencaselaw.ch/privacy.html

Built by Jonas Hertner — opencaselaw.ch

## Categories
- Productivity
- Legal
- Reference

## Keywords
Swiss law, case law, court decisions, citations, legal research, BGE, ATF, DTF, Bundesgericht, Tribunal federal, Rechtsprechung, jurisprudence, Schweizer Recht, droit suisse

## Supported languages
German, French, Italian, English

## Support URL
https://opencaselaw.ch

## Privacy policy URL
https://word.opencaselaw.ch/privacy.html

## Terms of use URL
https://word.opencaselaw.ch/terms.html

## Test notes for Microsoft reviewers

To test the add-in:

1. Open the add-in panel — it should show the welcome screen with search bar and feature cards
2. Type "Mietrecht Kündigung" in the search bar and press Enter — results should appear within 2-5 seconds
3. Click "Volltext" on any result — the detail view shows the regeste, key considerations (Erwägungen), and full text
4. Click "Insert" — in standalone browser mode this logs to console; in Word it inserts a correctly formatted citation at cursor
5. Search for "Art. 41 OR" — a law article card appears above the decision results showing the current article text
6. Change language to FR — all labels, placeholders, and court names switch to French
7. Click the gear icon — settings page shows the Pro upgrade section and a privacy toggle for anonymous usage statistics
8. The Pro features (Verify, Find Support, Scan) require a Pro subscription (CHF 5/month via Stripe) or a test license key. Contact team@jonashertner.com for a test key.

The API server is at mcp.opencaselaw.ch. No authentication is required for free features (search, statute lookup, citation insertion). Pro features require a license key validated against the billing API.

**Note on charges**: The add-in's core features (search, citation, statute lookup) are free. The optional Pro subscription (CHF 5/month) is handled via Stripe and is disclosed in the add-in description, settings panel, and terms of use.
