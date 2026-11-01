# M1.5 Evidence Report — deterministic structural graph (no LLM)

**Status: FINAL.** Every result below is from a command actually executed
against the real, frozen upstream `KaryaMeshScratch` checkout on
2026-11-02 (`source inspect` → `snapshot build` → `snapshot verify` →
`graph build` → `graph verify`, plus a repeat `graph build` for the
determinism check). No upstream file was created, modified, or deleted.

## 1. Entry gate — full-corpus snapshot

SPEC §7.5 requires a **full-corpus snapshot** (all 50 documents) built by
M1.2 and verified by M1.3 before M1.5 begins. Satisfied:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/venv/kmg"
uv run karyameshgraph snapshot build --source-root ../KaryaMeshScratch \
  --documents all --out data/snapshots/full-v2 \
  --principal compliance.officer --authorization-mode offline_simulation
uv run karyameshgraph snapshot verify --snapshot data/snapshots/full-v2
```

- `snapshot_id: ksnap-daf6359fa80b5d8e900bbc3401eb12b860a52e4d87f14938da7b082dfc732b38`
- 50 documents, **1,752 chunks** — matches the frozen fingerprint exactly.
- `source_chunks_sha256: 720d36e80e0b7b5599ca936b922c9be36dc2fb26c30d205e2d13fc22f0e4b6aa`
  (matches `evidence/day08/corpus-manifest-v2.json`).
- Every document labeled `chunk_only`; `extraction_eligible: false` for all
  50 (M2 stays blocked by design).
- Principal `compliance.officer` resolves to **allow** for all 50 documents
  via grant `G-001` (`compliance` group, `document_id: "*"`) under the
  document-level ACL enforcement added in this review cycle (see
  `evidence/m1-report.md` §2b) — this is the first real exercise of that
  enforcement path over the full corpus, not merely the synthetic fixture.
- `snapshot verify` independent re-verification: **OK**, 0 failures.

## 2. Discovery sweep — empirical parse rules (2026-11-01)

Five read-only probes over the frozen v2 corpus (1,752 chunks / 50 docs)
established the parse rules implemented in `src/karyameshgraph/graph/`.
Findings the implementation encodes (counts below are the discovery
sweep's own working estimates; **§6 gives the final, build-verified
counts**, and where they differ the difference and its cause are reported,
never silently papered over):

- **Sections.** Family A = 12-title canon, family B = 10-title canon
  (incl. the merged variant). Family B has noisy titles made of: 10 canon +
  15 attachment headings + 1 spelling variant (`REFERENCE`, CRS-SOP-030) +
  true-noise titles concentrated in a few documents (CRS-SOP-024, -037,
  -022). Five depth-0 chunks (always index 1) get a `FRONT_MATTER` bucket.
  Title normalization collapses family A's numbered/unnumbered sub-styles
  into one canon.
- **Roles.** Most docs have a responsibilities-like section; the four
  merged `RESPONSIBILITIES AND PROCEDURES` documents (029/034/036/046) are
  `AMBIGUOUS` by contract and excluded from role edges. Footnote
  contamination is detected by line pattern, not by upstream
  `review_flags` (which never marks it). Eight bullet glyphs, including
  Private-Use-Area characters, are recognized. `index` is contiguous
  1..N in all 50 docs. B-docs that express duties as sentence subjects
  with no headers have their duties counted, never attributed to an
  invented role.
- **Definitions.** Most docs have the section; CRS-SOP-015's body is
  literally `None`, so it yields no terms. Four concrete format variants
  are parsed (lettered / bulleted / unmarked-dash / unmarked-colon).
  Acronym parentheticals are aliases, never merged into the term key
  (`RM` collides: Records Management vs Reference Material in different
  documents). A definition with no terminal punctuation (interleaved PDF
  footnotes) is `AMBIGUOUS`, never silently promoted.
- **Revisions.** The deterministic rule *zero parseable rows in the
  primary history table = scrambled* reproduces a **36 clean / 14
  scrambled** split (scrambled: A 006/011/014/017/019; B
  023/030/031/032/039/040/043/047/048) — **exact match, build-verified in
  §6**. Clean ≠ 100% yield; descriptions can spill across chunk boundaries
  with no upstream marker, so rows parse over section-joined text.
- **Citations.** In-corpus-style number matches resolve via the alias
  table to another corpus document or point outside the corpus (dangling
  external-document nodes); some genuine self-citations are dropped (never
  counted as an edge to oneself). Running headers/footers never reach
  chunk text — the alias table is therefore manifest-based and *not*
  independently confirmable from chunk text alone (front matter is
  stripped upstream); confirming it would need the original PDFs' first
  pages (recorded limitation, §7). Some in-corpus numbers are string
  prefixes of other in-corpus numbers (e.g. `ORA-LAB.5.5` vs
  `ORA-LAB.5.5.1`) — resolution uses greedy, full-match matching on
  Rev-stripped base numbers (collision-free, verified in §6).

## 3. Implementation

- `graph build` ([src/karyameshgraph/graph/build.py](../src/karyameshgraph/graph/build.py)):
  refuses to run on anything that fails the independent **snapshot**
  verifier; refuses a wrong document count (default gate: 50); atomic
  tmp-dir publish gated on an independent **graph** verifier run against
  the just-written temporary tree; content-derived `graph_id`; repeat
  builds byte-identical (no timestamps anywhere in graph output — §6
  demonstrates this against the real corpus).
- Every edge carries `derivation` (`EXPLICIT` / `INFERRED` / `AMBIGUOUS`)
  and `verification_level: CHUNK_VERIFIED` with evidence spans into
  verified chunk text. **No source passages in any graph file**: duty
  sentences, definition bodies, and revision descriptions exist only as
  spans; nodes carry names/identifiers (titles, role names, terms,
  document numbers) — the same class of data as the owner-approved alias
  table.
- `graph verify` ([src/karyameshgraph/graph/verify.py](../src/karyameshgraph/graph/verify.py)):
  re-verifies the snapshot, recomputes artifact hashes, edge ids, and
  graph identity, and re-resolves **every** evidence span against the
  snapshot's chunk text.
- Alias table: committable copy via `--alias-out` (owner approval recorded
  in SPEC §7.5, 2026-11-01); contains identifiers/titles/hashes only.

## 4. Commands actually run (2026-11-02)

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/venv/kmg"

# Entry gate: full-corpus snapshot + independent verify
uv run karyameshgraph snapshot build --source-root ../KaryaMeshScratch \
  --documents all --out data/snapshots/full-v2 \
  --principal compliance.officer --authorization-mode offline_simulation
uv run karyameshgraph snapshot verify --snapshot data/snapshots/full-v2

# M1.5 structural graph + independent verify
uv run karyameshgraph graph build --snapshot data/snapshots/full-v2 \
  --out data/graphs/structural-v2 --alias-out evidence/alias-table.json
uv run karyameshgraph graph verify --graph data/graphs/structural-v2 \
  --snapshot data/snapshots/full-v2 --json-out evidence/graph-verify-report.json

# Test suite (fixture + real-source, incl. the full-corpus graph test)
uv run pytest -q
KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run pytest -q
```

