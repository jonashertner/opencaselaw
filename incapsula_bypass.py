"""
Incapsula/Imperva WAF bypass via multi-strategy cookie harvesting.

bger.ch and search.bger.ch are behind Imperva's CDN which serves a JS challenge.
Plain `requests` can't execute JS, so the challenge fails and we get a 212-byte
stub page.  We try multiple browser automation strategies to solve the challenge
and extract the resulting cookies for use with requests.Session.

Strategy order (strongest first):
  1. camoufox  — C++-level Firefox patching, strongest stealth
  2. playwright-stealth — patches 7+ detection vectors on Chromium
  3. plain Playwright — existing fallback (weakest)

Cookie lifecycle:
  - Incapsula cookies (visid_incap_*, incap_ses_*) typically last 20-30 min
  - We cache them to disk and refresh when a request detects expiry
  - PoW cookies (powData, powHash, etc.) are separate and still needed

Usage:
    from incapsula_bypass import IncapsulaCookieManager

    mgr = IncapsulaCookieManager(cache_dir=Path("state"))
    cookies = mgr.get_cookies("https://www.bger.ch")
    session.cookies.update(cookies)

Dependencies (install strongest first):
    pip install "camoufox[geoip]" && python -m camoufox fetch
    pip install playwright-stealth
    pip install playwright && playwright install chromium --with-deps
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# LAZY IMPORTS — only loaded when cookies must be refreshed
# ═══════════════════════════════════════════════════════════════════════════════

_playwright_available: Optional[bool] = None
_camoufox_available: Optional[bool] = None
_playwright_stealth_available: Optional[bool] = None


def _check_playwright() -> bool:
    global _playwright_available
    if _playwright_available is None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            _playwright_available = True
        except ImportError:
            _playwright_available = False
            logger.warning(
                "playwright not installed. Run: "
                "pip install playwright && playwright install chromium --with-deps"
            )
    return _playwright_available


def _check_camoufox() -> bool:
    global _camoufox_available
    if _camoufox_available is None:
        try:
            from camoufox.sync_api import Camoufox  # noqa: F401
            _camoufox_available = True
        except ImportError:
            _camoufox_available = False
            logger.debug("camoufox not installed (optional)")
    return _camoufox_available


def _check_playwright_stealth() -> bool:
    global _playwright_stealth_available
    if _playwright_stealth_available is None:
        try:
            from playwright_stealth import stealth_sync  # noqa: F401
            if not _check_playwright():
                _playwright_stealth_available = False
            else:
                _playwright_stealth_available = True
        except ImportError:
            _playwright_stealth_available = False
            logger.debug("playwright-stealth not installed (optional)")
    return _playwright_stealth_available


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Chrome UA — must match TLS fingerprint (Chromium browser sends Chromium TLS)
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

# Known Incapsula-protected domains and their seed URLs
PROTECTED_DOMAINS = {
    "www.bger.ch": {
        "seed_url": "https://www.bger.ch/ext/eurospider/live/de/php/aza/http/index.php",
        "success_marker": "Eurospider",
        "fail_markers": ["Incapsula", "_Incapsula_Resource", "robots"],
        # www.bger.ch serves its leaf certificate without the intermediate
        # (since 2026-08-24; requests carries the intermediate itself). The
        # harvesting browser has no such bundle: camoufox SEC_ERROR_UNKNOWN_ISSUER,
        # Chromium ERR_CERT_AUTHORITY_INVALID (2026-10-14 12:00). For this host's
        # cookie harvest only, the browser accepts the incomplete chain.
        "ignore_https_errors": True,
    },
    "search.bger.ch": {
        "seed_url": "https://search.bger.ch/ext/eurospider/live/de/php/clir/http/index_atf.php?lang=de",
        "success_marker": "Eurospider",
        "fail_markers": ["Incapsula", "_Incapsula_Resource", "robots"],
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _simulate_human_behavior(page) -> None:
    """Simulate human-like mouse movements and short delays."""
    try:
        # Random mouse movements
        for _ in range(random.randint(2, 4)):
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            page.mouse.move(x, y)
            time.sleep(random.uniform(0.1, 0.3))
        # Small delay to appear human
        time.sleep(random.uniform(0.5, 1.5))
    except Exception:
        pass  # Non-critical — some page states don't support mouse


def _wait_for_challenge(page, timeout_seconds: int, t0: float) -> bool:
    """
    Poll page until real content appears or timeout.

    Returns True if challenge appears solved.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        content = page.content()

        has_fail = any(
            marker in content
            for marker in ["_Incapsula_Resource", "robots"]
        )

        # Real page is usually >1000 bytes and lacks Incapsula markers
        if len(content) > 1000 and not has_fail:
            logger.info(
                f"Incapsula challenge solved in {time.time()-t0:.1f}s "
                f"(page size: {len(content)} chars)"
            )
            return True

        if "Eurospider" in content or "eurospider" in content.lower():
            logger.info(f"Eurospider content detected in {time.time()-t0:.1f}s")
            return True

        time.sleep(1)

    # Last attempt — wait for networkidle
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
        content = page.content()
        if len(content) > 1000:
            logger.info("Solved after networkidle wait")
            return True
    except Exception:
        pass

    logger.warning(
        f"Challenge may not be fully solved "
        f"(page size: {len(page.content())} chars after {timeout_seconds}s). "
        f"Extracting cookies anyway."
    )
    return False


