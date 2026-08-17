"""Approval hashing.

The workbook's content hash is what makes "I approved this" and "this is what
deploys" the same statement. Long review cycles mean they drift apart, and
nobody notices until after the deploy.

The hash covers only decision-bearing content — Final_*, Deploy, Notes, and the
row identity. It deliberately EXCLUDES writeback columns (Deployed_At,
Component_Id, Setup_Link, Result, Error), because 06 writes those after a
deploy and they must not invalidate an approval that is still in force.
"""

from __future__ import annotations

import hashlib
import json

# Columns whose values are decisions. Changing any of these invalidates approval.
DECISION_COLUMNS = (
    "Final_API",
    "Final_Type",
    "Final_Len",
    "Deploy",
    "Notes",
)

# Columns written by 06 after a deploy. Never part of the hash.
WRITEBACK_COLUMNS = (
    "Deployed_At",
    "Component_Id",
    "Setup_Link",
    "Result",
    "Error",
    "Error_Message",
)


def canonical_rows(rows):
    """Reduce workbook rows to a stable, order-independent decision digest.

    `rows` is an iterable of dicts, each carrying at least `_tab`, `_section`,
    and a natural key (`HS Property`, or `Final_API` when there is no source
    property, as with generated fields).
    """
    canon = []
    for row in rows:
        key = row.get("HS Property") or row.get("Final_API") or row.get("Name") or ""
        entry = {
            "tab": row.get("_tab", ""),
            "section": row.get("_section", ""),
            "key": key,
        }
        for col in DECISION_COLUMNS:
            value = row.get(col)
            # Normalise: None, "" and whitespace are all "unset".
            entry[col] = "" if value is None else str(value).strip()
        canon.append(entry)

    # Sort so that row reordering in Excel does not change the hash. A moved row
    # is not a changed decision.
    canon.sort(key=lambda e: (e["tab"], e["section"], e["key"]))
    return canon


def content_hash(rows) -> str:
    """SHA-256 over the canonical decision content. Returns a hex digest."""
    canon = canonical_rows(rows)
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def short_hash(full: str) -> str:
    """First 12 chars — for logs and human comparison, never for the check."""
    return (full or "")[:12]


def hashes_match(recorded: str | None, current: str | None) -> bool:
    """Constant-ish comparison, tolerant of case and surrounding whitespace.

    A missing recorded hash is NOT a match. An unapproved workbook must never
    pass by virtue of having no hash on file.
    """
    if not recorded or not current:
        return False
    return recorded.strip().lower() == current.strip().lower()