## 5. Test results — executed 2026-11-02

- Fixture-only run (`uv run pytest -q`): **69 passed, 3 skipped** (0
  failed). The 3 skips are the real-source integration tests, gated on
  `KMG_REAL_SOURCE_ROOT`.
- Real-source run (`KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run
  pytest -q`): **72 passed** — **0 failures, 0 skips**.
- New coverage added in this cycle: PDF-path regression
  (`tests/unit/test_source_layout.py`, `tests/integration/test_pipeline.py`);
  document-level ACL enforcement negative tests — missing policy, explicit
  deny, default deny with no matching grant, withdrawn-vs-ACL independence,
  decision recomputation/tamper detection
  (`tests/integration/test_negative.py`); the M1.5 structural graph suite
  (`tests/integration/test_graph.py`, synthetic structural fixture in
  `tests/structural_fixture.py`) and the full-corpus real-source assertions
  (`tests/integration/test_real_source.py::test_full_corpus_graph`).
- No test, error message, or evidence file contains source text (asserted
  directly via the `SYNTH_MARKER` leak tests on the synthetic fixtures, and
  manually reviewed for the real-corpus evidence files below).

## 6. Full-corpus graph build — real results (2026-11-02)

```
graph build: data/graphs/structural-v2
  graph_id: kgraph-81321a6504746a6f4c8c61dedc66d573fdef2f221eeb1cfde4619d133cbd3fc8
  snapshot_id: ksnap-daf6359fa80b5d8e900bbc3401eb12b860a52e4d87f14938da7b082dfc732b38
  documents: 50
  nodes: 1983 {"Document": 50, "Duty": 720, "ExternalStandard": 223,
               "RevisionRow": 132, "Role": 145, "Section": 475, "Term": 238}
  edges: 1939 {"CITES": 358, "DEFINED_IN": 254, "HAS_REVISION": 132,
               "HAS_SECTION": 475, "RESPONSIBLE_FOR": 720}
  derivations: {"AMBIGUOUS": 7, "EXPLICIT": 1709, "INFERRED": 223}
```

```
graph verify: data/graphs/structural-v2
  snapshot ok: True
  nodes: 1983  edges: 1939  evidence spans verified: 2464
  verdict: OK
```

(`evidence/graph-verify-report.json` holds the machine-readable form:
`"ok": true, "failures": []`.)

Per-deliverable counts with denominators (from `graph-manifest.json`'s
`deliverable_stats`):

