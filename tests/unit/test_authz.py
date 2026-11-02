"""Unit tests for ingest.authz: structural policy validation and the
document-access decision (2026-11-01 review, second pass). These exercise
the validator and evaluator directly, independent of a full snapshot build,
so every malformed-policy and timestamp edge case is cheap to cover."""

from __future__ import annotations

from karyameshgraph.ingest.authz import (
    EFFECT_ALLOW,
    RESOLUTION_ALLOW,
    RESOLUTION_DEFAULT_DENY,
    RESOLUTION_EXPLICIT_DENY,
    RESOLUTION_INVALID_POLICY_TIME,
    evaluate_document_access,
    validate_acl_policy,
)

PINFO = {"groups": ["compliance"], "status": "active"}
KNOWN_DOCS = {"DOC-1": {"source_series": "Series A"}, "DOC-2": {"source_series": "Series A"}}


def _good_grant(**overrides):
    g = {
        "grant_id": "g1", "subject_type": "principal", "subject_id": "p1",
        "document_id": "DOC-1", "effect": "allow",
        "granted_at": "2026-01-01T00:00:00+00:00",
    }
    g.update(overrides)
    return g


# -- validate_acl_policy: structural validation --------------------------------

def test_valid_policy_has_no_failures():
    acl = {"policy_version": "v1", "grants": [_good_grant()]}
    assert validate_acl_policy(acl, known_documents=KNOWN_DOCS) == []


def test_not_a_dict_is_rejected():
    assert validate_acl_policy([], known_documents=None) != []
    assert validate_acl_policy(None, known_documents=None) != []


def test_grants_not_a_list_is_rejected():
    assert validate_acl_policy({"policy_version": "v1", "grants": "nope"}) != []


def test_missing_required_field_rejects_whole_policy():
    acl = {"policy_version": "v1", "grants": [
        _good_grant(),  # one good grant
        {"grant_id": "g2", "subject_type": "principal", "subject_id": "p1",
         "document_id": "DOC-1"},  # missing effect and granted_at
    ]}
    failures = validate_acl_policy(acl)
    assert failures  # whole policy rejected, not just g2
    assert any("g2" in f for f in failures)


def test_duplicate_grant_id_rejected():
    acl = {"policy_version": "v1", "grants": [_good_grant(), _good_grant(document_id="DOC-2")]}
    failures = validate_acl_policy(acl)
    assert any("duplicate grant_id" in f for f in failures)


def test_unknown_effect_rejected():
    acl = {"policy_version": "v1", "grants": [_good_grant(effect="maybe")]}
    failures = validate_acl_policy(acl)
    assert any("unknown effect" in f for f in failures)


def test_unknown_subject_type_rejected():
    acl = {"policy_version": "v1", "grants": [_good_grant(subject_type="robot")]}
    failures = validate_acl_policy(acl)
    assert any("unknown subject_type" in f for f in failures)


def test_bad_target_both_present_rejected():
    acl = {"policy_version": "v1", "grants": [
        _good_grant(document_series="Series A")]}  # document_id AND document_series
    failures = validate_acl_policy(acl)
    assert any("exactly one of document_id/document_series" in f for f in failures)


def test_bad_target_neither_present_rejected():
    acl = {"policy_version": "v1", "grants": [
        {"grant_id": "g1", "subject_type": "principal", "subject_id": "p1",
         "effect": "allow", "granted_at": "2026-01-01T00:00:00+00:00"}]}
    failures = validate_acl_policy(acl)
    assert any("exactly one of document_id/document_series" in f for f in failures)


def test_unknown_document_rejected_when_known_documents_supplied():
    acl = {"policy_version": "v1", "grants": [_good_grant(document_id="NOPE")]}
    assert validate_acl_policy(acl, known_documents=KNOWN_DOCS) != []
    # Without known_documents (e.g. the snapshot verifier's partial corpus),
    # referential checks are skipped - the same grant is otherwise valid.
    assert validate_acl_policy(acl, known_documents=None) == []


def test_unknown_document_series_rejected():
    acl = {"policy_version": "v1", "grants": [
        _good_grant(document_id=None, document_series="Not A Real Series")]}
    assert validate_acl_policy(acl, known_documents=KNOWN_DOCS) != []


def test_naive_timestamp_rejected():
    acl = {"policy_version": "v1", "grants": [_good_grant(granted_at="2026-01-01T00:00:00")]}
    failures = validate_acl_policy(acl)
    assert any("timezone-aware" in f for f in failures)


def test_non_iso_timestamp_rejected():
    acl = {"policy_version": "v1", "grants": [_good_grant(granted_at="not-a-date")]}
    failures = validate_acl_policy(acl)
    assert any("timezone-aware ISO-8601" in f for f in failures)


def test_naive_expires_at_rejected():
    acl = {"policy_version": "v1", "grants": [
        _good_grant(expires_at="2026-02-01T00:00:00")]}  # naive
    failures = validate_acl_policy(acl)
    assert any("expires_at" in f and "timezone-aware" in f for f in failures)


def test_expires_at_before_granted_at_rejected():
    acl = {"policy_version": "v1", "grants": [_good_grant(
        granted_at="2026-02-01T00:00:00+00:00", expires_at="2026-01-01T00:00:00+00:00")]}
    failures = validate_acl_policy(acl)
    assert any("expires_at must follow granted_at" in f for f in failures)


