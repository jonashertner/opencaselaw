# Research CLI + shared contracts release (2026-09-05)

Scope: one commit on `codex/research-cli-20260905` (based on origin/main 8fda0c84), reviewed and
fixed on 2026-09-05. The detailed pre-release review is archived in the private maintenance workspace.
Serving change: `mcp_server.py` + new `research_contracts.py` (deploy together).
Site change: `docs/index.html`, `docs/api/index.html`, `docs/research-cli.md` (GitHub Pages from `main:/docs`).
Client: `clients/python` (`opencaselaw-cli`, console script `ocl`, on PyPI since 0.2.0); works against the already-deployed REST API.
Untouched: databases, `publish.py`, scrapers, `state/`, systemd units, billing, secrets, nginx.

Repository rule (AGENTS.md): no commit, push or deploy without explicit approval plus
passing `make test` and `make verify-offline`. `scripts/agent_safe_deploy.py` reports
`allowed: false` for this change (paths outside the autonomous-safe set), so every step
below is a human-approved step.

## Timing

- Serving code is picked up only by a worker restart. Restart in a quiet window: never
  between 03:30 UTC and the end of the full build (`opencaselaw-publish.service` must be
  `inactive`), and not while `opencaselaw-publish-incremental.service` (20:00 UTC, queued
  behind a late full build) is active. Gate every VPS step on both being inactive.
- Pushing to `main` publishes the website within minutes (GitHub Pages) and lands the code
  on the VPS disk at the latest at the end of the next nightly run (publish.py step 6 does
  `git pull --rebase origin main`). A later unplanned worker restart (crash, needrestart)
  would then boot the new code. Do the fast-forward and the rolling restart deliberately
  and soon after the push, inside the window; do not leave the push "pending" for days.
- If the BSV Evening-2 practice release (`runbooks/practice_tier1_deploy.md`) lands the
  same night, run it first and share its rolling restart; otherwise sequence the two.

## 1. Commit and pull request (laptop)

The scratch worktree carries an untracked `.venv` symlink and an `.env` copy that
`.gitignore` does not cover (`.venv/` matches a directory, not a symlink). Remove them and
stage explicit paths; never `git add -A` there.

```bash
cd <worktree on codex/research-cli-20260905>
rm -f .venv .env
make test && make verify-offline
git add README.md .github/workflows/ci.yml .github/workflows/release.yml clients docs/api/index.html docs/index.html docs/research-cli.md mcp_server.py pyproject.toml research_contracts.py runbooks/research_cli_release.md tests/test_decision_widget.py tests/test_incremental_per_pair_cutover.py tests/test_output_schema_declared.py tests/test_research_contracts.py
git status --short   # exactly the paths above, nothing else
GIT_AUTHOR_NAME=jonashertner GIT_COMMITTER_NAME=jonashertner \
GIT_AUTHOR_EMAIL=130315719+jonashertner@users.noreply.github.com \
GIT_COMMITTER_EMAIL=130315719+jonashertner@users.noreply.github.com \
git commit -F <commit-msg.txt>
git push -u origin codex/research-cli-20260905
gh pr create --base main --head codex/research-cli-20260905 --title "Add composable research CLI and shared API/MCP contracts" --body-file <pr-body.md>
```

Wait for CI (`CI` workflow: test + frontend + paper) to be green on the PR. The branch push
does not reach the VPS; only `main` does.

## 2. Merge to main

The bot pushes `Update stats.json + feeds` commits to `main` during the day. Rebase first
so the merge is a fast-forward and the VPS merge stays `--ff-only`:

```bash
git fetch origin && git rebase origin/main && git push --force-with-lease origin codex/research-cli-20260905
git push origin codex/research-cli-20260905:main
```

(`gh pr merge --rebase` is equivalent.) Record the resulting `main` SHA as `NEW_SHA`; the
previous `main` SHA is `PREV_SHA`.

## 3. Fast-forward the VPS checkout (read-only for the running workers)

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && systemctl is-active opencaselaw-publish.service opencaselaw-publish-incremental.service; git fetch origin && git merge --ff-only origin/main && python3 -m py_compile mcp_server.py research_contracts.py && sha256sum mcp_server.py research_contracts.py && git status --porcelain | grep -E "^(UU|AA)" || echo "no unmerged paths"'
```

Compare the two hashes with `git show main:mcp_server.py | shasum -a 256` and
`git show main:research_contracts.py | shasum -a 256` locally. Never copy files into
`/opt/caselaw/repo` by scp; the nightly stash/pop would corrupt them.

## 4. Rolling restart (Jonas)

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'bash /opt/caselaw/repo/scripts/rolling_restart_workers.sh'
```

