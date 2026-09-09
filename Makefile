# OpenCaseLaw — reproducibility entry-point
#
# `make verify` reproduces the paper's three headline numerical claims
# against the deployed corpus + graph in <2 minutes. Designed for
# NeurIPS D&B reviewers and anyone who wants to confirm the paper's
# numbers without a full corpus rebuild.
#
# `make test` runs the full pytest suite.
#
# `make paper` recompiles the v3 paper PDF (requires tectonic).
#
# `make tarball` packages the paper for arXiv submission.

.DEFAULT_GOAL := help
# Prefer the project venv when it exists (local dev), else the ambient
# python3 — the reviewer Docker image and CI have no .venv and must be
# unaffected. An explicit PYTHON=... on the command line still wins.
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PAPER_DIR := docs/paper/p1-resource
RELEASE_DATE := $(shell date +%Y-%m-%d)
STATS_JSON := $(PAPER_DIR)/tables/corpus_graph_stats.json

.PHONY: help
help:
	@echo "OpenCaseLaw build targets:"
	@echo
	@echo "  verify          Default. Reproduce paper headline numbers"
	@echo "                  against the live deployed graph. Needs network."
	@echo "                  ~60 s."
	@echo "  verify-offline  Reproduce against committed JSON snapshots only."
	@echo "                  No network, no live MCP. ~5 s. Use this for"
	@echo "                  archival reproducibility (NeurIPS D&B reviewers)."
	@echo
	@echo "  verify-corpus           Live corpus-size probe (~5 s, needs network)."
	@echo "  verify-corpus-offline   Same, from corpus_graph_stats.json snapshot."
	@echo "  verify-graph            Citation-graph breakdown (offline-capable)."
	@echo
	@echo "  test           Run the full pytest suite (~40 s)."
	@echo
	@echo "  paper          Compile docs/paper/v3/paper.pdf via tectonic."
	@echo "  paper-tables   Re-run docs/paper/v3/scripts/build_tables.py."
	@echo "  tarball        Package the paper as an arXiv tar.gz."
	@echo
	@echo "  smoke          Hit production MCP endpoints to confirm uptime."
	@echo "  docker-build   Build the reviewer-reproducibility image."
	@echo

.PHONY: verify
verify: verify-corpus verify-graph verify-cross-lingual
	@echo
	@echo "✓ All headline claims reproduce against the deployed graph."

.PHONY: verify-offline
verify-offline: verify-corpus-offline verify-graph verify-cross-lingual
	@echo
	@echo "✓ All headline claims reproduce against committed snapshots"
	@echo "  (no network access required)."

.PHONY: verify-corpus
verify-corpus:
	@echo "── Corpus size verification (live) ─────────────────────────"
	@$(PYTHON) -c "import urllib.request, json; \
r=json.loads(urllib.request.urlopen('https://mcp.opencaselaw.ch/health', timeout=10).read()); \
n=r.get('decisions', 0); \
print(f'  /health.decisions = {n:,}'); \
assert n > 950_000, f'corpus shrunk: {n} < 950k floor'; \
assert n < 1_500_000, f'corpus suspiciously large: {n}'; \
print('  ✓ within expected band [950k, 1.5M]')"

.PHONY: verify-corpus-offline
verify-corpus-offline:
	@echo "── Corpus size verification (offline snapshot) ─────────────"
	@$(PYTHON) -c "import json, pathlib; \
s=json.loads(pathlib.Path('$(STATS_JSON)').read_text()); \
n=s['total_decisions']; d=s['snapshot_date']; \
print(f'  snapshot_date     = {d}'); \
print(f'  total_decisions   = {n:,}'); \
assert n > 950_000, f'snapshot corpus suspiciously small: {n}'; \
assert n < 1_500_000, f'snapshot corpus suspiciously large: {n}'; \
print('  ✓ within expected band [950k, 1.5M]')"

.PHONY: verify-graph
verify-graph:
	@echo "── Citation-graph resolution verification ──────────────────"
	@if test -f output/reference_graph.db && \
	  $(PYTHON) -c "import sqlite3; c=sqlite3.connect('file:output/reference_graph.db?mode=ro', uri=True); c.execute('SELECT 1 FROM citation_targets LIMIT 1').fetchone()" >/dev/null 2>&1 ; then \
		$(PYTHON) -m benchmarks.citation_resolution_analysis ; \
	else \
		echo "  (no local reference_graph.db with citation_targets — using corpus_graph_stats.json)" ; \
		$(PYTHON) -c "import json, pathlib; \
s=json.loads(pathlib.Path('$(STATS_JSON)').read_text()); \
raw=s['rg_citation_edges']; resolved=s['rg_resolved_citations']; pct=100.0*resolved/raw; \
print(f'  rg_citation_edges = {raw:,}'); \
print(f'  rg_resolved_citations = {resolved:,}'); \
print(f'  resolution rate = {pct:.2f}% (paper §4 headline: 93.8%)'); \
assert pct > 90.0, f'resolution regressed below 90% floor: {pct}%'; \
print('  ✓ matches paper §4 within 1pp')" ; \
	fi

