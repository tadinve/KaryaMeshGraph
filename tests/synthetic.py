"""Synthetic fixture: a miniature, fully-hashed fake KaryaMeshRAG checkout.

All content is synthetic (SPEC.md M1.4) - no real SOP text anywhere. Every
chunk text embeds SYNTH_MARKER so leak tests can assert that no error
message or report ever contains source text.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


SYNTH_MARKER = "XYLOPHONE-QUARTZ-SYNTHETIC"
DOCS = ("SYN-SOP-001", "SYN-SOP-002")
VERSIONS = {
    "classifier": "2026-10-19.6",
    "reconstructor": "2026-10-19.1",
    "section": "2026-10-19.2",
    "fixed": "2026-10-19.1",
    "annotation": "2026-10-19.1",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chunk_record(doc: str, obj: str, pdf_sha: str, i: int) -> dict:
    text = f"{SYNTH_MARKER} {doc} chunk {i}: synthetic requirement text for testing."
    sha = _sha(text.encode("utf-8"))
    chunk_id = f"{doc}@{obj}/v1.0/{pdf_sha[:12]}/section-1000/c{i:04d}"
    n = len(text)
    return {
        "chunk_id": chunk_id,
        "chunk_sha256": sha,
        "chunk_uid": f"{chunk_id}#{sha[:16]}",
        "corpus_generation": "v2",
        "stable_id": doc,
        "object_id": obj,
        "version": "1.0",
        "pdf_sha256": pdf_sha,
        "text": text,
        "pages": [1],
        "index": i,
        "section_id": f"s{i}",
        "section_number": str(i),
        "section_path": [f"s{i}"],
        "section_path_titles": [f"Synthetic Section {i}"],
        "source_spans": [{
            "segment": f"{doc}@{obj}/p1/s{i}",
            "page": 1,
            "bbox": [10.0, 10.0, 200.0, 20.0],
            "segment_start": 0,
            "segment_end": n,
            "chunk_start": 0,
            "chunk_end": n,
            "partial": False,
        }],
        "elements": [{
            "element_id": f"e{i:04d}",
            "kind": "paragraph",
            "section_heading": f"Synthetic Section {i}",
            "element_start": 0,
            "element_end": n,
            "chunk_start": 0,
            "chunk_end": n,
            "element_length": n,
        }],
        "units": 1,
        "review_flags": [],
        "inserted": [],
        "chunker": "section_aware",
        "chunker_version": VERSIONS["section"],
        "max_chars": 1000,
        "overlap_chars": 0,
        "char_count": n,
        "context_header": f"Synthetic Section {i}",
        "split_within_element": False,
        "step_continuation": False,
    }


def build_synthetic_source(root: Path) -> Path:
    """Create a complete, internally consistent fake KaryaMeshRAG tree."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "src" / "karyamesh").mkdir(parents=True)
    (root / "src" / "karyamesh" / "__init__.py").write_text("# synthetic marker package\n")

    pdf_shas: dict[str, str] = {}
    records: list[dict] = []
    corpus_entries: list[dict] = []
    dms_docs: list[dict] = []
    originals = root / "data" / "originals"
    originals.mkdir(parents=True)
    for n, doc in enumerate(DOCS, start=1):
        obj = f"SIM-90000{n}"
        pdf_bytes = f"%PDF-1.4 synthetic {doc} not-a-real-document".encode()
        (originals / f"{doc}.pdf").write_bytes(pdf_bytes)
        pdf_sha = _sha(pdf_bytes)
        pdf_shas[doc] = pdf_sha
        for i in range(1, 4):
            records.append(_chunk_record(doc, obj, pdf_sha, i))
        corpus_entries.append({
            "stable_id": doc,
            "file": f"{doc}.pdf",
            "sha256": pdf_sha,
            "bytes": len(pdf_bytes),
            "pages": 1,
            "source_document_number": f"SYN-{n}00.1",
            "source_title": f"Synthetic SOP {n}",
            "source_series": "SYN Series",
            "candidate_id": f"CAND-{n:03d}",
        })
        dms_docs.append({
            "object_id": obj,
            "document_id": doc,
            "version": "1.0",
            "title": f"Synthetic SOP {n}",
            "lifecycle_state": "APPROVED",
            "approved_at": "2026-10-01T09:00:00Z",
            "effective_at": "2026-10-05T00:00:00Z",
            "withdrawn_at": None,
            "content_path": f"data/originals/{doc}.pdf",
            "source_sha256": pdf_sha,
            "source_ref": {"stable_id": doc, "provenance": "synthetic"},
            "review_status": "included",
            "is_simulated": True,
            "simulated_fields": ["approved_at", "effective_at"],
            "simulation_note": "synthetic test fixture",
        })

    chunks_dir = root / "var" / "v2" / "chunks"
    chunks_dir.mkdir(parents=True)
    payload = b"".join(json.dumps(r, sort_keys=True).encode() + b"\n" for r in records)
    chunks_file = chunks_dir / "corpus.section-1000.jsonl"
    chunks_file.write_bytes(payload)

    frozen = {
        "corpus_generation": "v2",
        "status": "frozen",
        "frozen_at": "2026-10-19T21:47:53+00:00",
        "frozen_from": "var/chunks-v2-candidate",
        "versions": VERSIONS,
        "fingerprints": {
            "section": {
                "file": "corpus.section-1000.jsonl",
                "sha256": _sha(payload),
                "chunks": len(records),
                "documents": len(DOCS),
                "candidate_sha256": _sha(b"synthetic-candidate"),
            },
        },
        "inputs": {"pdf_count": len(DOCS), "pdf_sha256": pdf_shas},
    }
    frozen_path = root / "evidence" / "day08" / "corpus-manifest-v2.json"
    frozen_path.parent.mkdir(parents=True)
    frozen_path.write_text(json.dumps(frozen, indent=1, sort_keys=True))

    (root / "corpus").mkdir()
    (root / "corpus" / "manifest.json").write_text(json.dumps(corpus_entries, indent=1))
    authz = root / "corpus" / "authz"
    authz.mkdir()
    (authz / "identities.json").write_text(json.dumps({
        "registry_version": "syn-ids-1",
        "note": "synthetic",
        "principals": {
            "svc.import": {"display_name": "Import Service", "groups": ["ops"], "status": "active"},
            "ghost.user": {"display_name": "Disabled User", "groups": ["ops"], "status": "disabled"},
        },
    }, indent=1))
    (authz / "acl-policy.json").write_text(json.dumps({
        "policy_version": "syn-acl-1",
        "resolution_order": ["explicit deny", "allow", "default deny"],
        "note": "synthetic",
        "grants": [{
            "grant_id": "g1", "subject_type": "principal", "subject_id": "svc.import",
            "document_id": "*", "effect": "allow", "granted_at": "2026-01-01T00:00:00Z",
        }],
    }, indent=1))

    dms = root / "data" / "mock_dms"
    dms.mkdir(parents=True)
    (dms / "documents.json").write_text(json.dumps(dms_docs, indent=1))
    (dms / "events.jsonl").write_text(
        json.dumps({"event": "approved", "document_id": DOCS[0]}) + "\n")
    return root


