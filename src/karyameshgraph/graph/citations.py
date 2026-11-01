"""Deliverable 5 — citation detection edges (SPEC.md §7.5).

In-corpus document-number citations resolve through the alias table —
resolution is interpretation, so those CITES edges are INFERRED; the
citation string itself is literal text, so edges to external targets are
EXPLICIT. Self-citations (a doc citing its own number — five genuine
body-text cases in the real corpus) are dropped: no self-loops. Numbers
that resolve to nothing in the corpus become external-document nodes —
37% of in-corpus-style numbers point outside the 50 docs and must dangle
honestly rather than disappear.

Matching is greedy/longest-match (ORA-LAB.5.5 is a string prefix of
ORA-LAB.5.5.1; substring lookup demonstrably produced a false self-hit).
Rev-suffix spelling drifts in text ('Rev. 1', 'Rev.1', ', Rev. 4'), so
resolution keys on the Rev-stripped base number, which is verified
collision-free before use; a colliding base resolves to NOTHING and is
reported. Running headers/footers never reach chunk text in this corpus
(verified 0/1752), so no positional exclusion is needed — the self-drop
is the defensive belt.
"""

from __future__ import annotations

import re

from .common import slug
from .models import (DERIVATION_EXPLICIT, DERIVATION_INFERRED, edge,
                     evidence_span, node)

IN_CORPUS_PATTERNS = (
    ("ORA_LAB_NUMBER", re.compile(r"ORA-LAB\.\d+(?:\.\d+)*")),
    ("MAPP_NUMBER", re.compile(r"MAPP\s+\d{4}\.\d+(?:,?\s*Rev\.\s*\d+)?")),
)
EXTERNAL_PATTERNS = (
    ("CFR_NUMERIC", re.compile(
        r"\b\d{1,2}\s+CFR\s+(?:[Pp]arts?\s+)?\d+(?:\.\d+)?(?:\([a-z0-9]{1,3}\))*")),
    ("CFR_SPELLED", re.compile(r"Code\s+of\s+Federal\s+Regulations")),
    ("USC", re.compile(
        r"\b\d{1,2}\s*U\.?S\.?C\.?\s*(?:§+\s*)?\d+[a-z]{0,2}(?:\([a-z0-9]{1,3}\))*")),
    ("FDCA_PHS_SECTION", re.compile(
        r"[Ss]ection\s+\d+[A-Za-z]?(?:\([a-z0-9]{1,3}\))*\s+of\s+the\s+"
        r"(?:FD&C\s+Act|Federal\s+Food,\s*Drug,?\s*and\s+Cosmetic\s+Act|"
        r"PHS\s+Act|Public\s+Health\s+Service\s+Act)")),
    ("ISO_IEC", re.compile(r"ISO(?:/IEC)?\s*\d{4,5}(?::\d{4})?")),
    ("ICH", re.compile(r"ICH\s+[QSEM]\d{1,2}\s*(?:\([A-Z]\d\))?")),
    ("GUIDANCE_FOR_INDUSTRY", re.compile(r"[Gg]uidance\s+for\s+[Ii]ndustry")),
    ("FDA_FORM", re.compile(r"(?:Form\s+FDA|FDA\s+Form)\s*[-\s]?\d{3,4}[a-z]?")),
    ("USP", re.compile(r"USP\s*(?:General\s+Chapter\s*)?<\d{1,4}>|USP\s+Chapter\s+\d+")),
    ("FEDERAL_REGISTER", re.compile(r"\b\d{1,3}\s+FR\s+\d{3,6}\b")),
    ("PUBLIC_LAW", re.compile(
        r"Pub(?:lic)?\.?\s*L(?:aw)?\.?\s*(?:No\.?\s*)?\d+[-–]\d+")),
    ("OMB_CIRCULAR", re.compile(r"OMB\s+Circular\s+[A-Z]-?\d+")),
)
_INTERNAL_MAN = re.compile(r"MAN-\d{6}")
_INTERNAL_SOP = re.compile(r"SOP-\d{6}")
_URL = re.compile(r"https?://[^\s)>\]]+")
_REV_SUFFIX = re.compile(r",?\s*Rev\.\s*\d+$")


def base_number(number: str) -> str:
    return _REV_SUFFIX.sub("", re.sub(r"\s+", " ", number.strip()))


