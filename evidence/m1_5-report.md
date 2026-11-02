# M1.5 Evidence Report — deterministic structural graph (no LLM)

**Status: FINAL — PASS (re-run by the owner on 2026-11-01 after the review
fixes; output in §5 and §6a).**
The first full-corpus build (graph `kgraph-81321a65…`) passed every check
that existed at the time. A five-angle adversarial review of the M1.5 code
then confirmed 22 defects, plus one the reviewers flagged but could not
verify. They include a parser bug that silently dropped 25% of definitions
and verifier gaps that would have let a swapped alias table or a
`SOURCE_VERIFIED` claim pass. A second, three-angle verification of the
fixes (test trace, closure, regressions) found no critical or major
problems; its 12 minor issues are also fixed, including two term
normalization bugs it surfaced. Everything is covered by new tests (§9).
Results that came from the pre-fix build are kept below and marked
**superseded**. All work in this report ran on 2026-11-01
(America/Los_Angeles); no upstream file was created, modified, or deleted.

## 1. Entry gate — full-corpus snapshot

SPEC §7.5 requires a **full-corpus snapshot** (all 50 documents) built by
M1.2 and verified by M1.3 before M1.5 begins. Satisfied:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/venv/kmg"
uv run karyameshgraph snapshot build --source-root ../KaryaMeshScratch \
  --documents all --out data/snapshots/full-v2 \
  --principal compliance.officer --authorization-mode offline_simulation \
  --document-mode historical_archive --archive-mode-authorized
