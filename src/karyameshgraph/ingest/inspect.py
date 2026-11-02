"""`source inspect` - read-only verification of the upstream state (SPEC.md M1.1).

Produces a machine-readable report with no source passages: only identifiers,
hashes, counts, versions and statuses. Any mismatch lands in ``failures`` and
drives a non-zero exit code; nothing is silently substituted.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..contracts.models import identity_tuple, validate_chunk_record
from ..errors import KMGError
from .authz import validate_acl_policy
from .source import STATUS_AVAILABLE, STATUS_INVALID, STATUS_MISSING, SourceLayout, sha256_file


def _fail(failures: list[dict], code: str, message: str) -> None:
    failures.append({"code": code, "message": message})


def inspect_source(layout: SourceLayout) -> dict:
    """Run every M1.1 check. Returns the report; ``report['ok']`` is the verdict."""
    failures: list[dict] = []
    report: dict = {
        "schema_version": "1.0.0",
        "source_root": str(layout.root),
        "generation": layout.generation,
        "strategy": layout.strategy,
        "failures": failures,
        "ok": False,
    }

    # 1. Root exists and is the expected project; record git commit + tree state.
    try:
        layout.check_root()
    except KMGError as e:
        _fail(failures, e.code, str(e))
        return report
    report["git"] = layout.git_state()

    # 2. Frozen manifest: declared generation, fingerprint, counts.
    try:
        manifest = layout.load_frozen_manifest()
    except KMGError as e:
        _fail(failures, e.code, str(e))
        return report
    fp = manifest["fingerprints"][layout.strategy]
    report["frozen_manifest"] = {
        "path": str(layout.frozen_manifest_path),
        "sha256": sha256_file(layout.frozen_manifest_path),
        "status": manifest.get("status"),
        "frozen_at": manifest.get("frozen_at"),
        "versions": manifest.get("versions"),
        "fingerprint": {k: fp.get(k) for k in ("file", "sha256", "chunks", "documents")},
    }
    if manifest.get("status") != "frozen":
        _fail(failures, "frozen_manifest", f"manifest status is {manifest.get('status')!r}, expected 'frozen'")

    # 3-4. Locate the chunks file; stream its hash; count records + identities.
    chunks_path = layout.chunks_path(manifest)
    if not chunks_path.is_file():
        _fail(failures, "artifact_missing", f"chunks file missing: {chunks_path}")
        return report
    actual_sha = sha256_file(chunks_path)
    chunk_info: dict = {"path": str(chunks_path), "sha256": actual_sha,
                        "matches_frozen_manifest": actual_sha == fp["sha256"]}
    report["chunks_file"] = chunk_info
    if actual_sha != fp["sha256"]:
        _fail(failures, "hash_mismatch",
              f"chunks file sha256 {actual_sha[:16]}.. != frozen {fp['sha256'][:16]}..")

    identities: dict[str, set] = {}
    record_count = 0
    record_failures = 0
    id_sha: dict[str, str] = {}
    uid_sha: dict[str, str] = {}
    collisions = 0
    try:
        for _line_no, _raw, rec in layout.stream_chunk_records(chunks_path):
            record_count += 1
            problems = validate_chunk_record(rec, expected_generation=layout.generation)
            if problems:
                record_failures += 1
                if record_failures <= 5:  # cap: report is a summary, not a dump
                    for p in problems[:3]:
                        _fail(failures, "malformed_record", p)
                continue  # identity/collision checks need a well-formed record
            identities.setdefault(rec["stable_id"], set()).add(identity_tuple(rec))
            cid, uid, sha = rec["chunk_id"], rec["chunk_uid"], rec["chunk_sha256"]
            if id_sha.get(cid, sha) != sha or uid_sha.get(uid, sha) != sha:
                collisions += 1
                if collisions <= 5:
                    _fail(failures, "duplicate_identity",
                          f"chunk id/uid reused with differing content: {cid}")
            id_sha[cid] = sha
            uid_sha[uid] = sha
    except KMGError as e:
        _fail(failures, e.code, str(e))
        return report

    multi = {sid: sorted(t) for sid, t in identities.items() if len(t) > 1}
    chunk_info.update({
        "records": record_count,
        "documents": len(identities),
        "record_validation_failures": record_failures,
        "chunk_id_collisions": collisions,
        "documents_with_multiple_identities": sorted(multi),
    })
    if record_count != fp.get("chunks"):
        _fail(failures, "frozen_manifest",
              f"record count {record_count} != fingerprint chunks {fp.get('chunks')}")
    if len(identities) != fp.get("documents"):
        _fail(failures, "frozen_manifest",
              f"document count {len(identities)} != fingerprint documents {fp.get('documents')}")
    for sid in multi:
        _fail(failures, "duplicate_identity", f"document {sid} has multiple identity tuples")

    # 5. Cross-check every document against corpus/manifest.json + frozen inputs.
    try:
        corpus = layout.load_corpus_manifest()
    except KMGError as e:
        _fail(failures, e.code, str(e))
        corpus = {}
    frozen_pdfs = (manifest.get("inputs") or {}).get("pdf_sha256") or {}
    pdf_mismatches: list[str] = []
    missing_in_corpus: list[str] = []
    for sid, tuples in sorted(identities.items()):
        pdf_sha = next(iter(tuples))[3]
        entry = corpus.get(sid)
        if entry is None:
            missing_in_corpus.append(sid)
            continue
        ok_corpus = entry.get("sha256") == pdf_sha
        ok_frozen = (frozen_pdfs.get(sid) == pdf_sha) if isinstance(frozen_pdfs, dict) and sid in frozen_pdfs else None
        if not ok_corpus or ok_frozen is False:
            pdf_mismatches.append(sid)
    # PDF bytes on disk: recompute each available original's hash against the
    # corpus manifest (SPEC.md §2: verify declared hashes against bytes).
    # An absent PDF is reported honestly; a present-but-different PDF fails.
    pdf_bytes = {"verified": 0, "missing": [], "mismatched": []}
    for sid in sorted(identities):
        entry = corpus.get(sid)
        if entry is None:
            continue
        # entry["file"] is already root-relative (e.g. "data/originals/..pdf");
        # joining it under another "data/originals/" prefix silently doubled
        # the path and made every PDF look missing (2026-11-01 review finding).
        pdf_path = layout.root / str(entry.get("file") or "")
        if not pdf_path.is_file():
            pdf_bytes["missing"].append(sid)
            continue
        if sha256_file(pdf_path) == entry.get("sha256"):
            pdf_bytes["verified"] += 1
        else:
            pdf_bytes["mismatched"].append(sid)
    report["corpus_manifest"] = {
        "documents": len(corpus),
        "missing_documents": missing_in_corpus,
        "pdf_sha256_mismatches": pdf_mismatches,
        "pdf_bytes": pdf_bytes,
    }
    for sid in missing_in_corpus:
        _fail(failures, "artifact_missing", f"document {sid} absent from corpus/manifest.json")
    for sid in pdf_mismatches:
        _fail(failures, "hash_mismatch", f"document {sid} pdf_sha256 disagrees across manifests")
    for sid in pdf_bytes["mismatched"]:
        _fail(failures, "hash_mismatch", f"document {sid} PDF bytes do not match declared sha256")

    # 6. Full-fidelity element/section exports: enumerate + label lineage.
    # Dissection fixtures are identified separately; the verdict is 'full'
    # only when every corpus document has a v2-compatible element layer.
    full = layout.enumerate_full_fidelity(manifest.get("versions") or {})
    v2_docs = sorted({f["document"] for f in full if f["v2_compatible"]} & set(identities))
    report["full_fidelity"] = {
        "artifacts": full,
        "dissection_fixture_documents": sorted({f["document"] for f in full
                                                if f["kind"] == "dissection_fixture"}),
        "v2_compatible_documents": v2_docs,
        "missing_full_source_documents": sorted(set(identities) - set(v2_docs)),
        "verdict": ("full" if identities and set(v2_docs) >= set(identities)
                    else "chunk_only"),
    }

    # 7. Revision + ACL metadata: available / missing / invalid - never 'public'.
    dms_status, dms, dms_detail = layout.load_mock_dms_documents()
    id_status, ids, id_detail = layout.load_identities()
    acl_status, acl, acl_detail = layout.load_acl_policy()
    # Structural validation of the policy itself (2026-11-01 review, second
    # pass): mirrors the upstream KaryaMeshRAG validator field-for-field. A
    # grant with an unknown effect/subject type, a duplicate grant_id, a
    # malformed/naive timestamp, or a bad target makes the WHOLE policy
    # unusable - downgraded to invalid here, never partially trusted.
    grant_failures: list[str] = []
    if acl_status == STATUS_AVAILABLE:
        grant_failures = validate_acl_policy(acl, known_documents=corpus)
        if grant_failures:
            acl_status = STATUS_INVALID
            acl_detail = f"{len(grant_failures)} grant validation failure(s)"
    report["revision_metadata"] = {
        "status": dms_status, "detail": dms_detail,
        "documents": len(dms) if isinstance(dms, list) else None,
        "all_simulated": (all(d.get("is_simulated") for d in dms)
                          if isinstance(dms, list) and dms else None),
    }
    # Content hashes of the ACL inputs themselves, so a silent edit to
    # identities.json / acl-policy.json (new/changed grants, principal
    # status flips) is independently detectable even if the *version*
    # string is left unchanged (2026-11-01 review finding).
    identities_path = layout.root / "corpus" / "authz" / "identities.json"
    acl_path = layout.root / "corpus" / "authz" / "acl-policy.json"
    report["acl"] = {
        "identities": {"status": id_status, "detail": id_detail,
                       "registry_version": (ids or {}).get("registry_version") if id_status == STATUS_AVAILABLE else None,
                       "principals": len((ids or {}).get("principals", {})) if id_status == STATUS_AVAILABLE else None,
                       "sha256": sha256_file(identities_path) if id_status == STATUS_AVAILABLE else None},
        "policy": {"status": acl_status, "detail": acl_detail,
                   "policy_version": (acl or {}).get("policy_version") if acl_status != STATUS_MISSING else None,
                   "grants": len((acl or {}).get("grants", [])) if acl_status != STATUS_MISSING else None,
                   "sha256": sha256_file(acl_path) if acl_status != STATUS_MISSING else None,
                   "validation_failures": grant_failures[:10]},
        "note": "missing or invalid policy is NOT public; access is denied by default",
    }
    for label, status in (("mock_dms documents", dms_status),
                          ("authz identities", id_status), ("acl policy", acl_status)):
        if status not in (STATUS_AVAILABLE, STATUS_MISSING):
            _fail(failures, "authorization", f"{label} metadata invalid: {status}")
        if status == STATUS_MISSING:
            # Missing metadata is a reportable condition for snapshot building;
            # it is not an inspection failure by itself, but it is never public.
            pass

    report["ok"] = not failures
    return report


def write_report_json(report: dict, out_path: Path) -> None:
    """Persist the machine-readable report (contains no source passages)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_report(report: dict) -> str:
    """Concise human-readable summary for the CLI."""
    lines = [f"source inspect: {report['source_root']} "
             f"[generation={report['generation']} strategy={report['strategy']}]"]
    git = report.get("git") or {}
    if git.get("status") == STATUS_AVAILABLE:
        dirty = f"DIRTY ({git['dirty_entries']} entries)" if git.get("dirty") else "clean"
        lines.append(f"  git: {git['commit'][:12]} tree={dirty}")
    else:
        lines.append("  git: UNKNOWN (upstream commit/tree state could not be determined)")
    ci = report.get("chunks_file") or {}
    if ci:
        ok = "MATCH" if ci.get("matches_frozen_manifest") else "MISMATCH"
        lines.append(f"  chunks: {ci.get('records')} records / {ci.get('documents')} documents, "
                     f"sha256 {str(ci.get('sha256'))[:16]}.. [{ok}]")
    ff = report.get("full_fidelity") or {}
    if ff:
        lines.append(f"  full-fidelity source layer: {ff.get('verdict')} "
                     f"({len(ff.get('v2_compatible_documents') or [])} v2-compatible documents)")
    rm = report.get("revision_metadata") or {}
    lines.append(f"  revision metadata: {rm.get('status')} "
                 f"(documents={rm.get('documents')}, all_simulated={rm.get('all_simulated')})")
    acl = report.get("acl") or {}
    lines.append(f"  acl: identities={acl.get('identities', {}).get('status')} "
                 f"policy={acl.get('policy', {}).get('status')}")
    if report["failures"]:
        lines.append(f"  FAILURES ({len(report['failures'])}):")
        for f in report["failures"]:
            lines.append(f"    - [{f['code']}] {f['message']}")
    lines.append("  verdict: " + ("OK" if report["ok"] else "FAIL"))
    return "\n".join(lines)
