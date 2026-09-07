# Contributors

OpenCaseLaw is built by a small, open community. This file recognises everyone
who has materially helped make the project better — through code, bug reports,
consumer integrations, institutional collaboration, legal feedback, or
adjacent public-good research.

## Maintainer

- **Jonas Hertner** — project lead and maintainer. Concept, architecture,
  scraper suite, FTS5 search stack, citation graph, MCP server, dashboard,
  Word add-in, paper. [jonashertner.com](https://jonashertner.com) ·
  [GitHub](https://github.com/jonashertner)

## Contributors & first users whose feedback shaped the tool

*Listed chronologically. See the "How to contribute" section below — we add
names here when someone's work lands in the project.*

- **[Adrian König](https://adriankoenig.ch)** — consumer integration
  (`apply_deltas.py`) against the daily HF delta feed, and the report that
  surfaced the 30-day federal-data gap in the delta pipeline (2026-04).
  Independent early validator of the incremental-update path.
- **Arne Holicki**

## Institutional acknowledgements

- **Federal Office of Justice** and the **Federal Tribunal (BGer)** — for
  operating the public `bger.ch` and `entscheidsuche.ch` infrastructures
  that make this dataset possible.
- **Fedlex / Federal Chancellery** — the canonical Swiss legislation SPARQL
  endpoint that backs the `get_law`, `search_laws`, and Materialien features.
- **LexFind.ch** — cantonal legislation aggregation that feeds the 26-canton
  law corpus.
- **OnlineKommentar.ch** and **OpenLegalCommentary.ch** — open-access
  scholarly commentary whose CC-BY licensing made the `get_commentary` /
  `search_commentaries` features possible.
- **Hugging Face** — hosting the public dataset mirror at
  [voilaj/swiss-caselaw](https://huggingface.co/datasets/voilaj/swiss-caselaw)
  under CC0.

## How to contribute

Small contributions welcome and credited here:

- **Bug reports** — open a GitHub issue at
  [jonashertner/opencaselaw/issues](https://github.com/jonashertner/opencaselaw/issues).
  Scraper breakages are especially valuable because Swiss court portals change
  their URL structures without notice. Include the court code and a date range
  that breaks.
- **New scrapers** — each scraper lives in `scrapers/<court_code>.py` and
  inherits from `base_scraper.py`. Model yours after an existing cantonal
  scraper (`scrapers/cantonal/sz_gerichte.py` is a good reference). Register
  the new scraper in `run_scraper.py:SCRAPERS`. See
  [README.md#how-it-works](README.md#how-it-works) for the end-to-end pipeline.
- **Quality / dedup PRs** — if you find decisions that are duplicates,
  missing regeste, broken PDF extractions, or wrong metadata, a PR touching
  the specific scraper or `search_stack/merge_shards.py` is welcome.
- **MCP tool additions** — `mcp_server.py` is the single source of truth.
  Tools that surface new structured views over the existing data (not new
  data pipelines) are the easiest class to accept.
- **Documentation / i18n** — the dashboard and Word add-in ship in DE/FR/IT/EN;
  translation improvements are accepted via PR.
- **Removal / correction requests** — see the dedicated
  [governance & removal policy](docs/governance-and-removal-policy.md); don't
  open these as GitHub issues.

If your contribution materially lands (merged PR, bug that led to a fix,
consumer integration that surfaces something structurally useful), add
yourself to this file in the same PR or ask us to.

## Funding & support

OpenCaseLaw is an independent nonprofit project with no external funding. If
you'd like to support it through sponsorship, institutional hosting, or
research collaboration, contact **team@jonashertner.com**.
