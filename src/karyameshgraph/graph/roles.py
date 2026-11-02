"""Deliverable 2 — Role nodes + RESPONSIBLE_FOR edges (SPEC.md §7.5).

Stateful parse in chunk-index order over RESPONSIBILITIES sections. A duty
whose role header sits in the same chunk is EXPLICIT; a duty attributed
via a header carried across a chunk boundary is INFERRED; the four merged
RESPONSIBILITIES-AND-PROCEDURES documents and fully footnote-contaminated
chunks yield no edges and an AMBIGUOUS exclusion record.

The section's lines are parsed as ONE ordered stream (chunks in index
order), so a role header's lookahead sees the first line of the next chunk:
a header that ends a chunk (real cases: CRS-SOP-033 i15, CRS-SOP-049 i16)
is recognized and its duties in the next chunk are attributed to it
(INFERRED), instead of the header being misread as prose and its duties
attaching to the previous role. The stream is split at any index gap, and
neither lookahead nor a carried role ever crosses a gap (fail closed).

Empirical grounding (discovery sweep 2026-11-01, counts in the M1.5
evidence report): upstream `review_flags` NEVER marks footnote
contamination, so it is detected from line patterns; family B uses eight
bullet glyphs, three of them private-use Symbol-font codepoints, all of
which must be recognized or hundreds of duty lines are lost; `index` is
contiguous 1..N for every document.

The graph stores role NAMES and evidence spans; duty sentences are never
copied into the graph — a duty is a span into verified chunk text.
"""

from __future__ import annotations

import hashlib
import re

from .common import (FAMILY_A, MERGED_RESP_TITLE, RESP_TITLES,
                     lines_with_offsets, slug, top_title)
from .models import (DERIVATION_EXPLICIT, DERIVATION_INFERRED, edge,
                     evidence_span, node)

# Line-level contamination (12 family-B docs; 0 false positives observed).
_FOOTNOTE = re.compile(r'^(?:\d{1,2}\s?(?=[A-Z“"(])|\d{1,2}\s*$)')
_HEADING_BLEED = re.compile(
    r"^(?:REFERENCES|BACKGROUND|DEFINITIONS|POLICY|PROCEDURES)\d*\s*$")
# Glued footnote superscripts on names ('OPQ Assessment Team10', 'Team:20').
_TRAILING_SUP = re.compile(r"(?<=[a-z):])\d{1,2}$")
# Same, also after a period ('...division.14'): used only to decide whether
# a line is a finished sentence, never to edit a role name.
_TRAILING_REF = re.compile(r"(?<=[a-z).:])\d{1,2}$")

# Family A: 'A. Laboratory Management:' / 'D. Division of Contracts (DFG)'
# — lettered, no terminal period (which excludes the fused role+duty
# sentences of CRS-SOP-015 by design).
_ROLE_A = re.compile(r"^[A-Z]\.\s+(?P<role>[A-Z].{0,78}?[^.\s])\s*:?$")
# Family A duty: numbered line.
_DUTY_A = re.compile(r"^\d{1,3}\.\s+")

# Family B bullet tiers. Round tier: U+2022 '•' and U+F0B7 (Symbol font).
# Dash tier: U+2012..U+2015 — duty lines in 033/046/047, one level below a
# round bullet. Sub tier: 'o ', U+F0A7, U+F09E, plain '-' — attached to the
# open duty. A round-bullet line followed by a dash- or sub-tier line is a
# bullet-tier ROLE (discovery r3, e.g. CRS-SOP-047's SRPM).
_B_ROUND_BULLET = re.compile("^[•]\\s*")
_B_DASH_BULLET = re.compile("^[‒–—―]\\s*")
_B_SUB_BULLET = re.compile("^(?:o\\s+|[]\\s*|-\\s+)")
_ANY_BULLET = re.compile(
    "^[•‒–—―]\\s*|^o\\s+|^-\\s+")

