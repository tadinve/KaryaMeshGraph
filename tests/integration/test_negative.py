"""The SPEC.md M1.4 negative battery. Every scenario must fail CLOSED with a
typed error or a failing report - and never leak source text (SYNTH_MARKER)."""

import json
import shutil

import pytest
from synthetic import DOCS, SYNTH_MARKER, rehash_record, rewrite_chunks

from karyameshgraph.errors import (
    ArtifactMissingError,
    AuthorizationError,
    FrozenManifestError,
    GenerationMismatchError,
    HashMismatchError,
    KMGError,
    MalformedRecordError,
    SnapshotStateError,
)
from karyameshgraph.ingest.inspect import inspect_source
from karyameshgraph.ingest.snapshot import build_snapshot
from karyameshgraph.ingest.source import SourceLayout
from karyameshgraph.ingest.verify import verify_snapshot

CREATED = "2026-10-31T00:00:00+00:00"
BUILD_KW = dict(principal="svc.import", authorization_mode="offline_simulation",
                created_at=CREATED)


def _codes(report):
    return {f["code"] for f in report["failures"]}


def _build(root, out, **overrides):
    return build_snapshot(SourceLayout(root), list(DOCS), out, **{**BUILD_KW, **overrides})


def _good_snapshot(root, tmp_path):
    out = tmp_path / "snap"
    _build(root, out)
    return out


# -- source-side failures ------------------------------------------------------

def test_source_checkout_missing(tmp_path):
    missing = tmp_path / "nowhere"
    report = inspect_source(SourceLayout(missing))
    assert not report["ok"] and "source_root" in _codes(report)
    with pytest.raises(KMGError):
        _build(missing, tmp_path / "s")


def test_frozen_manifest_missing(synthetic_source, tmp_path):
    (synthetic_source / "evidence/day08/corpus-manifest-v2.json").unlink()
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "frozen_manifest" in _codes(report)
    with pytest.raises(FrozenManifestError):
        _build(synthetic_source, tmp_path / "s")


def test_v2_jsonl_missing(synthetic_source, tmp_path):
    (synthetic_source / "var/v2/chunks/corpus.section-1000.jsonl").unlink()
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "artifact_missing" in _codes(report)
    with pytest.raises(ArtifactMissingError):
        _build(synthetic_source, tmp_path / "s")


def test_wrong_corpus_generation(synthetic_source, tmp_path):
    path = synthetic_source / "evidence/day08/corpus-manifest-v2.json"
    frozen = json.loads(path.read_text())
    frozen["corpus_generation"] = "v1"
    path.write_text(json.dumps(frozen))
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "generation_mismatch" in _codes(report)
    with pytest.raises(GenerationMismatchError):
        _build(synthetic_source, tmp_path / "s")


def test_wrong_chunks_sha256(synthetic_source, tmp_path):
    # Tamper one record's text WITHOUT refreshing hashes or the fingerprint.
    def tamper(records):
        records[0]["text"] += " tampered"
        return records
    rewrite_chunks(synthetic_source, tamper, update_manifest=False)
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "hash_mismatch" in _codes(report)
    with pytest.raises(HashMismatchError):
        _build(synthetic_source, tmp_path / "s")


def test_wrong_pdf_sha256(synthetic_source, tmp_path):
    path = synthetic_source / "corpus/manifest.json"
    entries = json.loads(path.read_text())
    entries[0]["sha256"] = "0" * 64
    path.write_text(json.dumps(entries))
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "hash_mismatch" in _codes(report)
    with pytest.raises(HashMismatchError):
        _build(synthetic_source, tmp_path / "s")


def test_truncated_jsonl_line(synthetic_source):
    chunks = synthetic_source / "var/v2/chunks/corpus.section-1000.jsonl"
    with open(chunks, "ab") as f:
        f.write(b'{"chunk_id": "SYN-TRUNC')
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"]
    assert {"malformed_record", "hash_mismatch"} & _codes(report)


def test_duplicate_chunk_id_conflicting_text(synthetic_source, tmp_path):
    def dup(records):
        clone = dict(records[0])
        clone["text"] = clone["text"] + " divergent copy"
        rehash_record(clone)  # valid record, but same chunk_id as records[0]
        records.append(clone)
        return records
    rewrite_chunks(synthetic_source, dup, update_manifest=True)
    with pytest.raises(SnapshotStateError):
        _build(synthetic_source, tmp_path / "s")
    # Fail-closed: no snapshot dir, but an explicit failure report exists.
    assert not (tmp_path / "s").exists()
    assert (tmp_path / "s.failed.json").is_file()


