"""Shared deterministic helpers for the M1.5 structural builders.

Every constant and regex here was verified empirically against the frozen
v2 corpus (2026-11-01 discovery sweep, five read-only probes; counts are
recorded in evidence/m1_5-report.md). Nothing is guessed: where upstream
data contradicted the spec's expectation, the data won and the deviation
is reported, never papered over.
"""

from __future__ import annotations

import hashlib
import re

from ..errors import MalformedRecordError
from .models import evidence_span

# --- Document families -----------------------------------------------------
# Family A = ORS laboratory SOPs (CRS-SOP-001..019), family B = CDER MAPPs
# (020..050). Detected from the corpus manifest's source_series, never from
# the stable_id spelling, so synthetic fixtures and future corpora behave
# identically.
FAMILY_A = "A"
FAMILY_B = "B"
_SERIES_FAMILY = (
    ("FDA ORA LABORATORY MANUAL", FAMILY_A),
    ("FDA CDER MAPP", FAMILY_B),
)


def family_for_series(source_series: str | None) -> str | None:
    if not isinstance(source_series, str):
        return None
    s = source_series.upper()
    for prefix, fam in _SERIES_FAMILY:
        if s.startswith(prefix):
            return fam
    return None


# --- Section-title normalization (verified: collapses family A's numbered
# and unnumbered sub-styles into one 12-title canon; 'Glossary/ Definitions'
# → 'GLOSSARY/DEFINITIONS'). Letter prefixes like 'S.2.1' are deliberately
# NOT stripped: they are CTD noise and must stay distinct.
_LEADING_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s+")
_SLASH_SPACE = re.compile(r"\s*/\s*")
_WS = re.compile(r"\s+")
# The one observed canon spelling variant (CRS-SOP-030).
_TITLE_ALIASES = {"REFERENCE": "REFERENCES"}


def normalize_title(raw: str) -> str:
    t = _LEADING_NUMBER.sub("", str(raw))
    t = t.strip().rstrip(":").strip()
    t = _SLASH_SPACE.sub("/", t)
    t = _WS.sub(" ", t).upper()
    return _TITLE_ALIASES.get(t, t)


# Canonical top-level section titles per family (doc counts verified
# 2026-11-01; family A 12 titles, family B 10 incl. the merged variant).
CANON_A = (
    "PURPOSE", "SCOPE", "RESPONSIBILITY", "BACKGROUND", "REFERENCES",
    "PROCEDURE", "GLOSSARY/DEFINITIONS", "RECORDS", "SUPPORTING DOCUMENTS",
    "DOCUMENT HISTORY", "CHANGE HISTORY", "ATTACHMENTS",
)
CANON_B = (
    "PURPOSE", "BACKGROUND", "POLICY", "RESPONSIBILITIES",
    "RESPONSIBILITIES AND PROCEDURES", "PROCEDURES", "REFERENCES",
    "DEFINITIONS", "EFFECTIVE DATE", "CHANGE CONTROL TABLE",
)
CANONS = {FAMILY_A: frozenset(CANON_A), FAMILY_B: frozenset(CANON_B)}

# Structural anchors used by the deliverable builders.
RESP_TITLES = {FAMILY_A: "RESPONSIBILITY", FAMILY_B: "RESPONSIBILITIES"}
MERGED_RESP_TITLE = "RESPONSIBILITIES AND PROCEDURES"
DEF_TITLES = frozenset({"DEFINITIONS", "GLOSSARY/DEFINITIONS"})
REV_TITLES = {FAMILY_A: "DOCUMENT HISTORY", FAMILY_B: "CHANGE CONTROL TABLE"}

FRONT_MATTER = "FRONT_MATTER"


def slug(text: str) -> str:
    """Deterministic id fragment for a name: lowercase alnum runs joined by
    '-', suffixed by a short content hash so distinct names never collide
    after slugging."""
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "x"
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{h}"


def top_title(rec: dict) -> str | None:
    """Normalized top-level section title of a chunk, or None for the five
    depth-0 front-matter chunks (empty section_path_titles, always index 1)."""
    titles = rec.get("section_path_titles") or []
    if not titles:
        return None
    return normalize_title(titles[0])


def deepest_title(rec: dict) -> str | None:
    titles = rec.get("section_path_titles") or []
    if not titles:
        return None
    return normalize_title(titles[-1])


def lines_with_offsets(text: str) -> list[tuple[str, int]]:
    out = []
    pos = 0
    for line in text.split("\n"):
        out.append((line, pos))
        pos += len(line) + 1
    return out


