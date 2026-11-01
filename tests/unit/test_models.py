"""Unit tests for the data contract (hashing conventions, validation, identity)."""

import hashlib

from karyameshgraph.contracts.models import (
    LEVEL_SOURCE_VERIFIED,
    chunk_text_sha256,
    expected_chunk_uid,
    identity_tuple,
    snapshot_identity,
    validate_chunk_record,
)


def _good_record() -> dict:
    text = "synthetic unit-test text"
    sha = hashlib.sha256(text.encode()).hexdigest()
    pdf = "a" * 64
    cid = f"DOC-1@OBJ-1/v1.0/{pdf[:12]}/section-1000/c0001"
    n = len(text)
    return {
        "chunk_id": cid, "chunk_sha256": sha, "chunk_uid": f"{cid}#{sha[:16]}",
        "corpus_generation": "v2", "stable_id": "DOC-1", "object_id": "OBJ-1",
        "version": "1.0", "pdf_sha256": pdf, "text": text, "pages": [1], "index": 1,
        "section_id": "s1", "section_path": ["s1"],
        "source_spans": [{"chunk_start": 0, "chunk_end": n}],
        "elements": [{"chunk_start": 0, "chunk_end": n}],
    }


def test_chunk_text_sha256_is_utf8_of_text_only():
    assert chunk_text_sha256("abc") == hashlib.sha256(b"abc").hexdigest()


def test_chunk_uid_convention():
    assert expected_chunk_uid("id", "f" * 64) == "id#" + "f" * 16


def test_valid_record_passes():
    assert validate_chunk_record(_good_record()) == []


def test_sha_mismatch_detected_without_leaking_text():
    rec = _good_record()
    rec["text"] = "tampered " + rec["text"]
    failures = validate_chunk_record(rec)
    assert any("chunk_sha256 mismatch" in f for f in failures)
    assert all("tampered" not in f for f in failures)  # ids only, never text


def test_missing_field_detected():
    rec = _good_record()
    del rec["pdf_sha256"]
    assert any("missing required fields" in f for f in validate_chunk_record(rec))


def test_wrong_generation_detected():
    rec = _good_record()
    rec["corpus_generation"] = "v1"
    assert any("corpus_generation" in f for f in validate_chunk_record(rec))


def test_span_out_of_bounds_detected():
    rec = _good_record()
    rec["source_spans"][0]["chunk_end"] = 10_000
    assert any("bounds" in f for f in validate_chunk_record(rec))


def test_source_verified_claim_rejected_at_chunk_level():
    rec = _good_record()
    rec["source_spans"][0]["verification_level"] = LEVEL_SOURCE_VERIFIED
    assert any("SOURCE_VERIFIED" in f for f in validate_chunk_record(rec))


def test_section_id_without_path_detected():
    rec = _good_record()
    rec["section_path"] = []
    assert any("section_path empty" in f for f in validate_chunk_record(rec))


def test_identity_tuple_fields():
    assert identity_tuple(_good_record()) == ("DOC-1", "OBJ-1", "1.0", "a" * 64)


def test_snapshot_identity_deterministic_and_content_sensitive():
    kw = dict(source_fingerprint_sha256="b" * 64, generation="v2", strategy="section",
              document_ids=["D2", "D1"], artifacts=[{"path": "x", "sha256": "c" * 64}])
    a = snapshot_identity(**kw)
    assert a == snapshot_identity(**kw)                      # deterministic
    assert a == snapshot_identity(**{**kw, "document_ids": ["D1", "D2"]})  # order-free
    changed = snapshot_identity(**{**kw, "artifacts": [{"path": "x", "sha256": "d" * 64}]})
    assert changed != a                                      # content-sensitive