uv run karyameshgraph snapshot verify --snapshot data/snapshots/full-v2
```

- `snapshot_id: ksnap-daf6359fa80b5d8e900bbc3401eb12b860a52e4d87f14938da7b082dfc732b38`
- 50 documents, **1,752 chunks** — matches the frozen fingerprint exactly.
- `source_chunks_sha256: 720d36e80e0b7b5599ca936b922c9be36dc2fb26c30d205e2d13fc22f0e4b6aa`
  (matches `evidence/day08/corpus-manifest-v2.json`).
- `document_mode: historical_archive` with `archive_mode_authorized: true`:
  the structural graph deliberately covers every document, including
  CRS-SOP-050, which is WITHDRAWN upstream (see `evidence/m1-report.md`
  §2c). A default (`current`) build refuses that document.
- Every document labeled `chunk_only`; `extraction_eligible: false` for all
  50 (M2 stays blocked by design).
- Principal `compliance.officer` resolves to **allow** for all 50 documents
  via grant `G-001` under the document-level ACL enforcement
  (`evidence/m1-report.md` §2b).
- `snapshot verify`: **OK**, 0 failures.

The review fixes touched only graph code, so this snapshot stays valid and
is reused as is; only the graph must be rebuilt.

## 2. Discovery sweep — empirical parse rules (2026-11-01)

Five read-only probes over the frozen v2 corpus (1,752 chunks / 50 docs)
established the parse rules implemented in `src/karyameshgraph/graph/`:

- **Sections.** Family A = 12-title canon, family B = 10-title canon
  (incl. the merged variant). Non-canon titles are attachment headings,
  one spelling variant (`REFERENCE`, CRS-SOP-030) and extraction noise
  concentrated in a few documents (CRS-SOP-024, -037, -022). Five depth-0
  chunks (always index 1) get a `FRONT_MATTER` bucket.
- **Roles.** The four merged `RESPONSIBILITIES AND PROCEDURES` documents
  (029/034/036/046) are `AMBIGUOUS` by contract and get no role edges.
  Footnote contamination is detected by line pattern; upstream
  `review_flags` never marks it. Eight bullet glyphs, including three
  Private-Use-Area characters. `index` is contiguous 1..N in all 50 docs.
  Role headers can end a chunk with all their duties in the next one.
  Eight family-B documents state duties as sentence subjects with no
  headers; those duties are counted, never attributed to an invented role.
- **Definitions.** 40 documents have the section; CRS-SOP-015's body is
  literally `None`. Four format variants (lettered / bulleted /
  unmarked-dash / unmarked-colon). The sweep's ad hoc count was **337
  definitions, 307 distinct terms, 25 defined in ≥2 documents**. The fixed
  build reconciles with it exactly (§6a) and matches the multi-document
  term list term for term. Acronym parentheticals are aliases, never
  merged into the term key.
- **Revisions.** *Zero parseable rows in the primary history table =
  scrambled* reproduces a **36 clean / 14 scrambled** split exactly.
- **Citations.** 126 in-corpus-style numbers: 74 resolve to another corpus
  document (42 distinct edges, 0 cross-family), 47 point outside the
  corpus, 5 are genuine self-citations. Running headers and footers never
  reach chunk text, so the alias table is manifest-based and cannot be
  confirmed from chunk text alone (§7).

## 3. Implementation

- `graph build` ([build.py](../src/karyameshgraph/graph/build.py)):
  - Refuses a snapshot that fails independent verification, or one with
    the wrong document count (default gate: 50).
  - Publishes atomically, only after the graph verifier passes on the
    temporary tree.
  - Refuses to start over an orphaned temporary tree, and removes a stale
    `.failed.json` after a success.
  - `--alias-out` refreshes a previous alias table in place and refuses to
    overwrite any other file.
- `graph_id` = SHA-256 of the canonical manifest, which pins every
  artifact's hash (nodes, edges, alias table, exclusions), the snapshot id,
  versions and all summary stats. No timestamps or machine paths are
  stored, so repeat builds are byte-identical however the snapshot path is
  spelled.
- Every edge carries `derivation` (`EXPLICIT` / `INFERRED` / `AMBIGUOUS`)
  and `verification_level: CHUNK_VERIFIED`, with evidence spans into
  verified chunk text.
- **No source passages in any graph file.** Duties, definition bodies and
  revision descriptions exist only as spans. Nodes carry names and
  identifiers. Excluded noisy titles are recorded by hash and chunk
  location, not text.
- `graph verify` ([verify.py](../src/karyameshgraph/graph/verify.py))
  runs two layers:
  1. **Independent checks.**
     - Re-verifies the snapshot and recomputes artifact hashes and edge ids.
     - Accepts only `CHUNK_VERIFIED`.
     - Re-resolves **every** evidence span; each must stay inside its own
       document and on the chunk's pages.
     - Recomputes the manifest's counts, histograms and document set, and
       recomputes `graph_id`.
  2. **Reproduction.** Recomputes the graph from the snapshot and requires
     a byte-for-byte match on all four artifacts and the manifest. This
     catches tampering that has been fully rehashed to look internally
     consistent.

## 4. Commands run (post-fix, 2026-11-01)

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/venv/kmg"

# The snapshot from §1 is still valid; the old graph (builder
# m1.5-structural-1) now fails verify by design and must be rebuilt.
rm -rf data/graphs/structural-v2
uv run karyameshgraph graph build --snapshot data/snapshots/full-v2 \
  --out data/graphs/structural-v2 --alias-out evidence/alias-table.json
uv run karyameshgraph graph verify --graph data/graphs/structural-v2 \
  --snapshot data/snapshots/full-v2 --json-out evidence/graph-verify-report.json

uv run pytest -q
KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run pytest -q
```

## 5. Test results

