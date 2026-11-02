"""Deliverable 1 — section skeleton (SPEC.md §7.5).

One Section node per (document, canonical top-level title); titles outside
the family canon are EXCLUDED from the skeleton and reported, never
silently kept (the verified corpus has 97 noisy family-B titles and 11
family-A ones). Depth-0 chunks (empty section_path_titles — five in the
real corpus, always index 1) land in an explicit FRONT_MATTER bucket.
Documents whose family cannot be determined from source_series get NO
skeleton (fail closed) and are reported.

Noisy titles are frequently not headings at all but wrapped sentence
fragments or table cells promoted by the extractor, so their exclusion
records carry a title hash and chunk locations — never the text itself;
the excluded title is recoverable from the snapshot by chunk_uid.
"""

from __future__ import annotations

import hashlib

from .common import CANONS, FRONT_MATTER, slug, top_title
from .models import DERIVATION_EXPLICIT, edge, evidence_span, node


def build_sections(docs_ctx: dict[str, dict]) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    noisy: list[dict] = []
    unknown_family_docs: list[str] = []
    front_matter_chunks = 0
    docs_with_skeleton = 0

    for sid in sorted(docs_ctx):
        ctx = docs_ctx[sid]
        family, chunks = ctx["family"], ctx["chunks"]
        if family is None:
            unknown_family_docs.append(sid)
            continue
        canon = CANONS[family]

        # First-occurrence-ordered map: normalized title -> details.
        seen: dict[str, dict] = {}
        for rec in chunks:
            title = top_title(rec)
            key = title if title is not None else FRONT_MATTER
            if title is None:
                front_matter_chunks += 1
            entry = seen.setdefault(key, {
                "raw_titles": set(),
                "first_rec": rec,
                "chunk_indices": [],
                "chunk_uids": [],
            })
            raw = (rec.get("section_path_titles") or [None])[0]
            if raw is not None:
                entry["raw_titles"].add(raw)
            entry["chunk_indices"].append(rec["index"])
            entry["chunk_uids"].append(rec["chunk_uid"])

        order = 0
        emitted = False
        for key, entry in seen.items():  # insertion order = document order
            is_front = key == FRONT_MATTER
            if not is_front and key not in canon:
                noisy.append({
                    "stable_id": sid,
                    "family": family,
                    "title_sha256": hashlib.sha256(key.encode("utf-8")).hexdigest(),
                    "raw_title_variants": len(entry["raw_titles"]),
                    "chunk_indices": entry["chunk_indices"],
                    "chunk_uids": entry["chunk_uids"],
                })
                continue
            first = entry["first_rec"]
            section_id = f"{sid}#sec/{slug(key)}"
            nodes.append(node(
                "Section", section_id,
                document=sid,
                title=key,
                raw_titles=sorted(entry["raw_titles"]),
                canonical=not is_front,
                front_matter=is_front,
                order=order,
                chunk_count=len(entry["chunk_indices"]),
                first_chunk_index=entry["chunk_indices"][0],
            ))
            pages = first.get("pages")
            edges.append(edge(
                "HAS_SECTION", sid, section_id,
                derivation=DERIVATION_EXPLICIT,
                evidence=[evidence_span(
                    first["chunk_uid"], 0, len(first["text"]),
                    page=pages[0] if isinstance(pages, list) and pages else None,
                    text_len=len(first["text"]),
                )],
            ))
            order += 1
            emitted = True
        if emitted:
            docs_with_skeleton += 1

    stats = {
        "documents_total": len(docs_ctx),
        "documents_with_skeleton": docs_with_skeleton,
        "section_nodes": len(nodes),
        "noisy_titles_excluded": len(noisy),
        "front_matter_chunks": front_matter_chunks,
        "unknown_family_docs": unknown_family_docs,
    }
    return {"nodes": nodes, "edges": edges, "stats": stats,
            "exclusions": {"noisy_titles": noisy,
                           "unknown_family_docs": unknown_family_docs}}
