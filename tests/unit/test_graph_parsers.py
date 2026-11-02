"""Unit tests for the M1.5 parse rules fixed in the 2026-11-01 review.

Pure-function level (no snapshot): chunk joining, the role classifier and
state machine, and definition lead-ins. Inputs are synthetic.
"""

from __future__ import annotations

import pytest

from karyameshgraph.graph.common import (JoinedSection, continues_into_next,
                                         joined_section)
from karyameshgraph.graph.roles import _classify_b, build_roles
from karyameshgraph.graph.terms import build_terms, normalize_term

SID = "SYN-U-001"


def _rec(i: int, titles: list[str], text: str, **extra) -> dict:
    rec = {
        "chunk_uid": f"{SID}@OBJ/v1.0/abc/section-1000/c{i:04d}#{i:016d}",
        "chunk_id": f"{SID}@OBJ/v1.0/abc/section-1000/c{i:04d}",
        "index": i,
        "text": text,
        "pages": [1],
        "section_path_titles": titles,
        "split_within_element": None,
        "elements": [{"element_end": len(text), "element_length": len(text)}],
    }
    rec.update(extra)
    return rec


# --- chunk joining ----------------------------------------------------------

def test_chunks_join_at_element_boundary():
    js = JoinedSection([("u1#a", "AAA", [1], False), ("u2#b", "BB", [2], False)])
    assert js.text == "AAA\n\nBB"
    assert js.spans(5, 7) == [{"chunk_uid": "u2#b", "start": 0, "end": 2, "page": 2}]
    both = js.spans(1, 6)
    assert [s["chunk_uid"] for s in both] == ["u1#a", "u2#b"]
    assert both[0]["start"] == 1 and both[0]["end"] == 3
    assert both[1]["start"] == 0 and both[1]["end"] == 1


def test_cut_element_parts_join_directly():
    # Upstream keeps the cut whitespace on the first part and the parts are
    # contiguous, so the faithful reconstruction adds nothing between them.
    first = _rec(1, ["DEFINITIONS"], "A. Term – first half of ",
                 split_within_element="sentence",
                 elements=[{"element_end": 24, "element_length": 44}])
    second = _rec(2, ["DEFINITIONS"], "the same definition.",
                  split_within_element="end")
    assert continues_into_next(first) and not continues_into_next(second)
    js = joined_section([first, second], lambda r: True)
    assert js.text == first["text"] + second["text"]
    assert not js.starts_new_element(len(first["text"]))
    spans = js.spans(0, len(js.text))
    assert [(s["start"], s["end"]) for s in spans] == [
        (0, len(first["text"])), (0, len(second["text"]))]


def test_starts_new_element_at_element_boundaries():
    js = JoinedSection([("u1#a", "AAA", [1], False), ("u2#b", "  BB", [1], False)])
    assert js.starts_new_element(0)
    assert js.starts_new_element(7)       # first non-blank char of piece 2
    assert not js.starts_new_element(1)


def test_geometry_alone_marks_a_continuation():
    rec = _rec(1, ["X"], "abc", elements=[{"element_end": 3, "element_length": 9}])
    assert continues_into_next(rec)


def test_continuation_never_crosses_a_gap():
    first = _rec(1, ["DEFINITIONS"], "cut", split_within_element="sentence")
    third = _rec(3, ["DEFINITIONS"], "not adjacent")
    js = joined_section([first, third], lambda r: True)
    assert js.text == "cut\n\nnot adjacent"


def test_definitions_in_separate_chunks_both_parse():
    """Regression: a single-newline join merged the second chunk's first
    entry into the previous paragraph."""
    ctx = {SID: {"family": "A", "chunks": [
        _rec(1, ["7. Glossary/Definitions"], "A. Alpha – first definition."),
        _rec(2, ["7. Glossary/Definitions"], "B. Beta – second definition."),
    ]}}
    result = build_terms(ctx)
    assert sorted(n["name"] for n in result["nodes"]) == ["alpha", "beta"]
    assert len(result["edges"]) == 2


def test_unterminated_definition_continues_into_next_chunk():
    """Upstream can split one sentence into two paragraph elements across a
    chunk boundary with no cut marker; the continuation starts uppercase."""
    ctx = {SID: {"family": "B", "chunks": [
        _rec(1, ["DEFINITIONS"], "Batch: a quantity produced under"),
        _rec(2, ["DEFINITIONS"], "Controlled conditions in one cycle.\n\n"
                                 "Lot: a defined portion of a batch."),
    ]}}
    result = build_terms(ctx)
    assert sorted(n["name"] for n in result["nodes"]) == ["batch", "lot"]
    batch = [e for e in result["edges"] if e["src"].startswith("term/batch")][0]
    assert batch["derivation"] == "EXPLICIT" and not batch["truncated"]
    assert [s["chunk_uid"] for s in batch["evidence"]] == [
        r["chunk_uid"] for r in ctx[SID]["chunks"]]
    assert result["stats"]["chunk_boundary_continuations"] == 1


