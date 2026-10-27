# Submission to the Claude Connectors Directory (Anthropic)

**Status (2026-09-02):** answer sheet ready; not yet submitted. Two things
must be live before the portal is opened (see "Sequence" below).

**Where:** the submission portal lives inside Claude.ai organization
settings — `https://claude.ai/admin-settings/directory/submissions/new`.
Track status and reviewer feedback afterwards at
`https://claude.ai/admin-settings/directory/submissions`.
Escalations: `mcp-review@anthropic.com`.

**Official docs (read these, not third-party blogs):**
- Submission walkthrough: https://claude.com/docs/connectors/building/submission
- Pre-submission checklist (what reviewers test): https://claude.com/docs/connectors/building/review-criteria
- Authentication modes (`none` is supported): https://claude.com/docs/connectors/building/authentication
- Software Directory Policy: https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy
- Software Directory Terms: https://support.claude.com/en/articles/13145338-anthropic-software-directory-terms

## Hard prerequisites

1. **A Team or Enterprise Claude.ai organization.** The portal is part of
   organization settings; it is not available on Free/Pro/Max. Only
   organization Owners (or, on Enterprise, a custom role with the
   *Directory* permission) can submit. Individual plans cannot submit.
2. **Every tool must expose a `title` plus `readOnlyHint`/`destructiveHint`.**
   The portal syncs tools live from the server and flags missing titles.
   As of 2026-09-02 the live server had annotations on all 42 tools but no
   titles. `mcp_server.py` now sets `Tool.title` on all 44 tools (42 public
   + 2 local admin); this must be **deployed** before submission.
3. **Privacy policy covering third-party sharing.** The server sends query
   text and claim/passage text to Anthropic's API (Haiku for query parse,
   expansion and rerank; Sonnet for `check_claim_support`, grounding audit
   and the Word add-in `/attest`). `docs/datenschutz/index.html` now
   carries a tier "AI processing via the Anthropic API" in DE/FR/IT/RM/EN;
   this must be **pushed** (GitHub Pages) before submission.
4. **Public documentation:** https://opencaselaw.ch/mcp/ (exists).

## Sequence

1. Deploy the tool titles: commit `mcp_server.py`, push, `git merge --ff-only`
   on the VPS, rolling restart of the 8 workers (see memory `mcp-deploy-path`).
   Verify: `tools/list` on `https://mcp.opencaselaw.ch/mcp` shows a `title`
   on every tool.
2. Push `docs/datenschutz/index.html`; verify
   https://opencaselaw.ch/datenschutz/?lang=en shows the new tier.
3. Open the portal and paste the answers below.

## Evidence that every tool was exercised

`scripts/tool_surface_check.py` against production, 2026-09-02:
**44 OK / 0 EMPTY / 0 FAIL** (log: `assets/tool-surface-check-2026-09-02.log`).
The portal's "I have run every tool" confirmation can be ticked on that
basis; additionally add the server as a custom connector in Claude.ai and
run the sample prompts under "Test & launch" once.

---

# Portal answers (paste as-is; sizes checked against the portal limits)

## Step: Connection

| Field | Value |
|---|---|
| Server URL | `https://mcp.opencaselaw.ch/mcp` |
| Transport | Streamable HTTP (SSE also available at `https://mcp.opencaselaw.ch/sse`, but submit the streamable-HTTP URL) |
| Same URL for every user? | Yes |

## Step: Tools

Synced automatically. Expect 42 tools, all in the **read-only** group
(`readOnlyHint: true, destructiveHint: false, openWorldHint: false`),
each with a title. No prompts, no resources (verified live 2026-09-02:
`resources/list` returns an empty list; the server advertises no prompts capability).

> The MCP App widgets (`decision_widget.py`, `law_widget.py`) are gated
> by `OCL_UI_WIDGETS` and are **off in production**. If that flag is ever
> enabled on prod, the tools gain `_meta.ui.resourceUri` and the portal
> treats the submission as an **MCP App**, which requires 3–5 carousel
> screenshots (PNG, ≥1000 px wide, cropped to the app response, paired
> prompt text). Keep the flag off for the initial listing, or prepare the
> screenshots first.

## Step: Listing