def test_revoked_at_before_granted_at_rejected():
    acl = {"policy_version": "v1", "grants": [_good_grant(
        granted_at="2026-02-01T00:00:00+00:00", revoked_at="2026-01-01T00:00:00+00:00")]}
    failures = validate_acl_policy(acl)
    assert any("revoked_at must follow granted_at" in f for f in failures)


# -- evaluate_document_access: decision + fail-closed timestamp handling -------

def test_allow_grant_resolves_allow():
    acl = {"policy_version": "v1", "grants": [_good_grant()]}
    d = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                 document_series=None, acl=acl,
                                 policy_time_iso="2026-06-01T00:00:00+00:00")
    assert d["effect"] == EFFECT_ALLOW and d["resolution"] == RESOLUTION_ALLOW


def test_explicit_deny_beats_allow():
    acl = {"policy_version": "v1", "grants": [
        _good_grant(document_id="*"),
        _good_grant(grant_id="g2", effect="deny"),
    ]}
    d = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                 document_series=None, acl=acl,
                                 policy_time_iso="2026-06-01T00:00:00+00:00")
    assert d["resolution"] == RESOLUTION_EXPLICIT_DENY


def test_no_matching_grant_is_default_deny():
    acl = {"policy_version": "v1", "grants": [_good_grant(document_id="DOC-2")]}
    d = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                 document_series=None, acl=acl,
                                 policy_time_iso="2026-06-01T00:00:00+00:00")
    assert d["resolution"] == RESOLUTION_DEFAULT_DENY


def test_expired_grant_is_default_deny_not_unrestricted():
    acl = {"policy_version": "v1", "grants": [_good_grant(
        expires_at="2026-03-01T00:00:00+00:00")]}
    # at/after expires_at (exclusive bound): the grant no longer applies.
    d = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                 document_series=None, acl=acl,
                                 policy_time_iso="2026-03-01T00:00:00+00:00")
    assert d["resolution"] == RESOLUTION_DEFAULT_DENY
    # Before expiry it is still allowed.
    d2 = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                  document_series=None, acl=acl,
                                  policy_time_iso="2026-02-01T00:00:00+00:00")
    assert d2["effect"] == EFFECT_ALLOW


def test_revoked_grant_is_default_deny():
    acl = {"policy_version": "v1", "grants": [_good_grant(
        revoked_at="2026-03-01T00:00:00+00:00")]}
    d = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                 document_series=None, acl=acl,
                                 policy_time_iso="2026-03-01T00:00:00+00:00")
    assert d["resolution"] == RESOLUTION_DEFAULT_DENY


def test_malformed_grant_timestamp_is_inactive_never_unrestricted():
    """Defense-in-depth: even if a malformed expires_at somehow reaches
    evaluation (validate_acl_policy would normally have rejected it), the
    grant must be treated as INACTIVE, never as having no expiry at all."""
    acl = {"policy_version": "v1", "grants": [_good_grant(expires_at="not-a-date")]}
    d = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                 document_series=None, acl=acl,
                                 policy_time_iso="2026-06-01T00:00:00+00:00")
    assert d["effect"] != EFFECT_ALLOW  # NOT silently treated as unrestricted


def test_missing_or_invalid_policy_is_default_deny():
    d = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                 document_series=None, acl=None,
                                 policy_time_iso="2026-06-01T00:00:00+00:00")
    assert d["resolution"] == RESOLUTION_DEFAULT_DENY


def test_malformed_policy_time_fails_closed_not_wall_clock():
    """The 2026-11-01 first-pass bug: an unparseable policy_time used to
    silently fall back to live datetime.now(). It must now hard-deny,
    deterministically, regardless of when the test actually runs."""
    acl = {"policy_version": "v1", "grants": [_good_grant(document_id="*")]}
    for bad in (None, "", "not-a-timestamp", "2026-01-01T00:00:00"):  # last one is naive
        d = evaluate_document_access(principal="p1", pinfo=PINFO, document_id="DOC-1",
                                     document_series=None, acl=acl, policy_time_iso=bad)
        assert d["effect"] != EFFECT_ALLOW
        assert d["resolution"] == RESOLUTION_INVALID_POLICY_TIME


def test_no_historical_policy_time_bypass():
    """There is no parameter anywhere in this module that evaluates the
    policy "as of" an instant other than the supplied policy_time_iso - the
    only way to change the outcome is to supply a different policy_time_iso
    (which the snapshot builder always sets to its own `created_at`, never
    to a caller-chosen historical override). This test pins that contract:
    the decision for a revoked grant flips exactly at revoked_at and at no
    other hidden boundary."""
    acl = {"policy_version": "v1", "grants": [_good_grant(
        revoked_at="2026-06-15T12:00:00+00:00")]}
    just_before = evaluate_document_access(
        principal="p1", pinfo=PINFO, document_id="DOC-1", document_series=None,
        acl=acl, policy_time_iso="2026-06-15T11:59:59+00:00")
    at_revocation = evaluate_document_access(
        principal="p1", pinfo=PINFO, document_id="DOC-1", document_series=None,
        acl=acl, policy_time_iso="2026-06-15T12:00:00+00:00")
    assert just_before["effect"] == EFFECT_ALLOW
    assert at_revocation["resolution"] == RESOLUTION_DEFAULT_DENY
