"""Document-level ACL evaluation — offline simulation (SPEC.md M1.3, M5).

Prior to the 2026-11-01 review this module's predecessor recorded whichever
grants happened to match a document as informational ``derived_grants``
without ever enforcing them - an active principal was silently treated as
authorized for every document. That gap is closed: the upstream policy's
own documented resolution order (``explicit deny`` > ``allow`` >
``default deny``) is evaluated per document, and a document the principal
is not allowed to see is never copied into the snapshot.

This 2026-11-01 (second pass) review added **structural policy validation**
that mirrors the upstream KaryaMeshRAG validator verbatim
(``karyamesh.authz.policy.AclPolicy.__init__`` in the KaryaMeshScratch
checkout): a grant with an unknown effect, an unknown/incomplete subject, a
target that is not exactly one of ``document_id``/``document_series``, a
non-ISO-8601 or timezone-naive timestamp, a duplicate ``grant_id``, or an
``expires_at``/``revoked_at`` that does not follow ``granted_at`` makes the
**entire policy** unusable - never just the one bad grant. It also fixes
two real fail-open bugs the first pass left behind:

- ``evaluate_document_access`` used to fall back to live wall-clock
  ``datetime.now()`` when ``policy_time_iso`` failed to parse, which both
  broke reproducibility (a non-deterministic "now" leaking into a supposedly
  offline, content-derived decision) and could silently resurrect access
  that would otherwise have expired/been revoked under the real, intended
  policy time. It now fails closed (``invalid_policy_time``).
- ``_grant_active`` used to silently *ignore* a malformed ``expires_at`` or
  ``revoked_at`` field, treating the grant as if it had **no** expiry or
  revocation (i.e. "unrestricted") rather than failing. It now treats any
  grant carrying an unparseable timestamp as inactive.

Both are defense-in-depth: by construction, a policy that reaches either
function has already passed ``validate_acl_policy`` at every call site
(``ingest.inspect``, ``ingest.snapshot``, ``ingest.verify``), so a malformed
timestamp should never actually be evaluated here - but a function must
never assume its caller validated correctly.

Also offline simulation, not production authorization:

- ``policy_time`` is the import's ``created_at`` (the "request's CURRENT
  policy time" the upstream policy note requires), not live wall-clock time
  pulled from the production access-control system, and there is
  deliberately no parameter anywhere in this module or its callers that
  lets a caller re-evaluate the ACL policy "as of" an arbitrary historical
  instant (the upstream design reserves that - its ``audit_as_of`` - for a
  separately authorized audit mode this project does not implement).
- Group membership is resolved only from the snapshotted identity registry,
  per its own note ("groups supplied by a request are ignored").
- A ``default_deny`` resolution (no matching grant at all) is NOT public —
  a missing/invalid policy evaluates every document to ``default_deny``.
- A successful import decision here is never proof that any end user may
  view the content at query time; the eventual query layer must reauthorize
  under current policy, independent of this import-time decision.
- An ``allow`` ACL decision is about *who may see this document's content*;
  it says nothing about whether the document is a currently *eligible*
  (approved, not withdrawn) SOP. See ``DOCUMENT_MODE_CURRENT`` /
  ``DOCUMENT_MODE_HISTORICAL_ARCHIVE`` below - the two are evaluated, and
  gated, independently (``ingest.snapshot.build_snapshot``).
"""

from __future__ import annotations

from datetime import datetime

RESOLUTION_EXPLICIT_DENY = "explicit_deny"
RESOLUTION_ALLOW = "allow"
RESOLUTION_DEFAULT_DENY = "default_deny"
RESOLUTION_INVALID_POLICY_TIME = "invalid_policy_time"

EFFECT_ALLOW = "allow"
EFFECT_DENY = "deny"
EFFECTS = (EFFECT_ALLOW, EFFECT_DENY)
SUBJECT_TYPES = ("principal", "group")
REQUIRED_GRANT_FIELDS = ("grant_id", "subject_type", "subject_id", "effect", "granted_at")

