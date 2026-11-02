"""Deliverable 3 — Term nodes + DEFINED_IN edges (SPEC.md §7.5).

Definitions sections are identified by the DEEPEST section-path title
(membership by any-title-in-path over-matches: CRS-SOP-010 nests RECORDS
under Glossary/Definitions). Chunks of a section are joined before
paragraph splitting so the one mid-entry chunk split in the corpus
(CRS-SOP-012) parses losslessly. Four concrete format variants exist
(lettered / bulleted / unmarked-dash / unmarked-colon — the spec's "3"
counts the two unmarked forms as one family; the deviation is reported).

Terms defined in multiple documents share ONE Term node with one
DEFINED_IN edge per document — linked for comparison, never merged: the
definitions themselves stay distinct per-edge evidence spans. Acronym
parentheticals become edge-level aliases, never part of the term key
('RM' = Records Management in one doc, Reference Material in another).
A definition with no terminal punctuation (interleaved PDF footnotes) is
AMBIGUOUS, never silently promoted. Definition text is never copied into
the graph — only spans.
"""

from __future__ import annotations

import re

from .common import DEF_TITLES, deepest_title, joined_section, slug
from .models import (DERIVATION_AMBIGUOUS, DERIVATION_EXPLICIT, edge, node)

_V1 = re.compile(  # 'A. Term – definition' (also ' - ', ': ', tight 'term- ')
    r"^(?P<letter>[A-Z])\.\s+(?P<term>\S.{0,78}?)"
    r"(?:\s[–—-]\s|(?<=\S)[–—-]\s|:\s)\s*(?P<defn>.+)$", re.S)
_V4 = re.compile(  # '• Term: def' / ' Term – def' (bulleted)
    r"^[•]\s*(?P<term>\S.{0,98}?)"
    r"(?::\s+|\s+[–—-]\s*|(?<=\S)[–—-]\s+)(?P<defn>.+)$", re.S)
_V2 = re.compile(  # 'Term – definition' (unmarked dash)
    r"^(?![A-Z]\.\s)(?![•‒]\s?)(?P<term>[A-Z\"“]\S.{0,76}?)"
    r"(?:\s+[–—-]\s*|(?<=\S)[–—-]\s+)(?P<defn>.+)$", re.S)
_V3 = re.compile(  # 'Term: definition' (unmarked colon)
    r"^(?![A-Z]\.\s)(?![•‒]\s?)(?P<term>[A-Z\"“][^:\n]{0,78}?):\s+"
    r"(?P<defn>.+)$", re.S)
_COPULA = re.compile(  # 1 case (CRS-SOP-007): 'B. Records Cut Off is a ...'
    r"^(?P<letter>[A-Z])\.\s+(?P<term>.{1,60}?)\s+is\s+(?P<defn>.+)$", re.S)
_VARIANTS = (("V1_lettered", _V1), ("V4_bulleted", _V4),
             ("V2_unmarked_dash", _V2), ("V3_unmarked_colon", _V3))

# Paragraph-level pre-filters (verified counts in the M1.5 report).
_P_FOOTNOTE = re.compile(r"^\d{1,2}\s+\S")
_P_HRULE = re.compile(r"^_{5,}")
_P_PREAMBLE = re.compile(r"^(?:NOTE:|Note to reader:|This MAPP includes)")
_P_ATTACH_NUM = re.compile(r"^\d{1,2}\.\s")        # numbered list inside a defn
_P_ATTACH_QUOTE = re.compile(r"^\([A-Z]\)\s")      # statute quote lines
_P_ATTACH_SUB = re.compile(r"^[‒—-]\s")  # example sub-bullets
_P_CONTINUATION = re.compile(r"^[a-z(\[]")
_P_BULLET = re.compile(r"^[•]\s?")
# A colon/bullet "term" that is really a list lead-in inside the previous
# definition ('Examples of potential risks include:' — flagged by the
# discovery sweep as a V3 false positive in CRS-SOP-044/045).
_LEADIN_TERM = re.compile(
    r"\b(?:include|includes|including|following|as follows)$", re.I)

