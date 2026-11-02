"""Optional integration against the real local KaryaMeshRAG checkout.

Runs only when KMG_REAL_SOURCE_ROOT is set (SPEC.md M1.4) - e.g.:

    KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run pytest tests/integration/test_real_source.py -q

Read-only: inspects the real corpus and builds the pilot snapshot in a
temporary directory. Never writes inside the source root.
"""

import json
import os

import pytest

from karyameshgraph.errors import LifecycleIneligibleError
from karyameshgraph.ingest.inspect import inspect_source
from karyameshgraph.ingest.snapshot import build_snapshot
from karyameshgraph.ingest.source import SourceLayout
from karyameshgraph.ingest.verify import verify_snapshot

ROOT = os.environ.get("KMG_REAL_SOURCE_ROOT")
pytestmark = pytest.mark.skipif(
    not ROOT, reason="KMG_REAL_SOURCE_ROOT not set; real-source tests skipped")

EXPECTED_SECTION_SHA = "720d36e80e0b7b5599ca936b922c9be36dc2fb26c30d205e2d13fc22f0e4b6aa"
PILOT_DOCS = ["CRS-SOP-009", "CRS-SOP-018"]
WITHDRAWN_DOC = "CRS-SOP-050"  # real upstream mock_dms: lifecycle_state WITHDRAWN


def test_inspect_real_v2():
    report = inspect_source(SourceLayout(ROOT))
    assert report["chunks_file"]["sha256"] == EXPECTED_SECTION_SHA
    assert report["chunks_file"]["records"] == 1752
    assert report["chunks_file"]["documents"] == 50
    assert report["full_fidelity"]["verdict"] == "chunk_only"
    assert report["ok"], report["failures"]


def test_withdrawn_document_blocked_under_current_mode_real(tmp_path):
    """The real upstream CRS-SOP-050 is WITHDRAWN; default document_mode
    must refuse it even though compliance.officer's wildcard ACL grant
    would otherwise allow it (SPEC.md §5.7, 2026-11-01 review second pass)."""
    with pytest.raises(LifecycleIneligibleError) as exc:
        build_snapshot(
            SourceLayout(ROOT), [WITHDRAWN_DOC], tmp_path / "s",
            principal="compliance.officer", authorization_mode="offline_simulation",
            created_at="2026-10-31T00:00:00+00:00",
        )
    assert exc.value.context["ineligible_documents"] == [WITHDRAWN_DOC]


def test_pilot_snapshot_roundtrip(tmp_path):
    out = tmp_path / "pilot-v2"
    manifest = build_snapshot(
        SourceLayout(ROOT), PILOT_DOCS, out,
        principal="compliance.officer", authorization_mode="offline_simulation",
        created_at="2026-10-31T00:00:00+00:00",
    )
    assert manifest["document_count"] == 2
    caps = {d["document_id"]: d["provenance_capability"] for d in manifest["documents"]}
    assert caps == {d: "chunk_only" for d in PILOT_DOCS}
    report = verify_snapshot(out)
    assert report["ok"], json.dumps(report["failures"][:5], indent=1)


def test_full_corpus_graph(tmp_path):
    """M1.5 over the real corpus: full-corpus snapshot (the entry gate),
    structural graph, independent verify, and the empirically verified
    invariants from the 2026-11-01 discovery sweep."""
    from karyameshgraph.graph.build import build_graph
    from karyameshgraph.graph.verify import verify_graph
    from karyameshgraph.ingest.source import SourceLayout as _SL

    layout = _SL(ROOT)
    all_docs = sorted(layout.load_corpus_manifest().keys())
    assert len(all_docs) == 50
    snap = tmp_path / "full-v2"
    # M1.5 is a structural/analytical graph over EVERY document regardless
    # of current production eligibility (CRS-SOP-050 is WITHDRAWN) - this is
    # exactly the deliberate, explicitly authorized archival-import case
    # SPEC.md §5.7 describes, never a silent mix of "ACL-allowed" with
    # "currently eligible" (2026-11-01 review, second pass).
    build_snapshot(
        layout, all_docs, snap,
        principal="compliance.officer", authorization_mode="offline_simulation",
        created_at="2026-11-01T00:00:00+00:00",
        document_mode="historical_archive", archive_mode_authorized=True,
    )
    graph = tmp_path / "graph-v2"
    manifest = build_graph(snap, graph, expected_documents=50)

    stats = manifest["deliverable_stats"]
    # Revision tables: the deterministic zero-row rule reproduces the
    # spec's 36/14 split exactly (verified against the frozen corpus).
    assert len(stats["revisions"]["clean_docs"]) == 36
    assert sorted(stats["revisions"]["scrambled_docs"]) == sorted([
        "CRS-SOP-006", "CRS-SOP-011", "CRS-SOP-014", "CRS-SOP-017",
        "CRS-SOP-019", "CRS-SOP-023", "CRS-SOP-030", "CRS-SOP-031",
        "CRS-SOP-032", "CRS-SOP-039", "CRS-SOP-040", "CRS-SOP-043",
        "CRS-SOP-047", "CRS-SOP-048"])
    # The four merged RESPONSIBILITIES-AND-PROCEDURES documents yield no
    # role edges, by contract.
    assert sorted(stats["roles"]["merged_docs_excluded"]) == [
        "CRS-SOP-029", "CRS-SOP-034", "CRS-SOP-036", "CRS-SOP-046"]
    # Citations: verified invariants — zero cross-family edges, five
    # genuine self-citations dropped, 42 distinct resolved doc→doc pairs.
    assert stats["citations"]["cross_family_resolved_edges"] == 0
    assert stats["citations"]["self_citations_dropped"] == 5
    assert stats["citations"]["resolved_edges"] == 42
    # Definitions, pinned to the frozen corpus and reconciled against the
    # discovery sweep's separate ad hoc count (337 entries / 307 terms / 25
    # multi-doc). Per format the build equals the sweep for lettered (152),
    # unmarked-dash (70) and unmarked-colon (60); bulleted is 56 vs 55 (same
    # ten documents); plus the 1 copula-form entry the sweep listed apart
    # from its 337 -> 339 entries. Distinct terms: those 2, plus 'Level 1'..
    # 'Level 4' (CRS-SOP-027), which the sweep's own trailing-digit rule
    # merged into one -> 312. The 25 multi-doc terms equal the sweep's list.
    # An earlier build's 254 / 238 / 12 was a parser bug (single-newline
    # chunk join), fixed in the 2026-11-01 review.
    assert stats["terms"]["entries_parsed"] == 339
    assert stats["terms"]["distinct_terms"] == 312
    assert len(stats["terms"]["multi_doc_terms"]) == 25
    assert stats["sections"]["noisy_titles_excluded"] == 108
    assert stats["sections"]["unknown_family_docs"] == []
    assert manifest["derivations"]["INFERRED"] > 0
    roles = stats["roles"]
    assert (roles["duties_explicit"] + roles["duties_inferred"]
            == manifest["edge_kinds"]["RESPONSIBLE_FOR"])

    report = verify_graph(graph, snap)
    assert report["ok"], json.dumps(report["failures"][:10], indent=1)
    assert report["reproduced"] is True
    assert report["evidence_spans_verified"] > 1000