def build_citations(docs_ctx: dict[str, dict], documents: dict[str, dict]) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    std_nodes: dict[str, dict] = {}

    # Base-number resolution map, collision-checked after Rev-stripping.
    base_map: dict[str, list[str]] = {}
    for sid, doc in documents.items():
        num = doc.get("source_document_number")
        if isinstance(num, str) and num:
            base_map.setdefault(base_number(num), []).append(sid)
    base_collisions = {b: sids for b, sids in base_map.items() if len(sids) > 1}
    resolve = {b: sids[0] for b, sids in base_map.items() if len(sids) == 1}
    own_base = {
        sid: base_number(doc["source_document_number"])
        for sid, doc in documents.items()
        if isinstance(doc.get("source_document_number"), str)
    }

    # (src, dst) -> aggregated spans/attrs; one edge per pair at the end.
    resolved_agg: dict[tuple[str, str], dict] = {}
    external_agg: dict[tuple[str, str], dict] = {}
    stats = {
        "documents_total": len(docs_ctx),
        "in_corpus_occurrences": 0,
        "resolved_occurrences": 0,
        "unresolved_occurrences": 0,
        "self_citations_dropped": 0,
        "base_number_collisions": sorted(base_collisions),
        "collision_skipped_occurrences": 0,
        "external_by_pattern": {name: 0 for name, _ in EXTERNAL_PATTERNS},
        "internal_control_numbers": {"MAN": 0, "SOP": 0},
        "url_occurrences": 0,
        "cross_family_resolved_edges": 0,
    }

    for sid in sorted(docs_ctx):
        chunks = docs_ctx[sid]["chunks"]
        for rec in chunks:
            uid, text = rec["chunk_uid"], rec["text"]
            tlen = len(text)
            for pname, rx in IN_CORPUS_PATTERNS:
                for m in rx.finditer(text):
                    stats["in_corpus_occurrences"] += 1
                    b = base_number(m.group(0))
                    if b == own_base.get(sid):
                        stats["self_citations_dropped"] += 1
                        continue
                    span = evidence_span(uid, m.start(), m.end(), text_len=tlen)
                    if b in base_collisions:
                        stats["collision_skipped_occurrences"] += 1
                        continue
                    target = resolve.get(b)
                    if target is not None and target != sid:
                        stats["resolved_occurrences"] += 1
                        agg = resolved_agg.setdefault((sid, target), {
                            "spans": [], "numbers": set()})
                        agg["spans"].append(span)
                        agg["numbers"].add(b)
                    else:
                        stats["unresolved_occurrences"] += 1
                        ext_id = f"ext/doc/{slug(b)}"
                        if ext_id not in std_nodes:
                            std_nodes[ext_id] = node(
                                "ExternalStandard", ext_id, name=b,
                                category="external_document", family=pname)
                            nodes.append(std_nodes[ext_id])
                        agg = external_agg.setdefault((sid, ext_id), {
                            "spans": [], "numbers": set()})
                        agg["spans"].append(span)
                        agg["numbers"].add(b)
            for pname, rx in EXTERNAL_PATTERNS:
                for m in rx.finditer(text):
                    stats["external_by_pattern"][pname] += 1
                    norm = re.sub(r"\s+", " ", m.group(0)).strip().rstrip(".,;").upper()
                    ext_id = f"ext/std/{slug(norm)}"
                    if ext_id not in std_nodes:
                        std_nodes[ext_id] = node(
                            "ExternalStandard", ext_id, name=norm,
                            category="standard", family=pname)
                        nodes.append(std_nodes[ext_id])
                    agg = external_agg.setdefault((sid, ext_id), {
                        "spans": [], "numbers": set()})
                    agg["spans"].append(
                        evidence_span(uid, m.start(), m.end(), text_len=tlen))
            stats["internal_control_numbers"]["MAN"] += len(_INTERNAL_MAN.findall(text))
            stats["internal_control_numbers"]["SOP"] += len(_INTERNAL_SOP.findall(text))
            stats["url_occurrences"] += len(_URL.findall(text))

    for (src, dst), agg in sorted(resolved_agg.items()):
        if docs_ctx[src]["family"] != docs_ctx[dst]["family"]:
            stats["cross_family_resolved_edges"] += 1
        edges.append(edge(
            "CITES", src, dst,
            derivation=DERIVATION_INFERRED,  # alias-resolved (SPEC.md §7.5)
            evidence=agg["spans"],
            cited_numbers=sorted(agg["numbers"]),
            occurrences=len(agg["spans"]),
        ))
    for (src, dst), agg in sorted(external_agg.items()):
        edges.append(edge(
            "CITES", src, dst,
            derivation=DERIVATION_EXPLICIT,  # literal citation string
            evidence=agg["spans"],
            occurrences=len(agg["spans"]),
        ))

    stats["resolved_edges"] = len(resolved_agg)
    stats["external_edges"] = len(external_agg)
    stats["docs_citing_in_corpus"] = len({s for s, _ in resolved_agg})
    stats["docs_cited_in_corpus"] = len({d for _, d in resolved_agg})
    return {"nodes": nodes, "edges": edges, "stats": stats, "exclusions": {}}
