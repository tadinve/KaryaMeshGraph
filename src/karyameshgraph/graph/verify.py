"""Independent M1.5 graph verifier.

Re-verifies from on-disk bytes only — never trusts builder-reported
fields: the snapshot is re-run through the snapshot verifier, every
artifact hash is recomputed, the graph identity is recomputed from the
stored nodes/edges, every edge id is recomputed from its content, and
EVERY evidence span is re-resolved against the snapshot's verified chunk
text (full re-resolution, which subsumes the exit gate's "spot-verified").
Malformed shapes become reported failures, not crashes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..contracts.models import canonical_json
from ..ingest.verify import verify_snapshot
from .models import (DERIVATIONS, DERIVATION_AMBIGUOUS, EDGE_KINDS,
                     GRAPH_SCHEMA_VERSION, NODE_KINDS, graph_identity)

_ARTIFACTS = ("nodes.jsonl", "edges.jsonl", "alias-table.json",
              "exclusions.json")


def _load_jsonl(path: Path, failures: list, label: str) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        failures.append(f"{label}: file missing")
        return out
    with path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                failures.append(f"{label}: line {i} is not valid JSON")
                continue
            if not isinstance(rec, dict):
                failures.append(f"{label}: line {i} is not an object")
                continue
            out.append(rec)
    return out


def verify_graph(graph_dir: Path, snapshot_dir: Path) -> dict:
    graph_dir, snapshot_dir = Path(graph_dir), Path(snapshot_dir)
    failures: list[str] = []
    report: dict = {"graph": str(graph_dir), "snapshot": str(snapshot_dir),
                    "failures": failures, "ok": False}

    # 1. Snapshot must independently verify — the graph's claims are
    # meaningless over a tampered or missing snapshot.
    snap_report = verify_snapshot(snapshot_dir)
    report["snapshot_ok"] = bool(snap_report.get("ok"))
    if not snap_report.get("ok"):
        failures.append("snapshot failed independent verification")
        return report

    mpath = graph_dir / "graph-manifest.json"
    if not mpath.is_file():
        failures.append("graph-manifest.json missing")
        return report
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        failures.append("graph-manifest.json is not valid JSON")
        return report
    if not isinstance(manifest, dict):
        failures.append("graph manifest is not an object")
        return report
    report["graph_id"] = manifest.get("graph_id")
    if manifest.get("schema_version") != GRAPH_SCHEMA_VERSION:
        failures.append(
            f"schema_version mismatch: {manifest.get('schema_version')!r}")

    # 2. Artifact hashes recomputed from bytes.
    arts = manifest.get("artifacts")
    if not isinstance(arts, dict):
        failures.append("manifest artifacts is not an object")
        arts = {}
    for name in _ARTIFACTS:
        declared = arts.get(name)
        p = graph_dir / name
        if not isinstance(declared, dict):
            failures.append(f"artifact {name}: not declared in manifest")
            continue
        if not p.is_file():
            failures.append(f"artifact {name}: file missing")
            continue
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != declared.get("sha256"):
            failures.append(f"artifact {name}: sha256 mismatch")
        if len(data) != declared.get("bytes"):
            failures.append(f"artifact {name}: size mismatch")
    extra = sorted(
        p.name for p in graph_dir.iterdir()
        if p.name not in _ARTIFACTS + ("graph-manifest.json",))
    if extra:
        failures.append(f"undeclared files in graph dir: {extra}")

    nodes = _load_jsonl(graph_dir / "nodes.jsonl", failures, "nodes.jsonl")
    edges = _load_jsonl(graph_dir / "edges.jsonl", failures, "edges.jsonl")
    report["node_count"] = len(nodes)
    report["edge_count"] = len(edges)
    if manifest.get("node_count") != len(nodes):
        failures.append("node_count mismatch between manifest and nodes.jsonl")
    if manifest.get("edge_count") != len(edges):
        failures.append("edge_count mismatch between manifest and edges.jsonl")

    node_ids: set[str] = set()
    for n in nodes:
        nid = n.get("id")
        if not isinstance(nid, str) or n.get("kind") not in NODE_KINDS:
            failures.append(f"malformed node record: {str(nid)[:80]!r}")
            continue
        if nid in node_ids:
            failures.append(f"duplicate node id: {nid[:80]}")
        node_ids.add(nid)

    # 3. Chunk text inventory from the verified snapshot for span
    # re-resolution (uid -> text length; text bytes already hash-verified).
    chunk_len: dict[str, int] = {}
    for cpath in sorted((snapshot_dir / "chunks").glob("*.jsonl")):
        with cpath.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    chunk_len[rec["chunk_uid"]] = len(rec["text"])

    spans_checked = 0
    for e in edges:
        eid = e.get("edge_id")
        if (e.get("kind") not in EDGE_KINDS
                or e.get("derivation") not in DERIVATIONS
                or not isinstance(e.get("evidence"), list)
                or not isinstance(eid, str)):
            failures.append(f"malformed edge record: {str(eid)[:80]!r}")
            continue
        core = canonical_json({k: v for k, v in e.items() if k != "edge_id"})
        if "e-" + hashlib.sha256(core.encode("utf-8")).hexdigest()[:24] != eid:
            failures.append(f"edge_id does not match edge content: {eid[:40]}")
        if e.get("src") not in node_ids or e.get("dst") not in node_ids:
            failures.append(f"edge references unknown node: {eid[:40]}")
        if e["derivation"] != DERIVATION_AMBIGUOUS and not e["evidence"]:
            failures.append(f"non-ambiguous edge without evidence: {eid[:40]}")
        for span in e["evidence"]:
            if not isinstance(span, dict):
                failures.append(f"non-object evidence span on {eid[:40]}")
                continue
            uid, start, end = span.get("chunk_uid"), span.get("start"), span.get("end")
            tlen = chunk_len.get(uid)
            if tlen is None:
                failures.append(f"span chunk_uid not in snapshot: {str(uid)[:80]}")
            elif not (isinstance(start, int) and isinstance(end, int)
                      and 0 <= start < end <= tlen):
                failures.append(
                    f"span does not resolve into chunk text: {str(uid)[:80]} "
                    f"[{start}:{end}] len={tlen}")
            else:
                spans_checked += 1
    report["evidence_spans_verified"] = spans_checked

    # 4. Graph identity recomputed from stored content.
    snap_manifest = json.loads(
        (snapshot_dir / "snapshot-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("snapshot_id") != snap_manifest.get("snapshot_id"):
        failures.append("graph manifest snapshot_id does not match snapshot")
    recomputed = graph_identity(
        snapshot_id=str(manifest.get("snapshot_id")), nodes=nodes, edges=edges)
    if recomputed != manifest.get("graph_id"):
        failures.append("graph_id does not match recomputed content identity")

    report["ok"] = not failures
    return report


def format_report(report: dict) -> str:
    lines = [f"graph verify: {report['graph']}"]
    if report.get("graph_id"):
        lines.append(f"  graph_id: {report['graph_id']}")
    lines.append(f"  snapshot ok: {report.get('snapshot_ok')}")
    lines.append(
        f"  nodes: {report.get('node_count', 0)}  edges: {report.get('edge_count', 0)}  "
        f"evidence spans verified: {report.get('evidence_spans_verified', 0)}")
    if report["failures"]:
        lines.append(f"  FAILURES ({len(report['failures'])}):")
        lines.extend(f"    - {f}" for f in report["failures"][:20])
        if len(report["failures"]) > 20:
            lines.append(f"    ... and {len(report['failures']) - 20} more")
    lines.append(f"  verdict: {'OK' if report['ok'] else 'FAILED'}")
    return "\n".join(lines)