ELEMENT_SEPARATOR = "\n\n"   # upstream's own inter-element separator
# The two parts of a cut element are contiguous (element_end of the first
# == element_start of the second on all 26 real cuts) and the cut
# whitespace stays on the first part, so they join with nothing between.
CONTINUATION_SEPARATOR = ""


def continues_into_next(rec: dict) -> bool:
    """True when a chunk's last element is cut and continues in the next
    chunk. Upstream marks the cut with ``split_within_element`` set to the
    cut kind ('sentence'/'whitespace') on the earlier part and 'end' on the
    final part; element geometry (element_end < element_length) agrees on
    all 26 real cases, and either signal suffices."""
    swe = rec.get("split_within_element")
    if isinstance(swe, str) and swe and swe != "end":
        return True
    els = rec.get("elements")
    if isinstance(els, list) and els and isinstance(els[-1], dict):
        end, length = els[-1].get("element_end"), els[-1].get("element_length")
        if isinstance(end, int) and isinstance(length, int) and end < length:
            return True
    return False


class JoinedSection:
    """Chunks of one titled section joined for cross-chunk parsing, with
    exact mapping from joined offsets back to per-chunk evidence spans
    (overlap_chars=0 upstream makes the join lossless).

    Chunk boundaries are element boundaries, so chunks are joined with
    upstream's element separator (a blank line); a chunk that continues a
    cut element is joined directly, reconstructing the element. Joining
    every boundary with one newline (the pre-2026-11-01-review behavior)
    fused the first definition of each chunk into the previous one and
    silently lost 85 of 339 real definitions."""

    def __init__(self, pieces: list[tuple]):
        # pieces: (chunk_uid, text, pages, continues_previous_piece
        #          [, index_adjacent_to_previous_piece — default False])
        parts: list[str] = []
        self._bounds: list[tuple[int, int, str, int, int | None]] = []
        self._adjacent: list[bool] = []
        pos = 0
        for i, (uid, t, pages, cont, *rest) in enumerate(pieces):
            self._adjacent.append(bool(rest[0]) if rest else False)
            if i:
                sep = CONTINUATION_SEPARATOR if cont else ELEMENT_SEPARATOR
                parts.append(sep)
                pos += len(sep)
            first_page = pages[0] if isinstance(pages, list) and pages else None
            self._bounds.append((pos, pos + len(t), uid, len(t), first_page))
            parts.append(t)
            pos += len(t)
        self.text = "".join(parts)

    def starts_new_element(self, offset: int) -> bool:
        """True when ``offset`` is the first non-whitespace position of a
        piece that was joined at an element boundary (not a continuation
        of a cut element)."""
        for i, (s, e, *_rest) in enumerate(self._bounds):
            if s <= offset < e or (offset == e and s == e):
                if i and self.text[self._bounds[i - 1][1]:s] == "":
                    return False  # continuation join: same element
                return self.text[s:offset].strip() == ""
        return False

    def starts_adjacent_element(self, offset: int) -> bool:
        """Like ``starts_new_element``, but only for a piece that directly
        follows the previous member piece in upstream index order: no
        index gap and no skipped non-member chunk in between. Only such a
        boundary can split one sentence across two chunks."""
        if not self.starts_new_element(offset):
            return False
        for i, (s, e, *_rest) in enumerate(self._bounds):
            if s <= offset < e or (offset == e and s == e):
                return i > 0 and self._adjacent[i]
        return False

    def spans(self, start: int, end: int) -> list[dict]:
        """Map a joined-text range to one or more chunk evidence spans."""
        out = []
        for s, e, uid, text_len, page in self._bounds:
            a, b = max(start, s), min(end, e)
            if a < b:
                out.append(evidence_span(uid, a - s, b - s, page=page,
                                         text_len=text_len))
        if not out:
            raise MalformedRecordError(
                "joined-section span maps to no chunk", start=start, end=end)
        return out


def joined_section(chunks: list[dict], member) -> JoinedSection | None:
    """Join a document's chunks whose record satisfies ``member(rec)``,
    preserving upstream index order (verified contiguous 1..N per doc)."""
    pieces = []
    prev = None
    for rec in chunks:
        if not member(rec):
            continue
        # A cut element continues only into the ADJACENT chunk; a member
        # filter that skipped chunks in between breaks the continuation.
        adjacent = prev is not None and rec["index"] == prev["index"] + 1
        cont = adjacent and continues_into_next(prev)
        pieces.append((rec["chunk_uid"], rec["text"], rec.get("pages"), cont,
                       adjacent))
        prev = rec
    return JoinedSection(pieces) if pieces else None