def rewrite_chunks(root: Path, transform, *, update_manifest: bool = True) -> None:
    """Apply ``transform(records) -> records`` to the chunks JSONL.

    With ``update_manifest=True`` the frozen fingerprint is refreshed so the
    mutation is *internally consistent* (tests record-level checks); with
    ``False`` the file no longer matches the fingerprint (tests hash gates).
    """
    chunks_file = root / "var" / "v2" / "chunks" / "corpus.section-1000.jsonl"
    records = [json.loads(l) for l in chunks_file.read_bytes().splitlines() if l.strip()]
    records = transform(records)
    payload = b"".join(json.dumps(r, sort_keys=True).encode() + b"\n" for r in records)
    chunks_file.write_bytes(payload)
    if update_manifest:
        frozen_path = root / "evidence" / "day08" / "corpus-manifest-v2.json"
        frozen = json.loads(frozen_path.read_text())
        fp = frozen["fingerprints"]["section"]
        fp["sha256"] = _sha(payload)
        fp["chunks"] = len(records)
        fp["documents"] = len({r.get("stable_id") for r in records})
        frozen_path.write_text(json.dumps(frozen, indent=1, sort_keys=True))


def rehash_record(rec: dict) -> dict:
    """Recompute sha/uid after editing a record's text (keeps it 'valid')."""
    sha = _sha(rec["text"].encode("utf-8"))
    rec["chunk_sha256"] = sha
    rec["chunk_uid"] = f"{rec['chunk_id']}#{sha[:16]}"
    return rec


def tree_digest(root: Path) -> dict[str, str]:
    """{relative_path: sha256} of every file - proves the source is untouched."""
    return {
        str(p.relative_to(root)): _sha(p.read_bytes())
        for p in sorted(root.rglob("*")) if p.is_file()
    }