_QUALIFIER = re.compile(r"\s*\(as defined for this MAPP\)\s*", re.I)
# Footnote superscripts glued to a word ('Record18'). Digits after a space
# or an uppercase letter are part of the term: an unconditional strip
# mangled 'ISO 17034' into 'iso 170' and merged 'Level 1'..'Level 4' into
# one term (2026-11-01 review).
_TRAIL_DIGITS = re.compile(r"(?<=[a-z)])\d{1,2}$")
_TRAIL_PAREN = re.compile(r"\s*\(([^()]{1,40})\)\s*$")
# Footnote refs glued after sentence punctuation, incl. a closing bracket
# or quote that _TERMINAL already accepts ('...5.)14').
_GLUED_FOOT_REFS = re.compile(r"(?<=[.?!)”\"])\s*\d{1,2}(?:,\d{1,2})*$")
_TERMINAL = re.compile(r"[.?!:)”\"]\s*$")


def normalize_term(raw: str) -> tuple[str, str | None]:
    """→ (term_key, acronym_alias_or_None). The alias is popped only when
    acronym-like and NEVER merged into the key."""
    t = raw.strip().strip("'\"“”").strip()
    t = _QUALIFIER.sub(" ", t)
    t = _TRAIL_DIGITS.sub("", t)
    alias = None
    m = _TRAIL_PAREN.search(t)
    if m:
        inner = m.group(1)
        if len(re.findall(r"[A-Z]", inner)) >= 2 and not re.search(r"\b[a-z]{3,}\b", inner):
            alias = inner
            t = t[: m.start()].rstrip()
    t = re.sub(r"[\s\xa0]+", " ", t).strip().rstrip(".,;").lower()
    return t, alias


def _paragraphs(text: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for m in re.finditer(r"\n[ \t]*\n+", text):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(text)))
    out = []
    for s, e in bounds:
        seg = text[s:e]
        if seg.strip():
            out.append((s + (len(seg) - len(seg.lstrip())),
                        e - (len(seg) - len(seg.rstrip()))))
    return out