def test_section_reference_nonexistent(synthetic_source, tmp_path):
    def break_section(records):
        records[0]["section_id"] = "s-ghost"
        records[0]["section_path"] = []
        return records
    rewrite_chunks(synthetic_source, break_section, update_manifest=True)
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "malformed_record" in _codes(report)
    with pytest.raises(MalformedRecordError):  # builder fails fast at collect
        _build(synthetic_source, tmp_path / "s")


def test_evidence_span_out_of_bounds(synthetic_source, tmp_path):
    def break_span(records):
        records[0]["source_spans"][0]["chunk_end"] = 99_999
        return records
    rewrite_chunks(synthetic_source, break_span, update_manifest=True)
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "malformed_record" in _codes(report)
    with pytest.raises(MalformedRecordError):
        _build(synthetic_source, tmp_path / "s")


def test_evidence_span_page_mismatch(synthetic_source, tmp_path):
    def break_page(records):
        records[0]["source_spans"][0]["page"] = 99  # not in the chunk's pages
        return records
    rewrite_chunks(synthetic_source, break_page, update_manifest=True)
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "malformed_record" in _codes(report)
    with pytest.raises(MalformedRecordError):
        _build(synthetic_source, tmp_path / "s")


def test_dissection_fixture_presented_as_v2(synthetic_source, tmp_path):
    def relabel(records):
        records[0]["corpus_generation"] = "dissection_fixture"
        return records
    rewrite_chunks(synthetic_source, relabel, update_manifest=True)
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "malformed_record" in _codes(report)
    with pytest.raises(MalformedRecordError):
        _build(synthetic_source, tmp_path / "s")


def test_tampered_pdf_bytes_detected(synthetic_source):
    pdf = synthetic_source / "data" / "originals" / f"{DOCS[0]}.pdf"
    pdf.write_bytes(b"%PDF-1.4 swapped-in different bytes")
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "hash_mismatch" in _codes(report)
    assert report["corpus_manifest"]["pdf_bytes"]["mismatched"] == [DOCS[0]]


# -- authorization failures ----------------------------------------------------

def test_missing_identities_fails_build(synthetic_source, tmp_path):
    (synthetic_source / "corpus/authz/identities.json").unlink()
    with pytest.raises(AuthorizationError):
        _build(synthetic_source, tmp_path / "s")


def test_unknown_and_disabled_principals_fail(synthetic_source, tmp_path):
    with pytest.raises(AuthorizationError):
        _build(synthetic_source, tmp_path / "s1", principal="nobody")
    with pytest.raises(AuthorizationError):
        _build(synthetic_source, tmp_path / "s2", principal="ghost.user")


def test_unsupported_authorization_mode_fails(synthetic_source, tmp_path):
    with pytest.raises(AuthorizationError):
        _build(synthetic_source, tmp_path / "s", authorization_mode="production")


def test_missing_acl_policy_is_recorded_never_public(synthetic_source, tmp_path):
    (synthetic_source / "corpus/authz/acl-policy.json").unlink()
    out = tmp_path / "snap"
    manifest = _build(synthetic_source, out)
    for doc in manifest["documents"]:
        assert doc["acl"]["status"] == "missing"
        assert doc["acl"]["derived_grants"] is None
        assert "public" not in json.dumps(doc["acl"]).lower()
    assert manifest["authorization_context"]["policy_status"] == "missing"
    assert verify_snapshot(out)["ok"]


def test_invalid_authz_json_fails_closed(synthetic_source, tmp_path):
    (synthetic_source / "corpus/authz/identities.json").write_text("{not valid json")
    report = inspect_source(SourceLayout(synthetic_source))
    assert not report["ok"] and "authorization" in _codes(report)
    with pytest.raises(AuthorizationError):
        _build(synthetic_source, tmp_path / "s")


def test_withdrawn_document_not_eligible_and_no_leak(synthetic_source, tmp_path):
    dms = synthetic_source / "data/mock_dms/documents.json"
    docs = json.loads(dms.read_text())
    docs[1]["lifecycle_state"] = "WITHDRAWN"
    docs[1]["withdrawn_at"] = "2026-10-20T00:00:00Z"
    dms.write_text(json.dumps(docs, indent=1))
    out = tmp_path / "snap"
    manifest = _build(synthetic_source, out)
    by_id = {d["document_id"]: d for d in manifest["documents"]}
    assert by_id[DOCS[1]]["eligibility"]["status"] == "not_eligible_offline_simulated"
    assert SYNTH_MARKER not in json.dumps(manifest)  # metadata only, no content
    assert verify_snapshot(out)["ok"]