> Numbers below (1,050,000+ decisions, 44,000+ scholarship records from 24 sources, 9.6M edges) follow the pending `mcp_server.py` instructions update. Re-check against `https://mcp.opencaselaw.ch/health` and the live server instructions on submission day.

**Server name** (49/100):

```
OpenCaseLaw — Swiss Case Law, Statutes & Doctrine
```

**Tagline** (53/55):

```
Verified Swiss law: 1M+ decisions, statutes, doctrine
```

**Description** (1,841/2,000):

```
OpenCaseLaw is a free, open Swiss legal research server: 1,050,000+ published decisions of the Federal Supreme Court, the federal courts and all 26 cantons (1875 to today), ~9,600 European Court of Human Rights judgments, 5,500+ federal and 15,600+ cantonal statutes, 1,100+ scholarly commentaries, the verbatim Federal Council Botschaft corpus, federal administrative practice (FINMA, SECO, ESTV, SEM, BAFU) and 44,000+ open-access scholarship records, connected by a citation graph of 9.6 million resolved edges. Updated daily. Data is CC0, code is MIT, and no account or API key is needed.

What Claude can do with it:
• Search decisions, statutes, commentaries, legislative materials and scholarship in German, French or Italian, with cross-language matching handled server-side.
• Retrieve the verbatim text of a decision, a single numbered Erwägung (consid.), a Regeste, a statute article or a commentary section.
• Trace the citation graph: leading cases for a statute or topic, who cites a decision, appeal chains, doctrine timelines and legislative intent.
• Produce canonical Swiss citations (BGE/ATF/DTF, BGer, BVGer, cantonal) from the corpus rather than from memory, and check whether a passage actually supports a claim.
• Audit a drafted text: attest_response parses every case reference Claude wrote, verifies it against the corpus and the cited Erwägung, and returns links to the canonical source.

All 42 tools are read-only. The server ships an anti-hallucination contract in its instructions: citation strings come only from the corpus, and quotations only from verbatim retrieval tools, which is what makes the output safe for practitioners, courts, students and researchers.

Documentation: https://opencaselaw.ch/mcp/ · Source: https://github.com/jonashertner/opencaselaw · Privacy: https://opencaselaw.ch/datenschutz/
```