# Role-name character class, exactly the discovery-verified r1/r2/r3 class:
# NO '.', so sentence fragments and numbered lead-ins never become roles.
_NAME_CHARS = "[A-Za-z0-9&/,()'’ \\-]"
# Bulleted role: '• The RBPM will:' at any tier.
_ROLE_B_WILL = re.compile(
    "^(?:[•‒–—―]\\s*|o\\s+)?"
    f"(?P<role>(?:The\\s)?[A-Z]{_NAME_CHARS}{{2,90}}?)\\s+will:\\s*$")
# Header lines (bare / colon / '(or designee)'): must start with a letter.
_ROLE_B_BARE_OK = re.compile(f"^(?:The\\s)?[A-Z]{_NAME_CHARS}{{2,90}}$")
# Bullet-tier role body (r3): may start with a digit ('921 Posting Team').
_ROLE_B_BULLET_OK = re.compile(f"^(?:The\\s)?[A-Z0-9]{_NAME_CHARS}{{2,90}}$")
# 'Director, Office of Management (OM) (or designee)' — discovery r1 branch.
_OR_DESIGNEE = re.compile(r"\(or\s+designee(?:\(s\))?\)\s*:?$", re.I)
# First words that open conditional lead-ins, not role names.
_ROLE_BLOCK_FIRST = frozenset(
    {"for", "if", "when", "upon", "where", "responsibilities", "note"})


def _clean_role_name(name: str) -> str:
    n = _TRAILING_SUP.sub("", name.strip()).strip()
    n = re.sub(r"\s*,\s*if applicable\s*[,:]?\s*$", "", n, flags=re.I)
    n = n.rstrip(":").strip()
    if n.lower().endswith(" will"):
        n = n[: -len(" will")].rstrip()
    return n


def _ends_sentence(s: str) -> bool:
    return _TRAILING_REF.sub("", s).endswith(".")


def _classify_b(line: str, next_nonblank: str | None) -> tuple[str, str | None]:
    """→ (kind, role_name_or_None); kinds: footnote/bleed/role/duty/subduty/
    continuation/blank. ``next_nonblank`` is the next significant line of
    the same section stream (it may sit in the next chunk)."""
    s = line.strip()
    if not s:
        return "blank", None
    if _FOOTNOTE.match(s):
        return "footnote", None
    if _HEADING_BLEED.match(s):
        return "bleed", None
    m = _ROLE_B_WILL.match(s)
    if m:
        return "role", _clean_role_name(m.group("role"))
    nxt = next_nonblank.strip() if next_nonblank is not None else ""
    if _B_ROUND_BULLET.match(s):
        body = _B_ROUND_BULLET.sub("", s).strip()
        # '• OND Safety Regulatory Project Manager (SRPM)' followed by a
        # lower-tier (dash or sub) line is a bullet-tier ROLE, not a duty.
        if ((_B_SUB_BULLET.match(nxt) or _B_DASH_BULLET.match(nxt))
                and not _ends_sentence(body)
                and _ROLE_B_BULLET_OK.match(_TRAILING_SUP.sub("", body))):
            return "role", _clean_role_name(body)
        return "duty", None
    if _B_DASH_BULLET.match(s):
        return "duty", None
    if _B_SUB_BULLET.match(s):
        return "subduty", None
    # Bare role headers: colon-terminated, '(or designee)', or followed by
    # a bulleted line. 'The X will:' is handled above.
    cleaned = _TRAILING_SUP.sub("", s)
    first = re.split(r"[\s:]", cleaned, maxsplit=1)[0].lower()
    if (first not in _ROLE_BLOCK_FIRST and ";" not in cleaned
            and not _ends_sentence(s)):
        if cleaned.endswith(":") and _ROLE_B_BARE_OK.match(cleaned.rstrip(":").strip()):
            return "role", _clean_role_name(cleaned)
        if _OR_DESIGNEE.search(cleaned) and _ROLE_B_BARE_OK.match(cleaned.rstrip(":").strip()):
            return "role", _clean_role_name(cleaned)
        if nxt and _ANY_BULLET.match(nxt) and _ROLE_B_BARE_OK.match(cleaned):
            return "role", _clean_role_name(cleaned)
    return "continuation", None


