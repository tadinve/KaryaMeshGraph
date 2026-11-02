"""Structural-graph fixture: four synthetic docs exercising every M1.5 rule.

- SYN-A-001 (family A): roles with a duty carried across a chunk boundary,
  two definitions in two SEPARATE chunks (regression: a single-newline
  chunk join fused them into one entry and lost 'Record'), a clean
  Document History table, one noisy section.
- SYN-A-002 (family A): self-citation + unresolvable citation, a duplicate
  'Record' definition (multi-doc term), a SCRAMBLED history table.
- SYN-B-001 (family B): 'will:'-role + colon-role + carried bullet duty +
  footnote contamination; a bare role header that ENDS a chunk with its
  duties in the next chunk (regression: per-chunk lookahead missed the
  header and gave its duties to the previous role); a list lead-in
  ('... include:') that must not become a term; a truncated definition
  (AMBIGUOUS); clean CHANGE CONTROL TABLE with a cross-line description.
- SYN-B-002 (family B): front-matter chunk, merged RESPONSIBILITIES AND
  PROCEDURES (no role edges, AMBIGUOUS exclusion), clean one-row table.

SYNTH_MARKER appears ONLY inside prose bodies (duties, definitions,
descriptions, purpose text) - never in titles, role names, or terms - so
tests can assert that no marker ever reaches a graph file: the graph
stores names and spans, not passages.
"""

from __future__ import annotations

import json
from pathlib import Path

from synthetic import SYNTH_MARKER, VERSIONS, _sha

M = SYNTH_MARKER

DOC_SPECS = [
    {
        "stable_id": "SYN-A-001",
        "number": "ORA-LAB.9.1",
        "title": "Synthetic Lab Quality Manual",
        "series": "FDA ORA Laboratory Manual Vol. II",
        "chunks": [
            {"titles": ["1. Purpose"],
             "text": f"{M} This synthetic manual explains testing per "
                     "ORA-LAB.9.2 and 21 CFR 11.10 and ISO/IEC 17025:2017."},
            {"titles": ["3. Responsibility"],
             "text": "A. Quality Manager:\n"
                     f"1. {M} Maintains the synthetic quality system.\n"
                     f"2. {M} Reviews synthetic records monthly."},
            {"titles": ["3. Responsibility"],
             "text": f"3. {M} Approves synthetic deviations after review.\n"
                     "B. Laboratory Director\n"
                     f"1. {M} Owns the synthetic manual end to end."},
            {"titles": ["7. Glossary/Definitions"],
             "text": f"A. Calibration – {M} synthetic comparison against "
                     "a reference standard."},
            {"titles": ["7. Glossary/Definitions"],
             "text": f"B. Record – {M} synthetic recorded evidence of an "
                     "activity."},
            {"titles": ["10. Document History"],
             "text": "Revision # | Status | Date | Author | Approver\n"
                     "1.0 | I | 01/02/20 | ABC | DEF\n"
                     "1.1 | R | 03/04/21 | ABC | DEF"},
            {"titles": ["Nonsense Attachment Table"],
             "text": f"{M} noisy table fragment that is not a canon section."},
        ],
    },
    {
        "stable_id": "SYN-A-002",
        "number": "ORA-LAB.9.2",
        "title": "Synthetic Sample Handling",
        "series": "FDA ORA Laboratory Manual Vol. II",
        "chunks": [
            {"titles": ["1. Purpose"],
             "text": f"{M} Synthetic purpose; this document ORA-LAB.9.2 "
                     "supplements ORA-LAB.9.9 which is not in the corpus."},
            {"titles": ["7. Glossary/Definitions"],
             "text": f"A. Record – {M} synthetic duplicate definition in "
                     "a second document."},
            {"titles": ["10. Document History"],
             "text": "Revision\n\n1.0\n\nREFER TO\n\nQMIS"},
        ],
    },
    {
        "stable_id": "SYN-B-001",
        "number": "MAPP 9001.1",
        "title": "Synthetic MAPP Procedures",
        "series": "FDA CDER MAPP",
        "chunks": [
            {"titles": ["PURPOSE"],
             "text": f"{M} Synthetic purpose citing MAPP 9001.2 and "
                     "MAPP 9999.9 and the Guidance for Industry."},
            {"titles": ["RESPONSIBILITIES"],
             "text": "The Synthetic Office of Testing (SOT) will:\n"
                     f"• {M} Develop synthetic procedures for intake.\n"
                     f"• {M} Maintain synthetic registers of work.\n"
                     f"12 {M} synthetic footnote line that must be dropped."},
            {"titles": ["RESPONSIBILITIES"],
             "text": f"• {M} Coordinate synthetic reviews quarterly.\n"
                     "Division Director:\n"
                     f"• {M} Sign synthetic memoranda when issued.\n"
                     "Records Liaison Officer"},
            {"titles": ["RESPONSIBILITIES"],
             "text": f"• {M} Track synthetic records holds.\n"
                     f"• {M} Report synthetic holds monthly."},
            {"titles": ["DEFINITIONS"],
             "text": f"Synthetic Term: {M} a term used only for testing "
                     "graph extraction.\n\n"
                     f"Examples of synthetic risks include: {M} alpha, beta "
                     "and gamma.\n\n"
                     f"Calibration (CAL): {M} second-family definition that "
                     "is visibly truncated"},
            {"titles": ["CHANGE CONTROL TABLE"],
             "text": "Effective Date | Revision Number | Revisions\n"
                     f"1/15/22 | Initial | {M} synthetic initial issuance.\n"
                     f"3/20/23 | Rev. 1 | {M} synthetic update adding tests\n"
                     "and a continuation line for the same revision row."},
            {"titles": ["EFFECTIVE DATE"],
             "text": f"{M} This synthetic MAPP is effective upon publication."},
        ],
    },
    {
        "stable_id": "SYN-B-002",
        "number": "MAPP 9001.2",
        "title": "Synthetic MAPP Records",
        "series": "FDA CDER MAPP",
        "chunks": [
            {"titles": [],
             "text": f"{M} synthetic title-page content before any heading."},
            {"titles": ["PURPOSE"],
             "text": f"{M} Synthetic purpose for the merged-document case."},
            {"titles": ["RESPONSIBILITIES AND PROCEDURES"],
             "text": f"• {M} merged synthetic duty that must yield no "
                     "role edge.\n"
                     f"• {M} second merged synthetic bullet."},
            {"titles": ["CHANGE CONTROL TABLE"],
             "text": f"2/2/24 | 1 | {M} synthetic single clean row."},
        ],
    },
]