def browser_proxy(proxy: Optional[str]) -> Optional[dict]:
    """A requests-style proxy URL as a browser launch option. Browsers take
    "socks5://host:port", not requests' "socks5h://" (remote DNS is the
    browser's default for SOCKS5). None when there is no proxy."""
    if not proxy:
        return None
    server = proxy.replace("socks5h://", "socks5://", 1)
    return {"server": server}


def _user_agent(page) -> Optional[str]:
    """The User-Agent the browser presented while earning the cookies.
    Incapsula ties its session to the client that solved the challenge, so the
    requests session must send the same one (camoufox presents a Firefox UA)."""
    try:
        return page.evaluate("navigator.userAgent")
    except Exception:
        return None


def _has_incapsula_cookies(cookies: dict[str, str]) -> bool:
    """Check if cookie dict contains Incapsula session cookies."""
    return any(
        k.startswith("visid_incap") or k.startswith("incap_ses")
        for k in cookies
    )


def _extract_cookies(context) -> dict[str, str]:
    """Extract all cookies from a browser context as a flat dict."""
    return {c["name"]: c["value"] for c in context.cookies()}


def _log_harvest_result(strategy: str, cookies: dict[str, str], t0: float) -> None:
    """Log the result of a cookie harvest attempt."""
    elapsed = time.time() - t0
    incap_cookies = [
        k for k in cookies
        if k.startswith("visid_incap") or k.startswith("incap_ses")
        or k.startswith("nlbi_") or k.startswith("_Incap")
    ]
    logger.info(
        f"[{strategy}] Harvested {len(cookies)} cookies "
        f"({len(incap_cookies)} Incapsula) in {elapsed:.1f}s"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1: CAMOUFOX (strongest stealth)
# ═══════════════════════════════════════════════════════════════════════════════


def _harvest_via_camoufox(
    url: str, timeout_seconds: int, headless: bool, proxy: Optional[str] = None,
    ignore_https_errors: bool = False,
) -> tuple[dict[str, str], Optional[str]]:
    """
    Harvest cookies using camoufox — C++-level Firefox patching.

    Camoufox patches the Firefox binary at the C++ level, making it
    nearly undetectable as automation. Strongest stealth option.
    """
    from camoufox.sync_api import Camoufox

    logger.info(f"[camoufox] Attempting cookie harvest for {url}")
    t0 = time.time()

    launch = {"headless": headless}
    if browser_proxy(proxy):
        launch["proxy"] = browser_proxy(proxy)
    with Camoufox(**launch) as browser:
        page = browser.new_context(ignore_https_errors=ignore_https_errors).new_page()
        # 60-s page.goto timeout (bumped 2026-05-13 from 30 s after
        # BGer's Incapsula was tuned up: camoufox started timing out
        # on Page.goto while playwright-plain fallback still solved
        # in 73 s). 60 s gives the heavier challenge enough head-room
        # without blocking the request thread indefinitely.
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        _simulate_human_behavior(page)
        _wait_for_challenge(page, timeout_seconds, t0)
        cookies = _extract_cookies(page.context)
        _log_harvest_result("camoufox", cookies, t0)
        return cookies, _user_agent(page)


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2: PLAYWRIGHT + STEALTH (medium stealth)
# ═══════════════════════════════════════════════════════════════════════════════


def _harvest_via_playwright_stealth(
    url: str, timeout_seconds: int, headless: bool, proxy: Optional[str] = None,
    ignore_https_errors: bool = False,
) -> tuple[dict[str, str], Optional[str]]:
    """
    Harvest cookies using playwright-stealth — patches 7+ detection vectors.

    Uses Chromium with Chrome UA (matching TLS fingerprint) and
    playwright-stealth to patch navigator.webdriver, chrome.runtime, etc.
    """
    from playwright.sync_api import sync_playwright
    from playwright_stealth import stealth_sync

    logger.info(f"[playwright-stealth] Attempting cookie harvest for {url}")
    t0 = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            proxy=browser_proxy(proxy),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=CHROME_UA,
            viewport={"width": 1920, "height": 1080},
            locale="de-CH",
            timezone_id="Europe/Zurich",
            ignore_https_errors=ignore_https_errors,
        )
        page = context.new_page()
        stealth_sync(page)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _simulate_human_behavior(page)
            _wait_for_challenge(page, timeout_seconds, t0)
            cookies = _extract_cookies(context)
            _log_harvest_result("playwright-stealth", cookies, t0)
            return cookies, _user_agent(page)
        finally:
            browser.close()


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3: PLAIN PLAYWRIGHT (fallback)
# ═══════════════════════════════════════════════════════════════════════════════