def test_terminated_definition_does_not_absorb_next_chunk():
    ctx = {SID: {"family": "B", "chunks": [
        _rec(1, ["DEFINITIONS"], "Batch: a quantity produced in one cycle."),
        _rec(2, ["DEFINITIONS"], "Unrelated Heading Text"),
    ]}}
    result = build_terms(ctx)
    assert result["stats"]["chunk_boundary_continuations"] == 0
    assert result["stats"]["paragraphs_unparsed"] == 1


def test_glued_footnote_after_bracket_is_not_truncation():
    ctx = {SID: {"family": "B", "chunks": [
        _rec(1, ["DEFINITIONS"], "Sponsor: the party named (see section 5.)14"),
    ]}}
    edge = build_terms(ctx)["edges"][0]
    assert edge["derivation"] == "EXPLICIT" and not edge["truncated"]


@pytest.mark.parametrize("raw,key,alias", [
    ("ISO 17034", "iso 17034", None),
    ("Level 1", "level 1", None),
    ("ICH Q8", "ich q8", None),
    ("Record18", "record", None),
    ("Calibration (CAL)", "calibration", "CAL"),
    ("Impurity (chemical substances)", "impurity (chemical substances)", None),
    ("Newly identified safety signal (NISS) (as defined for this MAPP)",
     "newly identified safety signal", "NISS"),
])
def test_normalize_term(raw, key, alias):
    assert normalize_term(raw) == (key, alias)


def test_list_leadin_attaches_to_open_definition():
    ctx = {SID: {"family": "B", "chunks": [
        _rec(1, ["DEFINITIONS"],
             "Risk: a possibility of harm.\n\n"
             "Examples of potential risks include: loss, delay.\n\n"
             "Signal: an indication worth review."),
    ]}}
    result = build_terms(ctx)
    assert sorted(n["name"] for n in result["nodes"]) == ["risk", "signal"]
    assert result["stats"]["leadins_rejected"] == 1
    assert result["stats"]["paragraphs_attached"] == 1


# --- role classifier ----------------------------------------------------------

@pytest.mark.parametrize("line,nxt,want", [
    ("The Office of Testing (OT) will:", "• x", ("role", "The Office of Testing (OT)")),
    ("Division Director:", "• x", ("role", "Division Director")),
    ("Records Liaison Officer", "• Track holds.", ("role", "Records Liaison Officer")),
    ("Director, Office of Management (OM) (or designee)", "Prose follows.",
     ("role", "Director, Office of Management (OM) (or designee)")),
    (" The RBPM will:", " sub", ("role", "The RBPM")),
    ("• Safety Project Manager (SPM)", "‒ duty",
     ("role", "Safety Project Manager (SPM)")),
    ("• 921 Posting Team19", "‒ duty", ("role", "921 Posting Team")),
    ("OPQ Assessment Team10", "• duty", ("role", "OPQ Assessment Team")),
    # Junk the pre-review class admitted ('.' and digit starts):
    ("1. The ETT will collaborate with offices, when appropriate, to:", "• x",
     ("continuation", None)),
    ("staff has not already created a new set.16", "• x", ("continuation", None)),
    ("Operations (ORO) staff has not already created a new set.16", "• x",
     ("continuation", None)),
    ("Section 3.2 of the procedure", "• x", ("continuation", None)),
    ("For OGD-Managed Submissions:", "• x", ("continuation", None)),
    ("12An inspection may follow.", None, ("footnote", None)),
    ("REFERENCES18", None, ("bleed", None)),
    ("‒ A dash-tier duty line.", None, ("duty", None)),
    ("o a sub-tier line", None, ("subduty", None)),
])
def test_classify_b(line, nxt, want):
    assert _classify_b(line, nxt) == want


# --- role state machine ---------------------------------------------------------

def _resp(*texts, start=1):
    return [_rec(start + i, ["RESPONSIBILITIES"], t) for i, t in enumerate(texts)]


def test_header_ending_a_chunk_owns_next_chunks_duties():
    ctx = {SID: {"family": "B", "chunks": _resp(
        "Team Lead:\n• Plan the work.\nRecords Officer",
        "• Track holds.\n• Report holds.",
    )}}
    result = build_roles(ctx)
    names = {n["id"]: n["name"] for n in result["nodes"] if n["kind"] == "Role"}
    by_role: dict[str, list[str]] = {}
    for e in result["edges"]:
        by_role.setdefault(names[e["src"]], []).append(e["derivation"])
    assert by_role == {"Team Lead": ["EXPLICIT"],
                       "Records Officer": ["INFERRED", "INFERRED"]}


def test_role_never_carries_across_an_index_gap():
    chunks = _resp("Team Lead:\n• Plan the work.") + _resp(
        "• Orphaned duty after a gap.", start=5)
    result = build_roles({SID: {"family": "B", "chunks": chunks}})
    assert len(result["edges"]) == 1
    assert result["stats"]["unattributed_duty_lines"] == 1


def test_lookahead_skips_footnotes():
    ctx = {SID: {"family": "B", "chunks": _resp(
        "Review Board\n12 A footnote between header and duties.\n• Approve plans.",
    )}}
    result = build_roles(ctx)
    assert [n["name"] for n in result["nodes"] if n["kind"] == "Role"] == ["Review Board"]
    assert result["stats"]["footnote_lines_dropped"] == 1