def _classify_a(line: str) -> tuple[str, str | None]:
    s = line.strip()
    if not s:
        return "blank", None
    if _FOOTNOTE.match(s):
        return "footnote", None
    m = _ROLE_A.match(s)
    if m:
        return "role", _clean_role_name(m.group("role"))
    if _DUTY_A.match(s):
        return "duty", None
    return "continuation", None


def _section_stream(resp: list[dict], sid: str, stats: dict,
                    exclusions: list[dict]) -> list[tuple]:
    """Flatten a section's chunks into ordered line entries
    ``(chunk_uid, line, offset, text_len, page, segment)``. ``segment``
    increments at every index gap; fully footnote-contaminated chunks are
    dropped whole and recorded as AMBIGUOUS exclusions."""
    stream: list[tuple] = []
    segment = 0
    prev_index: int | None = None
    for rec in resp:
        if prev_index is not None and rec["index"] != prev_index + 1:
            segment += 1
        prev_index = rec["index"]
        uid, text = rec["chunk_uid"], rec["text"]
        pages = rec.get("pages")
        page = pages[0] if isinstance(pages, list) and pages else None
        lines = lines_with_offsets(text)
        nonblank = [ln.strip() for ln, _ in lines if ln.strip()]
        if nonblank and all(_FOOTNOTE.match(s) for s in nonblank):
            # 100%-footnote chunk (CRS-SOP-042 i14): drop whole chunk.
            stats["contaminated_chunks_excluded"] += 1
            exclusions.append({
                "stable_id": sid, "deliverable": "roles",
                "derivation": "AMBIGUOUS", "chunk_uid": uid,
                "reason": "fully_footnote_contaminated_chunk",
            })
            continue
        for line, off in lines:
            stream.append((uid, line, off, len(text), page, segment))
    return stream


def _lookahead(stream: list[tuple]) -> list[str | None]:
    """For each entry, the next significant (non-blank, non-footnote,
    non-bleed) line in the SAME segment, or None."""
    out: list[str | None] = [None] * len(stream)
    nxt: str | None = None
    nxt_seg: int | None = None
    for i in range(len(stream) - 1, -1, -1):
        seg = stream[i][5]
        out[i] = nxt if nxt_seg == seg else None
        s = stream[i][1].strip()
        if s and not _FOOTNOTE.match(s) and not _HEADING_BLEED.match(s):
            nxt, nxt_seg = s, seg
    return out


