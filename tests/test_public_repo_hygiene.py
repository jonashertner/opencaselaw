from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

PRIVATE_TREES = (
    "docs/agent-loop",
    "docs/ops",
    "docs/letters",
    "docs/outreach",
    "docs/drafts",
)

PRIVATE_FILES = (
    "docs/maintenance-loop-prompt.md",
    "docs/court/dsfa-vorlage.md",
    "docs/court/go-to-market.md",
    "docs/court/isds-konzept-geruest.md",
    "docs/court/pilot-vereinbarung.md",
    "docs/court/preise-lizenz.md",
    "docs/court/technik.md",
    "deploy/nginx-mcp-server.conf",
    "deploy/nginx/bulk-export-throttle.conf",
    "deploy_incapsula_bypass.sh",
    "tools/ne_tunnel.sh",
    "tools/ne_tunnel.sh.bak-2026-07-14",
    "runbooks/zh_arbeitsgericht_yearbooks.md",
)

IGNORE_PROBES = (
    "docs/agent-loop/example.md",
    "docs/ops/example.md",
    "docs/letters/example.md",
    "docs/outreach/example.md",
    "docs/drafts/example.md",
    "notes/reply_client.md",
    "notes/reply_client.txt",
    "notes/client.eml",
    "notes/client.msg",
    "notes/client.pst",
    "notes/live.conf.bak",
    "notes/live.conf.bak-20260907",
    "notes/live.conf.backup",
    "notes/live.conf.orig",
    "notes/live.conf.rej",
    "notes/live.conf~",
    *PRIVATE_FILES,
)


def test_private_material_is_absent_from_public_checkout():
    exposed: list[str] = []
    for relative in PRIVATE_TREES:
        root = REPO / relative
        if root.exists():
            exposed.extend(
                str(path.relative_to(REPO)) for path in root.rglob("*") if path.is_file()
            )

    exposed.extend(relative for relative in PRIVATE_FILES if (REPO / relative).exists())
    exposed.extend(
        str(path.relative_to(REPO))
        for path in (REPO / "deploy" / "nginx").glob(
            "opencaselaw-client-denies*.conf"
        )
        if path.is_file()
    )
    exposed.extend(
        str(path.relative_to(REPO))
        for pattern in ("reply_*.md", "reply_*.txt")
        for path in REPO.rglob(pattern)
        if path.is_file()
    )
    for pattern in ("*.eml", "*.msg", "*.pst"):
        exposed.extend(
            str(path.relative_to(REPO)) for path in REPO.rglob(pattern) if path.is_file()
        )
    for pattern in ("*.bak", "*.bak-*", "*.backup", "*.orig", "*.rej", "*~"):
        exposed.extend(
            str(path.relative_to(REPO)) for path in REPO.rglob(pattern) if path.is_file()
        )

    assert exposed == []


def test_private_paths_are_ignored():
    not_ignored = []
    for relative in IGNORE_PROBES:
        proc = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", relative],
            cwd=REPO,
            check=False,
        )
        if proc.returncode != 0:
            not_ignored.append(relative)

    assert not_ignored == []


def test_public_nginx_snapshot_has_no_client_identifiers():
    config = (REPO / "deploy" / "nginx" / "mcp-server").read_text(encoding="utf-8")
    assert "opencaselaw-client-denies*.conf" in config

    deny_targets = re.findall(r"(?m)^\s*deny\s+([^;#]+)\s*;", config)
    assert [target for target in deny_targets if target.strip() != "all"] == []

    global_addresses = []
    for literal in re.findall(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", config):
        try:
            address = ipaddress.ip_address(literal)
        except ValueError:
            continue
        if address.is_global:
            global_addresses.append(literal)

    assert global_addresses == []
    assert "Mozilla/" not in config


def test_public_nginx_configs_have_no_literal_client_denies():
    exposed: list[str] = []
    for root in (REPO / "deploy" / "nginx", REPO / "ops" / "nginx"):
        for path in root.glob("*"):
            if not path.is_file():
                continue
            config = path.read_text(encoding="utf-8")
            targets = re.findall(r"(?m)^\s*deny\s+([^;#]+)\s*;", config)
            if any(target.strip() != "all" for target in targets):
                exposed.append(str(path.relative_to(REPO)))

    assert exposed == []


def test_tier1_log_fixture_is_explicitly_synthetic():
    fixture = (
        REPO / "tests" / "fixtures" / "nginx_tier1_sample.log"
    ).read_text(encoding="utf-8")
    assert fixture.startswith("# Fully synthetic fixture.")

    for literal in re.findall(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", fixture):
        address = ipaddress.ip_address(literal)
        assert not address.is_global

    assert "Mozilla/" not in fixture
    assert "Chrome/124" not in fixture
    assert "Googlebot/2.1" not in fixture