- **Post-fix (owner's run, 2026-11-01):**
  - Fixture-only (`uv run pytest -q`): **158 passed, 4 skipped**, 0 failed.
    The 4 skips are the real-source tests, gated on `KMG_REAL_SOURCE_ROOT`.
  - Real source (`KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run pytest
    -q`): **162 passed**, 0 failed, 0 skipped. This includes the pinned
    real-corpus counts and the reproduction check.
- **After the third-round fixes (owner's run, 2026-11-01):** **165 passed,
  4 skipped** fixture-only; **169 passed** with the real source; 0 failed.
  These include the 7 new latent-case tests (§9). The graph was not
  rebuilt: its output is byte-identical (§9, third round).
- Pre-fix (superseded): 69 passed / 3 skipped fixture-only; 72 passed with
  `KMG_REAL_SOURCE_ROOT`. Those runs asserted the buggy definition counts
  (≥230 terms, ≥10 multi-doc terms), so they could not catch the parser
  bug.
- Added in the review fixes: `tests/unit/test_graph_parsers.py` (chunk
  joining, role classifier and state machine, definition lead-ins and
  cross-chunk continuations, term normalization) and regression and tamper
  cases in `tests/integration/test_graph.py`. The structural fixture gained
  a definition in its own chunk, a role header that ends a chunk, and a
  list lead-in. The real-corpus test now pins 339 definitions, 312 terms
  and 25 multi-document terms, and requires the graph to reproduce from its
  snapshot.

## 6. Full-corpus graph build

### 6a. Post-fix — owner's CLI run (2026-11-01)

```
graph build: data/graphs/structural-v2
  graph_id: kgraph-76a1a3a7c843ece4c237964b4b16f833655158c186e71994b9ae9b7512087623
  snapshot_id: ksnap-daf6359fa80b5d8e900bbc3401eb12b860a52e4d87f14938da7b082dfc732b38
  documents: 50
  nodes: 2050 {"Document": 50, "Duty": 715, "ExternalStandard": 223,
               "RevisionRow": 132, "Role": 143, "Section": 475, "Term": 312}
  edges: 2019 {"CITES": 358, "DEFINED_IN": 339, "HAS_REVISION": 132,
               "HAS_SECTION": 475, "RESPONSIBLE_FOR": 715}
  derivations: {"AMBIGUOUS": 9, "EXPLICIT": 1788, "INFERRED": 222}
  alias table written: evidence/alias-table.json
```

```
graph verify: data/graphs/structural-v2
  graph_id: kgraph-76a1a3a7c843ece4c237964b4b16f833655158c186e71994b9ae9b7512087623
  snapshot ok: True
  nodes: 2050  edges: 2019  evidence spans verified: 2466
  reproduced from snapshot: True
  verdict: OK
```

The CLI `graph_id` equals the id measured in-process while fixing
(`compute_graph` over the same snapshot). `evidence/graph-verify-report.json`
records `"ok": true, "reproduced": true, "failures": []`. The refreshed
`evidence/alias-table.json` is byte-identical to the previously committed
copy.

| Deliverable | Post-fix result |
|---|---|
| **Sections** | Unchanged: 50/50 documents get a skeleton; 475 Section nodes; 5 FRONT_MATTER chunks; 108 noisy titles excluded (by hash and location). |
| **Roles** | 41 documents with a responsibilities section; 32 yield ≥1 role; 4 merged documents excluded; 143 Role nodes; 715 duties (535 EXPLICIT, 180 INFERRED); 51 role carries across chunk boundaries; 66 duty lines unattributed. |
| **Terms** | 339 definitions (EXPLICIT: 149 lettered, 70 unmarked-dash, 59 unmarked-colon, 52 bulleted; AMBIGUOUS: 8 truncated, 1 copula-form) → **312 distinct terms, 25 in ≥2 documents** (identical to the discovery list); 418 paragraphs, 47 attached as continuations (1 across a chunk boundary), 10 unparsed. |
| **Revisions** | Unchanged: 36 clean / 14 scrambled; 132 rows from the clean documents. |
| **Citations** | Unchanged: 42 resolved document→document edges (0 cross-family), 316 edges to 223 external nodes, 5 self-citations dropped, 0 base-number collisions. |

What changed versus the pre-fix build, and why:

- **Terms +74, none lost.**
  - +71 from the join fix. Each section's chunks were joined with a single
    newline, but paragraphs split on blank lines, so the first definition
    of every new chunk fused into the previous one. All 71 recovered terms
    were checked for sentence-like or verb-bearing names (none found).
  - +3 from normalization. Trailing digits were stripped as if they were
    footnote superscripts. That merged `Level 1`–`Level 4` (CRS-SOP-027)
    into one term "defined" four times, and mangled `ISO 17034` into
    `iso 170`. Only digits glued to a lowercase word are stripped now; no
    real term carries such a superscript.
- **Reconciliation with the sweep (337 / 307).** Per format the build
  equals the sweep for lettered (152), unmarked-dash (70) and
  unmarked-colon (60). Bulleted is 56 against 55, in the same ten
  documents; the sweep kept no per-document counts, so the extra entry
  cannot be named. The 1 copula-form entry (CRS-SOP-007) was listed by the
  sweep as an ambiguity case outside its 337. That gives 339. Distinct
  terms: 307 + those 2 + the 3 Level terms the sweep's own rule also
  merged = 312.
- **AMBIGUOUS 11 → 9.** One definition split across a chunk boundary into
  two paragraph elements, with no cut marker, now continues into the next
  chunk. One definition ending in a footnote number glued after `)` is no
  longer misread as truncated.
- **Roles −3 junk, +1 real.**
  - Removed: a numbered lead-in sentence in CRS-SOP-030 (a document the
    sweep classified as having no role headers) and two wrapped duty
    fragments in CRS-SOP-047, whose duties now go to the correct role.
  - Added: a group header that ends a chunk in CRS-SOP-033 (the sweep's
    cross-chunk case). It heads sub-roles, so it has no direct duties.
  - The 7 extra unattributed duty lines are the duties of the removed
    CRS-SOP-030 lead-in, now honestly unattributed.

### 6b. Pre-fix build (superseded)

Graph `kgraph-81321a6504746a6f4c8c61dedc66d573fdef2f221eeb1cfde4619d133cbd3fc8`:
1,983 nodes, 1,939 edges, 2,464 spans verified, repeat build
byte-identical. The sections, revisions and citations figures hold. The
terms (254 / 238 / 12) and roles (145 / 720) figures are wrong for the
reasons above.

**Retraction.** An earlier version of this report said the sweep's
"307 distinct terms / 337 definitions" was a rough estimate superseded by
the parser's 254 / 238. That was backwards: the sweep was close (it
reconciles exactly, §6a), and the parser was dropping a quarter of the
definitions. The real-corpus test threshold had been lowered to match the
bug. It now pins the build's counts, with the reconciliation recorded next
to the pins.

## 7. Known limitations

- All spans are `CHUNK_VERIFIED`; `SOURCE_VERIFIED` needs the regenerated
  element layer (M2 blocking precondition, SPEC §8).
- The alias table rests on the corpus manifest alone. Chunk text cannot
  confirm it (front matter is stripped upstream); confirming it against
  the PDFs' first pages is future work.
- Family-A "Change History" prose tables and the role-less family-B
  documents' sentence-subject duties are counted but not graphed:
  attributing them would need subject parsing, which is interpretation
  beyond M1.5's deterministic scope.
- 66 duty lines are unattributed (no role header to carry them under).
  They are reported in `deliverable_stats.roles`, not guessed at.
- The 14 `degraded_table` documents contribute zero revision rows by
  design; they await the element layer.
- 10 of 418 definitions-section paragraphs match no known entry format and
  are reported as unparsed.
- The list lead-in guard (`... include:`) found 0 cases in the real corpus.
  It is defensive and fixture-tested. Its word list is narrow (`include`,
  `following`, `as follows`); forms like `such as:` are not covered.
- A definition that continues across a chunk boundary is joined only when
  it is unterminated and the next chunk's first paragraph matches no entry
  format (1 real case). The same split inside one chunk still leaves the
  entry AMBIGUOUS, which fails safe.

## 8. Verdict — PASS

All three re-run criteria hold, from the owner's actual output:

1. Both test runs are green: 158 passed / 4 skipped fixture-only; 162 passed
   with the real source.
2. `graph build` printed `graph_id: kgraph-76a1a3a7…`, exactly the
   in-process measurement.
3. `graph verify` reported `verdict: OK` with `reproduced from snapshot:
   True` and 2,466 evidence spans re-resolved.

The full-corpus entry gate is satisfied, every edge carries a derivation
label and `CHUNK_VERIFIED` spans, and omissions are reported with
denominators. M2 (rule extraction) is still not started: no LLM or API
call, no graph database, and no upstream changes.

## 9. Review fixes (2026-11-01)

The five-angle review (crash paths, fail-closed, test trace, determinism,
regex fidelity) confirmed 22 findings; each was checked by an independent
skeptic. Duplicates are merged below.

| Severity | Finding | Fix | Covered by |
|---|---|---|---|
| critical | Chunks joined with one newline; 85 of 339 real definitions lost | Join at upstream's element separator; single newline only when a cut element continues into the adjacent chunk | `test_definitions_in_separate_chunks_both_parse`, `test_terms_multi_doc_linked_never_merged`, real counts pinned |
| critical | `graph_id` did not bind `alias-table.json` / `exclusions.json`; a swapped alias table verified with the same id | `graph_id` = hash of the full manifest (pins every artifact) + reproduction check | `test_alias_swap_changes_graph_id_and_fails_verify` |
| major | Verifier never checked `verification_level`; a `SOURCE_VERIFIED` claim passed | Only `CHUNK_VERIFIED` accepted; manifest ceiling checked; spans must stay in their own document and on the chunk's pages | `test_verify_rejects_promoted_verification_level`, `test_verify_rejects_cross_document_span` |
| major | Manifest stats (histograms, counts, document set, gaps) trusted, not recomputed | Recomputed from records and snapshot; everything else covered by byte-for-byte reproduction | `test_verify_catches_tampered_edges` |
| major | A role header ending a chunk was missed; its duties went to the previous role | Section-wide line stream with cross-chunk lookahead (never across an index gap) | `test_header_ending_a_chunk_keeps_its_duties`, unit state-machine tests |
| major | Bare-role pattern admitted `.` and digit starts, minting junk roles (3 in the real corpus) | Discovery-verified name class; header lines must start with a letter | `test_classify_b` cases |
| major | `section_path_titles` shape unvalidated; a string value silently emptied the graph | Shapes validated at load; typed `MalformedRecordError` | `test_malformed_section_titles_fail_typed` |
| minor | Colon list lead-ins (`... include:`) minted terms (flagged, not verified, in the review) | Lead-in attaches to the open definition | `test_list_leadin_is_not_a_term`, unit test |
| minor | Verifier crashed on non-UTF-8 graph files | Per-line decode, reported as a failure | `test_verify_reports_non_utf8_artifact` |
| minor | Unused corpus-manifest load crashed on malformed entries | Removed | — |
| minor | Index-gap enumeration unbounded | Bounded; implausible spans fail typed | — |
| minor | `snapshot_path` in the manifest broke byte-identity across path spellings | Removed from the manifest | `test_repeat_build_identical_across_path_spellings`, `test_manifest_has_no_machine_paths` |
| minor | `--alias-out` silently overwrote any file | Refreshes only a previous alias table; atomic write | `test_alias_out_refuses_foreign_file_refreshes_alias` |
| minor | A stale `.failed.json` survived a success; an orphaned temp tree was ignored | Removed on success / refused with its path | `test_stale_failure_report_removed_on_success`, `test_orphaned_tmp_tree_refused` |
| minor | Raw noisy-title text (often sentence fragments) stored in `exclusions.json` | Title hash + chunk locations | `test_noisy_title_excluded_without_text` |
| minor | `re.split` positional `maxsplit` deprecation | Fixed by the owner in `163a275` | — |

**Second round — verification of the fixes.** Three agents traced every
test, checked each finding's closure, and hunted regressions; each issue
was skeptic-verified. The test trace predicted no failing assertion. 18
findings were confirmed closed, 3 partially closed, and 1 closed with a
side effect. The 12 confirmed issues (all minor) are fixed:

| Finding | Fix | Covered by |
|---|---|---|
| Verifier still crashed on non-string ids or endpoints, or deeply nested JSON | Endpoints type-checked as malformed records; RecursionError reported as invalid JSON; every edge must resolve to a snapshot document | `test_verify_reports_non_string_ids` |
| A missing or renamed `section_path_titles` still produced a silent near-empty graph | Key required on every chunk (an empty list stays legal); a snapshot with no titled chunk fails typed | `test_malformed_section_titles_fail_typed[missing]` |
| Continuation join inserted a `\n` not in the source | Cut-element parts join directly (contiguous on all 26 real cuts) | `test_cut_element_parts_join_directly` |
| Blank-line join demoted one real definition split across chunks to AMBIGUOUS | An unterminated entry absorbs the next chunk's first paragraph when it matches no entry format | `test_unterminated_definition_continues_into_next_chunk` |
| Footnote ref glued after `)` read as truncation | Strip refs after a closing bracket or quote too | `test_glued_footnote_after_bracket_is_not_truncation` |
| Real-corpus pins (339/309) cited the sweep as confirmation without explaining the gap | Gap reconciled per format; the investigation exposed the trailing-digit bug (`Level 1`–`4` merged, `ISO 17034` mangled), now fixed (312 terms) | `test_normalize_term`, real pins |
| `--alias-out` could overwrite a published graph's pinned `alias-table.json`, or land inside this build's own output | Both refused before any output exists; ancestor-inspection errors fail closed; a post-publish write failure is a typed error | `test_alias_out_refuses_paths_inside_graph_trees` |
| Orphan check used the output name as a glob pattern | Literal prefix match | `test_orphan_check_matches_names_literally` |

**Third round — verification of the round-2 fixes.** Two agents re-traced
every graph test and hunted regressions, and each issue was
skeptic-verified. No assertion was predicted to fail. 10 issues were
confirmed, all low severity and all latent: none changes any output on
the frozen corpus. The rebuilt graph is byte-identical to the §6a build,
same `graph_id`, so the evidence above stands and no rebuild is needed.

| Finding | Fix | Covered by |
|---|---|---|
| Cross-chunk definition continuation could jump an index gap or a skipped chunk, or swallow a group header, promoting a truncated entry to EXPLICIT (3 findings) | Continuation requires the index-adjacent chunk; the group-header test runs first | `test_continuation_requires_index_adjacent_chunk`, `test_group_header_opening_a_chunk_is_not_absorbed` |
| `--alias-out` inside the input snapshot (or any snapshot tree) was accepted, then invalidated the snapshot (2 findings) | Refused before any output exists, like graph trees | `test_alias_out_refuses_snapshot_trees` |
| A case-variant spelling of the output path passed the own-output check on case-insensitive APFS | Casefolded containment check, plus a re-check after publish | `test_alias_out_refuses_paths_inside_graph_trees` |
| Manifest parse still crashed on oversized ints or deep nesting (2 findings) | Reported as invalid JSON, as for the JSONL files | `test_verify_reports_malformed_manifest` |
| The "cannot inspect ancestors → refuse" branch was dead code: `Path.is_file()` swallows permission errors on Python 3.14 (2 findings) | `os.stat`-based check; any error other than not-found refuses | `test_alias_out_unreadable_ancestor_fails_closed` |

The builder stays `m1.5-structural-2`, because these fixes change no
output on the reference corpus. Any other input is still covered by the
byte-for-byte reproduction check.

Schema `2.0.0` / builder `m1.5-structural-2` mark the change; graphs from
the earlier builder fail `graph verify` by design and must be rebuilt.