def build_roles(docs_ctx: dict[str, dict]) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    exclusions: list[dict] = []
    role_nodes: dict[str, dict] = {}
    stats = {
        "documents_total": len(docs_ctx),
        "documents_with_resp_section": 0,
        "documents_with_roles": 0,
        "merged_docs_excluded": [],
        "contaminated_chunks_excluded": 0,
        "footnote_lines_dropped": 0,
        "unattributed_duty_lines": 0,
        "duties_explicit": 0,
        "duties_inferred": 0,
        "carried_roles": 0,
    }

    for sid in sorted(docs_ctx):
        ctx = docs_ctx[sid]
        family, chunks = ctx["family"], ctx["chunks"]
        if family is None:
            continue
        # Merged RESPONSIBILITIES AND PROCEDURES: AMBIGUOUS by contract —
        # the source does not separate roles from procedure (SPEC.md §7.5).
        if any(top_title(rec) == MERGED_RESP_TITLE for rec in chunks):
            stats["merged_docs_excluded"].append(sid)
            exclusions.append({
                "stable_id": sid, "deliverable": "roles",
                "derivation": "AMBIGUOUS",
                "reason": "merged_responsibilities_and_procedures",
            })
            continue
        resp_title = RESP_TITLES[family]
        resp = [rec for rec in chunks if top_title(rec) == resp_title]
        if not resp:
            continue
        stats["documents_with_resp_section"] += 1

        stream = _section_stream(resp, sid, stats, exclusions)
        ahead = _lookahead(stream)
        current_role: dict | None = None   # {node_id, chunk_uid}
        open_duty: dict | None = None      # {role, spans:[[uid,s,e,page,len]]}
        doc_has_roles = False
        prev_segment: int | None = None
        carried: set[tuple[str, str]] = set()  # (role node, chunk) pairs counted

        def _note_carry(uid: str) -> None:
            key = (current_role["node_id"], uid)
            if current_role["chunk_uid"] != uid and key not in carried:
                carried.add(key)
                stats["carried_roles"] += 1

        def _finish_duty():
            nonlocal open_duty
            if open_duty is None:
                return
            role = open_duty["role"]
            spans = [evidence_span(u, s, e, page=p, text_len=ln)
                     for (u, s, e, p, ln) in open_duty["spans"]]
            first_uid = open_duty["spans"][0][0]
            if role["chunk_uid"] == first_uid:
                derivation = DERIVATION_EXPLICIT
                stats["duties_explicit"] += 1
            else:
                derivation = DERIVATION_INFERRED
                stats["duties_inferred"] += 1
            duty_key = hashlib.sha256(
                f"{first_uid}:{open_duty['spans'][0][1]}:{open_duty['spans'][-1][2]}"
                .encode("utf-8")).hexdigest()[:12]
            duty_id = f"{sid}/duty/{duty_key}"
            nodes.append(node("Duty", duty_id, document=sid,
                              role=role["node_id"]))
            edges.append(edge("RESPONSIBLE_FOR", role["node_id"], duty_id,
                              derivation=derivation, evidence=spans,
                              document=sid))
            open_duty = None

        for i, (uid, line, off, tlen, page, seg) in enumerate(stream):
            if prev_segment is not None and seg != prev_segment:
                # An index gap: never carry a role or a duty across it.
                _finish_duty()
                current_role = None
            prev_segment = seg
            if family == FAMILY_A:
                kind, role_name = _classify_a(line)
            else:
                kind, role_name = _classify_b(line, ahead[i])
            if kind in ("footnote", "bleed"):
                if kind == "footnote":
                    stats["footnote_lines_dropped"] += 1
                continue
            if kind == "blank":
                continue
            end = min(off + len(line), tlen)
            if kind == "role" and role_name:
                _finish_duty()
                role_id = f"{sid}/role/{slug(role_name)}"
                if role_id not in role_nodes:
                    role_nodes[role_id] = node(
                        "Role", role_id, document=sid, name=role_name)
                    nodes.append(role_nodes[role_id])
                    doc_has_roles = True
                current_role = {"node_id": role_id, "chunk_uid": uid}
                continue
            if kind == "duty":
                _finish_duty()
                if current_role is None:
                    stats["unattributed_duty_lines"] += 1
                    continue
                _note_carry(uid)
                open_duty = {"role": current_role,
                             "spans": [[uid, off, end, page, tlen]]}
                continue
            # subduty / continuation: extend the open duty (same chunk
            # extends in place; a new chunk adds a second span). A role
            # followed by plain prose instead of numbered/bulleted lines
            # (family A 'A. Requestor' style) opens one prose duty.
            if open_duty is not None:
                last = open_duty["spans"][-1]
                if last[0] == uid:
                    last[2] = max(last[2], end)
                else:
                    open_duty["spans"].append([uid, off, end, page, tlen])
                    _note_carry(uid)
            elif current_role is not None:
                _note_carry(uid)
                open_duty = {"role": current_role,
                             "spans": [[uid, off, end, page, tlen]]}
            else:
                stats["unattributed_duty_lines"] += 1
        _finish_duty()
        if doc_has_roles:
            stats["documents_with_roles"] += 1

    return {"nodes": nodes, "edges": edges, "stats": stats,
            "exclusions": {"roles": exclusions}}
