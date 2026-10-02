"""Recognise a portal refusing us, as opposed to a request that went wrong.

bger.ch's Incapsula answers 403 (or 429) to every request from an address it
has decided to block, for minutes to hours at a time (2026-09-28, 09-29,
09-30). A walk that keeps going through such a block learns nothing and adds
hundreds of refused requests, which is the traffic that gets an address
blocked in the first place. Scrapers use this to stop a walk early.

Since 2026-10-02 the same refusal also arrives in two other shapes:

* a 404 with a ~924-byte Tomcat error page, on index, listing and search
  URLs that always exist (measured: any request with a bare client signature;
  bge_egmr's session request at 01:02). `listing=True` counts a 404 as a
  refusal for such URLs. A 404 on a single DOCUMENT stays a plain error: a
  withdrawn ruling answers 404 for real.
* a 200 that is not the page asked for (challenge or block page). The caller
  recognises that from the content and raises PortalRefused.
"""
from __future__ import annotations

import requests

REFUSAL_STATUSES = frozenset({403, 429})
LISTING_REFUSAL_STATUSES = frozenset({403, 404, 429})


class PortalRefused(Exception):
    """The portal answered, but with a block or challenge page instead of the
    page asked for. The message starts with "portal refused" so that
    run_all_scrapers counts the logged line as a discovery failure."""

    def __init__(self, why: str):
        super().__init__(f"portal refused the request ({why})")


def is_refusal(exc: BaseException, listing: bool = False) -> bool:
    """True for an HTTP 403/429 answer (404 too when `listing`), for 429s that
    urllib3 gave up retrying, and for a block page served with status 200."""
    if isinstance(exc, PortalRefused):
        return True
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        statuses = LISTING_REFUSAL_STATUSES if listing else REFUSAL_STATUSES
        return getattr(response, "status_code", None) in statuses
    if isinstance(exc, requests.exceptions.RetryError):
        return "429" in str(exc)
    return False
