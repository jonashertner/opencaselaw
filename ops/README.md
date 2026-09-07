# Privacy operations templates

This directory contains public, reviewable templates for OpenCaseLaw's
privacy-preserving traffic pipeline: nginx log formats, short-retention
logrotate rules, aggregate derivation, and service/timer definitions.

The templates intentionally omit production host addresses, administrative
access instructions, live paths, credentials, client identifiers, and active
deny lists. Those belong in the private operations workspace and host-local
configuration.

## Public invariants

- Tier 1 may contain IP addresses and User-Agent strings and is destroyed
  after 72 hours. Raw entries must never be copied into source control,
  tickets, reports, or permanent archives.
- Tier 2 contains derived client classes without IP addresses and is retained
  for 14 days.
- Tier 3 contains daily aggregates protected by differential privacy
  (`epsilon = 1.0`, `k = 10`) and may be published.
- Client-specific deny rules must not be committed. The public nginx template
  loads them from an unversioned host-local include.
- Any production installation or recovery procedure must come from the
  private operations runbook, not from this directory.

The authoritative public description is the
[privacy notice](https://opencaselaw.ch/datenschutz/).
