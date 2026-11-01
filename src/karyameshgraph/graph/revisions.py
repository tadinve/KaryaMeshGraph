"""Deliverable 4 — revision rows from change-history tables (SPEC.md §7.5).

Primary tables only: family A 'Document History' (version|status|date|
author|approver) and family B 'CHANGE CONTROL TABLE' (date|version|
description). The deterministic clean/scrambled rule — zero parseable
rows in the joined section text means the extractor exploded the table —
reproduces the spec's 36/14 split exactly on the real corpus; scrambled
documents are flagged ``degraded_table`` and deferred to the full element
layer, never guessed at. upstream ``review_flags`` is empty on every
revision chunk, so degradation MUST be detected from text.

Rows are parsed over section-JOINED text because description cells spill
across chunk boundaries without any upstream marker (3 real docs); a
row's region runs to the next row match, stopping early at footnote lines
or a differently-shaped piped table (section bleed, e.g. CRS-SOP-024's
unrelated ERKS table under the same section title). Version/status/date
are stored as short identifiers; descriptions stay evidence spans only.
"""

from __future__ import annotations

import hashlib
import re

from .common import FAMILY_A, REV_TITLES, joined_section, lines_with_offsets, top_title
from .models import DERIVATION_EXPLICIT, edge, node

_ROW_A = re.compile(
    r"(?m)^(?P<version>\d+(?:\.\d+)?)\s*\|\s*(?P<status>[DIR])\s*\|\s*"
    r"(?P<date>[^|\n]+?)\s*\|\s*(?P<author>[^|\n]+?)\s*\|\s*"
    r"(?P<approver>[^|\n]+?)\s*\|?\s*$")
_ROW_B = re.compile(
    r"(?m)^(?P<date>\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*\|\s*"
    r"(?P<version>Rev(?:ision)?\.?\s*\d+|R\.\s*\d+|Initial|[Nn]/[Aa]|\d+)"
    r"\s*\|\s*(?P<desc>\S.*)$")
_FOOTNOTE_LINE = re.compile(r"^\d{1,2}\s+\S")


def _row_end(text: str, row_match_end: int, hard_stop: int, row_rx) -> int:
    """A row's region absorbs continuation lines up to the next row match,
    stopping early at a footnote line or a foreign piped table line."""
    end = row_match_end
    for line, off in lines_with_offsets(text[row_match_end:hard_stop]):
        s = line.strip()
        if not s:
            continue
        if _FOOTNOTE_LINE.match(s):
            break
        if s.count("|") >= 2 and not row_rx.match(s):
            break
        end = row_match_end + off + len(line)
    return min(end, hard_stop)


def build_revisions(docs_ctx: dict[str, dict]) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    exclusions: list[dict] = []
    stats = {
        "documents_total": len(docs_ctx),
        "documents_with_rev_section": 0,
        "clean_docs": [],
        "scrambled_docs": [],
        "rows_total": 0,
    }

    for sid in sorted(docs_ctx):
        ctx = docs_ctx[sid]
        family, chunks = ctx["family"], ctx["chunks"]
        if family is None:
            continue
        title = REV_TITLES[family]
        js = joined_section(chunks, lambda rec: top_title(rec) == title)
        if js is None:
            continue
        stats["documents_with_rev_section"] += 1
        row_rx = _ROW_A if family == FAMILY_A else _ROW_B
        matches = list(row_rx.finditer(js.text))
        if not matches:
            stats["scrambled_docs"].append(sid)
            exclusions.append({
                "stable_id": sid, "deliverable": "revisions",
                "flag": "degraded_table",
                "reason": "zero parseable rows in primary history table; "
                          "deferred to the full element layer",
            })
            continue
        stats["clean_docs"].append(sid)
        for i, m in enumerate(matches):
            hard_stop = matches[i + 1].start() if i + 1 < len(matches) else len(js.text)
            end = _row_end(js.text, m.end(), hard_stop, row_rx)
            row_key = hashlib.sha256(
                f"{sid}:{m.start()}:{m.group(0)[:80]}".encode("utf-8")
            ).hexdigest()[:12]
            row_id = f"{sid}/rev/{row_key}"
            attrs = {
                "document": sid,
                "version": m.group("version").strip(),
                "date": m.group("date").strip(),
                "row_order": i,
            }
            if family == FAMILY_A:
                attrs["status"] = m.group("status")
            nodes.append(node("RevisionRow", row_id, **attrs))
            edges.append(edge(
                "HAS_REVISION", sid, row_id,
                derivation=DERIVATION_EXPLICIT,
                evidence=js.spans(m.start(), end),
            ))
            stats["rows_total"] += 1

    return {"nodes": nodes, "edges": edges, "stats": stats,
            "exclusions": {"revisions": exclusions}}