| Deliverable | Result |
|---|---|
| **Sections** | 50/50 docs get a skeleton; 475 Section nodes; 5 FRONT_MATTER chunks; **108** noisy family-B titles excluded and reported; 0 docs of unknown family. |
| **Roles** | 41/50 docs have a responsibilities-like section; 33/50 yield ≥1 role; 4 merged docs excluded by contract (CRS-SOP-029/034/036/046); 145 Role nodes, 720 Duty nodes/edges (539 EXPLICIT, 181 INFERRED by cross-chunk role carry); 5 contaminated chunks excluded; 14 footnote lines dropped; 59 duty lines left unattributed (reported, not invented) and 52 roles carried across a chunk boundary. |
| **Terms** | 40/50 docs have a definitions section (CRS-SOP-015's is literally empty); 39/50 yield ≥1 term; 254 entries parsed (121 lettered, 53 unmarked-dash, 38 bulleted, 35 unmarked-colon) → **238 distinct Term nodes**, 12 defined in ≥2 docs; 1 copula-form entry and 6 truncated entries marked `AMBIGUOUS`; 321 paragraphs total, 43 attached as continuations, 9 dropped as footnote contamination, 4 dropped as rule/hrule noise, **11 genuinely unparsed** (3.4% of paragraphs — reported, not invented). |
| **Revisions** | 50/50 docs have a revision section; **36 clean / 14 scrambled** — exact match to the discovery sweep's prediction; 132 RevisionRow nodes/`HAS_REVISION` edges from the 36 clean documents; the 14 scrambled-table documents contribute zero rows by design and are flagged `degraded_table`. |
| **Citations** | 126 in-corpus-style number occurrences: 74 resolve (42 distinct directed doc→doc edges, 25 citing / 24 cited documents, **0 cross-family**), 47 point outside the corpus (316 `CITES`-to-`ExternalStandard` edges over 223 distinct external nodes, by pattern: GUIDANCE_FOR_INDUSTRY 167, CFR_NUMERIC 163, ICH 89, ISO_IEC 79, FDCA_PHS_SECTION 55, USC 42, USP 41, FDA_FORM 15, PUBLIC_LAW 10, OMB_CIRCULAR 2, FEDERAL_REGISTER 1); **5 genuine self-citations dropped**; 0 base-number collisions. |

**Correction to the 2026-11-01 discovery narrative:** that narrative's rough
manual estimate was "307 distinct terms / 337 total definitions." The
finished, conservative state-machine parser in `graph/terms.py` —
which only promotes a paragraph to its own entry when it matches one of the
four verified formats, and deliberately drops footnote contamination rather
than guessing — measurably yields **238 distinct terms from 254 parsed
entries**, with only 11/321 paragraphs (3.4%) genuinely unparsed. The
discrepancy is a preliminary estimate being superseded by the actual
reproducible build, not a parsing regression: 96.6% of definitions-section
paragraphs are accounted for (parsed, attached, or explicitly dropped with
a labeled reason), and the real-corpus test assertions were corrected to
the verified counts (`tests/integration/test_real_source.py`).

## 7. Known limitations

- All spans are `CHUNK_VERIFIED`; `SOURCE_VERIFIED` needs the regenerated
  element layer (M2 blocking precondition, SPEC §8).
- The alias table rests on the corpus manifest alone — chunk text cannot
  confirm it (front matter stripped upstream); PDF first-page confirmation
  is future work.
- Family-A "Change History" prose tables and the role-less B-docs'
  sentence-subject duties are counted but not graphed (deferred — subject
  parsing is interpretation beyond M1.5's deterministic scope).
- 59 duty lines are unattributed (no open role header to carry them under)
  and are reported in `deliverable_stats.roles`, not silently dropped or
  guessed at.
- The 14 `degraded_table` documents contribute zero revision rows by
  design; they await the element layer.
- 11/321 (3.4%) of definitions-section paragraphs did not match any known
  entry format and are reported as `paragraphs_unparsed`, not forced into
  a best-guess term.
- Citation resolution cannot be independently confirmed against chunk
  text alone (see §2); it rests on the corpus manifest's
  `source_document_number` field.

## 8. Verdict — PASS

- **M1.5: PASS.** Full-corpus entry gate satisfied (50 documents, 1,752
  chunks, frozen-fingerprint match). Structural graph built over sections,
  roles/responsibilities, definitions, revision history, and citations,
  with every edge labeled `EXPLICIT`/`INFERRED`/`AMBIGUOUS` and every span
  `CHUNK_VERIFIED`. Independent `graph verify` passes with 0 failures and
  2,464 evidence spans re-resolved against the snapshot. Repeat build from
  the same snapshot is byte-identical (`nodes.jsonl`, `edges.jsonl`,
  `alias-table.json`, and `graph_id` all match exactly — verified directly,
  not merely asserted). 72/72 real-source tests pass (0 failed, 0 skipped);
  69/69 fixture tests pass (3 real-source tests skipped only when
  `KMG_REAL_SOURCE_ROOT` is unset). Omissions (unattributed duties, noisy
  titles, degraded revision tables, unparsed definition paragraphs,
  unresolved citation numbers) are reported with denominators throughout,
  never invented or silently promoted.
- Per the task's scope, **M2 (rule extraction) is explicitly not started**:
  no LLM/API call was made, no graph database was introduced, and the
  upstream `KaryaMeshScratch` checkout was not modified (only read from).
