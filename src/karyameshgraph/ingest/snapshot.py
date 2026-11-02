"""`snapshot build` - one-way, verified, atomic import (SPEC.md M1.2).

Fail-closed rules implemented here:
- generation is what the caller asked for; the frozen fingerprint must match
  the bytes on disk before anything is copied (no v1 fallback, ever);
- the authorization mode must be ``offline_simulation`` with an ACTIVE
  principal from the snapshotted identity registry; missing identities fail
  the build; missing ACL policy is recorded as missing - never public;
- every REQUESTED document is evaluated against the snapshotted ACL policy
  (``ingest.authz``, explicit deny > allow > default deny); a principal that
  does not resolve to ``allow`` for a document never has that document's
  content copied - the whole build refuses closed instead (SPEC.md M5);
- an ACL policy that fails structural validation (unknown effect/subject
  type, duplicate grant_id, malformed/naive timestamp, bad target) is
  unusable as a WHOLE policy, never partially trusted;
- ``document_mode="current"`` (default) ALSO requires every document to be
  a currently eligible SOP (approved, not withdrawn) - an ACL allow is a
  separate axis and never substitutes for lifecycle eligibility;
  ``document_mode="historical_archive"`` bypasses that gate only under
  explicit ``archive_mode_authorized=True`` (SPEC.md §5.7);
- every document is labeled with its actual provenance capability
  (``chunk_only`` today); ``extraction_eligible`` is false for chunk_only;
- files are written into a temporary sibling directory and renamed into
  place only after internal verification passes; a failed build leaves a
  ``<out>.failed.json`` report and no snapshot directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..contracts.models import (
    AUTHORIZATION_MODE_OFFLINE,
    CAPABILITY_CHUNK_ONLY,
    SCHEMA_VERSION,
    document_version_record,
    eligibility_for,
    identity_tuple,
    sha256_bytes,
    snapshot_identity,
)
from ..contracts.models import validate_chunk_record
from ..errors import (
    ArtifactMissingError,
    AuthorizationError,
    DuplicateIdentityError,
    HashMismatchError,
    LifecycleIneligibleError,
    MalformedRecordError,
    SnapshotStateError,
)
from .authz import (
    DOCUMENT_MODE_CURRENT,
    DOCUMENT_MODES,
    DOCUMENT_MODE_HISTORICAL_ARCHIVE,
    EFFECT_ALLOW,
    evaluate_document_access,
    validate_acl_policy,
)
from .source import STATUS_AVAILABLE, SourceLayout, sha256_file
from .verify import verify_content



def _derived_grants(acl: dict | None, stable_id: str, series: str | None) -> list[dict] | None:
    """DERIVED document→grant join (upstream has no per-document policy ref).

    Grants keep their effect and subject so an explicit deny is never
    indistinguishable from an allow downstream.
    """
    if not acl:
        return None
    out = []
    for g in acl.get("grants", []):
        matches = (g.get("document_id") in (stable_id, "*")
                   or (g.get("document_series") and series
                       and g["document_series"] == series))
        if matches:
            out.append({"grant_id": g.get("grant_id"), "effect": g.get("effect"),
                        "subject_type": g.get("subject_type"),
                        "subject_id": g.get("subject_id")})
    return out


def _refuse_unignored_output(out_dir: Path) -> None:
    """Refuse a snapshot destination that git would commit (SPEC.md M1.2:
    snapshots hold real SOP text and the repo is public). Paths outside any
    git work tree are fine; inside one, the path must be gitignored."""
    probe = out_dir.resolve()
    # Anchor at the nearest EXISTING ancestor of the destination itself -
    # never the cwd, which would answer the question for the wrong directory
    # when the destination's parents do not exist yet.
    anchor = next((a for a in probe.parents if a.is_dir()), None)
    if anchor is None:
        return
    try:
        inside = subprocess.run(
            ["git", "-C", str(anchor), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return  # not inside any git work tree: nothing to commit from
        ignored = subprocess.run(
            ["git", "-C", str(anchor), "check-ignore", "-q", str(probe)],
            capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        # git exists but could not answer: inside-a-work-tree status is
        # unknown, so fail closed rather than silently allowing the write.
        raise SnapshotStateError(
            "could not determine whether the snapshot output is gitignored; "
            "refusing to write real SOP text (pass allow_unignored_out=True "
            "to override deliberately)",
            out=str(probe),
        ) from None
    if ignored.returncode != 0:
        raise SnapshotStateError(
            "snapshot output is inside a git work tree but not gitignored; "
            "real SOP text must never be committable (use data/snapshots/ or "
            "pass allow_unignored_out=True deliberately)",
            out=str(probe),
        )


def build_snapshot(
    layout: SourceLayout,
    documents: list[str],
    out_dir: Path,
    *,
    principal: str,
    authorization_mode: str,
    created_at: str,
    allow_unignored_out: bool = False,
    document_mode: str = DOCUMENT_MODE_CURRENT,
    archive_mode_authorized: bool = False,
) -> dict:
    """Build and internally verify a snapshot; returns the written manifest.

    Raises a typed error (and writes ``<out>.failed.json``) instead of ever
    leaving a partially-written snapshot at ``out_dir``.

    ``document_mode`` (SPEC.md §5.7, 2026-11-01 review second pass) gates
    LIFECYCLE eligibility independently of the ACL decision above - "an ACL
    allow does not itself make a withdrawn or ineffective SOP eligible":

    - ``"current"`` (default): every requested document must ALSO be a
      currently eligible SOP (``eligibility_for`` resolves to
      ``eligible_offline_simulated``: approved, not withdrawn). A document
      that is ACL-allowed but lifecycle-ineligible still refuses the whole
      build (``LifecycleIneligibleError``) - this is a snapshot of the
      currently-in-force, authorized document pool, never a mix of the two.
    - ``"historical_archive"``: deliberately bypasses the eligibility gate
      (the ACL gate still applies) for an explicitly authorized archival or
      structural-analysis import that intentionally wants withdrawn/
      ineligible documents too - e.g. M1.5's full-corpus structural graph,
      which analyzes every document's STRUCTURE regardless of its current
      production eligibility. Requires ``archive_mode_authorized=True``;
      requesting this mode without it is refused (``AuthorizationError``),
      so the bypass can never happen by accident.
    """
    if document_mode not in DOCUMENT_MODES:
        raise SnapshotStateError("unknown document_mode", mode=document_mode,
                                 supported=list(DOCUMENT_MODES))
    if document_mode == DOCUMENT_MODE_HISTORICAL_ARCHIVE and not archive_mode_authorized:
        raise AuthorizationError(
            "document_mode='historical_archive' requires explicit "
            "archive_mode_authorized=True; refusing to import lifecycle-"
            "ineligible documents without deliberate, recorded authorization",
            document_mode=document_mode,
        )
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise SnapshotStateError("snapshot output directory already exists; refusing to overwrite",
                                 out=str(out_dir))
    documents = sorted(set(documents))  # duplicates would double-count silently
    if not documents:
        raise ArtifactMissingError("no documents requested")
    if not allow_unignored_out:
        _refuse_unignored_output(out_dir)

    # Authorization context - offline simulation only (SPEC.md M1.3).
    if authorization_mode != AUTHORIZATION_MODE_OFFLINE:
        raise AuthorizationError("unsupported authorization mode",
                                 mode=authorization_mode, supported=AUTHORIZATION_MODE_OFFLINE)
    id_status, identities, id_detail = layout.load_identities()
    if id_status != STATUS_AVAILABLE:
        raise AuthorizationError("identity registry unavailable; cannot validate principal",
                                 status=id_status, detail=id_detail)
    pinfo = (identities.get("principals") or {}).get(principal)
    if not pinfo or pinfo.get("status") != "active":
        raise AuthorizationError("principal unknown or not active in identity registry",
                                 principal=principal)

    # Source verification before any copy (fail closed).
    layout.check_root()
    manifest_src = layout.load_frozen_manifest()
    fp = manifest_src["fingerprints"][layout.strategy]
    chunks_path = layout.chunks_path(manifest_src)
    if not chunks_path.is_file():
        raise ArtifactMissingError("chunks file missing", path=str(chunks_path))
    actual_sha = sha256_file(chunks_path)
    if actual_sha != fp["sha256"]:
        raise HashMismatchError("chunks file does not match frozen fingerprint; refusing import",
                                recorded=fp["sha256"], actual=actual_sha)

    corpus = layout.load_corpus_manifest()
    # A document the corpus manifest doesn't even know about is a plain
    # "document does not exist" failure (ArtifactMissingError), never an
    # authorization or eligibility question - check this FIRST so a bogus
    # document_id never reaches (and is never mischaracterized by) the ACL
    # or lifecycle gates below.
    unknown_documents = sorted(d for d in documents if d not in corpus)
    if unknown_documents:
        raise ArtifactMissingError("document(s) absent from corpus/manifest.json",
                                   documents=unknown_documents)
    source_manifest_sha = sha256_file(layout.frozen_manifest_path)
    frozen_pdfs = (manifest_src.get("inputs") or {}).get("pdf_sha256") or {}
    dms_status, dms_docs, _ = layout.load_mock_dms_documents()
    dms_by_id = ({d.get("document_id"): d for d in dms_docs}
                 if dms_status == STATUS_AVAILABLE and isinstance(dms_docs, list) else {})
    acl_status, acl, _ = layout.load_acl_policy()

    # Structural policy validation (2026-11-01 review, second pass): a
    # policy with an unknown effect/subject type, a duplicate grant_id, a
    # malformed/naive timestamp, or a bad target is UNUSABLE as a whole -
    # never partially trusted. Downgrading it to "unusable" here (acl=None)
    # makes every document resolve to default_deny below, which the
    # existing fail-closed `denied` check already refuses the build on -
    # but we raise a distinctly labeled error first so an operator sees
    # "the policy itself is broken" rather than "every document was denied".
    if acl_status == STATUS_AVAILABLE:
        policy_failures = validate_acl_policy(acl, known_documents=corpus)
        if policy_failures:
            raise AuthorizationError(
                "ACL policy fails structural validation; refusing import - "
                "the whole policy is unusable, not just the offending grant(s)",
                failures=policy_failures[:10], failure_count=len(policy_failures),
            )

    # Document-level authorization (SPEC.md M5, 2026-11-01 review finding):
    # an active principal is NOT automatically authorized for every document.
    # Evaluate the upstream policy's own resolution order per requested
    # document and refuse the entire build if any document does not resolve
    # to an explicit allow - a denied document's content is never copied,
    # and the build fails before any chunk line is even collected.
    decisions: dict[str, dict] = {}
    denied: list[str] = []
    for sid in sorted(documents):
        entry = corpus.get(sid)
        decision = evaluate_document_access(
            principal=principal, pinfo=pinfo, document_id=sid,
            document_series=entry.get("source_series") if entry else None,
            acl=acl if acl_status == STATUS_AVAILABLE else None,
            policy_time_iso=created_at,
        )
        decisions[sid] = decision
        if decision["effect"] != EFFECT_ALLOW:
            denied.append(sid)
    if denied:
        raise AuthorizationError(
            "principal is not authorized (offline-simulation ACL policy) for "
            "one or more requested documents; refusing import - no document "
            "content is copied for a denied document",
            principal=principal, denied_documents=denied,
        )

    # Lifecycle eligibility (SPEC.md §5.7, 2026-11-01 review second pass):
    # an ACL allow is NOT lifecycle eligibility. Under the default "current"
    # mode, every requested document must ALSO be a currently eligible SOP
    # (approved, not withdrawn) - independent of, and in addition to, the
    # ACL decision above. "historical_archive" deliberately bypasses this
    # gate only when the caller explicitly authorized it (checked earlier).
    if document_mode == DOCUMENT_MODE_CURRENT:
        ineligible = []
        for sid in sorted(documents):
            lifecycle = dms_by_id.get(sid)
            if eligibility_for(lifecycle)["status"] != "eligible_offline_simulated":
                ineligible.append(sid)
        if ineligible:
            raise LifecycleIneligibleError(
                "one or more requested documents are not currently eligible "
                "SOPs (not approved, or withdrawn); an ACL allow does not "
                "make them eligible - pass document_mode='historical_archive' "
                "with archive_mode_authorized=True for a deliberate archival "
                "import if that is actually intended",
                ineligible_documents=ineligible, document_mode=document_mode,
            )

    # Full-fidelity availability. Importing element/section artifacts is NOT
    # implemented yet, so a document with a demonstrably v2-compatible full
    # layer is refused loudly rather than mislabeled 'full' without its
    # artifacts, or silently downgraded to chunk_only (SPEC.md M1.2).
    full = layout.enumerate_full_fidelity(manifest_src.get("versions") or {})
    v2_full_docs = {f["document"] for f in full if f["v2_compatible"]}
    upgradable = sorted(v2_full_docs & set(documents))
    if upgradable:
        raise SnapshotStateError(
            "v2-compatible full source layers exist for requested documents, but "
            "importing element/section artifacts is not implemented; extend the "
            "builder before snapshotting these documents",
            documents=upgradable,
        )

    # Collect the requested documents' chunk lines verbatim, in file order.
    # Records are validated here as well (fail fast with the line number);
    # the internal verify pass re-checks everything independently after write.
    requested = set(documents)
    lines_by_doc: dict[str, list[bytes]] = {d: [] for d in documents}
    ids_by_doc: dict[str, set] = {d: set() for d in documents}
    for line_no, raw, rec in layout.stream_chunk_records(chunks_path):
        sid = rec.get("stable_id")
        if sid in requested:
            problems = validate_chunk_record(
                rec, expected_generation=layout.generation, expected_stable_id=sid)
            if problems:
                raise MalformedRecordError(
                    "invalid chunk record in source; refusing import",
                    line=line_no, first_problem=problems[0],
                )
            lines_by_doc[sid].append(raw if raw.endswith(b"\n") else raw + b"\n")
            ids_by_doc[sid].add(identity_tuple(rec))
    absent = sorted(d for d in documents if not lines_by_doc[d])
    if absent:
        raise ArtifactMissingError("requested documents not present in the chunks corpus",
                                   documents=absent)

    # Assemble DocumentVersion records (identity + lineage + authz context).
    doc_records: list[dict] = []
    for sid in sorted(documents):
        tuples = sorted(ids_by_doc[sid])
        if len(tuples) != 1:
            raise DuplicateIdentityError(
                "document resolves to multiple identity tuples; refusing import",
                document=sid, tuple_count=len(tuples),
            )
        ident = tuples[0]
        entry = corpus.get(sid)
        if entry is None:
            raise ArtifactMissingError("document absent from corpus/manifest.json", document=sid)
        lifecycle = dms_by_id.get(sid)
        rec = document_version_record(
            chunk_identity=ident,
            corpus_entry=entry,
            frozen_pdf_sha256=frozen_pdfs.get(sid) if isinstance(frozen_pdfs, dict) else None,
            lifecycle_source=lifecycle,
            acl_status=acl_status,
            acl_policy_version=(acl or {}).get("policy_version") if acl_status == STATUS_AVAILABLE else None,
            registry_version=identities.get("registry_version"),
            derived_grants=_derived_grants(acl if acl_status == STATUS_AVAILABLE else None,
                                           sid, entry.get("source_series")),
            authorization_decision=decisions[sid],
            provenance_capability=CAPABILITY_CHUNK_ONLY,
            source_generation=layout.generation,
            source_manifest_sha256=source_manifest_sha,
        )
        # Deterministic eligibility only; the evaluation timestamp lives in the
        # manifest (excluded from content identity), keeping repeat builds
        # byte-identical in documents.json.
        rec["eligibility"] = eligibility_for(lifecycle)
        bad = [k for k, v in rec["pdf_sha256_cross_checks"].items() if v is False]
        if bad:
            raise HashMismatchError("pdf_sha256 cross-check failed", document=sid, sources=bad)
        doc_records.append(rec)

    # Write everything into a temporary sibling, then verify, then rename.
    tmp = out_dir.with_name(out_dir.name + f".tmp-{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    artifacts: list[dict] = []

    def _write(rel: str, data: bytes) -> None:
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        artifacts.append({"path": rel, "sha256": sha256_bytes(data), "bytes": len(data)})

    try:
        chunk_count = 0
        for sid in sorted(documents):
            payload = b"".join(lines_by_doc[sid])
            chunk_count += len(lines_by_doc[sid])
            _write(f"chunks/{sid}.jsonl", payload)
        _write("documents.json",
               (json.dumps(doc_records, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        _write("source/frozen-manifest.json",
               layout.frozen_manifest_path.read_bytes())
        _write("source/corpus-manifest.selected.json",
               (json.dumps([corpus[s] for s in sorted(documents)], indent=2, sort_keys=True) + "\n").encode("utf-8"))
        if id_status == STATUS_AVAILABLE:
            _write("authz/identities.json",
                   (layout.root / "corpus/authz/identities.json").read_bytes())
        if acl_status == STATUS_AVAILABLE:
            _write("authz/acl-policy.json",
                   (layout.root / "corpus/authz/acl-policy.json").read_bytes())

        snapshot_id = snapshot_identity(
            source_fingerprint_sha256=fp["sha256"],
            generation=layout.generation,
            strategy=layout.strategy,
            document_ids=sorted(documents),
            artifacts=artifacts,
        )
        git = layout.git_state()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "source_project": "KaryaMeshScratch",
            "source_root": str(layout.root),
            "source_commit": git.get("commit"),
            "source_tree_dirty": git.get("dirty"),
            "corpus_generation": layout.generation,
            "strategy": layout.strategy,
            "source_manifest_sha256": source_manifest_sha,
            "source_chunks_sha256": fp["sha256"],
            "document_count": len(documents),
            "chunk_count": chunk_count,
            "documents": doc_records,
            "artifacts": artifacts,
            "authorization_context": {
                "mode": AUTHORIZATION_MODE_OFFLINE,
                "principal": principal,
                "registry_version": identities.get("registry_version"),
                "policy_version": (acl or {}).get("policy_version") if acl_status == STATUS_AVAILABLE else None,
                "policy_status": acl_status,
                # The "current policy time" the upstream policy note requires
                # (never a historical as-of); every included document's
                # authorization_decision was evaluated at this instant.
                "policy_time": created_at,
                "is_production_authorization": False,
                # Lifecycle-eligibility gate (SPEC.md §5.7): "current" means
                # every document here is ALSO a currently eligible SOP, not
                # merely ACL-allowed; "historical_archive" deliberately
                # bypassed that gate under explicit authorization.
                "document_mode": document_mode,
                "archive_mode_authorized": archive_mode_authorized,
            },
            "created_at": created_at,  # informational; excluded from snapshot_id
            "eligibility_evaluated_at": created_at,
            "validation_status": "pending",
        }

        # Internal verification of what was actually written (fail closed).
        content_failures = verify_content(tmp, manifest)
        if content_failures:
            failed_report = {"snapshot_id": snapshot_id, "out": str(out_dir),
                             "failures": content_failures, "created_at": created_at}
            fail_path = out_dir.with_name(out_dir.name + ".failed.json")
            fail_path.parent.mkdir(parents=True, exist_ok=True)
            fail_path.write_text(json.dumps(failed_report, indent=2) + "\n", encoding="utf-8")
            raise SnapshotStateError("internal verification failed; snapshot not finalized",
                                     failures=len(content_failures), report=str(fail_path))

        manifest["validation_status"] = "verified"
        (tmp / "snapshot-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        os.rename(tmp, out_dir)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return manifest
