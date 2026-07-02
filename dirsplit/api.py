"""Thin GraphQL client for the trafikkdata API — retries, backoff, no key needed."""

from __future__ import annotations

import json
import time
import urllib.request

from .config import TRAFIKKDATA_URL

MAX_RETRIES = 4
TIMEOUT_S   = 60


def query(gql: str) -> dict:
    """POST a GraphQL query; return the 'data' payload. Retries with backoff."""
    body = json.dumps({"query": gql}).encode()
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                TRAFIKKDATA_URL, data=body,
                headers={"Content-Type": "application/json",
                         "User-Agent": "gothenburg-traffic-sim (student project)"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
                payload = json.loads(res.read())
            if "errors" in payload:
                raise RuntimeError(f"GraphQL errors: {payload['errors'][:1]}")
            return payload["data"]
        except Exception as e:   # network hiccup or 5xx — back off and retry
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"trafikkdata query failed after {MAX_RETRIES} tries: {last_err}")