def build_terms(docs_ctx: dict[str, dict]) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    term_nodes: dict[str, dict] = {}
    stats = {
        "documents_total": len(docs_ctx),
        "documents_with_def_section": 0,
        "documents_with_terms": 0,
        "empty_glossaries": [],
        "entries_parsed": 0,
        "entries_by_variant": {name: 0 for name, _ in _VARIANTS},
        "entries_copula_ambiguous": 0,
        "entries_truncated_ambiguous": 0,
        "paragraphs_total": 0,
        "paragraphs_attached": 0,
        "paragraphs_dropped_footnote": 0,
        "paragraphs_dropped_other": 0,
        "paragraphs_unparsed": 0,
        "group_headers": 0,
        "leadins_rejected": 0,
        "chunk_boundary_continuations": 0,
    }

    for sid in sorted(docs_ctx):
        chunks = docs_ctx[sid]["chunks"]
        js = joined_section(
            chunks, lambda rec: deepest_title(rec) in DEF_TITLES)
        if js is None:
            continue
        stats["documents_with_def_section"] += 1
        if js.text.strip() == "None":  # CRS-SOP-015: literal empty glossary
            stats["empty_glossaries"].append(sid)
            continue

        paras = _paragraphs(js.text)
        stats["paragraphs_total"] += len(paras)
        entries: list[dict] = []
        open_entry: dict | None = None

        for pi, (ps, pe) in enumerate(paras):
            body = js.text[ps:pe]
            if _P_FOOTNOTE.match(body):
                stats["paragraphs_dropped_footnote"] += 1
                continue
            if _P_HRULE.match(body) or _P_PREAMBLE.match(body):
                stats["paragraphs_dropped_other"] += 1
                continue
            if (_P_ATTACH_NUM.match(body) or _P_ATTACH_QUOTE.match(body)
                    or _P_ATTACH_SUB.match(body) or _P_CONTINUATION.match(body)):
                if open_entry is not None:
                    open_entry["end"] = pe
                    stats["paragraphs_attached"] += 1
                else:
                    stats["paragraphs_unparsed"] += 1
                continue

            matched = None
            for vname, rx in _VARIANTS:
                m = rx.match(body)
                if m and m.group("defn").strip():
                    matched = (vname, m)
                    break
            if (matched is not None
                    and matched[0] in ("V3_unmarked_colon", "V4_bulleted")
                    and _LEADIN_TERM.search(matched[1].group("term").strip())):
                # List lead-in, not an entry: part of the open definition.
                if open_entry is not None:
                    open_entry["end"] = pe
                    stats["paragraphs_attached"] += 1
                else:
                    stats["paragraphs_unparsed"] += 1
                stats["leadins_rejected"] += 1
                continue
            if matched is None and _P_BULLET.match(body):
                # Bullet without a separator: example list under the open
                # entry, never a term.
                if open_entry is not None:
                    open_entry["end"] = pe
                    stats["paragraphs_attached"] += 1
                else:
                    stats["paragraphs_unparsed"] += 1
                continue
            if matched is None:
                m = _COPULA.match(body)
                if m:
                    matched = ("copula", m)
            if matched is None:
                # Term-only group header ('MAPP' heading sub-bullets)? Tested
                # first, so a header that happens to open a chunk is never
                # absorbed as a continuation.
                nxt = js.text[paras[pi + 1][0]:paras[pi + 1][1]] if pi + 1 < len(paras) else ""
                if ("\n" not in body and len(body) <= 80 and body[:1].isupper()
                        and not body.rstrip().endswith(".") and _P_BULLET.match(nxt)):
                    stats["group_headers"] += 1
                elif (open_entry is not None
                        and js.starts_adjacent_element(ps)
                        and not _TERMINAL.search(_GLUED_FOOT_REFS.sub(
                            "", js.text[open_entry["start"]:open_entry["end"]].rstrip()))):
                    # Upstream sometimes splits one sentence into two
                    # paragraph elements across a chunk boundary with no cut
                    # marker: the unterminated entry continues into the
                    # first paragraph of the index-ADJACENT chunk (never
                    # across a gap or a skipped chunk).
                    open_entry["end"] = pe
                    stats["paragraphs_attached"] += 1
                    stats["chunk_boundary_continuations"] += 1
                else:
                    stats["paragraphs_unparsed"] += 1
                continue

            vname, m = matched
            if open_entry is not None:
                entries.append(open_entry)
            key, alias = normalize_term(m.group("term"))
            if not key:
                stats["paragraphs_unparsed"] += 1
                open_entry = None
                continue
            open_entry = {
                "key": key, "alias": alias, "raw": m.group("term").strip(),
                "variant": vname, "start": ps, "end": pe,
            }
        if open_entry is not None:
            entries.append(open_entry)

        doc_terms = 0
        for entry in entries:
            defn_text = _GLUED_FOOT_REFS.sub(
                "", js.text[entry["start"]:entry["end"]].rstrip())
            truncated = not _TERMINAL.search(defn_text)
            if entry["variant"] == "copula":
                derivation = DERIVATION_AMBIGUOUS
                stats["entries_copula_ambiguous"] += 1
            elif truncated:
                derivation = DERIVATION_AMBIGUOUS
                stats["entries_truncated_ambiguous"] += 1
            else:
                derivation = DERIVATION_EXPLICIT
                stats["entries_by_variant"][entry["variant"]] += 1
            stats["entries_parsed"] += 1

            term_id = f"term/{slug(entry['key'])}"
            if term_id not in term_nodes:
                term_nodes[term_id] = node("Term", term_id, name=entry["key"],
                                           aliases=[], documents=[])
                nodes.append(term_nodes[term_id])
            tn = term_nodes[term_id]
            if entry["alias"] and entry["alias"] not in tn["aliases"]:
                tn["aliases"].append(entry["alias"])
            if sid not in tn["documents"]:
                tn["documents"].append(sid)
            edges.append(edge(
                "DEFINED_IN", term_id, sid,
                derivation=derivation,
                evidence=js.spans(entry["start"], entry["end"]),
                term_raw=entry["raw"], variant=entry["variant"],
                alias=entry["alias"], truncated=truncated,
            ))
            doc_terms += 1
        if doc_terms:
            stats["documents_with_terms"] += 1

    for tn in term_nodes.values():
        tn["aliases"].sort()
        tn["doc_count"] = len(tn["documents"])
    stats["distinct_terms"] = len(term_nodes)
    stats["multi_doc_terms"] = sorted(
        tn["name"] for tn in term_nodes.values() if tn["doc_count"] >= 2)
    return {"nodes": nodes, "edges": edges, "stats": stats, "exclusions": {}}