# Document lifecycle vs. ACL authorization are independent axes (SPEC.md
# §5.7). DOCUMENT_MODE_CURRENT requires BOTH an ACL allow AND lifecycle
# eligibility (approved, not withdrawn) for every requested document -
# mirroring the upstream secure-retrieval design's "authorized pool =
# eligible ∧ allowed". DOCUMENT_MODE_HISTORICAL_ARCHIVE deliberately bypasses
# the eligibility gate (an ACL allow is still mandatory) for an explicitly
# authorized archival/structural-analysis import that intentionally wants
# withdrawn or otherwise ineligible documents too; it must never be the
# default and must never be reachable without the caller setting
# ``archive_mode_authorized=True``.
DOCUMENT_MODE_CURRENT = "current"
DOCUMENT_MODE_HISTORICAL_ARCHIVE = "historical_archive"
DOCUMENT_MODES = (DOCUMENT_MODE_CURRENT, DOCUMENT_MODE_HISTORICAL_ARCHIVE)


def _parse(ts: str | None) -> datetime | None:
    """Lenient parse for internal use once a policy is already known-valid.
    Returns ``None`` (never raises) for anything unparseable - callers must
    treat ``None`` as "fail closed", never as "no constraint"."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_aware(ts: str | None) -> datetime | None:
    """Like ``_parse``, but a timezone-naive result is ALSO treated as
    unparseable (upstream: "must be a timezone-aware timestamp")."""
    dt = _parse(ts)
    if dt is None or dt.tzinfo is None:
        return None
    return dt


def principal_groups(pinfo: dict) -> set[str]:
    groups = pinfo.get("groups") if isinstance(pinfo, dict) else None
    return set(groups) if isinstance(groups, list) else set()


def validate_acl_policy(
    acl: dict, known_documents: dict[str, dict] | None = None
) -> list[str]:
    """Structurally validate an ACL policy, mirroring the upstream
    KaryaMeshRAG validator (``karyamesh.authz.policy.AclPolicy.__init__``)
    field-for-field: unknown effect, unknown/incomplete subject, a target
    that is not exactly one of ``document_id``/``document_series``, a
    duplicate ``grant_id``, a non-ISO-8601 or timezone-naive timestamp, and
    ``expires_at``/``revoked_at`` not following ``granted_at``.

    ``known_documents`` (``stable_id -> corpus-manifest entry``), when
    supplied, additionally checks that every ``document_id``/
    ``document_series`` target actually refers to a real document/series -
    matching upstream's own referential check. It is deliberately **optional**
    because the independent snapshot *verifier* only has the snapshot's own
    partial document selection embedded (``source/corpus-manifest.selected.json``),
    not the full corpus; re-running the referential check there would flag
    a perfectly legitimate grant naming a document outside this particular
    snapshot's selection as "unknown". Callers with access to the full
    corpus manifest (``ingest.inspect``, ``ingest.snapshot``) MUST pass it;
    ``ingest.verify`` deliberately does not (documented at its call site).

    Returns failure reason strings (grant ids and field names only, never
    grant content beyond ids). **A non-empty result means the whole policy
    is unusable** - every caller must then treat the policy exactly like a
    missing one (``acl=None``, default deny for every document), never
    silently evaluate only the well-formed grants.
    """
    failures: list[str] = []
    if not isinstance(acl, dict):
        return ["acl-policy.json is not an object"]
    grants = acl.get("grants")
    if not isinstance(grants, list):
        return ["acl-policy.json 'grants' is not a list"]
    known_series = (
        {d.get("source_series") for d in known_documents.values()}
        if known_documents is not None else None
    )
    seen_ids: set[str] = set()
    for g in grants:
        if not isinstance(g, dict):
            failures.append("a grant entry is not an object")
            continue
        gid = g.get("grant_id")
        label = gid if isinstance(gid, str) and gid else "<no id>"
        if not isinstance(gid, str) or not gid:
            failures.append("a grant is missing a string grant_id")
        elif gid in seen_ids:
            failures.append(f"{label}: duplicate grant_id")
        else:
            seen_ids.add(gid)
        missing = [f for f in REQUIRED_GRANT_FIELDS if not g.get(f)]
        if missing:
            failures.append(f"{label}: missing required field(s) {missing}")
            continue  # cannot check further without the basics
        if g.get("effect") not in EFFECTS:
            failures.append(f"{label}: unknown effect {g.get('effect')!r}")
        if g.get("subject_type") not in SUBJECT_TYPES:
            failures.append(f"{label}: unknown subject_type {g.get('subject_type')!r}")
        targets = [k for k in ("document_id", "document_series") if g.get(k)]
        if len(targets) != 1:
            failures.append(f"{label}: exactly one of document_id/document_series is required")
        if known_documents is not None:
            did = g.get("document_id")
            if did and did != "*" and did not in known_documents:
                failures.append(f"{label}: unknown document {did!r}")
            series = g.get("document_series")
            if series and series not in known_series:
                failures.append(f"{label}: unknown document_series {series!r}")
        granted = _parse_aware(g.get("granted_at"))
        if granted is None:
            failures.append(f"{label}: granted_at is not a timezone-aware ISO-8601 timestamp")
        for field in ("expires_at", "revoked_at"):
            raw = g.get(field)
            if raw is None:
                continue
            parsed = _parse_aware(raw)
            if parsed is None:
                failures.append(f"{label}: {field} is not a timezone-aware ISO-8601 timestamp")
            elif granted is not None and parsed <= granted:
                failures.append(f"{label}: {field} must follow granted_at")
    return failures


def _grant_active(grant: dict, policy_time: datetime) -> bool:
    """Inclusive ``granted_at``; exclusive ``expires_at``; ``revoked_at``
    ends the grant from that instant (upstream policy note, verbatim). A
    grant carrying a *present but unparseable* timestamp is treated as
    INACTIVE (fail closed) - never as "that bound does not apply"/
    unrestricted, which was the 2026-11-01 first-pass bug."""
    for field, exclusive in (("granted_at", False), ("expires_at", True), ("revoked_at", True)):
        raw = grant.get(field)
        if raw is None:
            continue
        ts = _parse(raw)
        if ts is None:
            return False  # malformed timestamp present: inert, not unrestricted
        if field == "granted_at":
            if policy_time < ts:
                return False
        elif policy_time >= ts:
            return False
    return True


def _subject_matches(grant: dict, principal: str, groups: set[str]) -> bool:
    stype, sid = grant.get("subject_type"), grant.get("subject_id")
    if stype == "principal":
        return sid == principal
    if stype == "group":
        return sid in groups
    return False


def _document_matches(grant: dict, document_id: str, document_series: str | None) -> bool:
    gid = grant.get("document_id")
    if gid is not None and gid in (document_id, "*"):
        return True
    gseries = grant.get("document_series")
    return bool(gseries and document_series and gseries == document_series)


def evaluate_document_access(
    *,
    principal: str,
    pinfo: dict,
    document_id: str,
    document_series: str | None,
    acl: dict | None,
    policy_time_iso: str,
) -> dict:
    """Evaluate one document for ``principal`` at ``policy_time_iso``.

    Returns ``{"effect": "allow"|"deny", "resolution": ..., "matched_grant_ids": [...],
    "policy_time": policy_time_iso}``. ``acl=None`` (missing/invalid policy)
    matches no grants and resolves to ``default_deny`` - never allow. An
    unparseable or timezone-naive ``policy_time_iso`` is `invalid_policy_time`
    - a hard deny, NEVER a silent fall back to live wall-clock time (the
    2026-11-01 first-pass bug: see module docstring).
    """
    policy_time = _parse_aware(policy_time_iso)
    if policy_time is None:
        return {"effect": EFFECT_DENY, "resolution": RESOLUTION_INVALID_POLICY_TIME,
                "matched_grant_ids": [], "policy_time": policy_time_iso}
    groups = principal_groups(pinfo if isinstance(pinfo, dict) else {})
    grants = (acl or {}).get("grants") or []
    matched_deny: list[str] = []
    matched_allow: list[str] = []
    for g in grants:
        if not isinstance(g, dict):
            continue
        if not _subject_matches(g, principal, groups):
            continue
        if not _document_matches(g, document_id, document_series):
            continue
        if not _grant_active(g, policy_time):
            continue
        target = matched_deny if g.get("effect") == "deny" else matched_allow
        target.append(str(g.get("grant_id")))
    if matched_deny:
        return {"effect": EFFECT_DENY, "resolution": RESOLUTION_EXPLICIT_DENY,
                "matched_grant_ids": sorted(matched_deny), "policy_time": policy_time_iso}
    if matched_allow:
        return {"effect": EFFECT_ALLOW, "resolution": RESOLUTION_ALLOW,
                "matched_grant_ids": sorted(matched_allow), "policy_time": policy_time_iso}
    return {"effect": EFFECT_DENY, "resolution": RESOLUTION_DEFAULT_DENY,
            "matched_grant_ids": [], "policy_time": policy_time_iso}

