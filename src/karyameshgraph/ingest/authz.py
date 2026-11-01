"""Document-level ACL evaluation — offline simulation (SPEC.md M1.3, M5).

Prior to this module the snapshot builder validated only that the principal
was *active*, then recorded whichever grants happened to match a document as
informational ``derived_grants`` — an active principal was silently treated
as authorized for every document, which is exactly the gap SPEC.md M5 and
the 2026-11-01 review flag. This module makes the authorization decision
real: the upstream policy's own documented resolution order
(``explicit deny`` > ``allow`` > ``default deny``) is evaluated per document,
and a document the principal is not allowed to see is never copied into the
snapshot - the build refuses closed instead.

This is still **offline simulation**, not production authorization:

- ``policy_time`` is the import's ``created_at`` (the "request's CURRENT
  policy time" the upstream policy note requires), not live wall-clock time
  pulled from the production access-control system.
- Group membership is resolved only from the snapshotted identity registry,
  per its own note ("groups supplied by a request are ignored").
- A ``default_deny`` resolution (no matching grant at all) is NOT public —
  a missing/invalid policy evaluates every document to ``default_deny``.
- A successful import decision here is never proof that any end user may
  view the content at query time; the eventual query layer must reauthorize
  under current policy, independent of this import-time decision.
"""

from __future__ import annotations

from datetime import datetime, timezone

RESOLUTION_EXPLICIT_DENY = "explicit_deny"
RESOLUTION_ALLOW = "allow"
RESOLUTION_DEFAULT_DENY = "default_deny"

EFFECT_ALLOW = "allow"
EFFECT_DENY = "deny"


def _parse(ts: str | None):
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def principal_groups(pinfo: dict) -> set[str]:
    groups = pinfo.get("groups") if isinstance(pinfo, dict) else None
    return set(groups) if isinstance(groups, list) else set()


def _grant_active(grant: dict, policy_time: datetime) -> bool:
    """Inclusive ``granted_at``; exclusive ``expires_at``; ``revoked_at``
    ends the grant from that instant (upstream policy note, verbatim)."""
    granted_at = _parse(grant.get("granted_at"))
    if granted_at is not None and policy_time < granted_at:
        return False
    expires_at = _parse(grant.get("expires_at"))
    if expires_at is not None and policy_time >= expires_at:
        return False
    revoked_at = _parse(grant.get("revoked_at"))
    if revoked_at is not None and policy_time >= revoked_at:
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
    matches no grants and resolves to ``default_deny`` - never allow.
    """
    policy_time = _parse(policy_time_iso) or datetime.now(timezone.utc)
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