| Field | Value |
|---|---|
| Categories (pick 1–5 from the portal's fixed list) | Legal; Research; Education; Productivity (use whichever of these exist; "Legal" first) |
| Documentation URL | `https://opencaselaw.ch/mcp/?lang=en` |
| Privacy policy URL | `https://opencaselaw.ch/datenschutz/?lang=en` |
| Support contact | `team@jonashertner.com` |
| Icon | `assets/icon-512.png` (Swiss cross, same mark as the site favicon; `icon-1024.png` and `icon.svg` alongside) |
| URL slug (permanent) | `opencaselaw` |

## Step: Use cases

**Primary use cases:**

```
1. Legal research for Swiss practitioners: find the leading cases and the governing statute for a question, in DE/FR/IT, and get canonical citations with pinpoints (E. / consid.) that resolve to the real text.
2. Drafting with verification: write a memo or submission with Claude, then run attest_response so every cited decision and quoted Erwägung is checked against the corpus before the text leaves the desk.
3. Legislative intent and doctrine: for an article of the OR/ZGB/StGB/BV etc., retrieve the current text, its amendment history, the Federal Council's Botschaft, the doctrinal timeline and scholarly commentary in one call (get_doctrine, get_article_purpose, get_article_history).
4. Teaching and study: case briefs, structured decisions (facts / considerations / ruling), exam questions generated from real leading cases, and open-access scholarship linked both ways to decisions and statutes.
5. Regulatory practice: FINMA circulars (all versions), SECO commentary on the Labour Act, ESTV circulars, SEM directives, VPB/JAAC decisions.
```

**What users need before they can connect:**

```
Nothing. No account, no API key, no plan. The corpus is public-domain (CC0) and the server is free.
```

**Reads / writes:** Reads only. No tool writes, modifies or deletes anything.

## Step: Company

| Field | Value |
|---|---|
| Company name | OpenCaseLaw (Jonas Hertner) |
| Website | `https://opencaselaw.ch` |
| Primary contact | pre-filled from the account; email `team@jonashertner.com` |

## Step: Authentication

**No authentication.** Every exposed operation is intentionally public.
(Auth type `none` is supported out of the box per the docs; no
coordination with the review team needed.) No partial-auth mode.

## Step: Data handling

| Question | Answer |
|---|---|
| Underlying API | **Our own first-party API.** The MCP server and the data (scraped from official court and Fedlex/cantonal portals, republished under CC0) are operated by us on `opencaselaw.ch`; the MCP domain matches the service. |
| Sponsored content | No. |
| Personal health data | **Jonas to decide.** The connector does not process the user's own health data. The corpus does contain published, court-anonymised decisions on social insurance (IV/AHV/KVG) that discuss claimants' medical conditions. Suggested answer: "No — the connector processes no user health data; it retrieves published, anonymised court decisions, some of which concern social-insurance matters." |

## Step: Test & launch

**Test-account setup / access instructions:**

```
No account or credentials are required. Add the connector with the server URL https://mcp.opencaselaw.ch/mcp and no authentication.

Health check: https://mcp.opencaselaw.ch/health returns {"status":"ok","decisions":<count>,...}.

Suggested prompts (one per tool cluster):
- Search: "Find Swiss Federal Supreme Court decisions on employer liability for bullying at work (Art. 328 OR)." → search_decisions, find_leading_cases
- Verbatim retrieval: "Give me the exact text of BGE 140 III 86 E. 4.1." → get_erwaegung, get_regeste
- Citation graph: "Which decisions cite BGE 125 III 70, and what is the appeal chain of BGer 4A_747/2012?" → find_citations, find_appeal_chain
- Statutes & intent: "What is the purpose of Art. 336 OR according to the Botschaft, and how has the article changed?" → get_doctrine, get_article_purpose, get_article_history, get_law
- Cantonal law: "Show me the Zurich law on public procurement." → search_laws, search_legislation
- Scholarship: "Which open-access articles cite Art. 8 ZGB?" → find_scholarship_citing_statute, search_scholarship
- Administrative practice: "Summarise FINMA-Rundschreiben 2023/1 on operational risks." → search_practice, get_practice
- Verification: paste a paragraph containing "BGE 140 III 86 E. 4.1" and ask "Check every citation in this text." → attest_response, cite, check_claim_support
- ECtHR: "Find ECtHR judgments against Switzerland on Art. 8 ECHR family reunification." → search_decisions
- Statistics: "How many decisions per court are in the corpus?" → get_statistics, list_courts

Multilingual: any prompt may be asked in French or Italian; the server matches across languages.
```

**"I have run every tool myself":** yes — `scripts/tool_surface_check.py`
(44 OK / 0 FAIL, 2026-09-02) plus manual use as a custom connector.

## Step: Compliance (seven acknowledgments)

All seven can be acknowledged truthfully:

1. Directory guidelines — read (links above).
2. First-party API usage — yes, our own service.
3. Financial transactions — none.
4. AI media generation — none (text only; `draft_mock_decision` and
   `generate_exam_question` produce text from real decisions, not images/audio/video).
5. Prompt injection — tool descriptions describe what each tool does and
   when to call it; none instructs Claude to call other software, pull
   instructions from external sources, or promote products. (2026-09-02:
   removed a "see system instructions U3" reference in `find_leading_cases`
   and the "ChatGPT Deep Research" wording in `search`/`fetch`.)
6. Conversation data collection — the server logs tool name, parameters
   (including query text), timestamps and a rotating session id, no IP
   with requests; disclosed at https://opencaselaw.ch/datenschutz/. It
   does not read Claude's memory, chat history or user files.
7. Public documentation — https://opencaselaw.ch/mcp/.

## After submission

- Status and reviewer feedback: submissions dashboard (link above).
- New submissions are auto-scanned and listed as a **community
  connector**; Anthropic may escalate to **verified** review (functional
  test of every tool) on its own. No action needed.
- Tool changes after listing need no resubmission; Claude reads the live
  tool surface on connection. Listing text and the slug are edited in the
  dashboard (slug is permanent).
- Keep `server.json` (official MCP registry) in sync when the tool count
  or description changes.
