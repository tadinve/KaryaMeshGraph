"""Regression test for the inspector's PDF-path construction (2026-11-01
review finding): `corpus/manifest.json` entries carry a root-relative
`file` path (e.g. "data/originals/CRS-SOP-001.pdf"), and the inspector must
resolve it directly under the source root rather than re-prepending
"data/originals/" and silently doubling the path."""

from __future__ import annotations

import hashlib
from pathlib import Path

from synthetic import build_synthetic_source

from karyameshgraph.ingest.inspect import inspect_source
from karyameshgraph.ingest.source import SourceLayout


def test_pdf_path_is_root_relative_not_double_prefixed(tmp_path: Path):
    root = build_synthetic_source(tmp_path / "fake-kms")
    entry = next(iter(__import__("json").loads(
        (root / "corpus" / "manifest.json").read_text())))
    # The manifest's `file` field is already root-relative.
    assert entry["file"].startswith("data/originals/")
    resolved = root / entry["file"]
    assert resolved.is_file()
    # The historically buggy construction must NOT be where the PDF lives.
    buggy = root / "data" / "originals" / entry["file"]
    assert not buggy.is_file()


def test_inspect_verifies_pdf_bytes_present_on_disk(tmp_path: Path):
    root = build_synthetic_source(tmp_path / "fake-kms")
    report = inspect_source(SourceLayout(root))
    pdf_bytes = report["corpus_manifest"]["pdf_bytes"]
    assert pdf_bytes["missing"] == []
    assert pdf_bytes["mismatched"] == []
    assert pdf_bytes["verified"] == 2