def test_deny_grant_keeps_its_effect(synthetic_source, tmp_path):
    acl_path = synthetic_source / "corpus/authz/acl-policy.json"
    pol = json.loads(acl_path.read_text())
    pol["grants"].append({"grant_id": "g2", "subject_type": "principal",
                          "subject_id": "svc.import", "document_id": DOCS[0],
                          "effect": "deny", "granted_at": "2026-01-01T00:00:00Z"})
    acl_path.write_text(json.dumps(pol, indent=1))
    manifest = _build(synthetic_source, tmp_path / "snap")
    by_id = {d["document_id"]: d for d in manifest["documents"]}
    effects = {g["grant_id"]: g["effect"] for g in by_id[DOCS[0]]["acl"]["derived_grants"]}
    assert effects == {"g1": "allow", "g2": "deny"}  # deny never reads as allow


# -- snapshot tamper detection --------------------------------------------------

def test_verification_catches_tampered_chunks(synthetic_source, tmp_path):
    out = _good_snapshot(synthetic_source, tmp_path)
    target = out / "chunks" / f"{DOCS[0]}.jsonl"
    data = target.read_bytes().replace(b"synthetic requirement", b"synthetic ALTERED req")
    target.write_bytes(data)
    report = verify_snapshot(out)
    assert not report["ok"] and "hash_mismatch" in _codes(report)


def test_verification_catches_tampered_documents_json(synthetic_source, tmp_path):
    out = _good_snapshot(synthetic_source, tmp_path)
    path = out / "documents.json"
    docs = json.loads(path.read_text())
    docs[0]["source_generation"] = "dissection_fixture"
    docs[0]["extraction_eligible"] = True
    path.write_text(json.dumps(docs, indent=2, sort_keys=True) + "\n")
    report = verify_snapshot(out)
    assert not report["ok"]
    assert {"fixture_mixing", "hash_mismatch", "snapshot_state"} & _codes(report)


def test_verify_reports_non_object_chunk_line(synthetic_source, tmp_path):
    out = _good_snapshot(synthetic_source, tmp_path)
    with open(out / "chunks" / f"{DOCS[0]}.jsonl", "ab") as f:
        f.write(b"[1, 2, 3]\n")
    report = verify_snapshot(out)  # must report, never crash
    assert not report["ok"] and "malformed_record" in _codes(report)


def test_verify_catches_principal_swap_in_manifest(synthetic_source, tmp_path):
    """The manifest is unauthenticated by artifact hashes; the verifier must
    resolve the claimed principal against the snapshotted registry."""
    out = _good_snapshot(synthetic_source, tmp_path)
    mpath = out / "snapshot-manifest.json"
    manifest = json.loads(mpath.read_text())
    manifest["authorization_context"]["principal"] = "ghost.user"  # disabled
    mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    report = verify_snapshot(out)
    assert not report["ok"] and "authorization" in _codes(report)


def test_verification_catches_undeclared_file(synthetic_source, tmp_path):
    out = _good_snapshot(synthetic_source, tmp_path)
    (out / "extra.txt").write_text("planted")
    report = verify_snapshot(out)
    assert not report["ok"] and "snapshot_state" in _codes(report)


def test_refuses_to_overwrite_existing_snapshot(synthetic_source, tmp_path):
    out = _good_snapshot(synthetic_source, tmp_path)
    with pytest.raises(SnapshotStateError):
        _build(synthetic_source, out)


def test_requested_document_absent(synthetic_source, tmp_path):
    with pytest.raises(ArtifactMissingError):
        build_snapshot(SourceLayout(synthetic_source), ["SYN-SOP-999"],
                       tmp_path / "s", **BUILD_KW)


# -- leakage guarantee -----------------------------------------------------------

def test_no_source_text_in_any_error_or_report(synthetic_source, tmp_path):
    """Denied/failed paths never leak chunk text (SPEC.md M1.4)."""
    collected: list[str] = []

    def tamper(records):
        records[0]["text"] += " tampered"
        return records
    rewrite_chunks(synthetic_source, tamper, update_manifest=False)

    report = inspect_source(SourceLayout(synthetic_source))
    collected.append(json.dumps(report))
    try:
        _build(synthetic_source, tmp_path / "s")
    except KMGError as e:
        collected.append(str(e))
        collected.append(json.dumps(e.context, default=str))
    for blob in collected:
        assert SYNTH_MARKER not in blob
