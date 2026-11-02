"""M1.5 graph build orchestrator.

Mirrors the snapshot builder's safety contract: refuse an existing output,
build into a temporary directory, run the INDEPENDENT graph verifier on
the temporary tree, and only then rename into place; on verification
failure the evidence lands in ``<out>.failed.json`` and nothing appears at
the output path. The graph manifest carries no timestamps and no machine
paths, so repeat builds from the same snapshot are byte-identical with the
same graph_id regardless of how the snapshot path is spelled.

``compute_graph`` is the pure part (snapshot contents in, artifact bytes
and manifest out); ``graph verify`` calls it to check that a graph
reproduces exactly from its snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from ..contracts.models import canonical_json
from ..errors import SnapshotStateError
from .alias import alias_table_json, build_alias_table
from .citations import build_citations
from .common import family_for_series
from .input import load_verified_snapshot
from .models import (BUILDER_VERSION, GRAPH_SCHEMA_VERSION, LEVEL_CHUNK_VERIFIED,
                     graph_identity, node)
from .revisions import build_revisions
from .roles import build_roles
from .sections import build_sections
from .terms import build_terms

ARTIFACT_FILES = ("nodes.jsonl", "edges.jsonl", "alias-table.json",
                  "exclusions.json")
MANIFEST_FILE = "graph-manifest.json"
GRAPH_KIND = "karyameshgraph-structural-graph"
ALIAS_KIND = "karyameshgraph-alias-table"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_graph(snap: dict) -> tuple[dict[str, bytes], dict]:
    """Snapshot contents (from ``load_snapshot_contents``) → (artifact
    bytes by file name, manifest dict incl. graph_id). Deterministic."""
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
    for result in deliverables.values():
        nodes.extend(result["nodes"])
        edges.extend(result["edges"])
        exclusions.update(result["exclusions"])

    node_ids: set[str] = set()
    for n in nodes:
        if n["id"] in node_ids:
            raise SnapshotStateError("duplicate node id in built graph",
                                     node_id=n["id"])
        node_ids.add(n["id"])
    for e in edges:
        if e["src"] not in node_ids or e["dst"] not in node_ids:
            raise SnapshotStateError("edge references unknown node",
                                     edge_id=e["edge_id"],
                                     src=e["src"], dst=e["dst"])

    nodes.sort(key=lambda n: (n["kind"], n["id"]))
    edges.sort(key=lambda e: (e["kind"], e["src"], e["dst"], e["edge_id"]))
    snapshot_id = snap["manifest"]["snapshot_id"]
    alias = build_alias_table(documents)

    derivations = {"EXPLICIT": 0, "INFERRED": 0, "AMBIGUOUS": 0}
    edge_kinds: dict[str, int] = {}
    for e in edges:
        derivations[e["derivation"]] += 1
        edge_kinds[e["kind"]] = edge_kinds.get(e["kind"], 0) + 1
    node_kinds: dict[str, int] = {}
    for n in nodes:
        node_kinds[n["kind"]] = node_kinds.get(n["kind"], 0) + 1

    payloads = {
        "nodes.jsonl": ("".join(canonical_json(n) + "\n" for n in nodes)).encode("utf-8"),
        "edges.jsonl": ("".join(canonical_json(e) + "\n" for e in edges)).encode("utf-8"),
        "alias-table.json": (alias_table_json(alias, snapshot_id=snapshot_id) + "\n").encode("utf-8"),
        "exclusions.json": (canonical_json(exclusions) + "\n").encode("utf-8"),
    }
    manifest = {
        "kind": GRAPH_KIND,
        "schema_version": GRAPH_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "snapshot_id": snapshot_id,
        "document_count": len(documents),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_kinds": node_kinds,
        "edge_kinds": edge_kinds,
        "derivations": derivations,
        "verification_level_ceiling": LEVEL_CHUNK_VERIFIED,
        "deliverable_stats": {k: v["stats"] for k, v in deliverables.items()},
        "index_gaps": snap["index_gaps"],
        "artifacts": {name: {"sha256": _sha256_bytes(data), "bytes": len(data)}
                      for name, data in payloads.items()},
    }
    manifest["graph_id"] = graph_identity(manifest)
    return payloads, manifest


def manifest_bytes(manifest: dict) -> bytes:
    return (canonical_json(manifest) + "\n").encode("utf-8")


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _check_alias_target(alias_out: Path, *, out_dir: Path, tmp: Path,
                        failed: Path) -> None:
    """Refuse to overwrite anything but a standalone previous alias table
    (the committed evidence copy is meant to be refreshed in place). A
    path inside the graph being built, its temporary tree or failure
    report, or inside any published graph tree (whose alias-table.json is
    hash-pinned by that graph's manifest), is refused before any output is
    created."""
    target = alias_out.resolve()
    for own in (out_dir, tmp, failed):
        if _within(target, own.resolve()):
            raise SnapshotStateError(
                "alias output path collides with this build's own output",
                path=str(alias_out), out=str(out_dir))
    for d in (target.parent, *target.parent.parents):
        try:
            in_graph = (d / MANIFEST_FILE).is_file()
        except OSError:
            raise SnapshotStateError(
                "cannot inspect alias output path's ancestors; refusing",
                path=str(alias_out), dir=str(d)) from None
        if in_graph:
            raise SnapshotStateError(
                "alias output path is inside a published graph tree; its "
                "alias-table.json is pinned by that graph's manifest",
                path=str(alias_out), graph=str(d))
    if not alias_out.exists():
        return
    try:
        existing = json.loads(alias_out.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        existing = None
    if not (isinstance(existing, dict) and existing.get("kind") == ALIAS_KIND):
        raise SnapshotStateError(
            "alias output path exists and is not a KaryaMeshGraph alias "
            "table; refusing to overwrite", path=str(alias_out))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}"
    tmp.write_bytes(data)
    os.replace(tmp, path)


def build_graph(snapshot_dir: Path, out_dir: Path, *,
                expected_documents: int | None = None,
                alias_out: Path | None = None) -> dict:
    snapshot_dir, out_dir = Path(snapshot_dir), Path(out_dir)
    if out_dir.exists():
        raise SnapshotStateError("graph output path already exists; refusing "
                                 "to overwrite", out=str(out_dir))
    # Literal prefix match, not a glob: '[', ']', '*', '?' in the output
    # name must not act as wildcards.
    prefix = f".{out_dir.name}.tmp-"
    orphans = sorted(
        str(p) for p in (out_dir.parent.iterdir() if out_dir.parent.is_dir() else ())
        if p.name.startswith(prefix))
    if orphans:
        raise SnapshotStateError(
            "orphaned temporary graph tree from an interrupted build; inspect "
            "and delete it before rebuilding", paths=orphans)
    failed = out_dir.parent / f"{out_dir.name}.failed.json"
    tmp = out_dir.parent / f"{prefix}{os.getpid()}"
    if alias_out is not None:
        alias_out = Path(alias_out)
        _check_alias_target(alias_out, out_dir=out_dir, tmp=tmp, failed=failed)

    snap = load_verified_snapshot(snapshot_dir,
                                  expected_documents=expected_documents)
    payloads, manifest = compute_graph(snap)

    tmp.mkdir(parents=True)
    try:
        for name, data in payloads.items():
            (tmp / name).write_bytes(data)
        (tmp / MANIFEST_FILE).write_bytes(manifest_bytes(manifest))

        from .verify import verify_graph  # late import avoids a cycle
        report = verify_graph(tmp, snapshot_dir)
        if not report["ok"]:
            failed.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
            raise SnapshotStateError(
                "graph failed internal verification before publish",
                failures=len(report["failures"]), report=str(failed))
        os.rename(tmp, out_dir)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    # A failure report from an earlier attempt must not outlive a success.
    failed.unlink(missing_ok=True)

    if alias_out is not None:
        try:
            _atomic_write(alias_out, payloads["alias-table.json"])
        except OSError as e:
            raise SnapshotStateError(
                "graph was published and verified, but writing the alias "
                "table copy failed", out=str(out_dir), path=str(alias_out),
                reason=type(e).__name__) from None
    return manifest
