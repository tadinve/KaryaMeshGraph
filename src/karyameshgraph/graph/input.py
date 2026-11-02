"""Verified-snapshot input for the M1.5 graph builders.

The graph is built ONLY from a snapshot that passes the independent
verifier at load time (fail closed — a stale or tampered snapshot must not
silently become a graph). Chunks are ordered by the upstream ``index``
field per document, which the role parser relies on for carrying headers
across chunk boundaries; duplicate indices are a typed failure and gaps
are recorded, never papered over.

The snapshot verifier does not inspect the structural fields the graph
reads (``section_path_titles``, ``pages``), so their SHAPES are validated
here: an upstream serialization change (e.g. a title path delivered as one
string) must fail typed, not silently yield an empty but "verified" graph.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import MalformedRecordError, SnapshotStateError
from ..ingest.verify import verify_snapshot

# An index span this much larger than the record count is corruption, not a
# gap worth enumerating (and enumerating it could exhaust memory).
_MAX_GAP_FACTOR = 10
_MIN_GAP_ALLOWANCE = 1000


def _check_chunk_shape(rec: dict, sid: str) -> None:
    cid = str(rec.get("chunk_id"))[:96]
    idx = rec.get("index")
    if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
        raise MalformedRecordError(
            "chunk record has no non-negative integer index",
            stable_id=sid, chunk_id=cid)
    if not isinstance(rec.get("text"), str) or not isinstance(rec.get("chunk_uid"), str):
        raise MalformedRecordError(
            "chunk record text/chunk_uid is not a string", stable_id=sid, chunk_id=cid)
    # Required (a missing or renamed key would silently turn every chunk
    # into front matter); an empty list is legitimate for depth-0 chunks.
    titles = rec.get("section_path_titles")
    if not (isinstance(titles, list) and all(isinstance(t, str) for t in titles)):
        raise MalformedRecordError(
            "section_path_titles is missing or not a list of strings",
            stable_id=sid, chunk_id=cid, type=type(titles).__name__)
    pages = rec.get("pages")
    if pages is not None and not (
            isinstance(pages, list)
            and all(isinstance(p, int) and not isinstance(p, bool) for p in pages)):
        raise MalformedRecordError(
            "pages is not a list of integers", stable_id=sid, chunk_id=cid)


def load_snapshot_contents(snapshot_dir: Path) -> dict:
    """Load (WITHOUT re-verifying) everything the builders need from a
    snapshot directory, validating the shapes the graph depends on.

    Returns ``{manifest, documents, chunks, index_gaps}`` where
    ``documents`` maps stable_id → DocumentVersion record and ``chunks``
    maps stable_id → records sorted by ``index``. Callers must have
    verified the snapshot first (see ``load_verified_snapshot``).
    """
    snapshot_dir = Path(snapshot_dir)
    manifest = json.loads((snapshot_dir / "snapshot-manifest.json").read_text(encoding="utf-8"))
    doc_list = json.loads((snapshot_dir / "documents.json").read_text(encoding="utf-8"))
    if not isinstance(doc_list, list) or not all(
            isinstance(d, dict) and isinstance(d.get("document_id"), str) for d in doc_list):
        raise MalformedRecordError("documents.json is not a list of document records")
    documents = {d["document_id"]: d for d in doc_list}

    chunks: dict[str, list[dict]] = {}
    index_gaps: dict[str, list[int]] = {}
    for sid in sorted(documents):
        path = snapshot_dir / "chunks" / f"{sid}.jsonl"
        recs: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if not isinstance(rec, dict):
                        raise MalformedRecordError(
                            "chunk line is not an object", stable_id=sid)
                    _check_chunk_shape(rec, sid)
                    recs.append(rec)
        seen: set[int] = set()
        for rec in recs:
            if rec["index"] in seen:
                raise MalformedRecordError(
                    "duplicate chunk index within document",
                    stable_id=sid, index=rec["index"])
            seen.add(rec["index"])
        recs.sort(key=lambda r: r["index"])
        if recs:
            lo, hi = recs[0]["index"], recs[-1]["index"]
            missing = (hi - lo + 1) - len(recs)
            if missing > max(_MIN_GAP_ALLOWANCE, _MAX_GAP_FACTOR * len(recs)):
                raise MalformedRecordError(
                    "chunk index span is implausibly large for the record count",
                    stable_id=sid, first=lo, last=hi, records=len(recs))
            if missing:
                index_gaps[sid] = [i for i in range(lo, hi + 1) if i not in seen]
        chunks[sid] = recs

    if any(chunks.values()) and not any(
            rec["section_path_titles"] for recs in chunks.values() for rec in recs):
        raise MalformedRecordError(
            "no chunk in the snapshot carries a section title path; the "
            "structural layer is absent (upstream schema drift?)",
            documents=len(documents))

    return {
        "manifest": manifest,
        "documents": documents,
        "chunks": chunks,
        "index_gaps": index_gaps,
    }


def load_verified_snapshot(snapshot_dir: Path, *, expected_documents: int | None = None) -> dict:
    """Re-verify the snapshot, then load it (``load_snapshot_contents``),
    enforcing the expected document count (the M1.5 entry gate)."""
    snapshot_dir = Path(snapshot_dir)
    report = verify_snapshot(snapshot_dir)
    if not report.get("ok"):
        raise SnapshotStateError(
            "snapshot failed independent verification; refusing to build graph",
            snapshot=str(snapshot_dir),
            failures=len(report.get("failures", [])),
        )
    snap = load_snapshot_contents(snapshot_dir)
    if expected_documents is not None and len(snap["documents"]) != expected_documents:
        raise SnapshotStateError(
            "snapshot does not contain the expected document count "
            "(M1.5 entry gate is the FULL corpus; pass the actual count "
            "explicitly only for fixture or pilot work)",
            expected=expected_documents, actual=len(snap["documents"]),
        )
    snap["verify_report"] = report
    return snap