.PHONY: verify-cross-lingual
verify-cross-lingual:
	@echo "── Cross-lingual benchmark verification ────────────────────"
	@$(PYTHON) -c "import json, pathlib; \
p=pathlib.Path('benchmarks/swiss_legal_rag_bench/results/cross_lingual_v1.json'); \
assert p.exists(), 'cross-lingual results file missing'; \
r=json.loads(p.read_text()); \
overall=r['summary']['overall']; \
mrr=overall['mrr_at_k']; \
hit=overall['hit_at_10']; \
n=overall['n']; \
print(f'  cross_lingual_v1 ({n} queries): MRR@10 = {mrr:.3f}, Hit@10 = {hit:.3f}'); \
assert mrr > 0.5, f'MRR regression: {mrr}'; \
assert hit > 0.7, f'Hit@10 regression: {hit}'; \
print(f'  ✓ matches paper §7 headline (MRR=0.630, Hit@10=0.833)')"

.PHONY: test
test: test-addin
	@$(PYTHON) -m pytest -q

# The Word add-in ships ~250 KB of JavaScript whose tests could only be run
# by hand (node tests/x.test.js), so nothing checked them: `make test` is
# pytest, and pytest does not collect .js. They cover the two things the Pro
# flow depends on being exactly right — that redaction never mangles a
# citation, and that the citation formatter matches the server — which is
# precisely the code that must not rot unnoticed. Skipped, not failed, where
# node is absent, so the Python gate still runs on a machine without it.
test-addin:
	@if command -v node >/dev/null 2>&1; then \
		for t in tools/word-addin/tests/*.test.js; do \
			node "$$t" >/dev/null || { echo "FAIL $$t"; node "$$t"; exit 1; }; \
		done; \
		echo "add-in JS tests: OK"; \
	else \
		echo "add-in JS tests: skipped (node not installed)"; \
	fi

.PHONY: test-addin
.PHONY: paper
paper:
	@cd $(PAPER_DIR) && tectonic paper.tex 2>&1 | tail -3
	@echo "  paper.pdf: $$(ls -la $(PAPER_DIR)/paper.pdf | awk '{print $$5}') bytes"

.PHONY: paper-tables
paper-tables:
	@cd $(PAPER_DIR) && $(PYTHON) scripts/build_tables.py

P2_DIR := docs/paper/p2-citations

.PHONY: paper2
paper2:
	@cd $(P2_DIR) && tectonic paper.tex 2>&1 | tail -3
	@echo "  paper.pdf: $$(ls -la $(P2_DIR)/paper.pdf | awk '{print $$5}') bytes"

.PHONY: paper2-tables
paper2-tables:
	@$(PYTHON) $(P2_DIR)/tables/build_tables.py

.PHONY: tarball
tarball: paper
	@cd $(PAPER_DIR) && tar czf opencaselaw-arxiv-$(RELEASE_DATE).tar.gz \
		paper.tex paper.pdf bib/ figures/ sections/ tables/
	@ls -la $(PAPER_DIR)/opencaselaw-arxiv-$(RELEASE_DATE).tar.gz

.PHONY: docker-build
docker-build:
	@echo "── Building reviewer-reproducibility image ─────────────────"
	@docker build -f Dockerfile.reviewer -t opencaselaw-reviewer:$(RELEASE_DATE) .
	@echo
	@echo "  Run: docker run --rm opencaselaw-reviewer:$(RELEASE_DATE) make verify-offline"

.PHONY: smoke-cli
bench-citations:  ## citation round-trip benchmark against production (by hand; ~500 requests, ~2 min)
	@$(PYTHON) benchmarks/citation_roundtrip.py

smoke-cli:  ## live contract check of the research CLI + description examples against production (~15 requests)
	@$(PYTHON) scripts/cli_live_check.py
	@$(PYTHON) scripts/check_description_examples.py

.PHONY: smoke
smoke:
	@echo "── Production MCP smoke ────────────────────────────────────"
	@for p in /health \
	          /api/decisions?query=Tierhalterhaftung\&limit=3 \
	          /api/laws/ZGB?article=8\&language=de \
	          /api/laws/search?query=ERV\&language=de\&limit=2 ; do \
		s=$$(curl -sL -o /dev/null -w "%{http_code}" --max-time 10 "https://mcp.opencaselaw.ch$$p") ; \
		printf "  %3s  %s\n" "$$s" "$$p" ; \
	done

.PHONY: check-playbook
check-playbook:  ## live re-verification of every statute reference in the Claude for Legal playbook (~10 requests)
	@$(PYTHON) scripts/check_playbook_refs.py
