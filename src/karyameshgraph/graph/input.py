"""Verified-snapshot input for the M1.5 graph builders.

The graph is built ONLY from a snapshot that passes the independent
verifier at load time (fail closed — a stale or tampered snapshot must not
silently become a graph). Chunks are ordered by the upstream ``index``
field per document, which the role parser relies on for carrying headers
across chunk boundaries; duplicate indices are a typed failure and gaps
are recorded, never papered over.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..errors import MalformedRecordError, SnapshotStateError
from ..ingest.verify import verify_snapshot


def load_verified_snapshot(snapshot_dir: Path, *, expected_documents: int | None = None) -> dict:
    """Re-verify the snapshot, then load everything the builders need.

    Returns ``{manifest, documents, corpus_entries, chunks, index_gaps,
    verify_report}`` where ``documents`` maps stable_id → DocumentVersion
    record, ``corpus_entries`` maps stable_id → embedded corpus-manifest
    entry, and ``chunks`` maps stable_id → records sorted by ``index``.
    """
    snapshot_dir = Path(snapshot_dir)
    report = verify_snapshot(snapshot_dir)
    if not report.get("ok"):
        raise SnapshotStateError(
            "snapshot failed independent verification; refusing to build graph",
            snapshot=str(snapshot_dir),
            failures=len(report.get("failures", [])),
        )

    manifest = json.loads((snapshot_dir / "snapshot-manifest.json").read_text(encoding="utf-8"))
    doc_list = json.loads((snapshot_dir / "documents.json").read_text(encoding="utf-8"))
    documents = {d["document_id"]: d for d in doc_list}

    if expected_documents is not None and len(documents) != expected_documents:
        raise SnapshotStateError(
            "snapshot does not contain the expected document count "
            "(M1.5 entry gate is the FULL corpus; pass the actual count "
            "explicitly only for fixture or pilot work)",
            expected=expected_documents, actual=len(documents),
        )

    corpus_list = json.loads(
        (snapshot_dir / "source" / "corpus-manifest.selected.json").read_text(encoding="utf-8"))
    corpus_entries = {e["stable_id"]: e for e in corpus_list}

    chunks: dict[str, list[dict]] = {}
    index_gaps: dict[str, list[int]] = {}
    for sid in sorted(documents):
        path = snapshot_dir / "chunks" / f"{sid}.jsonl"
        recs: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    recs.append(json.loads(line))
        seen: dict[int, str] = {}
        for rec in recs:
            idx = rec.get("index")
            if not isinstance(idx, int):
                raise MalformedRecordError(
                    "chunk record has no integer index", stable_id=sid,
                    chunk_id=str(rec.get("chunk_id"))[:96])
            if idx in seen:
                raise MalformedRecordError(
                    "duplicate chunk index within document", stable_id=sid, index=idx)
            seen[idx] = rec.get("chunk_uid", "")
        recs.sort(key=lambda r: r["index"])
        gaps = [
            i for i in range(recs[0]["index"], recs[-1]["index"] + 1)
            if i not in seen
        ] if recs else []
        if gaps:
            index_gaps[sid] = gaps
        chunks[sid] = recs

    return {
        "manifest": manifest,
        "documents": documents,
        "corpus_entries": corpus_entries,
        "chunks": chunks,
        "index_gaps": index_gaps,
        "verify_report": report,
    }
