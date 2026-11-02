"""Independent M1.5 graph verifier.

Re-verifies from on-disk bytes only — never trusts builder-reported
fields. Two complementary layers:

1. Independent checks that do not depend on the builder's parse logic:
   the snapshot is re-run through the snapshot verifier; every artifact
   hash is recomputed; every edge id is recomputed from its content; every
   edge must claim exactly CHUNK_VERIFIED (the only level M1.5 may claim);
   EVERY evidence span is re-resolved against the snapshot's chunk text,
   must stay inside its edge's own document and on the chunk's pages; the
   manifest's counts, kind/derivation histograms and document set are
   recomputed from the stored records; graph_id is recomputed from the
   manifest, which pins every artifact hash (alias table and exclusions
   included).
2. Reproduction: the graph is recomputed from the snapshot and must match
   byte for byte (all four artifacts and the manifest). This catches a
   graph that is internally consistent but was not produced from this
   snapshot by this builder version (e.g. a fully rehashed tamper).

Malformed shapes become reported failures, not crashes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..contracts.models import canonical_json
from ..errors import KMGError
from ..ingest.verify import verify_snapshot
from .input import load_snapshot_contents
from .models import (BUILDER_VERSION, DERIVATIONS, DERIVATION_AMBIGUOUS,
                     EDGE_KINDS, GRAPH_SCHEMA_VERSION, LEVEL_CHUNK_VERIFIED,
                     NODE_KINDS, graph_identity)

_ARTIFACTS = ("nodes.jsonl", "edges.jsonl", "alias-table.json",
              "exclusions.json")
_MANIFEST = "graph-manifest.json"
_GRAPH_KIND = "karyameshgraph-structural-graph"
# Which endpoint of each edge kind is the document whose chunks its
# evidence must come from (RESPONSIBLE_FOR carries a `document` attribute).
_DOC_END = {"HAS_SECTION": "src", "HAS_REVISION": "src", "CITES": "src",
            "DEFINED_IN": "dst"}


def _load_jsonl(path: Path, failures: list, label: str) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        failures.append(f"{label}: file missing")
        return out
    for i, raw in enumerate(path.read_bytes().split(b"\n"), 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            failures.append(f"{label}: line {i} is not valid UTF-8")
            continue
        except (ValueError, RecursionError):  # incl. JSONDecodeError
            failures.append(f"{label}: line {i} is not valid JSON")
            continue
        if not isinstance(rec, dict):
            failures.append(f"{label}: line {i} is not an object")
            continue
        out.append(rec)
    return out


def _edge_document(e: dict) -> str | None:
    if e.get("kind") == "RESPONSIBLE_FOR":
        doc = e.get("document")
    else:
        end = _DOC_END.get(e.get("kind"))
        doc = e.get(end) if end else None
    return doc if isinstance(doc, str) else None


def _histogram(records: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        v = r.get(key)
        if isinstance(v, str):
            out[v] = out.get(v, 0) + 1
    return out


def verify_graph(graph_dir: Path, snapshot_dir: Path) -> dict:
    graph_dir, snapshot_dir = Path(graph_dir), Path(snapshot_dir)
    failures: list[str] = []
    report: dict = {"graph": str(graph_dir), "snapshot": str(snapshot_dir),
                    "failures": failures, "ok": False, "reproduced": False}

    # 1. Snapshot must independently verify — the graph's claims are
    # meaningless over a tampered or missing snapshot.
    snap_report = verify_snapshot(snapshot_dir)
    report["snapshot_ok"] = bool(snap_report.get("ok"))
    if not snap_report.get("ok"):
        failures.append("snapshot failed independent verification")
        return report
    try:
        snap = load_snapshot_contents(snapshot_dir)
    except KMGError as e:
        failures.append(f"snapshot contents rejected by graph loader: {e.code}")
        return report

    mpath = graph_dir / _MANIFEST
    if not mpath.is_file():
        failures.append(f"{_MANIFEST} missing")
        return report
    manifest_raw = mpath.read_bytes()
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        failures.append(f"{_MANIFEST} is not valid JSON")
        return report
    if not isinstance(manifest, dict):
        failures.append("graph manifest is not an object")
        return report
    report["graph_id"] = manifest.get("graph_id")
    if manifest.get("kind") != _GRAPH_KIND:
        failures.append(f"manifest kind mismatch: {str(manifest.get('kind'))[:60]!r}")
    if manifest.get("schema_version") != GRAPH_SCHEMA_VERSION:
        failures.append(
            f"schema_version mismatch: {str(manifest.get('schema_version'))[:40]!r}")
    if manifest.get("builder_version") != BUILDER_VERSION:
        failures.append(
            f"builder_version {str(manifest.get('builder_version'))[:40]!r} is not "
            f"the current {BUILDER_VERSION!r}; rebuild the graph")
    if manifest.get("verification_level_ceiling") != LEVEL_CHUNK_VERIFIED:
        failures.append("verification_level_ceiling must be CHUNK_VERIFIED at M1.5")

    # 2. Artifact hashes recomputed from bytes.
    arts = manifest.get("artifacts")
    if not isinstance(arts, dict):
        failures.append("manifest artifacts is not an object")
        arts = {}
    on_disk: dict[str, bytes] = {}
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
        on_disk[name] = data
        if hashlib.sha256(data).hexdigest() != declared.get("sha256"):
            failures.append(f"artifact {name}: sha256 mismatch")
        if len(data) != declared.get("bytes"):
            failures.append(f"artifact {name}: size mismatch")
    extra = sorted(
        p.name for p in graph_dir.iterdir()
        if p.name not in _ARTIFACTS + (_MANIFEST,))
    if extra:
        failures.append(f"undeclared files in graph dir: {extra}")

    nodes = _load_jsonl(graph_dir / "nodes.jsonl", failures, "nodes.jsonl")
    edges = _load_jsonl(graph_dir / "edges.jsonl", failures, "edges.jsonl")
    report["node_count"] = len(nodes)
    report["edge_count"] = len(edges)

    # 3. Manifest summary fields recomputed from the stored records and
    # the snapshot (never trusted as written).
    if manifest.get("node_count") != len(nodes):
        failures.append("node_count mismatch between manifest and nodes.jsonl")
    if manifest.get("edge_count") != len(edges):
        failures.append("edge_count mismatch between manifest and edges.jsonl")
    if manifest.get("node_kinds") != _histogram(nodes, "kind"):
        failures.append("node_kinds histogram does not match nodes.jsonl")
    if manifest.get("edge_kinds") != _histogram(edges, "kind"):
        failures.append("edge_kinds histogram does not match edges.jsonl")
    derivations = {d: 0 for d in DERIVATIONS}
    derivations.update(_histogram(edges, "derivation"))
    if manifest.get("derivations") != derivations:
        failures.append("derivations histogram does not match edges.jsonl")
    snap_docs = set(snap["documents"])
    doc_nodes = {n["id"] for n in nodes
                 if n.get("kind") == "Document" and isinstance(n.get("id"), str)}
    if manifest.get("document_count") != len(snap_docs):
        failures.append("document_count does not match the snapshot")
    if doc_nodes != snap_docs:
        failures.append("Document nodes do not match the snapshot's documents")

    node_ids: set[str] = set()
    for n in nodes:
        nid = n.get("id")
        if not isinstance(nid, str) or n.get("kind") not in NODE_KINDS:
            failures.append(f"malformed node record: {str(nid)[:80]!r}")
            continue
        if nid in node_ids:
            failures.append(f"duplicate node id: {nid[:80]}")
        node_ids.add(nid)

    # 4. Edges: content ids, the only claimable verification level, and
    # every evidence span re-resolved against the snapshot.
    chunk_info: dict[str, tuple[str, int, list]] = {
        rec["chunk_uid"]: (sid, len(rec["text"]), rec.get("pages") or [])
        for sid, recs in snap["chunks"].items() for rec in recs
    }
    spans_checked = 0
    for e in edges:
        eid = e.get("edge_id")
        if (e.get("kind") not in EDGE_KINDS
                or e.get("derivation") not in DERIVATIONS
                or not isinstance(e.get("evidence"), list)
                or not isinstance(eid, str)
                or not isinstance(e.get("src"), str)
                or not isinstance(e.get("dst"), str)):
            failures.append(f"malformed edge record: {str(eid)[:80]!r}")
            continue
        core = canonical_json({k: v for k, v in e.items() if k != "edge_id"})
        if "e-" + hashlib.sha256(core.encode("utf-8")).hexdigest()[:24] != eid:
            failures.append(f"edge_id does not match edge content: {eid[:40]}")
        if e.get("verification_level") != LEVEL_CHUNK_VERIFIED:
            failures.append(
                f"edge claims verification_level "
                f"{str(e.get('verification_level'))[:30]!r}; only CHUNK_VERIFIED "
                f"is claimable at M1.5: {eid[:40]}")
        if e.get("src") not in node_ids or e.get("dst") not in node_ids:
            failures.append(f"edge references unknown node: {eid[:40]}")
        if e["derivation"] != DERIVATION_AMBIGUOUS and not e["evidence"]:
            failures.append(f"non-ambiguous edge without evidence: {eid[:40]}")
        edge_doc = _edge_document(e)
        if edge_doc not in snap_docs:
            failures.append(f"edge does not resolve to a snapshot document: {eid[:40]}")
            continue
        for span in e["evidence"]:
            if not isinstance(span, dict):
                failures.append(f"non-object evidence span on {eid[:40]}")
                continue
            uid, start, end = span.get("chunk_uid"), span.get("start"), span.get("end")
            info = chunk_info.get(uid) if isinstance(uid, str) else None
            if info is None:
                failures.append(f"span chunk_uid not in snapshot: {str(uid)[:80]}")
                continue
            span_doc, tlen, pages = info
            if not (isinstance(start, int) and isinstance(end, int)
                    and 0 <= start < end <= tlen):
                failures.append(
                    f"span does not resolve into chunk text: {uid[:80]} "
                    f"[{start}:{end}] len={tlen}")
                continue
            if span_doc != edge_doc:
                failures.append(
                    f"span comes from another document's chunk: {eid[:40]}")
                continue
            if "page" in span and span["page"] not in pages:
                failures.append(f"span page not among the chunk's pages: {eid[:40]}")
                continue
            spans_checked += 1
    report["evidence_spans_verified"] = spans_checked

    # 5. Identity: snapshot binding and graph_id over the whole manifest.
    if manifest.get("snapshot_id") != snap["manifest"].get("snapshot_id"):
        failures.append("graph manifest snapshot_id does not match snapshot")
    if graph_identity(manifest) != manifest.get("graph_id"):
        failures.append("graph_id does not match recomputed manifest identity")

    # 6. Reproduction from the snapshot, byte for byte.
    try:
        from .build import compute_graph, manifest_bytes  # late: avoids a cycle
        payloads, expected = compute_graph(snap)
    except KMGError as e:
        failures.append(f"graph could not be recomputed from the snapshot: {e.code}")
    else:
        mismatched = [n for n in _ARTIFACTS if on_disk.get(n) != payloads[n]]
        if manifest_raw != manifest_bytes(expected):
            mismatched.append(_MANIFEST)
        if mismatched:
            failures.append(
                f"graph does not reproduce from the snapshot: {mismatched}")
        else:
            report["reproduced"] = True
        report["expected_graph_id"] = expected["graph_id"]

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
    lines.append(f"  reproduced from snapshot: {report.get('reproduced')}")
    if report["failures"]:
        lines.append(f"  FAILURES ({len(report['failures'])}):")
        lines.extend(f"    - {f}" for f in report["failures"][:20])
        if len(report["failures"]) > 20:
            lines.append(f"    ... and {len(report['failures']) - 20} more")
    lines.append(f"  verdict: {'OK' if report['ok'] else 'FAILED'}")
    return "\n".join(lines)
