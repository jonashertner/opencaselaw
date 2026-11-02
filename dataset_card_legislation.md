---
license: other
language:
  - de
  - fr
  - it
  - rm
tags:
  - legal
  - swiss-law
  - legislation
  - statutes
  - fedlex
pretty_name: Swiss Legislation (Federal + Cantonal, article-level)
size_categories:
  - 100K<n<1M
configs:
  - config_name: federal
    data_files: federal/*.parquet
  - config_name: cantonal
    data_files: cantonal/*.parquet
---

# Swiss Legislation (article-level)

Private companion to [`voilaj/swiss-caselaw`](https://huggingface.co/datasets/voilaj/swiss-caselaw).
Article-level export of Swiss statutory law: **751,017 article rows** across two
configs.

- **`federal`** (`federal/fedlex.parquet`) — 401,313 article rows from Fedlex
  (the Federal Council's official consolidated law portal, SPARQL-sourced).
  Public domain.
- **`cantonal`** (`cantonal/<canton>.parquet`, one shard per canton) — 349,704
  article rows across all 26 cantons. Sourced by direct scraping of the official
  cantonal law portals for 23 cantons, with LexFind for JU, SZ, VD and ZH's gaps.
  The `text_source` column preserves this provenance per row.

## Why private

Fedlex is public-domain and freely redistributable. The cantonal side mixes
direct-portal text with LexFind-fallback rows, whose bulk republication we keep
private. A federal-only public release is a separate, later decision. Treat this
repo as an internal working mirror, not a redistribution.

## Schema

### `federal`
| column | type | notes |
|---|---|---|
| `sr_number` | string | Systematic Compilation (SR) number |
| `law_title` | string | title in the article's language |
| `abbreviation` | string | official abbreviation |
| `article_num` | string | e.g. `Art. 41` |
| `heading` | string | marginal note / article heading |
| `text` | string | verbatim article text |
| `language` | string | `de` / `fr` / `it` (/ `rm`) |
| `consolidation_date` | string | Fedlex consolidation date |
| `work_uri` | string | Fedlex work URI |
| `source` | string | always `fedlex` |

### `cantonal`
| column | type | notes |
|---|---|---|
| `canton` | string | 2-letter canton code |
| `sr_number` | string | cantonal systematic number (where available) |
| `law_title` | string | |
| `category` | string | subject category |
| `article_num` | string | |
| `seq` | int64 | article order within the law |
| `heading` | string | |
| `text` | string | verbatim article text |
| `language` | string | |
| `is_active` | int64 | 1 = currently in force |
| `text_source` | string | provenance label: direct-portal scraper id (e.g. `zhlex_pdf`) vs `lexfind` fallback |
| `original_url` | string | source document URL |
| `version_active_since` | string | version validity start |
| `lexfind_id` | string | LexFind law id (join key) |
| `source` | string | always `cantonal` |

## Provenance and freshness

Derived from the same nightly pipeline that backs `mcp.opencaselaw.ch`
(`statutes.db` for federal, `cantonal_laws.db` for cantonal). Rows with empty
article text are excluded. Not a substitute for the official portals for
legally-binding text; the `original_url` / `work_uri` columns point back to the
authoritative source.

## Usage

```python
from datasets import load_dataset

fed = load_dataset("voilaj/swiss-legislation", "federal", split="train")
zh  = load_dataset("voilaj/swiss-legislation", "cantonal",
                   data_files="cantonal/zh.parquet", split="train")
```

Licensed data upstream: Fedlex (public domain); cantonal portals + LexFind under
their respective terms. CC0 does not apply to the cantonal LexFind-fallback rows.