def _harvest_via_playwright_plain(
    url: str, timeout_seconds: int, headless: bool, proxy: Optional[str] = None,
    ignore_https_errors: bool = False,
) -> tuple[dict[str, str], Optional[str]]:
    """
    Harvest cookies using plain Playwright with Chrome UA.

    This is the original approach, upgraded to use Chrome UA (not Firefox)
    to match Chromium's TLS fingerprint.
    """
    from playwright.sync_api import sync_playwright

    logger.info(f"[playwright-plain] Attempting cookie harvest for {url}")
    t0 = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            proxy=browser_proxy(proxy),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=CHROME_UA,
            viewport={"width": 1920, "height": 1080},
            locale="de-CH",
            timezone_id="Europe/Zurich",
            ignore_https_errors=ignore_https_errors,
        )

        # Remove webdriver indicator
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _simulate_human_behavior(page)
            _wait_for_challenge(page, timeout_seconds, t0)
            cookies = _extract_cookies(context)
            _log_harvest_result("playwright-plain", cookies, t0)
            return cookies, _user_agent(page)
        finally:
            browser.close()


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-STRATEGY DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════════


def harvest_cookies(
    url: str,
    timeout_seconds: int = 60,
    headless: bool = True,
    proxy: Optional[str] = None,
    ignore_https_errors: bool = False,
) -> dict[str, str]:
    """Cookies only; see harvest() for the User-Agent that earned them."""
    return harvest(url, timeout_seconds, headless, proxy, ignore_https_errors)[0]


def harvest(
    url: str,
    timeout_seconds: int = 60,
    headless: bool = True,
    proxy: Optional[str] = None,
    ignore_https_errors: bool = False,
) -> tuple[dict[str, str], Optional[str]]:
    """
    Launch a browser, navigate to URL, wait for Incapsula JS challenge
    to resolve, and extract cookies.

    Tries strategies in order of stealth strength:
      1. camoufox (if installed)
      2. playwright-stealth (if installed)
      3. plain Playwright (always available)

    Each strategy is tried; if it fails or doesn't yield Incapsula cookies,
    the next strategy is attempted.

    Args:
        url: Target URL (must be on an Incapsula-protected domain)
        timeout_seconds: Max time to wait for challenge resolution per strategy
        headless: Run browser headlessly (set False for debugging)
        proxy: requests-style proxy URL (the residential tunnel); the browser
            then egresses there, like the requests that use the cookies

    Returns:
        (dict of cookie name -> value for the domain, the browser's User-Agent)

    Raises:
        RuntimeError: If no strategy succeeds
    """
    strategies = []

    if _check_camoufox():
        strategies.append(("camoufox", _harvest_via_camoufox))

    if _check_playwright_stealth():
        strategies.append(("playwright-stealth", _harvest_via_playwright_stealth))

    if _check_playwright():
        strategies.append(("playwright-plain", _harvest_via_playwright_plain))

    if not strategies:
        raise RuntimeError(
            "No browser automation available. Install at least one of:\n"
            '  pip install "camoufox[geoip]" && python -m camoufox fetch\n'
            "  pip install playwright-stealth playwright && playwright install chromium\n"
            "  pip install playwright && playwright install chromium --with-deps"
        )

    logger.info(
        f"Harvesting Incapsula cookies for {url} "
        f"(strategies: {[s[0] for s in strategies]})"
    )

    last_error = None
    last_ua = None
    for name, strategy_fn in strategies:
        try:
            cookies, ua = strategy_fn(url, timeout_seconds, headless, proxy, ignore_https_errors)
            if _has_incapsula_cookies(cookies):
                logger.info(f"Strategy '{name}' succeeded with Incapsula cookies")
                return cookies, ua
            else:
                logger.warning(
                    f"Strategy '{name}' completed but no Incapsula cookies found, "
                    f"trying next strategy"
                )
                # Still return if this is the last strategy
                last_error = None
                last_cookies = cookies
                last_ua = ua
        except Exception as e:
            logger.warning(f"Strategy '{name}' failed: {e}")
            last_error = e
            last_cookies = {}

    # If we get here, no strategy produced Incapsula cookies
    if last_error:
        raise RuntimeError(
            f"All {len(strategies)} strategies failed. Last error: {last_error}"
        )

    # Return whatever the last strategy gave us (may still work)
    logger.warning("No strategy produced Incapsula cookies, returning best result")
    return last_cookies, last_ua  # type: ignore[possibly-undefined]


