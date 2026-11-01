"""Document-identity alias table (SPEC.md §7.5 deliverable 5).

Maps the source document number (MAPP / ORA-LAB style) to the corpus
``stable_id``, title and series — the product's first "domain
relationships" seed input (SPEC.md §0.5). Built from the snapshot's
verified DocumentVersion records only; identifiers and titles, never
source passages, so the owner-approved committable copy is safe for the
public repository (decision recorded in SPEC.md §7.5, 2026-11-01).
"""

from __future__ import annotations

from ..contracts.models import canonical_json


def build_alias_table(documents: dict[str, dict]) -> dict:
    """``documents`` maps stable_id → DocumentVersion record (verified).

    Returns ``{rows, collisions, missing_number}``: rows sorted by
    stable_id; duplicate document numbers are reported as collisions and
    excluded from resolution (never silently first-wins); documents with
    no number are listed, not invented.
    """
    rows: list[dict] = []
    by_number: dict[str, list[str]] = {}
    missing: list[str] = []
    for sid in sorted(documents):
        doc = documents[sid]
        number = doc.get("source_document_number")
        if not number or not isinstance(number, str):
            missing.append(sid)
            continue
        rows.append({
            "document_number": number,
            "stable_id": sid,
            "title": doc.get("title"),
            "series": doc.get("source_series"),
            "version": doc.get("version"),
            "pdf_sha256": doc.get("pdf_sha256"),
        })
        by_number.setdefault(number, []).append(sid)

    collisions = {n: sids for n, sids in by_number.items() if len(sids) > 1}
    return {"rows": rows, "collisions": collisions, "missing_number": missing}


def resolution_map(alias: dict) -> dict[str, str]:
    """document_number → stable_id for UNAMBIGUOUS numbers only; a number
    claimed by two documents resolves to neither (fail closed, reported
    via ``collisions``)."""
    return {
        row["document_number"]: row["stable_id"]
        for row in alias["rows"]
        if row["document_number"] not in alias["collisions"]
    }


def alias_table_json(alias: dict, *, snapshot_id: str) -> str:
    """Canonical serialization of the committable alias table."""
    return canonical_json({
        "kind": "karyameshgraph-alias-table",
        "snapshot_id": snapshot_id,
        "rows": alias["rows"],
        "collisions": alias["collisions"],
        "missing_number": alias["missing_number"],
    })
