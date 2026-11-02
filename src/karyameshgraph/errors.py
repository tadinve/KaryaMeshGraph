"""Typed errors for KaryaMeshGraph (SPEC.md §0.6, M1.4).

Every error carries a stable ``code`` plus identifier context (document ids,
chunk uids, paths, hashes, counts). Error messages must NEVER contain source
document text - only identifiers and reasons. Tests enforce this.
"""

from __future__ import annotations


class KMGError(Exception):
    """Base class. ``context`` holds identifiers only, never source text."""

    code = "kmg_error"

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.context = context

    def __str__(self) -> str:  # include context ids for actionable CLI output
        base = super().__str__()
        if self.context:
            ctx = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
            return f"[{self.code}] {base} ({ctx})"
        return f"[{self.code}] {base}"


class SourceRootError(KMGError):
    """Source checkout missing, unreadable, or not the expected project."""

    code = "source_root"


class FrozenManifestError(KMGError):
    """Frozen corpus manifest missing, unparseable, or inconsistent."""

    code = "frozen_manifest"


class GenerationMismatchError(KMGError):
    """Requested generation does not match what the source declares.

    Fail closed: never silently substitute another generation (no v1 fallback).
    """

    code = "generation_mismatch"


class ArtifactMissingError(KMGError):
    """A required source artifact or requested document is absent."""

    code = "artifact_missing"


class HashMismatchError(KMGError):
    """A recomputed SHA-256 does not equal the recorded/expected value."""

    code = "hash_mismatch"


class MalformedRecordError(KMGError):
    """A JSONL line is truncated, invalid JSON, or missing required fields."""

    code = "malformed_record"


class DuplicateIdentityError(KMGError):
    """One document resolves to multiple incompatible identity tuples,
    or one chunk_id carries conflicting content."""

    code = "duplicate_identity"


class FixtureMixingError(KMGError):
    """Dissection-fixture or non-v2 lineage presented as frozen v2."""

    code = "fixture_mixing"


class AuthorizationError(KMGError):
    """Authorization context invalid: unknown/disabled principal, missing
    identity registry, or unsupported mode. Missing policy never means public.
    Also raised when an ACL policy fails structural validation (unknown
    effect/subject type, duplicate grant id, malformed timestamp, ...) -
    the whole policy is then unusable, not just the bad grant - and when
    ``document_mode="historical_archive"`` is requested without the explicit
    ``archive_mode_authorized=True`` flag."""

    code = "authorization"


class LifecycleIneligibleError(KMGError):
    """A requested document is not a currently eligible SOP (not approved,
    or withdrawn) under ``document_mode="current"``. An ACL allow is a
    SEPARATE axis (SPEC.md §5.7) and never substitutes for lifecycle
    eligibility; importing an ineligible document requires the caller to
    deliberately opt into ``document_mode="historical_archive"`` with
    ``archive_mode_authorized=True``."""

    code = "lifecycle_ineligible"


class SnapshotStateError(KMGError):
    """Snapshot directory in an unexpected state (exists, partial, tampered),
    or a build failed verification and was not finalized."""

    code = "snapshot_state"