def _structural_chunk(doc: str, obj: str, pdf_sha: str, i: int,
                      titles: list[str], text: str) -> dict:
    sha = _sha(text.encode("utf-8"))
    chunk_id = f"{doc}@{obj}/v1.0/{pdf_sha[:12]}/section-1000/c{i:04d}"
    n = len(text)
    sec_ids = [f"e{i:02d}{j}" for j in range(len(titles))]
    return {
        "chunk_id": chunk_id,
        "chunk_sha256": sha,
        "chunk_uid": f"{chunk_id}#{sha[:16]}",
        "corpus_generation": "v2",
        "stable_id": doc,
        "object_id": obj,
        "version": "1.0",
        "pdf_sha256": pdf_sha,
        "text": text,
        "pages": [1],
        "index": i,
        "section_id": sec_ids[-1] if sec_ids else None,
        "section_number": None,
        "section_path": sec_ids,
        "section_path_titles": titles,
        "source_spans": [{
            "segment": f"{doc}@{obj}/p1/s{i}", "page": 1,
            "bbox": [10.0, 10.0, 200.0, 20.0],
            "segment_start": 0, "segment_end": n,
            "chunk_start": 0, "chunk_end": n, "partial": False,
        }],
        "elements": [{
            "element_id": f"e{i:04d}", "kind": "paragraph",
            "section_heading": titles[-1] if titles else None,
            "element_start": 0, "element_end": n,
            "chunk_start": 0, "chunk_end": n, "element_length": n,
        }],
        "units": 1,
        "review_flags": [],
        "inserted": [],
        "chunker": "section_aware",
        "chunker_version": VERSIONS["section"],
        "max_chars": 1000,
        "overlap_chars": 0,
        "char_count": n,
        "context_header": titles[-1] if titles else "",
        "split_within_element": False,
        "step_continuation": False,
    }


