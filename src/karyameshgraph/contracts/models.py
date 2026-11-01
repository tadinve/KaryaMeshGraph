"""Data contract v1: builders and validators for snapshot records (SPEC.md §5).

Everything is plain, JSON-serializable dicts produced and checked by explicit
functions. The hashing conventions mirror the verified upstream rules
(SPEC.md §2 "Verified findings"):

- ``chunk_sha256``  = SHA-256 of the chunk's ``text`` field alone, UTF-8,
  no normalization (upstream ``chunk/fixed.py``).
- ``chunk_uid``     = ``chunk_id + '#' + chunk_sha256[:16]``.
- ``chunk_id``      embeds ``pdf_sha256[:12]`` as its third ``/``-segment.
- file fingerprints = SHA-256 of raw file bytes.

Validators return a list of failure strings (empty = valid). Failure strings
carry identifiers only - never chunk/source text.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..errors import MalformedRecordError

SCHEMA_VERSION = "1.0.0"

# Chunk-record fields the snapshot relies on. The upstream record has more;
# these are the ones every v2 record must provide for identity + verification.
REQUIRED_CHUNK_FIELDS = (
    "chunk_id",
    "chunk_sha256",
    "chunk_uid",
    "corpus_generation",
    "stable_id",
    "object_id",
    "version",
    "pdf_sha256",
    "text",
    "pages",
    "index",
)

# Provenance capability labels (SPEC.md M1.2) and span verification levels
# (SPEC.md §5.5). A chunk_only snapshot can never contain SOURCE_VERIFIED spans.
CAPABILITY_FULL = "full"
CAPABILITY_CHUNK_ONLY = "chunk_only"
CAPABILITY_MISSING = "missing"
LEVEL_SOURCE_VERIFIED = "SOURCE_VERIFIED"
LEVEL_CHUNK_VERIFIED = "CHUNK_VERIFIED"

AUTHORIZATION_MODE_OFFLINE = "offline_simulation"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chunk_text_sha256(text: str) -> str:
    """Upstream convention: hash of the raw text field, UTF-8, nothing else."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expected_chunk_uid(chunk_id: str, chunk_sha256: str) -> str:
    return f"{chunk_id}#{chunk_sha256[:16]}"


