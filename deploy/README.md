# deploy/

Public-safe infrastructure templates for review and disaster recovery. Nothing
in this directory is consumed automatically by a build or publish step, and
the live host configuration is intentionally not mirrored here.

## Files

| file | what it is |
|---|---|
| `nginx/mcp-server` | Public site template for TLS termination, rate limiting, worker proxying, and redirects |
| `nginx/ocl-bulk-export.conf` | `http`-context declaration for the `ocl_export` rate-limit zone used by the site template |
| `../ops/nginx/ocl-logging.conf` | `http`-context definitions for the three public privacy-tier log formats |
| `nginx/bulk-access.txt` | Static guidance returned to sustained bulk-export clients |
| `certs/build-ca-bundle.sh` | Atomic builder for certifi plus approved public intermediates |
| `certs/extra/*.pem` | Public CA intermediates required by portals that omit their chain |

## Refreshing this snapshot

After an nginx change on the VPS, pull a private comparison copy first. Do not
overwrite the public template with the live file:

```bash
scp -i <SSH_KEY> <HOST>:/path/to/live/mcp-server \
    <PRIVATE_LOCAL_PATH>/mcp-server.live

# Port only generic changes by hand, then enforce the confidentiality guard.
python -m pytest tests/test_public_repo_hygiene.py
git diff --check -- deploy/nginx/mcp-server
```

## Applying from this snapshot in a DR scenario

Install all four nginx inputs listed above in their matching `http`, `server`,
and static-file contexts. In particular, `mcp-server` is not standalone:
`ocl_export` comes from `nginx/ocl-bulk-export.conf`, while the `tier1`,
`tier2`, and `tier3` log formats come from `../ops/nginx/ocl-logging.conf`.

Client-specific deny rules, credentials, certificate locations, and concrete
host paths must come from the private recovery runbook. Validate the assembled
configuration with `nginx -t` before any reload.

## Notable endpoints (as of 2026-04-20)

- `/health`, `/metrics`, `/api/`, `/entscheid/`, `/sitemap*`, `/robots.txt` — all proxy to workers
- `/` — SSE stream for legacy MCP clients
- `/sse` — alias of `/`
- `/messages/` — POST side-channel for legacy MCP clients
- **`/mcp`** — Streamable HTTP transport (added 2026-04-20 after 6 clients hit 404s)

## Pitfalls

- Do not place backup files in an nginx auto-include directory; duplicate
  `limit_req_zone` declarations will make validation fail.
- The security blocklist regex near the top of the file drops requests to known scanner paths (`/wp-admin`, `/phpmyadmin`, etc.) with `return 444` (silent close). Previously included `/mcp/` which blocked legitimate Streamable-HTTP sub-paths — removed 2026-04-20.

## Scraper CA bundle

Several portals serve a **leaf-only chain** — they omit the intermediate and
rely on clients chasing the leaf's AIA extension. Browsers do; OpenSSL does
not, so `requests` fails with `CERTIFICATE_VERIFY_FAILED`. Known cases:

| host | missing intermediate | since |
|---|---|---|
| `www.bger.ch` | DigiCert Global G2 TLS RSA SHA256 2020 CA1 | 2026-08-24 |
| `publicationtc.fr.ch` | RapidSSL TLS RSA CA G1 | 2025-11-03 (only surfaced 2026-08-26) |
| `www.appellationsgericht.bs.ch` and every other `*.bs.ch` court host | Thawte TLS RSA CA G1 | latent; found 2026-08-26 |

### Adding a portal whose chain breaks

```bash
# 1. take the CA Issuers URL from the leaf itself, not from a search engine
echo | openssl s_client -connect HOST:443 -servername HOST 2>/dev/null \
  | openssl x509 -noout -text | grep -A2 "Authority Information Access"
# 2. fetch it and convert DER -> PEM
curl -fsS -o /tmp/i.crt http://cacerts.EXAMPLE.com/Intermediate.crt
openssl x509 -inform DER -in /tmp/i.crt -out deploy/certs/extra/NAME.pem
# 3. it must already chain to certifi, or the builder will (correctly) skip it
openssl verify -CAfile "$(python3 -c 'import certifi;print(certifi.where())')" \
  deploy/certs/extra/NAME.pem
```

Do **not** reach for `VERIFY_SSL = False` instead. It no longer means what it
looks like (see `base_scraper._build_session`), and
`tests/test_scraper_tls_verification.py` fails if it is reintroduced.

Install the certificate builder and public intermediates using the private
recovery runbook. Never copy environment files or live service configuration
back into this public snapshot.