Health-gated, one worker at a time. A few `RuntimeError: Expected ASGI message
'http.response.body'` lines are in-flight requests being cut, harmless.

## 5. Bounded post-deployment checks (10 minutes)

```bash
make smoke
make smoke-cli                                            # client + contract + description examples, live (~15 requests)
.venv/bin/python scripts/tool_surface_check.py          # all 42 tools answer (~2 min)
curl -s https://mcp.opencaselaw.ch/api/research/openapi.json | python3 -c 'import sys,json; s=json.load(sys.stdin); print(s["openapi"], s["x-opencaselaw-contract-version"], len(s["paths"]), "paths", len(s["components"]["schemas"]), "schemas")'
# expected: 3.0.3 1.0.0 7 paths 15 schemas
```

The application and Copilot Studio documents must be unchanged apart from two description
strings (`/decisions`, `/lookup`). Compare against the pre-deploy capture ignoring
descriptions:

```bash
python3 - <<'EOF'
import json, urllib.request
def strip(o):
    if isinstance(o, dict): return {k: strip(v) for k, v in o.items() if k != "description"}
    return [strip(v) for v in o] if isinstance(o, list) else o
for name in ("openapi.copilot.json", "openapi.json"):
    live = json.load(urllib.request.urlopen("https://mcp.opencaselaw.ch/api/" + name))
    before = json.load(open("release/baseline/" + name))   # pre-deploy capture
    print(name, "unchanged ignoring descriptions:", strip(live) == strip(before))
EOF
```

MCP wire check (JSON-RPC over `/mcp`): `tools/list` shows 42 tools and `outputSchema` on
`search_decisions`, `get_decision`, `get_erwaegung`, `get_law`, `find_citations`, `cite`;
`tools/call cite {"reference":"BGE 136 III 513"}` returns `structuredContent.exists == true`
and `isError` false; `tools/call get_decision {"decision_id":"does_not_exist"}` returns
`isError` true with `structuredContent.error`. `scripts/tool_surface_check.py` classifies by
text, so it is unaffected by `isError`.

CLI check against the deployed API:

```bash
ocl decisions search --court bge --sort date_desc --max-results 3 --timeout 15 --retries 0
ocl decisions passage bge_BGE_136_III_513 2.3 --timeout 15 --retries 0
ocl citations resolve 'BGE 136 III 513' bge_BGE_136_III_513 --timeout 15 --retries 0
ocl bundle create '' --court bge --date-from 2010-10-07 --date-to 2010-10-07 --max-results 1 --law OR:41 --out /tmp/ocl-check --timeout 15 --retries 0
```

Watch for the contract guard for 15 minutes (Jonas; journal reads are not always allowed
in auto mode):

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 "journalctl -u 'mcp-server@*' --since '-15min' | grep -c 'Invalid research response contract'"
```

Expected 0. A non-zero count means a real payload shape the strict contract rejects;
roll back and capture the tool name from the log line.

## 6. Rollback

```bash
git revert --no-edit NEW_SHA && git push origin HEAD:main
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && git fetch origin && git merge --ff-only origin/main && python3 -m py_compile mcp_server.py'
ssh -i ~/.ssh/caselaw root@46.225.212.40 'bash /opt/caselaw/repo/scripts/rolling_restart_workers.sh'
```

About five minutes. The revert also restores the website. Workers keep serving the
in-memory code throughout; nothing in the databases or the pipeline is touched. The CLI
keeps working after a rollback because it only uses pre-existing REST routes.

## 7. PyPI publication

Published since 2026-09-05 (0.2.0, trusted publisher registered by Jonas). `.github/workflows/release-cli.yml`
publishes `clients/python` with trusted publishing when a `cli-v<version>` tag is pushed
(the tag must equal `__version__` in `clients/python/src/opencaselaw_cli/_version.py`, currently 0.2.0; the workflow builds,
`twine check`s, smoke-installs the wheel, runs the client tests, then uploads). Nothing is
uploaded until PyPI trusts this repository. One-time setup, in Jonas's PyPI account:

1. PyPI → Publishing → "Add a new pending publisher": PyPI project name `opencaselaw-cli`,
   owner `jonashertner`, repository `opencaselaw`, workflow name `release-cli.yml`,
   environment name `pypi-cli`.
2. GitHub → repository Settings → Environments → create `pypi-cli` (optionally with a
   required reviewer).
3. Tag and push: `git tag cli-v0.2.0 <merged sha> && git push origin cli-v0.2.0`.
4. Done for 0.2.0: install instructions everywhere say `pipx install opencaselaw-cli`.
   Later releases: bump `__version__`, merge, push the matching `cli-v` tag.

The root package's `release.yml` publishes `swiss-caselaw-scrapers` on `v*` tags; do not
reuse it for the client.