def build_structural_source(root: Path) -> Path:
    """A complete fake KaryaMeshRAG tree whose four documents exercise the
    M1.5 structural rules (same layout/hashing contract as
    ``synthetic.build_synthetic_source``)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "src" / "karyamesh").mkdir(parents=True)
    (root / "src" / "karyamesh" / "__init__.py").write_text("# synthetic\n")

    pdf_shas: dict[str, str] = {}
    records: list[dict] = []
    corpus_entries: list[dict] = []
    dms_docs: list[dict] = []
    originals = root / "data" / "originals"
    originals.mkdir(parents=True)
    for n, spec in enumerate(DOC_SPECS, start=1):
        doc = spec["stable_id"]
        obj = f"SIM-91000{n}"
        pdf_bytes = f"%PDF-1.4 synthetic structural {doc}".encode()
        (originals / f"{doc}.pdf").write_bytes(pdf_bytes)
        pdf_sha = _sha(pdf_bytes)
        pdf_shas[doc] = pdf_sha
        for i, chunk in enumerate(spec["chunks"], start=1):
            records.append(_structural_chunk(
                doc, obj, pdf_sha, i, chunk["titles"], chunk["text"]))
        corpus_entries.append({
            "stable_id": doc,
            "file": f"data/originals/{doc}.pdf",
            "sha256": pdf_sha,
            "bytes": len(pdf_bytes),
            "pages": 1,
            "source_document_number": spec["number"],
            "source_title": spec["title"],
            "source_series": spec["series"],
            "candidate_id": f"CAND-S{n:02d}",
        })
        dms_docs.append({
            "object_id": obj, "document_id": doc, "version": "1.0",
            "title": spec["title"], "lifecycle_state": "APPROVED",
            "approved_at": "2026-10-01T09:00:00Z",
            "effective_at": "2026-10-05T00:00:00Z", "withdrawn_at": None,
            "content_path": f"data/originals/{doc}.pdf",
            "source_sha256": pdf_sha,
            "source_ref": {"stable_id": doc, "provenance": "synthetic"},
            "review_status": "included", "is_simulated": True,
            "simulated_fields": ["approved_at", "effective_at"],
            "simulation_note": "synthetic structural fixture",
        })

    chunks_dir = root / "var" / "v2" / "chunks"
    chunks_dir.mkdir(parents=True)
    payload = b"".join(json.dumps(r, sort_keys=True).encode() + b"\n" for r in records)
    (chunks_dir / "corpus.section-1000.jsonl").write_bytes(payload)

    frozen = {
        "corpus_generation": "v2",
        "status": "frozen",
        "frozen_at": "2026-10-19T21:47:53+00:00",
        "frozen_from": "var/chunks-v2-candidate",
        "versions": VERSIONS,
        "fingerprints": {
            "section": {
                "file": "corpus.section-1000.jsonl",
                "sha256": _sha(payload),
                "chunks": len(records),
                "documents": len(DOC_SPECS),
                "candidate_sha256": _sha(b"synthetic-candidate"),
            },
        },
        "inputs": {"pdf_count": len(DOC_SPECS), "pdf_sha256": pdf_shas},
    }
    frozen_path = root / "evidence" / "day08" / "corpus-manifest-v2.json"
    frozen_path.parent.mkdir(parents=True)
    frozen_path.write_text(json.dumps(frozen, indent=1, sort_keys=True))

    (root / "corpus").mkdir()
    (root / "corpus" / "manifest.json").write_text(json.dumps(corpus_entries, indent=1))
    authz = root / "corpus" / "authz"
    authz.mkdir()
    (authz / "identities.json").write_text(json.dumps({
        "registry_version": "syn-ids-1",
        "note": "synthetic",
        "principals": {
            "svc.import": {"display_name": "Import Service",
                           "groups": ["ops"], "status": "active"},
        },
    }, indent=1))
    (authz / "acl-policy.json").write_text(json.dumps({
        "policy_version": "syn-acl-1",
        "resolution_order": ["explicit deny", "allow", "default deny"],
        "note": "synthetic",
        "grants": [{
            "grant_id": "g1", "subject_type": "principal",
            "subject_id": "svc.import", "document_id": "*",
            "effect": "allow", "granted_at": "2026-01-01T00:00:00Z",
        }],
    }, indent=1))

    dms = root / "data" / "mock_dms"
    dms.mkdir(parents=True)
    (dms / "documents.json").write_text(json.dumps(dms_docs, indent=1))
    (dms / "events.jsonl").write_text(
        json.dumps({"event": "approved", "document_id": DOC_SPECS[0]["stable_id"]}) + "\n")
    return root
