"""Every opencaselaw.ch page in each of its languages is plain HTML on the server,
written by scripts/build_page_languages.mjs from the page and its dictionary.
A page edited without running the script is caught here. Offline; needs Node,
skipped without it."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_language_pages_are_up_to_date():
    run = subprocess.run([NODE, str(ROOT / "scripts/build_page_languages.mjs"), "--check"],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, "run `make site-languages`:\n" + run.stdout + run.stderr


def test_each_copy_names_itself_and_its_languages():
    copies = sorted(ROOT.glob("docs/**/fr/index.html")) + sorted(ROOT.glob("docs/fr/*.html"))
    assert copies, "no French pages"
    for copy in copies:
        html = copy.read_text(encoding="utf-8")
        assert '<html lang="fr" data-ocl-lang="fr">' in html, copy
        canonical = re.search(r'<link rel="canonical" href="([^"]+)">', html).group(1)
        assert re.search(r'hreflang="fr" href="%s"' % re.escape(canonical), html), copy
        assert 'hreflang="x-default"' in html and "location.replace(" not in html, copy
