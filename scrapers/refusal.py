"""Recognise a portal refusing us, as opposed to a request that went wrong.

bger.ch's Incapsula answers 403 (or 429) to every request from an address it
has decided to block, for minutes to hours at a time (2026-09-28, 09-29,
09-30). A walk that keeps going through such a block learns nothing and adds
hundreds of refused requests, which is the traffic that gets an address
blocked in the first place. Scrapers use this to stop a walk early.
"""
from __future__ import annotations

import requests

REFUSAL_STATUSES = frozenset({403, 429})


def is_refusal(exc: BaseException) -> bool:
    """True for an HTTP 403/429 answer, including 429s urllib3 gave up retrying."""
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        return getattr(response, "status_code", None) in REFUSAL_STATUSES
    if isinstance(exc, requests.exceptions.RetryError):
        return "429" in str(exc)
    return False
