"""Positive path on the synthetic fixture: inspect → build → verify.

Also proves the two cross-cutting guarantees: the source tree is untouched
by an import, and a repeat build from unchanged inputs has the same
content-derived snapshot identity.
"""

import json

from synthetic import DOCS, SYNTH_MARKER, tree_digest

from karyameshgraph.cli import main
from karyameshgraph.ingest.inspect import inspect_source
from karyameshgraph.ingest.snapshot import build_snapshot
from karyameshgraph.ingest.source import SourceLayout
from karyameshgraph.ingest.verify import verify_snapshot

CREATED = "2026-10-31T00:00:00+00:00"


def test_inspect_ok(synthetic_source):
    report = inspect_source(SourceLayout(synthetic_source))
    assert report["ok"], report["failures"]
    assert report["chunks_file"]["records"] == 6
    assert report["chunks_file"]["documents"] == 2
    assert report["chunks_file"]["matches_frozen_manifest"] is True
    assert report["full_fidelity"]["verdict"] == "chunk_only"
    assert report["revision_metadata"]["all_simulated"] is True
    assert report["acl"]["identities"]["status"] == "available"
    # Regression (2026-11-01 review): the PDF-bytes check joined
    # "data/originals/" onto a corpus-manifest `file` value that is already
    # root-relative (e.g. "data/originals/X.pdf"), so every real PDF read
    # as missing even though it existed on disk. Both fixture documents'
    # PDFs are on disk and must independently hash-verify, not merely
    # fail to appear in "mismatched".
    assert report["corpus_manifest"]["pdf_bytes"]["verified"] == len(DOCS)
    assert report["corpus_manifest"]["pdf_bytes"]["missing"] == []
    assert report["corpus_manifest"]["pdf_bytes"]["mismatched"] == []
    # The machine-readable report never contains source text.
    assert SYNTH_MARKER not in json.dumps(report)


def test_build_verify_roundtrip(synthetic_source, tmp_path):
    before = tree_digest(synthetic_source)
    out = tmp_path / "snap"
    manifest = build_snapshot(
        SourceLayout(synthetic_source), list(DOCS), out,
        principal="svc.import", authorization_mode="offline_simulation",
        created_at=CREATED,
    )
    # Source tree untouched by the import (SPEC.md M1.4).
    assert tree_digest(synthetic_source) == before
    # No temp dirs left behind; snapshot finalized atomically.
    assert not list(tmp_path.glob("snap.tmp-*"))
    assert (out / "snapshot-manifest.json").is_file()

    assert manifest["validation_status"] == "verified"
    assert manifest["document_count"] == 2 and manifest["chunk_count"] == 6
    ctx = manifest["authorization_context"]
    assert ctx["mode"] == "offline_simulation"
    assert ctx["is_production_authorization"] is False
    for doc in manifest["documents"]:
        assert doc["provenance_capability"] == "chunk_only"
        assert doc["extraction_eligible"] is False
        assert doc["verification_level_ceiling"] == "CHUNK_VERIFIED"
        assert doc["is_simulated"] is True
        assert doc["acl"]["derivation"] == "derived"
        assert doc["eligibility"]["status"] == "eligible_offline_simulated"

    report = verify_snapshot(out)
    assert report["ok"], report["failures"]


def test_repeat_build_same_content_identity(synthetic_source, tmp_path):
    kw = dict(principal="svc.import", authorization_mode="offline_simulation")
    m1 = build_snapshot(SourceLayout(synthetic_source), list(DOCS), tmp_path / "a",
                        created_at="2026-10-31T01:00:00+00:00", **kw)
    m2 = build_snapshot(SourceLayout(synthetic_source), list(DOCS), tmp_path / "b",
                        created_at="2026-10-31T02:00:00+00:00", **kw)
    assert m1["snapshot_id"] == m2["snapshot_id"]  # timestamps excluded from identity
    assert (tmp_path / "a" / "documents.json").read_bytes() == \
           (tmp_path / "b" / "documents.json").read_bytes()


def test_cli_roundtrip(synthetic_source, tmp_path, capsys):
    rc = main(["source", "inspect", "--source-root", str(synthetic_source),
               "--json-out", str(tmp_path / "inspect.json")])
    assert rc == 0
    assert SYNTH_MARKER not in (tmp_path / "inspect.json").read_text()

    out = tmp_path / "snap-cli"
    rc = main(["snapshot", "build", "--source-root", str(synthetic_source),
               "--documents", ",".join(DOCS), "--out", str(out),
               "--principal", "svc.import", "--authorization-mode", "offline_simulation"])
    assert rc == 0
    rc = main(["snapshot", "verify", "--snapshot", str(out)])
    assert rc == 0
    assert SYNTH_MARKER not in capsys.readouterr().out
