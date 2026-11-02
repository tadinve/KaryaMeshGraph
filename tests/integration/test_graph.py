"""M1.5 structural-graph tests over the four-document structural fixture.

Positive path: source → snapshot → graph → independent verify, with every
deliverable's rules exercised (carried roles, a header that ends a chunk,
merged-doc exclusion, definitions split across chunks, list lead-ins,
truncated definition, scrambled table, self-citation, noisy section).
Negative path: tampering at every layer is caught — including a fully
rehashed tamper, which only the reproduction check can see; no source
passage ever reaches a graph file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from structural_fixture import DOC_SPECS, build_structural_source
from synthetic import SYNTH_MARKER, rewrite_chunks, tree_digest

from karyameshgraph.cli import main as cli_main
from karyameshgraph.contracts.models import canonical_json
from karyameshgraph.errors import MalformedRecordError, SnapshotStateError
from karyameshgraph.graph.build import build_graph, manifest_bytes
from karyameshgraph.graph.models import graph_identity
from karyameshgraph.graph.verify import verify_graph
from karyameshgraph.ingest.snapshot import build_snapshot
from karyameshgraph.ingest.source import SourceLayout

DOCS = [s["stable_id"] for s in DOC_SPECS]


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _build_snapshot(source: Path, out: Path) -> None:
    build_snapshot(SourceLayout(source), DOCS, out,
                   principal="svc.import",
                   authorization_mode="offline_simulation",
                   created_at="2026-11-01T00:00:00+00:00")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("m15")
    source = build_structural_source(base / "source")
    digest_before = tree_digest(source)
    snap = base / "snap"
    _build_snapshot(source, snap)
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


def _snapshot_text(built, chunk_uid: str) -> str:
    sid = chunk_uid.split("@", 1)[0]
    for rec in _load_jsonl(built["snap"] / "chunks" / f"{sid}.jsonl"):
        if rec["chunk_uid"] == chunk_uid:
            return rec["text"]
    raise AssertionError(f"chunk not found: {chunk_uid}")


def _rehash_graph(gdir: Path) -> None:
    """Make a tampered graph internally consistent again: refresh every
    artifact hash in the manifest and recompute graph_id — the strongest
    tamper, which only reproduction from the snapshot can detect."""
    mpath = gdir / "graph-manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    for name in manifest["artifacts"]:
        data = (gdir / name).read_bytes()
        manifest["artifacts"][name] = {
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    manifest["graph_id"] = graph_identity(manifest)
    mpath.write_bytes(manifest_bytes(manifest))


def _write_edges(gdir: Path, edges: list[dict]) -> None:
    (gdir / "edges.jsonl").write_text(
        "".join(canonical_json(e) + "\n" for e in edges), encoding="utf-8")


def _reid(e: dict) -> dict:
    core = canonical_json({k: v for k, v in e.items() if k != "edge_id"})
    e["edge_id"] = "e-" + hashlib.sha256(core.encode("utf-8")).hexdigest()[:24]
    return e


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
    assert all(e["derivation"] == "EXPLICIT" for e in _edges(built, "HAS_SECTION"))


def test_noisy_title_excluded_without_text(built):
    noisy = built["exclusions"]["noisy_titles"]
    want = hashlib.sha256(b"NONSENSE ATTACHMENT TABLE").hexdigest()
    hits = [n for n in noisy if n["stable_id"] == "SYN-A-001"]
    assert len(hits) == 1 and hits[0]["title_sha256"] == want
    assert hits[0]["chunk_uids"] and all("#" in u for u in hits[0]["chunk_uids"])
    # The excluded title is located, never copied.
    assert "NONSENSE" not in (built["graph"] / "exclusions.json").read_text().upper()


# --- deliverable 2: roles ---------------------------------------------------

def test_role_nodes_and_edges(built):
    roles = {r["id"]: r for r in _nodes(built, "Role")}
    names = sorted(r["name"] for r in roles.values())
    assert names == ["Division Director", "Laboratory Director",
                     "Quality Manager", "Records Liaison Officer",
                     "The Synthetic Office of Testing (SOT)"]
    rf = _edges(built, "RESPONSIBLE_FOR")
    assert len(rf) == 10
    inferred = [e for e in rf if e["derivation"] == "INFERRED"]
    explicit = [e for e in rf if e["derivation"] == "EXPLICIT"]
    assert len(inferred) == 4 and len(explicit) == 6
    # Carried duties belong to the roles whose headers sit in an earlier
    # chunk: Quality Manager (A), SOT (B), and the chunk-ending header.
    carried_roles = sorted(roles[e["src"]]["name"] for e in inferred)
    assert carried_roles == ["Quality Manager", "Records Liaison Officer",
                             "Records Liaison Officer",
                             "The Synthetic Office of Testing (SOT)"]
    assert built["manifest"]["deliverable_stats"]["roles"]["carried_roles"] == 3


def test_header_ending_a_chunk_keeps_its_duties(built):
    """Regression: a bare header on the last line of a chunk used to be
    read as prose (no lookahead into the next chunk), so its duties were
    attached to the previous role (Division Director)."""
    roles = {r["name"]: r["id"] for r in _nodes(built, "Role")}
    rf = _edges(built, "RESPONSIBLE_FOR")
    rlo = [e for e in rf if e["src"] == roles["Records Liaison Officer"]]
    dd = [e for e in rf if e["src"] == roles["Division Director"]]
    assert len(rlo) == 2 and all(e["derivation"] == "INFERRED" for e in rlo)
    assert len(dd) == 1
    texts = [_snapshot_text(built, s["chunk_uid"])[s["start"]:s["end"]]
             for e in rlo for s in e["evidence"]]
    assert any("Track synthetic records holds" in t for t in texts)
    dd_text = " ".join(_snapshot_text(built, s["chunk_uid"])[s["start"]:s["end"]]
                       for e in dd for s in e["evidence"])
    assert "Records Liaison Officer" not in dd_text


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
    # Regression: SYN-A-001's 'Record' sits in its own chunk; a single-
    # newline join fused it into 'Calibration' and dropped it.
    assert terms["record"]["doc_count"] == 2
    assert sorted(terms["record"]["documents"]) == ["SYN-A-001", "SYN-A-002"]
    defined = _edges(built, "DEFINED_IN")
    assert len(defined) == 5
    cal_edges = [e for e in defined if e["src"] == terms["calibration"]["id"]]
    assert sorted(e["dst"] for e in cal_edges) == ["SYN-A-001", "SYN-B-001"]
    # Definitions stay distinct per edge: different evidence spans.
    assert cal_edges[0]["evidence"] != cal_edges[1]["evidence"]


def test_list_leadin_is_not_a_term(built):
    names = {t["name"] for t in _nodes(built, "Term")}
    assert not [n for n in names if "include" in n]
    assert names >= {"synthetic term", "calibration", "record"}
    stats = built["manifest"]["deliverable_stats"]["terms"]
    assert stats["leadins_rejected"] == 1


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

def test_no_source_passages_in_graph_files(built):
    for name in ("nodes.jsonl", "edges.jsonl", "exclusions.json",
                 "graph-manifest.json"):
        assert SYNTH_MARKER not in (built["graph"] / name).read_text(
            encoding="utf-8"), f"source text leaked into {name}"
    assert SYNTH_MARKER not in (built["base"] / "alias.json").read_text()


def test_manifest_has_no_machine_paths(built):
    raw = (built["graph"] / "graph-manifest.json").read_text(encoding="utf-8")
    assert "snapshot_path" not in raw
    assert str(built["base"]) not in raw


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
    for name in ("nodes.jsonl", "edges.jsonl", "alias-table.json",
                 "exclusions.json", "graph-manifest.json"):
        assert (graph2 / name).read_bytes() == (built["graph"] / name).read_bytes()


def test_repeat_build_identical_across_path_spellings(built):
    """No machine path is stored, so a differently spelled path to the
    same snapshot yields byte-identical output."""
    link = built["base"] / "snap-link"
    os.symlink(built["snap"], link)
    graph3 = built["base"] / "graph3"
    manifest3 = build_graph(link, graph3, expected_documents=4)
    assert manifest3["graph_id"] == built["manifest"]["graph_id"]
    assert ((graph3 / "graph-manifest.json").read_bytes()
            == (built["graph"] / "graph-manifest.json").read_bytes())


def test_graph_verify_ok_and_cli(built, capsys):
    report = verify_graph(built["graph"], built["snap"])
    assert report["ok"], report["failures"]
    assert report["reproduced"] is True
    assert report["evidence_spans_verified"] > 0
    rc = cli_main(["graph", "verify", "--graph", str(built["graph"]),
                   "--snapshot", str(built["snap"])])
    out = capsys.readouterr().out
    assert rc == 0 and "verdict: OK" in out
    assert "reproduced from snapshot: True" in out
    assert SYNTH_MARKER not in out


# --- negative path: verification -------------------------------------------------

def _copy_graph(built, tmp_path, name):
    dst = tmp_path / name
    shutil.copytree(built["graph"], dst)
    return dst


def test_verify_catches_tampered_edges(built, tmp_path):
    bad = _copy_graph(built, tmp_path, "graph-bad")
    edges = _load_jsonl(bad / "edges.jsonl")
    edges[0]["derivation"] = ("INFERRED" if edges[0]["derivation"] == "EXPLICIT"
                              else "EXPLICIT")
    _write_edges(bad, edges)
    report = verify_graph(bad, built["snap"])
    assert not report["ok"] and not report["reproduced"]
    assert any("edges.jsonl: sha256 mismatch" in f for f in report["failures"])
    assert any("edge_id does not match" in f for f in report["failures"])
    assert any("derivations histogram" in f for f in report["failures"])
    assert any("does not reproduce" in f for f in report["failures"])


def test_alias_swap_changes_graph_id_and_fails_verify(built, tmp_path):
    """The committed alias table is bound into graph_id: swapping it with a
    refreshed artifact hash but the old graph_id fails identity; fully
    rehashing it yields a NEW graph_id and still fails reproduction."""
    bad = _copy_graph(built, tmp_path, "graph-alias")
    alias = json.loads((bad / "alias-table.json").read_text())
    alias["rows"][0]["stable_id"] = alias["rows"][1]["stable_id"]
    (bad / "alias-table.json").write_text(canonical_json(alias) + "\n")
    manifest = json.loads((bad / "graph-manifest.json").read_text())
    data = (bad / "alias-table.json").read_bytes()
    manifest["artifacts"]["alias-table.json"] = {
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    (bad / "graph-manifest.json").write_bytes(manifest_bytes(manifest))
    report = verify_graph(bad, built["snap"])
    assert not report["ok"]
    assert any("graph_id does not match" in f for f in report["failures"])
    assert any("alias-table.json" in f and "reproduce" in f
               for f in report["failures"])

    _rehash_graph(bad)
    rehashed = json.loads((bad / "graph-manifest.json").read_text())
    assert rehashed["graph_id"] != built["manifest"]["graph_id"]
    report = verify_graph(bad, built["snap"])
    assert not report["ok"]
    assert any("does not reproduce" in f for f in report["failures"])


def test_verify_rejects_promoted_verification_level(built, tmp_path):
    """An edge silently promoted to SOURCE_VERIFIED is rejected even when
    the tamper is fully rehashed (edge id, artifact hashes, graph_id)."""
    bad = _copy_graph(built, tmp_path, "graph-level")
    edges = _load_jsonl(bad / "edges.jsonl")
    edges[0]["verification_level"] = "SOURCE_VERIFIED"
    _reid(edges[0])
    _write_edges(bad, edges)
    _rehash_graph(bad)
    report = verify_graph(bad, built["snap"])
    assert not report["ok"]
    assert any("only CHUNK_VERIFIED is claimable" in f for f in report["failures"])
    assert not any("edge_id does not match" in f for f in report["failures"])
    assert not any("graph_id does not match" in f for f in report["failures"])


def test_verify_rejects_cross_document_span(built, tmp_path):
    bad = _copy_graph(built, tmp_path, "graph-xdoc")
    edges = _load_jsonl(bad / "edges.jsonl")
    foreign = [e for e in edges if e["kind"] == "HAS_SECTION"
               and e["src"] == "SYN-A-002"][0]["evidence"][0]
    victim = [e for e in edges if e["kind"] == "HAS_SECTION"
              and e["src"] == "SYN-A-001"][0]
    victim["evidence"] = [dict(foreign)]
    _reid(victim)
    _write_edges(bad, edges)
    _rehash_graph(bad)
    report = verify_graph(bad, built["snap"])
    assert not report["ok"]
    assert any("another document's chunk" in f for f in report["failures"])


def test_verify_reports_non_utf8_artifact(built, tmp_path):
    bad = _copy_graph(built, tmp_path, "graph-utf8")
    with (bad / "nodes.jsonl").open("ab") as fh:
        fh.write(b"\xff\xfe{}\n")
    report = verify_graph(bad, built["snap"])  # must report, not raise
    assert not report["ok"]
    assert any("not valid UTF-8" in f for f in report["failures"])


def test_verify_reports_non_string_ids(built, tmp_path):
    """Unhashable ids/endpoints must be reported, never crash the verifier."""
    bad = _copy_graph(built, tmp_path, "graph-ids")
    edges = _load_jsonl(bad / "edges.jsonl")
    edges[0]["src"] = ["not", "a", "string"]
    _write_edges(bad, edges)
    nodes = _load_jsonl(bad / "nodes.jsonl")
    doc = next(n for n in nodes if n["kind"] == "Document")
    doc["id"] = {"not": "a string"}
    (bad / "nodes.jsonl").write_text(
        "".join(canonical_json(n) + "\n" for n in nodes), encoding="utf-8")
    report = verify_graph(bad, built["snap"])  # must report, not raise
    assert not report["ok"]
    assert any("malformed edge record" in f for f in report["failures"])
    assert any("malformed node record" in f for f in report["failures"])


def test_verify_catches_undeclared_file(built, tmp_path):
    bad = _copy_graph(built, tmp_path, "graph-extra")
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


# --- negative path: build ---------------------------------------------------------

def test_build_refuses_existing_out(built):
    with pytest.raises(SnapshotStateError):
        build_graph(built["snap"], built["graph"], expected_documents=4)


def test_build_enforces_expected_documents(built, tmp_path):
    with pytest.raises(SnapshotStateError) as exc:
        build_graph(built["snap"], tmp_path / "wrong-count",
                    expected_documents=50)
    assert "50" in str(exc.value) and SYNTH_MARKER not in str(exc.value)
    assert not (tmp_path / "wrong-count").exists()


def test_alias_out_refuses_foreign_file_refreshes_alias(built, tmp_path):
    foreign = tmp_path / "notes.txt"
    foreign.write_text("hand-written notes")
    with pytest.raises(SnapshotStateError):
        build_graph(built["snap"], tmp_path / "g-foreign", expected_documents=4,
                    alias_out=foreign)
    assert foreign.read_text() == "hand-written notes"
    assert not (tmp_path / "g-foreign").exists()

    previous = tmp_path / "alias.json"
    shutil.copy(built["base"] / "alias.json", previous)
    build_graph(built["snap"], tmp_path / "g-alias", expected_documents=4,
                alias_out=previous)
    assert previous.read_bytes() == (built["base"] / "alias.json").read_bytes()


def test_alias_out_refuses_paths_inside_graph_trees(built, tmp_path):
    """A published graph's alias-table.json is hash-pinned by its manifest;
    overwriting it (or writing into this build's own output) is refused
    before anything is built."""
    pinned = built["graph"] / "alias-table.json"
    before = pinned.read_bytes()
    with pytest.raises(SnapshotStateError) as exc:
        build_graph(built["snap"], tmp_path / "g-pinned", expected_documents=4,
                    alias_out=pinned)
    assert "published graph tree" in str(exc.value)
    assert pinned.read_bytes() == before
    out = tmp_path / "g-self"
    with pytest.raises(SnapshotStateError) as exc:
        build_graph(built["snap"], out, expected_documents=4,
                    alias_out=out / "alias.json")
    assert "this build's own output" in str(exc.value)
    assert not (tmp_path / "g-pinned").exists() and not out.exists()


def test_stale_failure_report_removed_on_success(built, tmp_path):
    out = tmp_path / "g-stale"
    stale = tmp_path / "g-stale.failed.json"
    stale.write_text('{"ok": false}')
    build_graph(built["snap"], out, expected_documents=4)
    assert out.is_dir() and not stale.exists()


def test_orphaned_tmp_tree_refused(built, tmp_path):
    orphan = tmp_path / ".g-orphan.tmp-99999"
    orphan.mkdir()
    with pytest.raises(SnapshotStateError) as exc:
        build_graph(built["snap"], tmp_path / "g-orphan", expected_documents=4)
    assert "orphaned" in str(exc.value)
    orphan.rmdir()
    build_graph(built["snap"], tmp_path / "g-orphan", expected_documents=4)


def test_orphan_check_matches_names_literally(built, tmp_path):
    """Glob metacharacters in the output name are literal: an unrelated
    '.g1.tmp-*' does not block 'g[1]', while '.g[1].tmp-*' does."""
    unrelated = tmp_path / ".g1.tmp-5"
    unrelated.mkdir()
    build_graph(built["snap"], tmp_path / "g[1]", expected_documents=4)
    real_orphan = tmp_path / ".g[2].tmp-5"
    real_orphan.mkdir()
    with pytest.raises(SnapshotStateError):
        build_graph(built["snap"], tmp_path / "g[2]", expected_documents=4)


@pytest.mark.parametrize("drift_kind", ["string", "missing"])
def test_malformed_section_titles_fail_typed(tmp_path, drift_kind):
    """A title path delivered as one string, or a missing/renamed key (an
    upstream schema drift the snapshot verifier does not inspect), must
    fail typed, not silently produce an empty but 'verified' graph."""
    source = build_structural_source(tmp_path / "source")

    def drift(records):
        for rec in records:
            if rec["stable_id"] == "SYN-A-001" and rec["index"] == 2:
                if drift_kind == "string":
                    rec["section_path_titles"] = "3. Responsibility"
                else:
                    del rec["section_path_titles"]
        return records

    rewrite_chunks(source, drift)
    snap = tmp_path / "snap"
    _build_snapshot(source, snap)
    with pytest.raises(MalformedRecordError) as exc:
        build_graph(snap, tmp_path / "graph", expected_documents=4)
    assert "section_path_titles" in str(exc.value)
    assert not (tmp_path / "graph").exists()