# ═══════════════════════════════════════════════════════════════════════════════
# COOKIE MANAGER (with caching and auto-refresh)
# ═══════════════════════════════════════════════════════════════════════════════


class IncapsulaCookieManager:
    """
    Manages Incapsula cookies with disk caching and automatic refresh.

    Cookies are cached per-domain in the state directory as JSON files.
    When cookies expire (detected by Incapsula response or age), they
    are re-harvested automatically.

    Usage:
        mgr = IncapsulaCookieManager(cache_dir=Path("state"))

        # Get fresh cookies (from cache or by harvesting)
        cookies = mgr.get_cookies("www.bger.ch")

        # Apply to requests.Session
        session.cookies.update(cookies)

        # After a request, check if we need to refresh
        if mgr.is_incapsula_blocked(response):
            cookies = mgr.refresh_cookies("www.bger.ch")
            session.cookies.update(cookies)
    """

    # Cookies are good for ~20 minutes typically, but be conservative
    COOKIE_MAX_AGE_SECONDS = 900  # 15 minutes
    # Browser harvests per domain for the life of one manager (one scraper run).
    # Every refused request may ask for a refresh; without a budget a blocked run
    # launches a browser per page from the same address, the request burst that
    # got the tunnel address challenged before (memory: bger-capture-guarantees).
    MAX_HARVESTS_PER_DOMAIN = 3

    def __init__(self, cache_dir: Path = Path("state"), headless: bool = True,
                 proxy: Optional[str] = None):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        # The egress the cookies are earned through. A scraper that sends its
        # requests through the residential tunnel sets this to the tunnel, so
        # the browser solves the challenge from that address too: cookies
        # earned from the datacenter address are no use through the tunnel,
        # and a browser launched there only hammers an address Incapsula
        # already blocks (2026-10-14: the tunnel address is challenged too).
        self.proxy = proxy
        self._cookies: dict[str, dict[str, str]] = {}
        self._timestamps: dict[str, float] = {}
        self._user_agents: dict[str, Optional[str]] = {}
        self._harvests: dict[str, int] = {}

    def _key(self, domain: str) -> str:
        return f"{domain}|{'proxy' if self.proxy else 'direct'}"

    def _cache_path(self, domain: str) -> Path:
        safe_domain = domain.replace(".", "_")
        suffix = "_via_proxy" if self.proxy else ""
        return self.cache_dir / f"incapsula_{safe_domain}{suffix}.json"

    def user_agent(self, domain: str) -> Optional[str]:
        """The User-Agent that earned the current cookies for ``domain``."""
        return self._user_agents.get(self._key(domain))

    def _load_cache(self, domain: str) -> Optional[dict[str, str]]:
        """Load cached cookies from disk if still fresh."""
        path = self._cache_path(domain)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text())
            timestamp = data.get("timestamp", 0)
            age = time.time() - timestamp

            if age > self.COOKIE_MAX_AGE_SECONDS:
                logger.info(
                    f"Cached cookies for {domain} expired "
                    f"({age:.0f}s > {self.COOKIE_MAX_AGE_SECONDS}s)"
                )
                return None

            cookies = data.get("cookies", {})
            logger.info(
                f"Loaded {len(cookies)} cached cookies for {domain} "
                f"(age: {age:.0f}s)"
            )
            self._cookies[self._key(domain)] = cookies
            self._timestamps[self._key(domain)] = timestamp
            self._user_agents[self._key(domain)] = data.get("user_agent")
            return cookies

        except Exception as e:
            logger.warning(f"Failed to load cookie cache for {domain}: {e}")
            return None

    def _save_cache(self, domain: str, cookies: dict[str, str],
                    user_agent: Optional[str] = None) -> None:
        """Save cookies to disk cache."""
        path = self._cache_path(domain)
        timestamp = time.time()
        data = {
            "domain": domain,
            "timestamp": timestamp,
            "via_proxy": bool(self.proxy),
            "user_agent": user_agent,
            "cookies": cookies,
        }
        path.write_text(json.dumps(data, indent=2))
        self._cookies[self._key(domain)] = cookies
        self._timestamps[self._key(domain)] = timestamp
        self._user_agents[self._key(domain)] = user_agent
        logger.debug(f"Cached {len(cookies)} cookies for {domain}")

    def get_cookies(self, domain: str) -> dict[str, str]:
        """
        Get cookies for a domain. Returns cached if fresh, harvests if not.

        Args:
            domain: e.g. "www.bger.ch" or "search.bger.ch"

        Returns:
            Dict of cookie name -> value
        """
        # Try in-memory cache first
        key = self._key(domain)
        if key in self._cookies:
            age = time.time() - self._timestamps.get(key, 0)
            if age < self.COOKIE_MAX_AGE_SECONDS:
                return self._cookies[key]

        # Try disk cache
        cached = self._load_cache(domain)
        if cached:
            return cached

        # Harvest fresh cookies
        return self.refresh_cookies(domain)

    def refresh_cookies(self, domain: str) -> dict[str, str]:
        """
        Force-refresh cookies for a domain via browser automation.

        Args:
            domain: e.g. "www.bger.ch"

        Returns:
            Fresh cookie dict
        """
        config = PROTECTED_DOMAINS.get(domain)
        if config is None:
            raise ValueError(
                f"Unknown domain: {domain}. "
                f"Known: {list(PROTECTED_DOMAINS.keys())}"
            )

        key = self._key(domain)
        if self._harvests.get(key, 0) >= self.MAX_HARVESTS_PER_DOMAIN:
            raise RuntimeError(
                f"Incapsula harvest budget for {domain} spent "
                f"({self.MAX_HARVESTS_PER_DOMAIN} this run); not launching another browser")
        self._harvests[key] = self._harvests.get(key, 0) + 1
        url = config["seed_url"]
        logger.info(f"Harvesting Incapsula cookies for {domain} "
                    f"({'through the proxy' if self.proxy else 'direct'})")
        cookies, user_agent = harvest(url, headless=self.headless, proxy=self.proxy,
                                      ignore_https_errors=bool(config.get("ignore_https_errors")))
        self._save_cache(domain, cookies, user_agent)
        return cookies

    @staticmethod
    def is_incapsula_blocked(response_text: str) -> bool:
        """
        Check if a response is an Incapsula block page.

        Args:
            response_text: The response body text

        Returns:
            True if the response appears to be an Incapsula challenge/block
        """
        # The challenge page grew: 953-955 bytes on 2026-10-14 ("Request
        # unsuccessful. Incapsula incident ID"), so a 500-byte cut missed it
        # and a blocked request read as an empty page.
        if len(response_text) < 5000 and "_Incapsula_Resource" in response_text:
            return True
        if len(response_text) < 500:
            markers = ["_Incapsula_Resource", "Incapsula", "robots"]
            return any(m in response_text for m in markers)
        return False

    @staticmethod
    def is_incapsula_blocked_response(response) -> bool:
        """
        Check if a requests.Response is an Incapsula block.

        Args:
            response: requests.Response object

        Returns:
            True if blocked
        """
        return IncapsulaCookieManager.is_incapsula_blocked(response.text)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI for testing
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    domain = sys.argv[1] if len(sys.argv) > 1 else "www.bger.ch"

    if domain not in PROTECTED_DOMAINS:
        # Treat as full URL
        url = domain
        cookies = harvest_cookies(url, headless=True)
    else:
        mgr = IncapsulaCookieManager(cache_dir=Path("state"))
        cookies = mgr.get_cookies(domain)

    print(f"\n{'='*60}")
    print(f"Cookies for {domain}:")
    print(f"{'='*60}")
    for name, value in sorted(cookies.items()):
        print(f"  {name}: {value[:60]}{'...' if len(value)>60 else ''}")

    # Verify with plain requests
    import requests as req
    config = PROTECTED_DOMAINS.get(domain, {"seed_url": domain})
    url = config["seed_url"] if isinstance(config, dict) else domain
    r = req.get(url, cookies=cookies, headers={"User-Agent": CHROME_UA}, timeout=15)
    print(f"\nVerification: GET {url}")
    print(f"  Status: {r.status_code}, Length: {len(r.text)}")
    print(f"  Blocked: {IncapsulaCookieManager.is_incapsula_blocked(r.text)}")
    if "Eurospider" in r.text or "eurospider" in r.text.lower():
        print("  SUCCESS — Eurospider content reached!")
    else:
        print(f"  Content preview: {r.text[:300]}")
