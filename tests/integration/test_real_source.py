"""Optional integration against the real local KaryaMeshRAG checkout.

Runs only when KMG_REAL_SOURCE_ROOT is set (SPEC.md M1.4) - e.g.:

    KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run pytest tests/integration/test_real_source.py -q

Read-only: inspects the real corpus and builds the pilot snapshot in a
temporary directory. Never writes inside the source root.
"""

import json
import os

import pytest

from karyameshgraph.ingest.inspect import inspect_source
from karyameshgraph.ingest.snapshot import build_snapshot
from karyameshgraph.ingest.source import SourceLayout
from karyameshgraph.ingest.verify import verify_snapshot

ROOT = os.environ.get("KMG_REAL_SOURCE_ROOT")
pytestmark = pytest.mark.skipif(
    not ROOT, reason="KMG_REAL_SOURCE_ROOT not set; real-source tests skipped")

EXPECTED_SECTION_SHA = "720d36e80e0b7b5599ca936b922c9be36dc2fb26c30d205e2d13fc22f0e4b6aa"
PILOT_DOCS = ["CRS-SOP-009", "CRS-SOP-018"]


def test_inspect_real_v2():
    report = inspect_source(SourceLayout(ROOT))
    assert report["chunks_file"]["sha256"] == EXPECTED_SECTION_SHA
    assert report["chunks_file"]["records"] == 1752
    assert report["chunks_file"]["documents"] == 50
    assert report["full_fidelity"]["verdict"] == "chunk_only"
    assert report["ok"], report["failures"]


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
