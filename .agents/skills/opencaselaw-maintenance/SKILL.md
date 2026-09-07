---
name: opencaselaw-maintenance
description: "Use for OpenCaseLaw autonomous maintenance loops, production health checks, scraper/completeness triage, safe deploy decisions, and Codex automation work in this repo. Do not use for ordinary feature work unless the task is about operational safety, corpus completeness, or maintenance automation."
---

# OpenCaseLaw Maintenance

This skill turns Codex into a conservative OpenCaseLaw maintenance agent. Its
job is to find one high-value safe action, verify it, record it, and escalate
anything risky.

## Workflow

1. Bootstrap by reading `CLAUDE.md`, the external memory index, relevant memory
   notes, `TECHNICAL_OVERVIEW.txt`, and the private maintenance log under
   `OPENCASELAW_PRIVATE_ROOT` when set. Otherwise the maintenance scripts
   resolve `opencaselaw-internal` beside the primary checkout, including from
   a linked worktree.
2. Run or inspect a deterministic assessment before deciding:

   ```bash
   python3 scripts/agent_assess.py --json
   ```

   Use `--no-network` when tests or sandbox policy require an offline run.
3. Read `ops/autonomy-policy.json` before edits. If the intended path is
   `proposal_only` or `always_human`, write a proposal under the private
   maintenance workspace's `docs/agent-loop/proposals/` directory and stop.
4. Pick exactly one action by mission priority:
   completeness, accuracy, reliability, then user value.
5. Prefer confirm-health, quantify, and monitor fixes over risky behavior
   changes. Never use entscheidsuche as a scraper data path.
6. For code, add or update offline tests first when practical.
7. Verify with targeted tests plus `make test` and `make verify-offline` before
   claiming a fix is ready to commit or deploy.
8. Record the outcome:

   ```bash
   python3 scripts/agent_record.py --action "..." --evidence "..." --outcome "..."
   ```

## Deployment Guard

Before any autonomous deploy candidate, run:

```bash
python3 scripts/agent_safe_deploy.py --json
```

Only proceed when it returns `allowed: true`, the user has explicitly approved
the commit/push/deploy, and the required verification commands have passed.
Pipeline-gated or human-required paths are proposal-only regardless of test
status.

## Output Discipline

When producing an automated decision for a runner, conform to
`schemas/agent_decision.schema.json`. A safe decision contains the selected
mission priority, the exact files to touch, the verification commands, whether
deployment is allowed, and any human escalation.
