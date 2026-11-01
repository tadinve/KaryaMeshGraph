"""M1.5 structural-graph tests over the four-document structural fixture.

Positive path: source → snapshot → graph → independent verify, with every
deliverable's rules exercised (carried roles, merged-doc exclusion,
truncated definition, scrambled table, self-citation, noisy section).
Negative path: tampering at every layer is caught; no source passage ever
reaches a graph file.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from structural_fixture import DOC_SPECS, build_structural_source
from synthetic import SYNTH_MARKER, tree_digest

from karyameshgraph.cli import main as cli_main
from karyameshgraph.errors import SnapshotStateError
from karyameshgraph.graph.build import build_graph
from karyameshgraph.graph.verify import verify_graph
from karyameshgraph.ingest.snapshot import build_snapshot
from karyameshgraph.ingest.source import SourceLayout

DOCS = [s["stable_id"] for s in DOC_SPECS]


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("m15")
    source = build_structural_source(base / "source")
    digest_before = tree_digest(source)
    snap = base / "snap"
    build_snapshot(SourceLayout(source), DOCS, snap,
                   principal="svc.import",
                   authorization_mode="offline_simulation",
                   created_at="2026-11-01T00:00:00+00:00")
    graph = base / "graph"
    manifest = build_graph(snap, graph, expected_documents=4,
                           alias_out=base / "alias.json")
    return {
        "base": base, "source": source, "snap": snap, "graph": graph,
        "manifest": manifest,
        "digest_before": digest_before,
        "nodes": _load_jsonl(graph / "nodes.jsonl"),
        "edges": _load_jsonl(graph / "edges.jsonl"),
        "exclusions": json.loads((graph / "exclusions.json").read_text()),
        "alias": json.loads((base / "alias.json").read_text()),
    }


def _nodes(built, kind):
    return [n for n in built["nodes"] if n["kind"] == kind]


def _edges(built, kind):
    return [e for e in built["edges"] if e["kind"] == kind]


# --- deliverable 1: section skeleton ---------------------------------------

def test_section_skeleton(built):
    sections = _nodes(built, "Section")
    # A1: 4 canon (noisy excluded), A2: 3, B1: 5, B2: 3 canon + front matter.
    assert len(sections) == 16
    by_doc = {}
    for s in sections:
        by_doc.setdefault(s["document"], []).append(s)
    assert len(by_doc["SYN-A-001"]) == 4
    assert len(by_doc["SYN-B-001"]) == 5
    fm = [s for s in by_doc["SYN-B-002"] if s["front_matter"]]
    assert len(fm) == 1 and fm[0]["title"] == "FRONT_MATTER"
    assert not fm[0]["canonical"]
    noisy = built["exclusions"]["noisy_titles"]
    assert any(n["stable_id"] == "SYN-A-001"
               and n["normalized_title"] == "NONSENSE ATTACHMENT TABLE"
               for n in noisy)
    assert all(e["derivation"] == "EXPLICIT" for e in _edges(built, "HAS_SECTION"))


# --- deliverable 2: roles ---------------------------------------------------

def test_role_nodes_and_edges(built):
    roles = {r["id"]: r for r in _nodes(built, "Role")}
    names = sorted(r["name"] for r in roles.values())
    assert names == ["Division Director", "Laboratory Director",
                     "Quality Manager", "The Synthetic Office of Testing (SOT)"]
    rf = _edges(built, "RESPONSIBLE_FOR")
    assert len(rf) == 8
    inferred = [e for e in rf if e["derivation"] == "INFERRED"]
    explicit = [e for e in rf if e["derivation"] == "EXPLICIT"]
    assert len(inferred) == 2 and len(explicit) == 6
    # The carried duties belong to the roles whose headers sit in the
    # previous chunk: Quality Manager (A) and SOT (B).
    carried_roles = {roles[e["src"]]["name"] for e in inferred}
    assert carried_roles == {"Quality Manager",
                             "The Synthetic Office of Testing (SOT)"}


def test_merged_doc_yields_no_role_edges(built):
    assert not [e for e in _edges(built, "RESPONSIBLE_FOR")
                if e.get("document") == "SYN-B-002"]
    excl = built["exclusions"]["roles"]
    assert any(x["stable_id"] == "SYN-B-002"
               and x["reason"] == "merged_responsibilities_and_procedures"
               and x["derivation"] == "AMBIGUOUS" for x in excl)


def test_footnote_contamination_dropped(built):
    stats = built["manifest"]["deliverable_stats"]["roles"]
    assert stats["footnote_lines_dropped"] >= 1
    assert stats["merged_docs_excluded"] == ["SYN-B-002"]


# --- deliverable 3: terms ---------------------------------------------------

def test_terms_multi_doc_linked_never_merged(built):
    terms = {t["name"]: t for t in _nodes(built, "Term")}
    assert terms["calibration"]["doc_count"] == 2
    assert terms["calibration"]["aliases"] == ["CAL"]
    assert terms["record"]["doc_count"] == 2
    defined = _edges(built, "DEFINED_IN")
    assert len(defined) == 5
    cal_edges = [e for e in defined if e["src"] == terms["calibration"]["id"]]
    assert sorted(e["dst"] for e in cal_edges) == ["SYN-A-001", "SYN-B-001"]
    # Definitions stay distinct per edge: different evidence spans.
    assert cal_edges[0]["evidence"] != cal_edges[1]["evidence"]


def test_truncated_definition_is_ambiguous(built):
    defined = _edges(built, "DEFINED_IN")
    truncated = [e for e in defined if e["truncated"]]
    assert len(truncated) == 1
    assert truncated[0]["derivation"] == "AMBIGUOUS"
    assert truncated[0]["dst"] == "SYN-B-001"
    clean = [e for e in defined if not e["truncated"]]
    assert all(e["derivation"] == "EXPLICIT" for e in clean)


# --- deliverable 4: revisions ------------------------------------------------

def test_revision_rows_and_degraded_table(built):
    stats = built["manifest"]["deliverable_stats"]["revisions"]
    assert stats["clean_docs"] == ["SYN-A-001", "SYN-B-001", "SYN-B-002"]
    assert stats["scrambled_docs"] == ["SYN-A-002"]
    assert stats["rows_total"] == 5
    rows = _nodes(built, "RevisionRow")
    a1 = sorted((r["version"], r["date"]) for r in rows
                if r["document"] == "SYN-A-001")
    assert a1 == [("1.0", "01/02/20"), ("1.1", "03/04/21")]
    assert any(x["stable_id"] == "SYN-A-002" and x["flag"] == "degraded_table"
               for x in built["exclusions"]["revisions"])
    assert all(e["derivation"] == "EXPLICIT" for e in _edges(built, "HAS_REVISION"))


def test_revision_description_continuation_absorbed(built):
    # SYN-B-001 Rev. 1's description continues on the next line; the row's
    # evidence must cover the continuation text.
    rows = [r for r in _nodes(built, "RevisionRow")
            if r["document"] == "SYN-B-001" and r["version"] == "Rev. 1"]
    assert len(rows) == 1
    e = [x for x in _edges(built, "HAS_REVISION") if x["dst"] == rows[0]["id"]][0]
    text = _snapshot_text(built, e["evidence"][0]["chunk_uid"])
    covered = text[e["evidence"][0]["start"]:e["evidence"][0]["end"]]
    assert "continuation line for the same revision row" in covered


# --- deliverable 5: citations -------------------------------------------------

def test_citations_resolution_and_derivation(built):
    cites = _edges(built, "CITES")
    stats = built["manifest"]["deliverable_stats"]["citations"]
    doc_ids = {d["id"] for d in _nodes(built, "Document")}
    resolved = [e for e in cites if e["dst"] in doc_ids]
    assert {(e["src"], e["dst"]) for e in resolved} == {
        ("SYN-A-001", "SYN-A-002"), ("SYN-B-001", "SYN-B-002")}
    assert all(e["derivation"] == "INFERRED" for e in resolved)
    assert stats["self_citations_dropped"] == 1
    assert stats["cross_family_resolved_edges"] == 0
    ext_doc = [n for n in _nodes(built, "ExternalStandard")
               if n["category"] == "external_document"]
    assert sorted(n["name"] for n in ext_doc) == ["MAPP 9999.9", "ORA-LAB.9.9"]
    ext_edges = [e for e in cites if e["dst"] not in doc_ids]
    assert all(e["derivation"] == "EXPLICIT" for e in ext_edges)
    std_names = {n["name"] for n in _nodes(built, "ExternalStandard")}
    assert "21 CFR 11.10" in std_names
    assert "ISO/IEC 17025:2017" in std_names
    assert "GUIDANCE FOR INDUSTRY" in std_names


def test_alias_table(built):
    alias = built["alias"]
    assert [r["document_number"] for r in alias["rows"]] == [
        "ORA-LAB.9.1", "ORA-LAB.9.2", "MAPP 9001.1", "MAPP 9001.2"]
    assert alias["collisions"] == {}
    assert alias["missing_number"] == []
    assert alias["snapshot_id"].startswith("ksnap-")


# --- cross-cutting guarantees --------------------------------------------------

def _snapshot_text(built, chunk_uid: str) -> str:
    sid = chunk_uid.split("@", 1)[0]
    for rec in _load_jsonl(built["snap"] / "chunks" / f"{sid}.jsonl"):
        if rec["chunk_uid"] == chunk_uid:
            return rec["text"]
    raise AssertionError(f"chunk not found: {chunk_uid}")


def test_no_source_passages_in_graph_files(built):
    for name in ("nodes.jsonl", "edges.jsonl", "exclusions.json",
                 "graph-manifest.json"):
        assert SYNTH_MARKER not in (built["graph"] / name).read_text(
            encoding="utf-8"), f"source text leaked into {name}"
    assert SYNTH_MARKER not in (built["base"] / "alias.json").read_text()


def test_evidence_spans_resolve_to_expected_text(built):
    roles = {r["id"]: r for r in _nodes(built, "Role")}
    qm = [e for e in _edges(built, "RESPONSIBLE_FOR")
          if roles[e["src"]]["name"] == "Quality Manager"
          and e["derivation"] == "EXPLICIT"]
    texts = []
    for e in qm:
        for span in e["evidence"]:
            texts.append(_snapshot_text(built, span["chunk_uid"])
                         [span["start"]:span["end"]])
    joined = " ".join(texts)
    assert "Maintains the synthetic quality system" in joined
    assert "Reviews synthetic records monthly" in joined


def test_source_tree_untouched(built):
    assert tree_digest(built["source"]) == built["digest_before"]


def test_repeat_build_identical(built):
    graph2 = built["base"] / "graph2"
    manifest2 = build_graph(built["snap"], graph2, expected_documents=4)
    assert manifest2["graph_id"] == built["manifest"]["graph_id"]
    for name in ("nodes.jsonl", "edges.jsonl", "graph-manifest.json"):
        assert (graph2 / name).read_bytes() == (built["graph"] / name).read_bytes()


def test_graph_verify_ok_and_cli(built, capsys):
    report = verify_graph(built["graph"], built["snap"])
    assert report["ok"], report["failures"]
    assert report["evidence_spans_verified"] > 0
    rc = cli_main(["graph", "verify", "--graph", str(built["graph"]),
                   "--snapshot", str(built["snap"])])
    out = capsys.readouterr().out
    assert rc == 0 and "verdict: OK" in out
    assert SYNTH_MARKER not in out


# --- negative path ---------------------------------------------------------------

def test_verify_catches_tampered_edges(built, tmp_path):
    bad = tmp_path / "graph-bad"
    shutil.copytree(built["graph"], bad)
    edges = _load_jsonl(bad / "edges.jsonl")
    edges[0]["derivation"] = ("INFERRED" if edges[0]["derivation"] == "EXPLICIT"
                              else "EXPLICIT")
    (bad / "edges.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n"
                for e in edges), encoding="utf-8")
    report = verify_graph(bad, built["snap"])
    assert not report["ok"]
    assert any("sha256 mismatch" in f for f in report["failures"])
    assert any("edge_id does not match" in f for f in report["failures"])
    assert any("graph_id does not match" in f for f in report["failures"])


def test_verify_catches_undeclared_file(built, tmp_path):
    bad = tmp_path / "graph-extra"
    shutil.copytree(built["graph"], bad)
    (bad / "stray.txt").write_text("not declared")
    report = verify_graph(bad, built["snap"])
    assert not report["ok"]
    assert any("undeclared files" in f for f in report["failures"])


def test_verify_catches_tampered_snapshot(built, tmp_path):
    bad_snap = tmp_path / "snap-bad"
    shutil.copytree(built["snap"], bad_snap)
    target = bad_snap / "chunks" / "SYN-A-001.jsonl"
    target.write_bytes(target.read_bytes().replace(b"synthetic", b"synthetiX", 1))
    report = verify_graph(built["graph"], bad_snap)
    assert not report["ok"]
    assert any("snapshot failed" in f for f in report["failures"])
    with pytest.raises(SnapshotStateError):
        build_graph(bad_snap, tmp_path / "never-built", expected_documents=4)
    assert not (tmp_path / "never-built").exists()


def test_build_refuses_existing_out(built):
    with pytest.raises(SnapshotStateError):
        build_graph(built["snap"], built["graph"], expected_documents=4)


def test_build_enforces_expected_documents(built, tmp_path):
    with pytest.raises(SnapshotStateError) as exc:
        build_graph(built["snap"], tmp_path / "wrong-count",
                    expected_documents=50)
    assert "50" in str(exc.value) and SYNTH_MARKER not in str(exc.value)
    assert not (tmp_path / "wrong-count").exists()