def canonical_json(obj: Any) -> str:
    """Deterministic serialization used for content-derived identifiers."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def identity_tuple(rec: dict) -> tuple:
    """The immutable document-version identity (SPEC.md §5.1).

    Raises a typed error (never a bare KeyError) when an identity field is
    absent, so callers fail closed with an actionable record reference.
    """
    try:
        return (rec["stable_id"], rec["object_id"], rec["version"], rec["pdf_sha256"])
    except KeyError as e:
        raise MalformedRecordError(
            "chunk record lacks an identity field",
            field=str(e), chunk_id=rec.get("chunk_id"),
        ) from None


def validate_chunk_record(
    rec: dict, *, expected_generation: str = "v2", expected_stable_id: str | None = None
) -> list[str]:
    """Validate one chunk record. Returns failure reasons (ids only, no text)."""
    if not isinstance(rec, dict):
        return ["record <no-id>: not a JSON object"]
    failures: list[str] = []
    missing = [f for f in REQUIRED_CHUNK_FIELDS if f not in rec]
    rid = rec.get("chunk_uid") or rec.get("chunk_id") or "<no-id>"
    if missing:
        failures.append(f"record {rid}: missing required fields {missing}")
        return failures  # cannot check further without identity/text

    # Type-check identity/hash fields before any slicing or splitting, so a
    # type-malformed record yields a failure string, never a TypeError.
    str_fields = ("chunk_id", "chunk_sha256", "chunk_uid", "pdf_sha256",
                  "stable_id", "object_id", "version", "corpus_generation")
    bad_types = [f for f in str_fields if not isinstance(rec[f], str)]
    if bad_types:
        failures.append(f"record {rid}: non-string identity fields {bad_types}")
        return failures

    text = rec["text"]
    if not isinstance(text, str):
        failures.append(f"record {rid}: text is not a string")
        return failures

    actual_sha = chunk_text_sha256(text)
    if actual_sha != rec["chunk_sha256"]:
        failures.append(
            f"record {rid}: chunk_sha256 mismatch "
            f"(recorded {rec['chunk_sha256'][:16]}.., recomputed {actual_sha[:16]}..)"
        )
    if rec["chunk_uid"] != expected_chunk_uid(rec["chunk_id"], rec["chunk_sha256"]):
        failures.append(f"record {rid}: chunk_uid does not follow chunk_id#sha16 convention")

    # chunk_id embeds pdf_sha256[:12] as segment 2 (0-based) of the '/'-split id.
    parts = rec["chunk_id"].split("/")
    if len(parts) < 4 or parts[2] != rec["pdf_sha256"][:12]:
        failures.append(f"record {rid}: chunk_id does not embed pdf_sha256[:12]")

    if rec["corpus_generation"] != expected_generation:
        failures.append(
            f"record {rid}: corpus_generation={rec['corpus_generation']!r}, "
            f"expected {expected_generation!r}"
        )
    if expected_stable_id is not None and rec["stable_id"] != expected_stable_id:
        failures.append(
            f"record {rid}: stable_id={rec['stable_id']!r} not the expected "
            f"{expected_stable_id!r}"
        )

    # Section self-consistency: a section_id must come with a section path.
    if rec.get("section_id") and not rec.get("section_path"):
        failures.append(f"record {rid}: section_id set but section_path empty")

    # Span bounds (CHUNK_VERIFIED level: offsets exact within chunk text) and
    # page consistency: a span's page must be one of the chunk's pages.
    n = len(text)
    pages = rec.get("pages") if isinstance(rec.get("pages"), list) else None
    for i, span in enumerate(rec.get("source_spans") or []):
        if not isinstance(span, dict):
            failures.append(f"record {rid}: source_spans[{i}] is not an object")
            continue
        cs, ce = span.get("chunk_start"), span.get("chunk_end")
        if not (isinstance(cs, int) and isinstance(ce, int) and 0 <= cs < ce <= n):
            failures.append(f"record {rid}: source_spans[{i}] bounds [{cs},{ce}) outside text[0,{n})")
        pg = span.get("page")
        if pg is not None and pages is not None and pg not in pages:
            failures.append(f"record {rid}: source_spans[{i}] page {pg} not in chunk pages")
        if span.get("verification_level") == LEVEL_SOURCE_VERIFIED:
            failures.append(
                f"record {rid}: source_spans[{i}] claims SOURCE_VERIFIED in a chunk-level record"
            )
    for i, el in enumerate(rec.get("elements") or []):
        if not isinstance(el, dict):
            failures.append(f"record {rid}: elements[{i}] is not an object")
            continue
        cs, ce = el.get("chunk_start"), el.get("chunk_end")
        if not (isinstance(cs, int) and isinstance(ce, int) and 0 <= cs < ce <= n):
            failures.append(f"record {rid}: elements[{i}] bounds [{cs},{ce}) outside text[0,{n})")
    return failures


def document_version_record(
    *,
    chunk_identity: tuple,
    corpus_entry: dict,
    frozen_pdf_sha256: str | None,
    lifecycle_source: dict | None,
    acl_status: str,
    acl_policy_version: str | None,
    registry_version: str | None,
    derived_grants: list[dict] | None,
    provenance_capability: str,
    source_generation: str,
    source_manifest_sha256: str,
) -> dict:
    """Build one DocumentVersion dict (SPEC.md §5.1).

    - ``lifecycle_source`` is the verbatim upstream mock-DMS record (preserving
      null-vs-absent semantics and the ``is_simulated`` markers); ``None`` when
      the upstream record is missing - which is recorded, never invented.
    - ACL linkage is a DERIVED join (upstream has no per-document policy ref);
      ``acl_status`` is one of available/missing/invalid/not_loaded and missing
      is never treated as public.
    """
    stable_id, object_id, version, pdf_sha256 = chunk_identity
    rec: dict = {
        "document_id": stable_id,
        "object_id": object_id,
        "version": version,
        "pdf_sha256": pdf_sha256,
        "title": corpus_entry.get("source_title"),
        "source_document_number": corpus_entry.get("source_document_number"),
        "source_series": corpus_entry.get("source_series"),
        "source_generation": source_generation,
        "source_manifest_sha256": source_manifest_sha256,
        "pdf_sha256_cross_checks": {
            "corpus_manifest": corpus_entry.get("sha256") == pdf_sha256,
            "frozen_manifest": (frozen_pdf_sha256 == pdf_sha256) if frozen_pdf_sha256 else None,
            "mock_dms": (
                (lifecycle_source.get("source_sha256") == pdf_sha256)
                if lifecycle_source
                else None
            ),
        },
        "lifecycle_source": lifecycle_source,  # verbatim; None = missing upstream
        "approval_status": (lifecycle_source or {}).get("lifecycle_state", "missing"),
        "is_simulated": (lifecycle_source or {}).get("is_simulated"),
        "simulated_fields": (lifecycle_source or {}).get("simulated_fields"),
        "acl": {
            "status": acl_status,
            "policy_version": acl_policy_version,
            "registry_version": registry_version,
            # Grants keep their effect: an explicit deny must never read as
            # an allow. None = policy missing/invalid (never 'public').
            "derived_grants": derived_grants,
            "derivation": "derived",  # never upstream fact (SPEC.md §5.1)
        },
        "provenance_capability": provenance_capability,
        # chunk_only snapshots must refuse later rule extraction (SPEC.md M1.2).
        "extraction_eligible": provenance_capability == CAPABILITY_FULL,
        "verification_level_ceiling": (
            LEVEL_SOURCE_VERIFIED
            if provenance_capability == CAPABILITY_FULL
            else LEVEL_CHUNK_VERIFIED
        ),
    }
    return rec


def eligibility_for(lifecycle_source: dict | None) -> dict:
    """Offline-simulated eligibility (SPEC.md §5.1). Explicitly simulated,
    never production authorization. ``evaluated_at`` is stamped by the caller
    and kept out of content-derived identity."""
    if lifecycle_source is None:
        return {"status": "unknown_lifecycle_missing", "basis": "offline_simulation"}
    state = lifecycle_source.get("lifecycle_state")
    withdrawn = lifecycle_source.get("withdrawn_at")
    eligible = state == "APPROVED" and withdrawn in (None, "")
    return {
        "status": "eligible_offline_simulated" if eligible else "not_eligible_offline_simulated",
        "basis": "offline_simulation",
        "lifecycle_state": state,
    }


def snapshot_identity(
    *,
    source_fingerprint_sha256: str,
    generation: str,
    strategy: str,
    document_ids: list[str],
    artifacts: list[dict],
) -> str:
    """Content-derived snapshot id (SPEC.md §5.6).

    Depends only on verified content: the frozen source fingerprint, the
    generation/strategy, the document set, and every written artifact's path
    and hash. Timestamps and machine paths are excluded, so a repeat build
    from unchanged inputs yields the same id.
    """
    core = {
        "schema_version": SCHEMA_VERSION,
        "source_fingerprint_sha256": source_fingerprint_sha256,
        "corpus_generation": generation,
        "strategy": strategy,
        "documents": sorted(document_ids),
        "artifacts": sorted((a["path"], a["sha256"]) for a in artifacts),
    }
    return "ksnap-" + hashlib.sha256(canonical_json(core).encode("utf-8")).hexdigest()
