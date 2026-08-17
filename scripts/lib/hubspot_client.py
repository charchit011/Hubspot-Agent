"""HubSpot CRM API client. Read-only.

Handles the two things that break naive fetch scripts: cursor pagination via
`paging.next.after`, and 429 rate limiting via exponential backoff with jitter.

The token is never logged. `_redact` scrubs it from any error text before it
reaches a console or a log file — HubSpot occasionally echoes request context
into error bodies.
"""

from __future__ import annotations

import os
import random
import time

import requests

BASE = "https://api.hubapi.com"
MAX_RETRIES = 6
BACKOFF_BASE = 1.5
PAGE_LIMIT = 100
TIMEOUT = 30


class HubSpotError(Exception):
    pass


class ScopeError(HubSpotError):
    """403 — the Private App is missing a scope.

    Worth its own type: a missing scope produces a silently incomplete schema
    rather than an obvious failure, and it is by far the most common cause of
    "the client's custom object isn't in raw/".
    """


class HubSpotClient:
    def __init__(self, token: str | None = None, verbose: bool = True):
        self.token = token or os.environ.get("HUBSPOT_TOKEN", "")
        if not self.token or self.token.startswith("pat-na1-REPLACE"):
            raise HubSpotError(
                "HUBSPOT_TOKEN is missing or still the placeholder. "
                "Set it in .env, or run with --fixture to use fixtures/."
            )
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    # -- internals ---------------------------------------------------------

    def _redact(self, text: str) -> str:
        """Never let the token reach a console or a log file."""
        if not text:
            return text
        out = text.replace(self.token, "***REDACTED***")
        if len(self.token) > 12:
            out = out.replace(self.token[:12], "***REDACTED***")
        return out

    def _log(self, message: str):
        if self.verbose:
            print(f"  {self._redact(message)}")

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE}{path}"
        delay = BACKOFF_BASE

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params or {}, timeout=TIMEOUT)
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise HubSpotError(self._redact(f"{path}: {exc}")) from None
                time.sleep(delay)
                delay *= 2
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                # Honour Retry-After when present; HubSpot usually sends it.
                wait = float(resp.headers.get("Retry-After", delay))
                wait += random.uniform(0, 0.5)  # jitter, avoid thundering herd
                self._log(f"429 rate limited, waiting {wait:.1f}s ({attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                delay *= 2
                continue

            if resp.status_code == 403:
                raise ScopeError(self._redact(
                    f"403 on {path} — the Private App is missing a scope. "
                    f"This yields an INCOMPLETE schema, not an error. Body: {resp.text[:300]}"
                ))

            if resp.status_code == 404:
                raise HubSpotError(f"404 on {path} — object type does not exist in this portal.")

            if resp.status_code >= 500 and attempt < MAX_RETRIES:
                self._log(f"{resp.status_code} on {path}, retrying in {delay:.1f}s")
                time.sleep(delay)
                delay *= 2
                continue

            raise HubSpotError(self._redact(
                f"{resp.status_code} on {path}: {resp.text[:300]}"
            ))

        raise HubSpotError(f"{path}: exhausted {MAX_RETRIES} retries.")

    def get_paged(self, path: str, params: dict | None = None) -> list[dict]:
        """Follow `paging.next.after` until exhausted, returning all results."""
        params = dict(params or {})
        params.setdefault("limit", PAGE_LIMIT)
        results: list[dict] = []
        after = None
        pages = 0

        while True:
            if after:
                params["after"] = after
            payload = self.get(path, params)
            batch = payload.get("results", [])
            results.extend(batch)
            pages += 1

            after = (payload.get("paging") or {}).get("next", {}).get("after")
            if not after:
                break
            if pages > 500:  # runaway guard — a portal this big needs a real look
                self._log(f"WARNING: {path} exceeded 500 pages, stopping.")
                break

        return results

    # -- endpoints ---------------------------------------------------------

    def schemas(self) -> list[dict]:
        """All object types including custom ones."""
        return self.get("/crm/v3/schemas").get("results", [])

    def properties(self, object_type: str) -> list[dict]:
        return self.get_paged(f"/crm/v3/properties/{object_type}")

    def owners(self) -> list[dict]:
        return self.get_paged("/crm/v3/owners")

    def pipelines(self, object_type: str) -> list[dict]:
        try:
            return self.get(f"/crm/v3/pipelines/{object_type}").get("results", [])
        except HubSpotError:
            return []  # most object types have no pipelines; not an error

    def association_labels(self, from_type: str, to_type: str) -> list[dict]:
        try:
            return self.get(
                f"/crm/v4/associations/{from_type}/{to_type}/labels"
            ).get("results", [])
        except HubSpotError:
            return []

    def sample_records(self, object_type: str, properties: list[str], limit: int = 100) -> list[dict]:
        """A small sample, used only to compute fill rates and length sizing.

        Never persisted as client data beyond `raw/` — and `raw/` is gitignored.
        """
        try:
            payload = self.get(
                f"/crm/v3/objects/{object_type}",
                {"limit": min(limit, 100), "properties": ",".join(properties[:100])},
            )
            return payload.get("results", [])
        except HubSpotError:
            return []

    def probe_sensitive_data(self) -> dict:
        """Detect HubSpot Sensitive Data by attempting an engagements read.

        There is no direct "is it on" endpoint. A 403 on an activity object when
        other scopes work is the reliable signal. Returns a verdict rather than
        raising — an unknown is a legitimate, reportable state.
        """
        for object_type in ("emails", "calls", "notes"):
            try:
                self.get(f"/crm/v3/objects/{object_type}", {"limit": 1})
                return {
                    "sensitive_data_enabled": False,
                    "evidence": f"read {object_type} successfully",
                    "probed": object_type,
                }
            except ScopeError as exc:
                return {
                    "sensitive_data_enabled": True,
                    "evidence": f"403 on {object_type}: {exc}"[:200],
                    "probed": object_type,
                    "warning": "Activity objects may be inaccessible. Confirm with the client.",
                }
            except HubSpotError:
                continue

        return {
            "sensitive_data_enabled": "unknown",
            "evidence": "no activity object was reachable to probe",
            "warning": "Could not determine. Do not assume it is off.",
        }
