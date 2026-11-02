"""Graph data contracts for M1.5 (SPEC.md §7.5).

Nodes and edges are plain dicts with content-derived identities so a repeat
build from the same snapshot produces the same graph byte for byte. Every
edge carries a derivation label (EXPLICIT / INFERRED / AMBIGUOUS) and the
verification level of its evidence spans. At M1.5 the only claimable level
is CHUNK_VERIFIED (SPEC.md §5.5): spans resolve into hash-verified chunk
text, not into reconstructed source elements.

Evidence spans are (chunk_uid, start, end) character offsets into the
snapshot's verbatim chunk text. Builders must validate offsets at creation
time; `graph verify` re-resolves them independently from on-disk bytes.
"""

from __future__ import annotations

import hashlib

from ..contracts.models import canonical_json
from ..errors import MalformedRecordError

# 2.0.0: graph_id is the hash of the whole manifest core (binding every
# artifact, incl. alias-table.json and exclusions.json); snapshot_path
# removed from the manifest. structural-2: element-boundary chunk join,
# cross-chunk role lookahead and definition continuation, tightened role
# names, list lead-ins rejected as terms, faithful term normalization,
# text-free noisy-title exclusions (2026-11-01 review fixes).
GRAPH_SCHEMA_VERSION = "2.0.0"
BUILDER_VERSION = "m1.5-structural-2"

DERIVATION_EXPLICIT = "EXPLICIT"
DERIVATION_INFERRED = "INFERRED"
DERIVATION_AMBIGUOUS = "AMBIGUOUS"
DERIVATIONS = (DERIVATION_EXPLICIT, DERIVATION_INFERRED, DERIVATION_AMBIGUOUS)

LEVEL_CHUNK_VERIFIED = "CHUNK_VERIFIED"

NODE_KINDS = (
    "Document",
    "Section",
    "Role",
    "Duty",
    "Term",
    "RevisionRow",
    "ExternalStandard",
)
EDGE_KINDS = (
    "HAS_SECTION",
    "RESPONSIBLE_FOR",
    "DEFINED_IN",
    "HAS_REVISION",
    "CITES",
)


def evidence_span(chunk_uid: str, start: int, end: int, *, page: int | None = None,
                  text_len: int | None = None) -> dict:
    """A span into verified chunk text. Offsets are validated here so a
    builder bug cannot emit an unresolvable span; `text_len` is the length
    of the chunk's text when the caller has it in hand."""
    if not isinstance(chunk_uid, str) or "#" not in chunk_uid:
        raise MalformedRecordError("evidence span requires a chunk_uid", value=str(chunk_uid)[:64])
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end):
        raise MalformedRecordError("evidence span offsets invalid",
                                   chunk_uid=chunk_uid, start=start, end=end)
    if text_len is not None and end > text_len:
        raise MalformedRecordError("evidence span exceeds chunk text",
                                   chunk_uid=chunk_uid, end=end, text_len=text_len)
    span = {"chunk_uid": chunk_uid, "start": start, "end": end}
    if page is not None:
        span["page"] = page
    return span


def node(kind: str, node_id: str, **attrs) -> dict:
    if kind not in NODE_KINDS:
        raise MalformedRecordError("unknown node kind", kind=kind)
    if not node_id or not isinstance(node_id, str):
        raise MalformedRecordError("node id must be a non-empty string", kind=kind)
    rec = {"id": node_id, "kind": kind}
    rec.update(attrs)
    return rec


def edge(kind: str, src: str, dst: str, *, derivation: str,
         evidence: list[dict], **attrs) -> dict:
    """Build an edge with a content-derived edge_id. `evidence` may be empty
    only for AMBIGUOUS edges (where the point is that the source does not
    establish the relationship); EXPLICIT and INFERRED edges must cite spans."""
    if kind not in EDGE_KINDS:
        raise MalformedRecordError("unknown edge kind", kind=kind)
    if derivation not in DERIVATIONS:
        raise MalformedRecordError("unknown derivation label", derivation=derivation)
    if derivation != DERIVATION_AMBIGUOUS and not evidence:
        raise MalformedRecordError(
            "non-ambiguous edge requires evidence spans", kind=kind, src=src, dst=dst)
    rec = {
        "kind": kind,
        "src": src,
        "dst": dst,
        "derivation": derivation,
        "verification_level": LEVEL_CHUNK_VERIFIED,
        "evidence": evidence,
    }
    rec.update(attrs)
    core = canonical_json({k: v for k, v in rec.items() if k != "edge_id"})
    rec["edge_id"] = "e-" + hashlib.sha256(core.encode("utf-8")).hexdigest()[:24]
    return rec


def graph_identity(manifest: dict) -> str:
    """Content-derived graph id: the hash of the canonical manifest with
    ``graph_id`` removed. The manifest pins the SHA-256 of every artifact
    (nodes, edges, alias table, exclusions) plus the snapshot id, versions
    and summary stats, so ANY change to any graph file or manifest field
    yields a different id. Stable across repeat builds from the same
    snapshot: no timestamps or machine paths are stored anywhere."""
    core = canonical_json({k: v for k, v in manifest.items() if k != "graph_id"})
    return "kgraph-" + hashlib.sha256(core.encode("utf-8")).hexdigest()
