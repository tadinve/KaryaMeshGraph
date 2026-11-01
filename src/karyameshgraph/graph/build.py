"""M1.5 graph build orchestrator.

Mirrors the snapshot builder's safety contract: refuse an existing output,
build into a temporary directory, run the INDEPENDENT graph verifier on
the temporary tree, and only then rename into place; on verification
failure the evidence lands in ``<out>.failed.json`` and nothing appears at
the output path. The graph manifest carries no timestamps, so repeat
builds from the same snapshot are byte-identical with the same graph_id.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..contracts.models import canonical_json
from ..errors import SnapshotStateError
from .alias import alias_table_json, build_alias_table
from .citations import build_citations
from .common import family_for_series
from .input import load_verified_snapshot
from .models import BUILDER_VERSION, GRAPH_SCHEMA_VERSION, graph_identity, node
from .revisions import build_revisions
from .roles import build_roles
from .sections import build_sections
from .terms import build_terms

ARTIFACT_FILES = ("nodes.jsonl", "edges.jsonl", "alias-table.json",
                  "exclusions.json")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_graph(snapshot_dir: Path, out_dir: Path, *,
                expected_documents: int | None = None,
                alias_out: Path | None = None) -> dict:
    snapshot_dir, out_dir = Path(snapshot_dir), Path(out_dir)
    if out_dir.exists():
        raise SnapshotStateError("graph output path already exists; refusing "
                                 "to overwrite", out=str(out_dir))

    snap = load_verified_snapshot(snapshot_dir,
                                  expected_documents=expected_documents)
    documents, chunks = snap["documents"], snap["chunks"]

    docs_ctx = {
        sid: {
            "family": family_for_series(documents[sid].get("source_series")),
            "chunks": chunks[sid],
        }
        for sid in sorted(documents)
    }

    # Document nodes from verified DocumentVersion records (identifiers only).
    nodes: list[dict] = [
        node("Document", sid,
             title=documents[sid].get("title"),
             source_document_number=documents[sid].get("source_document_number"),
             source_series=documents[sid].get("source_series"),
             family=docs_ctx[sid]["family"],
             version=documents[sid].get("version"),
             pdf_sha256=documents[sid].get("pdf_sha256"),
             provenance_capability=documents[sid].get("provenance_capability"),
             chunk_count=len(chunks[sid]))
        for sid in sorted(documents)
    ]
    edges: list[dict] = []
    deliverables = {
        "sections": build_sections(docs_ctx),
        "roles": build_roles(docs_ctx),
        "terms": build_terms(docs_ctx),
        "revisions": build_revisions(docs_ctx),
        "citations": build_citations(docs_ctx, documents),
    }
    exclusions: dict = {}
    for name, result in deliverables.items():
        nodes.extend(result["nodes"])
        edges.extend(result["edges"])
        exclusions.update(result["exclusions"])

    seen_nodes: set[str] = set()
    for n in nodes:
        if n["id"] in seen_nodes:
            raise SnapshotStateError("duplicate node id in built graph",
                                     node_id=n["id"])
        seen_nodes.add(n["id"])
    node_ids = seen_nodes
    for e in edges:
        if e["src"] not in node_ids or e["dst"] not in node_ids:
            raise SnapshotStateError("edge references unknown node",
                                     edge_id=e["edge_id"],
                                     src=e["src"], dst=e["dst"])

    nodes.sort(key=lambda n: (n["kind"], n["id"]))
    edges.sort(key=lambda e: (e["kind"], e["src"], e["dst"], e["edge_id"]))
    snapshot_id = snap["manifest"]["snapshot_id"]
    graph_id = graph_identity(snapshot_id=snapshot_id, nodes=nodes, edges=edges)
    alias = build_alias_table(documents)

    derivations = {"EXPLICIT": 0, "INFERRED": 0, "AMBIGUOUS": 0}
    for e in edges:
        derivations[e["derivation"]] += 1
    node_kinds: dict[str, int] = {}
    for n in nodes:
        node_kinds[n["kind"]] = node_kinds.get(n["kind"], 0) + 1
    edge_kinds: dict[str, int] = {}
    for e in edges:
        edge_kinds[e["kind"]] = edge_kinds.get(e["kind"], 0) + 1

    payloads = {
        "nodes.jsonl": ("".join(canonical_json(n) + "\n" for n in nodes)).encode("utf-8"),
        "edges.jsonl": ("".join(canonical_json(e) + "\n" for e in edges)).encode("utf-8"),
        "alias-table.json": (alias_table_json(alias, snapshot_id=snapshot_id) + "\n").encode("utf-8"),
        "exclusions.json": (canonical_json(exclusions) + "\n").encode("utf-8"),
    }
    manifest = {
        "kind": "karyameshgraph-structural-graph",
        "schema_version": GRAPH_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "graph_id": graph_id,
        "snapshot_id": snapshot_id,
        "snapshot_path": str(snapshot_dir),
        "document_count": len(documents),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_kinds": node_kinds,
        "edge_kinds": edge_kinds,
        "derivations": derivations,
        "verification_level_ceiling": "CHUNK_VERIFIED",
        "deliverable_stats": {k: v["stats"] for k, v in deliverables.items()},
        "index_gaps": snap["index_gaps"],
        "artifacts": {name: {"sha256": _sha256_bytes(data), "bytes": len(data)}
                      for name, data in payloads.items()},
    }

    tmp = out_dir.parent / f".{out_dir.name}.tmp-{os.getpid()}"
    if tmp.exists():
        raise SnapshotStateError("temporary graph dir already exists",
                                 path=str(tmp))
    tmp.mkdir(parents=True)
    try:
        for name, data in payloads.items():
            (tmp / name).write_bytes(data)
        (tmp / "graph-manifest.json").write_text(
            canonical_json(manifest) + "\n", encoding="utf-8")

        from .verify import verify_graph  # late import avoids a cycle
        report = verify_graph(tmp, snapshot_dir)
        if not report["ok"]:
            failed = out_dir.parent / f"{out_dir.name}.failed.json"
            failed.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
            raise SnapshotStateError(
                "graph failed internal verification before publish",
                failures=len(report["failures"]), report=str(failed))
        os.rename(tmp, out_dir)
    finally:
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    if alias_out is not None:
        alias_out = Path(alias_out)
        alias_out.parent.mkdir(parents=True, exist_ok=True)
        alias_out.write_bytes(payloads["alias-table.json"])
    return manifest
