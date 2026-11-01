"""`snapshot verify` - independent recomputation over a snapshot (SPEC.md M1.3).

``verify_content`` checks written content against a manifest dict (used both
by the builder's internal gate and by the standalone verifier);
``verify_snapshot`` loads the manifest from disk and adds manifest-level
checks: snapshot_id recomputation, authorization context resolved against the
snapshotted identity registry, manifest↔documents.json agreement, and the
undeclared-file scan.

Independence principle: the verifier recomputes relationships from the
artifacts the snapshot itself carries (chunks, documents.json, the embedded
frozen + corpus manifests, authz files) rather than trusting builder-recorded
fields. All failures carry identifiers only - never chunk text - and anything
unexpected (including structurally malformed input) is a failing report, not
a crash.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..contracts.models import (
    AUTHORIZATION_MODE_OFFLINE,
    CAPABILITY_FULL,
    LEVEL_CHUNK_VERIFIED,
    LEVEL_SOURCE_VERIFIED,
    SCHEMA_VERSION,
    canonical_json,
    eligibility_for,
    identity_tuple,
    sha256_bytes,
    snapshot_identity,
    validate_chunk_record,
)
from ..errors import MalformedRecordError
from .authz import EFFECT_ALLOW, evaluate_document_access


def _fail(failures: list[dict], code: str, message: str) -> None:
    failures.append({"code": code, "message": message})


def _load_json_artifact(snapshot_dir: Path, rel: str):
    """Load a JSON artifact → (data, error_message)."""
    p = snapshot_dir / rel
    if not p.is_file():
        return None, f"artifact missing: {rel}"
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, f"artifact is not valid JSON: {rel}"


def verify_content(snapshot_dir: Path, manifest: dict) -> list[dict]:
    """Recompute and cross-check everything below the manifest. Returns failures."""
    snapshot_dir = Path(snapshot_dir)
    failures: list[dict] = []

    # Manifest structure sanity (a tampered manifest must fail, not crash).
    raw_docs = manifest.get("documents")
    if not isinstance(raw_docs, list) or not all(
            isinstance(d, dict) and isinstance(d.get("document_id"), str) for d in raw_docs):
        _fail(failures, "snapshot_state", "manifest documents list is malformed")
        return failures
    raw_arts = manifest.get("artifacts")
    if not isinstance(raw_arts, list) or not all(
            isinstance(a, dict) and isinstance(a.get("path"), str)
            and isinstance(a.get("sha256"), str) for a in raw_arts):
        _fail(failures, "snapshot_state", "manifest artifacts list is malformed")
        return failures
    docs = {d["document_id"]: d for d in raw_docs}
    generation = manifest.get("corpus_generation")

    # An empty snapshot can never be a valid build product.
    if not docs or not raw_arts:
        _fail(failures, "snapshot_state", "snapshot declares no documents or no artifacts")
        return failures

    # Artifact inventory: every listed file exists with the recorded hash.
    declared_paths = {a["path"] for a in raw_arts}
    for art in raw_arts:
        p = snapshot_dir / art["path"]
        if not p.is_file():
            _fail(failures, "artifact_missing", f"artifact missing: {art['path']}")
            continue
        data = p.read_bytes()
        if sha256_bytes(data) != art["sha256"]:
            _fail(failures, "hash_mismatch",
                  f"artifact hash mismatch (tampered?): {art['path']}")
        if len(data) != art.get("bytes"):
            _fail(failures, "hash_mismatch", f"artifact size mismatch: {art['path']}")

    # Embedded source manifests, re-read from the snapshot itself.
    corpus_sel, err = _load_json_artifact(snapshot_dir, "source/corpus-manifest.selected.json")
    if err:
        _fail(failures, "artifact_missing", err)
        corpus_by_id: dict[str, dict] = {}
    else:
        corpus_by_id = {e.get("stable_id"): e for e in corpus_sel
                        if isinstance(e, dict)} if isinstance(corpus_sel, list) else {}
        if not corpus_by_id:
            _fail(failures, "malformed_record", "embedded corpus manifest selection is malformed")
    frozen, err = _load_json_artifact(snapshot_dir, "source/frozen-manifest.json")
    if err:
        _fail(failures, "artifact_missing", err)
        frozen = None
    elif not isinstance(frozen, dict):
        _fail(failures, "malformed_record", "embedded frozen manifest is not an object")
        frozen = None

    # source hashes: the manifest's recorded source hashes must agree with the
    # embedded frozen manifest artifact (not merely with builder memory).
    strategy = manifest.get("strategy")
    if frozen is not None and not isinstance(strategy, str):
        _fail(failures, "snapshot_state", "manifest strategy field is malformed")
        frozen = None
    if frozen is not None:
        fingerprints = frozen.get("fingerprints")
        fp = (fingerprints.get(strategy) if isinstance(fingerprints, dict) else None) or {}
        if not isinstance(fp, dict):
            fp = {}
        if fp.get("sha256") != manifest.get("source_chunks_sha256"):
            _fail(failures, "hash_mismatch",
                  "source_chunks_sha256 disagrees with the embedded frozen manifest")
        if frozen.get("corpus_generation") != generation:
            _fail(failures, "generation_mismatch",
                  "embedded frozen manifest declares a different corpus generation")
    for art in raw_arts:
        if art["path"] == "source/frozen-manifest.json" and \
                art["sha256"] != manifest.get("source_manifest_sha256"):
            _fail(failures, "hash_mismatch",
                  "source_manifest_sha256 disagrees with the frozen manifest artifact hash")

    # Per-record verification of every chunks file.
    uid_to_sha: dict[str, str] = {}
    id_to_sha: dict[str, str] = {}
    total_records = 0
    for sid, doc in sorted(docs.items()):
        p = snapshot_dir / "chunks" / f"{sid}.jsonl"
        if f"chunks/{sid}.jsonl" not in declared_paths or not p.is_file():
            _fail(failures, "artifact_missing", f"chunks file missing for document {sid}")
            continue
        tuples: set = set()
        with open(p, "rb") as f:
            for line_no, raw in enumerate(f, start=1):
                if not raw.strip():
                    continue
                total_records += 1
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    _fail(failures, "malformed_record",
                          f"{sid}.jsonl line {line_no}: invalid JSON")
                    continue
                if not isinstance(rec, dict):
                    _fail(failures, "malformed_record",
                          f"{sid}.jsonl line {line_no}: not a JSON object")
                    continue
                problems = validate_chunk_record(
                    rec, expected_generation=generation, expected_stable_id=sid)
                for problem in problems:
                    _fail(failures, "malformed_record", problem)
                if problems:
                    continue
                uid, cid, sha = rec["chunk_uid"], rec["chunk_id"], rec["chunk_sha256"]
                if uid in uid_to_sha and uid_to_sha[uid] != sha:
                    _fail(failures, "duplicate_identity",
                          f"chunk_uid collision with differing content: {uid}")
                if cid in id_to_sha and id_to_sha[cid] != sha:
                    _fail(failures, "duplicate_identity",
                          f"chunk_id reused with differing content: {cid}")
                uid_to_sha[uid] = sha
                id_to_sha[cid] = sha
                try:
                    tuples.add(identity_tuple(rec))
                except MalformedRecordError:
                    pass  # already reported by validate_chunk_record
        # Identity agreement: chunk records vs documents.json vs embedded corpus manifest.
        if len(tuples) > 1:
            _fail(failures, "duplicate_identity",
                  f"document {sid} has multiple identity tuples in snapshot")
        elif len(tuples) == 1:
            t = next(iter(tuples))
            declared = (doc.get("document_id"), doc.get("object_id"),
                        doc.get("version"), doc.get("pdf_sha256"))
            if t != declared:
                _fail(failures, "duplicate_identity",
                      f"document {sid}: chunk identity disagrees with documents.json")
        entry = corpus_by_id.get(sid)
        if corpus_by_id and entry is None:
            _fail(failures, "artifact_missing",
                  f"document {sid} absent from embedded corpus manifest selection")
        elif entry is not None and entry.get("sha256") != doc.get("pdf_sha256"):
            _fail(failures, "hash_mismatch",
                  f"document {sid}: pdf_sha256 disagrees with embedded corpus manifest")

    # Counts, generation purity, cross-check booleans, derived-field honesty.
    if total_records != manifest.get("chunk_count"):
        _fail(failures, "snapshot_state",
              f"chunk_count {manifest.get('chunk_count')} != records on disk {total_records}")
    if len(docs) != manifest.get("document_count"):
        _fail(failures, "snapshot_state",
              f"document_count {manifest.get('document_count')} != documents.json entries {len(docs)}")
    for sid, doc in sorted(docs.items()):
        if doc.get("source_generation") != generation:
            _fail(failures, "fixture_mixing",
                  f"document {sid} labeled {doc.get('source_generation')!r} inside a "
                  f"{generation!r} snapshot")
        if doc.get("source_manifest_sha256") != manifest.get("source_manifest_sha256"):
            _fail(failures, "hash_mismatch",
                  f"document {sid}: source_manifest_sha256 disagrees with manifest")
        for src, ok in (doc.get("pdf_sha256_cross_checks") or {}).items():
            if ok is False:
                _fail(failures, "hash_mismatch",
                      f"document {sid}: pdf_sha256 cross-check against {src} recorded false")
        # Recompute derived fields instead of trusting stored values.
        capability = doc.get("provenance_capability")
        expect_eligible = capability == CAPABILITY_FULL
        expect_ceiling = (LEVEL_SOURCE_VERIFIED if capability == CAPABILITY_FULL
                          else LEVEL_CHUNK_VERIFIED)
        if doc.get("extraction_eligible") != expect_eligible:
            _fail(failures, "snapshot_state",
                  f"document {sid}: extraction_eligible inconsistent with "
                  f"capability {capability!r}")
        if doc.get("verification_level_ceiling") != expect_ceiling:
            _fail(failures, "snapshot_state",
                  f"document {sid}: verification_level_ceiling inconsistent with "
                  f"capability {capability!r}")
        if capability == CAPABILITY_FULL and f"elements/{sid}.jsonl" not in declared_paths:
            _fail(failures, "artifact_missing",
                  f"document {sid} claims full capability but carries no element artifact")
        recomputed_elig = eligibility_for(doc.get("lifecycle_source"))
        stored_elig = {k: v for k, v in (doc.get("eligibility") or {}).items()
                       if k != "evaluated_at"}
        if stored_elig != recomputed_elig:
            _fail(failures, "snapshot_state",
                  f"document {sid}: eligibility does not recompute from lifecycle_source")
    return failures


def verify_snapshot(snapshot_dir: Path) -> dict:
    """Standalone verification: load the manifest, verify everything, report."""
    snapshot_dir = Path(snapshot_dir)
    failures: list[dict] = []
    report: dict = {"snapshot": str(snapshot_dir), "failures": failures, "ok": False}

    mpath = snapshot_dir / "snapshot-manifest.json"
    if not mpath.is_file():
        _fail(failures, "snapshot_state", "snapshot-manifest.json missing")
        return report
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        _fail(failures, "snapshot_state", "snapshot-manifest.json is not valid JSON")
        return report
    if not isinstance(manifest, dict):
        _fail(failures, "snapshot_state", "snapshot-manifest.json is not an object")
        return report
    report["snapshot_id"] = manifest.get("snapshot_id")

    if manifest.get("schema_version") != SCHEMA_VERSION:
        _fail(failures, "snapshot_state",
              f"unknown schema_version {manifest.get('schema_version')!r}")
    if manifest.get("validation_status") != "verified":
        _fail(failures, "snapshot_state",
              f"manifest validation_status is {manifest.get('validation_status')!r}")

    # Normalize possibly-tampered manifest shapes ONCE, so every later block
    # reports failures instead of crashing on a non-dict/non-list field.
    ctx = manifest.get("authorization_context")
    if not isinstance(ctx, dict):
        _fail(failures, "authorization", "authorization_context is missing or malformed")
        ctx = {}
    manifest_docs = manifest.get("documents")
    if not isinstance(manifest_docs, list):
        manifest_docs = []
    manifest_arts = [a for a in manifest.get("artifacts", [])
                     if isinstance(a, dict)] if isinstance(manifest.get("artifacts"), list) else []

    # Authorization context: offline simulation only, never production proof,
    # and the principal must resolve as ACTIVE in the snapshotted registry -
    # a manifest edit naming a disabled/unknown principal must fail here.
    if ctx.get("mode") != AUTHORIZATION_MODE_OFFLINE:
        _fail(failures, "authorization",
              f"authorization mode {ctx.get('mode')!r} is not {AUTHORIZATION_MODE_OFFLINE!r}")
    if ctx.get("is_production_authorization") is not False:
        _fail(failures, "authorization",
              "authorization_context must state is_production_authorization: false")
    principal = ctx.get("principal")
    principal = ctx.get("principal")
    pinfo = None
    identities = None
    if not principal:
        _fail(failures, "authorization", "authorization_context lacks a principal")
    else:
        identities, err = _load_json_artifact(snapshot_dir, "authz/identities.json")
        if err or not isinstance(identities, dict):
            _fail(failures, "authorization",
                  "cannot resolve principal: snapshotted identity registry unavailable")
            identities = None
        else:
            pinfo = (identities.get("principals") or {}).get(principal)
            if not isinstance(pinfo, dict) or pinfo.get("status") != "active":
                _fail(failures, "authorization",
                      f"principal {principal!r} is not an active principal in the "
                      f"snapshotted registry")
            if ctx.get("registry_version") != identities.get("registry_version"):
                _fail(failures, "authorization",
                      "authorization_context registry_version disagrees with the "
                      "snapshotted registry")
    acl = None
    if ctx.get("policy_status") == "available":
        acl, err = _load_json_artifact(snapshot_dir, "authz/acl-policy.json")
        if err or not isinstance(acl, dict):
            _fail(failures, "authorization",
                  "policy_status says available but the ACL policy artifact is unusable")
            acl = None
        elif ctx.get("policy_version") != acl.get("policy_version"):
            _fail(failures, "authorization",
                  "authorization_context policy_version disagrees with the snapshotted policy")

    # Independent re-evaluation of every included document's authorization
    # decision (2026-11-01 review finding): the builder refuses to include a
    # denied document, but the verifier must not simply trust the stored
    # "allow" - it recomputes the same explicit-deny/allow/default-deny
    # resolution from the snapshotted policy and fails if either the
    # recomputed effect disagrees with what was stored (tampering), or the
    # recomputed effect is not "allow" (a document that should never have
    # been included). policy_time is read from the manifest's
    # authorization_context, never from wall-clock "now".
    policy_time = ctx.get("policy_time")
    if principal and isinstance(policy_time, str):
        for doc in manifest_docs:
            if not isinstance(doc, dict):
                continue
            sid = doc.get("document_id")
            stored = ((doc.get("acl") or {}).get("authorization_decision")
                      if isinstance(doc.get("acl"), dict) else None)
            if not isinstance(stored, dict):
                _fail(failures, "authorization",
                      f"document {sid}: missing authorization_decision")
                continue
            recomputed = evaluate_document_access(
                principal=principal, pinfo=pinfo or {}, document_id=sid,
                document_series=doc.get("source_series"),
                acl=acl, policy_time_iso=policy_time,
            )
            recomputed_stripped = {k: v for k, v in recomputed.items() if k != "policy_time"}
            if stored != recomputed_stripped:
                _fail(failures, "authorization",
                      f"document {sid}: authorization_decision does not recompute "
                      f"from the snapshotted policy (tampered?)")
            elif stored.get("effect") != EFFECT_ALLOW:
                _fail(failures, "authorization",
                      f"document {sid}: included in snapshot despite a non-allow "
                      f"authorization decision ({stored.get('resolution')})")

    # The manifest's embedded document records must equal the hash-verified
    # documents.json artifact - the manifest itself is otherwise unauthenticated.
    docs_artifact, err = _load_json_artifact(snapshot_dir, "documents.json")
    if err:
        _fail(failures, "artifact_missing", err)
    elif canonical_json(docs_artifact) != canonical_json(manifest.get("documents")):
        _fail(failures, "snapshot_state",
              "manifest documents disagree with the documents.json artifact")

    failures.extend(verify_content(snapshot_dir, manifest))

    # Extra files on disk that the manifest does not declare = tampering.
    declared = {a.get("path") for a in manifest_arts} | {"snapshot-manifest.json"}
    on_disk = {str(p.relative_to(snapshot_dir))
               for p in snapshot_dir.rglob("*") if p.is_file()}
    for extra in sorted(on_disk - declared):
        _fail(failures, "snapshot_state", f"undeclared file in snapshot: {extra}")

    # Content-derived identity must recompute identically (determinism check).
    try:
        recomputed = snapshot_identity(
            source_fingerprint_sha256=manifest.get("source_chunks_sha256", ""),
            generation=manifest.get("corpus_generation", ""),
            strategy=manifest.get("strategy", ""),
            document_ids=[d.get("document_id") for d in manifest_docs
                          if isinstance(d, dict)],
            artifacts=manifest_arts,
        )
    except (TypeError, KeyError):
        recomputed = None
    if recomputed != manifest.get("snapshot_id"):
        _fail(failures, "hash_mismatch", "snapshot_id does not recompute from contents")

    report["document_count"] = manifest.get("document_count")
    report["chunk_count"] = manifest.get("chunk_count")
    report["capabilities"] = {
        d["document_id"]: d.get("provenance_capability")
        for d in manifest_docs
        if isinstance(d, dict) and "document_id" in d
    }
    report["ok"] = not failures
    return report


def format_report(report: dict) -> str:
    lines = [f"snapshot verify: {report['snapshot']}"]
    if report.get("snapshot_id"):
        lines.append(f"  snapshot_id: {report['snapshot_id']}")
    if report.get("capabilities"):
        caps = ", ".join(f"{k}={v}" for k, v in sorted(report["capabilities"].items()))
        lines.append(f"  documents: {report.get('document_count')} "
                     f"({report.get('chunk_count')} chunks) [{caps}]")
    if report["failures"]:
        lines.append(f"  FAILURES ({len(report['failures'])}):")
        for f in report["failures"]:
            lines.append(f"    - [{f['code']}] {f['message']}")
    lines.append("  verdict: " + ("OK" if report["ok"] else "FAIL"))
    return "\n".join(lines)
