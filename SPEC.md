# KaryaMeshGraph — Business-Rule Knowledge Graph from KaryaMesh SOPs

**Status:** Implementation specification (v0.4 — v0.3 + four review clarifications: provenance levels, M1.5 derivation labels, M1.5 full-corpus gate, offline authorization mode)  
**Prepared:** 2026-10-31  
**Target repository:** `tadinve/KaryaMeshGraph` ([existing GitHub repository](https://github.com/tadinve/KaryaMeshGraph); **currently public**)  
**Upstream read-only repository:** `tadinve/KaryaMeshScratch` — called **KaryaMeshRAG** in this spec: the Extract/retrieval layer of KaryaMesh  
**Product:** KaryaMesh ([www.karyamesh.com](https://www.karyamesh.com), a CloudKarya product) — "Connected intelligence for enterprise artifacts" (see §0.5)  
**Project name:** KaryaMeshGraph  
**Initial corpus:** The 50 curated SOPs already processed by KaryaMeshScratch  
**Primary language/tooling:** Python, `uv`, `pytest`  
**First execution target:** M0 + M1 below. Do **not** implement the entire roadmap in one pass.

---

## 0. Instructions to Claude — read first

You are helping implement a **new, standalone engineering project inside an existing GitHub repository**, not modifying or extending the frozen RAG benchmark. The target `tadinve/KaryaMeshGraph` already exists; do not create another repository or rename it.

1. **Inspect before implementing.** Verify actual files, data shapes, command behavior, and dependencies in the local `KaryaMeshScratch` checkout. This specification distinguishes verified repository facts from proposed interfaces. If a runtime artifact is missing, report it explicitly; do not pretend it is present.
2. **Never modify the upstream RAG repository or its frozen data.** No `git checkout`, migrations, rewrites, auto-rebuilds, syncs, `uv` operations, or scripts inside `KaryaMeshScratch` that can change any of its files without a separate, explicit user decision. Do not change upstream code to make this integration work.
3. **Use `uv` throughout** for dependency management, lockfile, and command execution. Build small testable Python modules. Prefer standard library and a small set of justified dependencies.
4. **Hand-understandable engineering.** Keep each operation visible and independently testable. Explain input/output contracts, identity rules, failure modes, tradeoffs, and verification steps. Do not add agents, a graph database, embeddings, cloud infrastructure, or a service API unless an accepted milestone needs them.
5. **Evidence over claims.** Only report commands actually executed and measurements actually collected. Do not fabricate rule extraction quality, graph accuracy, completeness, or security results.
6. **Fail closed.** Never silently substitute another corpus generation, PDF version, parser version, source document, missing access-control metadata, or a different file when the specified artifact is absent or invalid.
7. **Review gate.** Complete M0/M1, show tests and inspection findings, and summarize any changes needed to this spec before starting LLM rule extraction (M2).
8. **Public-repository safety.** `tadinve/KaryaMeshGraph` is currently **PUBLIC**. Keep all PDFs, local snapshots, source text, credentials, prompts containing SOP passages, raw model outputs, and potentially sensitive review labels **out of Git and out of GitHub Actions logs/artifacts**. Commit only source code, small synthetic fixtures, and redacted evidence reports. Verify staged files before every push. Do not change repository visibility without explicit user authorization. Do not transmit SOP content to any external model until the provider and data handling are explicitly approved.

**Definition of engineering ownership:** every feature must have readable code, deterministic fixtures, negative tests, traceable artifacts, and an explanation of where it can fail.

---

## 0.5 Product context — what KaryaMeshGraph is part of

**KaryaMesh** ([www.karyamesh.com](https://www.karyamesh.com), CloudKarya) turns disconnected enterprise artifacts into a connected, queryable, verifiable intelligence layer: ingest artifacts → extract their meaning → build a connected mesh (knowledge graph) of facts, entities, relationships, approvals and dependencies → apply questions, rules and verification, with every answer traced to evidence. The product's framing: move **"from 'find the information' to 'verify the business condition.'"** Its lead industry is Pharma & Life Sciences (SOPs, batch records, deviations, CAPAs, specifications, quality records) — exactly the corpus this pilot uses.

The product vision (architecture diagram, 2026-10-31) is **Extract → Remember → Enforce**:

1. **Extract** — ingest enterprise artifacts and discover candidate rules and knowledge with source evidence. The parsing/retrieval foundation already exists as **KaryaMeshRAG** (`tadinve/KaryaMeshScratch`): deterministic, provenance-exact PDF parsing, section-aware chunking, and secure retrieval over the 50-SOP corpus. Frozen and read-only for this project.
2. **Remember** — **this repository, KaryaMeshGraph**: Extract & Link → Validate & Refine (evidence checked against source text; conflict/overlap/revision detection) → Classify & Govern (rule vs guidance vs fact; human review approve/reject/supersede; full status history), producing the Queryable Knowledge graph and the Business Rules Layer. This spec's M0–M5 implement "Remember" in batch mode over one input class.
3. **Enforce** — runtime verification of business conditions (batch records, purchase orders, change orders), decision support for agents and applications, and the operational feedback loop. **Explicitly deferred**; not part of M0–M5.

### Implementation implications (binding on design decisions below)

- **Structured, machine-evaluable rule parts.** `preconditions`, `exceptions`, and `scope` in the M2 rule schema are structured fields, not prose — Enforce can never gate a transaction on a sentence.
- **Append-only governance.** Rule status transitions and review decisions are event-sourced history, never overwritten (M5).
- **Pluggable sources.** KaryaMeshRAG is the first source adapter behind the §5 contracts; contracts/emails/meeting notes later mean new importers, not rewrites.
- **Relationship hints are a first-class input.** The document-identity alias table (M1.5) is the first instance of the product's "domain relationships" seed input.
- **Rule-type taxonomy target.** The initial M2 `rule_type` set grows toward the product taxonomy: required approvals, valid versions, mandatory evidence, consistency checks, tolerance thresholds, deadline compliance, exception routing, required linkages.
- **Query layer stays embeddable.** Graph queries are plain functions over the versioned graph so a later phase can expose them (e.g. MCP-style) to agents and applications without redesign.
- **The pilot is the proof.** Every public product claim (evidence-backed answers, discrepancy detection, rule enforcement) maps to a measurable M1–M5 deliverable; the fail-closed rules in §0 are what make those claims defensible.

---

## 1. Problem statement

KaryaMeshRAG (`tadinve/KaryaMeshScratch`) has already built a reproducible, source-traceable document-processing and RAG baseline. KaryaMeshGraph must use those **existing document artifacts**, not start a second PDF parsing/RAG project.

The new system's purpose is to identify explicit and implied **business rules** in SOPs, normalize the entities and relationships they mention, construct a **provenance-backed knowledge graph (KG)**, and surface potential contradictions, gaps, exceptions, and dependencies for human review.

A rule is **not accepted as true simply because an LLM produced it**. Each assertion must reference the precise approved, accessible source document revision and evidence spans, and remain a candidate until verification/review rules are satisfied.

### Target use cases

- "Which SOPs require a management review, and who is responsible?"
- "What conditions and exceptions apply to corrective actions?"
- "Do two procedures impose conflicting requirements on the same activity?"
- "Which rules depend on this role, document, or approval?"
- "What changed in the rules when an SOP revision became effective?"
- "Show the original PDF page and highlighted source text for this extracted rule."

Answers must support **evidence-first navigation**, with no unsupported graph traversal presented as a confirmed rule.

### Non-goals of the first implementation

- Replacing the existing RAG retriever or changing its gold questions, chunkers, frozen data, or metrics.
- Re-extracting all PDFs with Graphify or another generic PDF-to-KG pipeline.
- Automatically approving business rules or silently rewriting SOPs.
- Runtime monitoring of batch records, purchase orders, or change orders (later KaryaMesh phases).
- Building a multi-agent framework or deploying a production graph service.
- Promising accurate contradiction detection without labeled evaluation data.

---

## 2. Confirmed upstream facts (verify locally before coding)

The following were verified from `tadinve/KaryaMeshScratch` on its `main` branch at spec-writing time; **the local checkout and its current files remain authoritative**.

### Source code and metadata

| Upstream path | Purpose |
|---|---|
| `corpus/manifest.json` | Document identifiers, PDF source filenames, SHA-256, page counts, title/series |
| `corpus/provenance.json` | Corpus provenance metadata |
| `corpus/review.csv` | Human include/exclude decisions |
| `src/karyamesh/parse/extract.py` | Approved/lifecycle/integrity-gated PDF segment extraction, page and geometry |
| `src/karyamesh/parse/paragraphs.py` | Reconstructed source elements with offsets/spans |
| `src/karyamesh/parse/sections.py` | Section hierarchy and element-to-section assignments |
| `src/karyamesh/chunk/section.py` | Section-aware chunk generation and source provenance |
| `src/karyamesh/paths.py` | Generation-specific runtime paths |
| `src/karyamesh/authz/secure_retrieval.py` | Principal + lifecycle + ACL gating in RAG retrieval |
| `src/karyamesh/revision/` | Revision lifecycle models and transitions |
| `corpus/authz/` | Example identity/ACL policy configuration |

### Frozen corpus version

- Frozen generation: `v2`.
- Frozen manifest committed to Git: `evidence/day08/corpus-manifest-v2.json`.
- Intended local runtime chunk file: `var/v2/chunks/corpus.section-1000.jsonl`.
- Expected frozen section-chunk file SHA-256: `720d36e80e0b7b5599ca936b922c9be36dc2fb26c30d205e2d13fc22f0e4b6aa`.
- Expected section-chunk records: **1,752** across **50 documents**.
- Alternative frozen fixed-chunk SHA-256: `d57af49f656621224c211fbfd62fe1ae80e961ed79ec69ae6994e254e433d422`; it is **not** the default input for this KG pilot.
- Runtime `var/` and `data/originals` are Git-ignored. A GitHub clone alone therefore **does not contain the complete dataset**.

**Critical:** Verify the manifest's declared hash against bytes on disk. The hash above is a known expectation, not proof of what exists on the implementer's machine.

### Available committed examples

These samples can be used to understand and test input shapes:

- `evidence/dissect/CRS-SOP-009/02_extract.jsonl`
- `evidence/dissect/CRS-SOP-009/05_elements.jsonl`
- `evidence/dissect/CRS-SOP-009/07_sections.json`
- `evidence/dissect/CRS-SOP-009/08_section_chunks.jsonl`
- Corresponding dissection outputs under `evidence/dissect/CRS-SOP-018/`

**Do not quietly mix these examples with frozen v2 chunks.** The dissection snapshots can have different extractor/chunker versions and/or extraction times. Treat their lineage as distinct until hashes, parser versions, and invariants prove compatibility. A prototype using examples must be visibly labeled `dissection_fixture`, never `v2_verified`.

### Important limitation of RAG chunks

The upstream RAG chunking policy excludes some categories, including headings, TOC, headers/footers, diagrams, and unknown elements. The KG extraction corpus therefore needs the **reconstructed elements and section hierarchy in addition to RAG chunks**. Chunks are useful context/navigation units, but they are not proof of complete source-document coverage for business rules.

### Verified findings (2026-10-31 read-only inspection)

Five read-only probes of the local KaryaMeshRAG checkout (commit `c069874c`) resolved this spec's §12 open questions:

1. **The frozen v2 generation is chunk-only.** `var/v2/` holds chunks, BM25 and embeddings; the v2 build parsed in memory and persisted no segment/element/section exports. The only persisted element layers are v1-era (`var/extract/CRS-SOP-005.*`; `evidence/dissect/CRS-SOP-009` and `-018`) and are **proven non-v2** by version stamps (v1-era classifier 2026-10-16.6 vs v2 classifier 2026-10-19.6, reconstructor 2026-10-19.1). **0 of 50 documents have a v2-compatible full source layer.** Regeneration outside the frozen tree is feasible with provable lineage (code constants at HEAD equal the v2 manifest versions; input PDFs hash-pinned; candidate fingerprints recorded in the manifest) — but the exact pdfplumber version used for v2 is inferred (0.11.10), not recorded.
2. **Hashing conventions are pinned and empirically verified.** `chunk_sha256 = sha256(utf-8(text))` over the `text` field only, no normalization; `chunk_uid = chunk_id + '#' + chunk_sha256[:16]` (defined in `src/karyamesh/chunk/fixed.py:58-62`, reused by `section.py`); `chunk_id` embeds `pdf_sha256[:12]`; `pdf_sha256` and the whole-file fingerprints are SHA-256 of raw bytes (`fingerprints.<strategy>.sha256` in the generation corpus-manifest). Recomputation on live v2 records matched exactly.
3. **Frozen v2 verified on disk.** The section chunks file hashes to the expected `720d36e8…b6aa`; 1,752 records; 50 documents; exactly one `(stable_id, object_id, version, pdf_sha256)` tuple per document; PDF hashes agree across the v2 manifest, `corpus/manifest.json`, chunk records and PDF bytes; all 50 PDFs present. Caveat: the upstream working tree was **dirty** (4 modified, 4 untracked files) — inspection must record commit *and* tree state, never assume clean.
4. **Revision/ACL metadata is exportable read-only but simulated.** Every lifecycle record carries `is_simulated: true` with `simulated_fields`; `supersedes` is NULL for all 50 rows (the only populated link is synthetic, in `revtest`); there is **no per-document `acl_policy_ref`** upstream — document→grant linkage is a derived join over `corpus/authz/acl-policy.json` targets and must be labeled derived. Upstream enforcement is fail-closed and evaluates ACL at current policy time only.
5. **A deterministic structural layer is ~80–85% parseable from chunks alone.** Two families: CRS-SOP-001..019 (ORS, 12-section canon) and CRS-SOP-020..050 (CDER MAPP, 9-section canon, 81 noisy top-level titles needing a whitelist). Role→duty parsing needs a stateful pass in chunk-index order (58/143 family-B RESPONSIBILITIES chunks open with an orphan bullet whose role header is in the previous chunk). 40/50 documents have definitions sections; 20 terms are defined in ≥2 documents. Change-history tables ARE in the chunks: clean pipe-delimited rows in 36/50 documents, scrambled (unruled tables) in 14. Citation *detection* is ~100% deterministic (92 MAPP, 160 CFR, 79 ISO, 47 AOAC, 35 ORA-LAB, 27 SOP-id mentions), but in-corpus *resolution* fails without an external alias table because documents do not self-identify in chunk text (front matter is never chunked): only 8/48 MAPP pairs and 0/26 ORA-LAB pairs resolve from text alone.

---

## 3. Architecture and repository boundary

```text
KaryaMeshRAG = KaryaMeshScratch (read-only source; no dependency at KG query time)
  ├── frozen v2 corpus manifest
  ├── versioned section-aware RAG chunks
  ├── reconstructed source elements and section hierarchy (when available)
  ├── source PDF identity / provenance
  └── revision + ACL inputs
              │
              ▼
  Local, one-way snapshot importer (KG repo)
     verify generation, SHA-256, identities,
     exact spans, completeness and authorization context
              │
              ▼
KaryaMeshGraph — immutable snapshot, local only
  ├── source documents / versions / elements / sections / spans
  ├── section chunks (secondary navigation reference)
  └── machine-verifiable import report
              │
              ▼
  Rule candidate extraction (M2)
     explicit clauses / obligations / conditions / exceptions
              │
              ▼
  Independent evidence verification + review status
              │
              ▼
  Entity resolution + typed knowledge graph (M3)
              │
              ▼
  Queries, contradiction candidates, review workflow (M4/M5)
```

### Integration rules

- The dependency direction is **one way**: KaryaMeshGraph reads exported/locally available data from KaryaMeshScratch. Never write back to the RAG repository.
- **No Git submodule requirement**, mutable shared SQLite DB, import of RAG internal Python packages at KG query time, or common writable data directory.
- The importer may depend on a specific **published artifact schema**, not on private implementation details of RAG runtime classes.
- An import is a **snapshot**. Record the source path, Git commit, source artifact hashes, schema/producer versions where available, corpus generation, creation time, and verification status.
- The local source path is configurable; do not hardcode a developer's machine path.
- If a required full-corpus element/section export is missing, **do not invent its contents**. Report exactly what is available; propose a controlled export or reproducible regeneration mechanism for a later task. Any such regeneration must be isolated from the frozen upstream tree.
- No automatic HTTP/API connector in the first milestone. A local artifact adapter is simpler and easier to audit.

---

## 4. Target repository structure (existing GitHub repository)

```text
KaryaMeshGraph/
├── README.md
├── SPEC.md
├── pyproject.toml
├── uv.lock
├── .gitignore
├── config/
│   └── source.example.yaml
├── src/karyameshgraph/
│   ├── __init__.py
│   ├── cli.py
│   ├── ingest/
│   │   ├── inspect.py
│   │   ├── snapshot.py
│   │   └── verify.py
│   ├── contracts/
│   │   ├── models.py
│   │   └── schemas.py
│   ├── extract/           # M2
│   │   └── rules.py
│   ├── provenance/        # M2
│   │   └── evidence.py
│   ├── entities/          # M3
│   │   └── resolve.py
│   ├── graph/             # M3
│   │   ├── build.py
│   │   └── query.py
│   └── analysis/          # M4
│       └── contradictions.py
├── tests/
│   ├── fixtures/          # Small non-sensitive synthetic inputs
│   ├── unit/
│   └── integration/
├── data/
│   ├── snapshots/         # GIT-IGNORED; real source text + manifests
│   └── gold/              # Real reviewed labels only when approved for commit
├── evidence/              # Reports with no confidential source text
└── docs/
    └── contracts.md
```

`SPEC.md` belongs in the existing repository root alongside its README; do not overwrite any user work. Keep package APIs small and explicit. CLI can be implemented with `argparse`; add a CLI framework only if justified.

### Example local source config

```yaml
source:
  project: KaryaMeshScratch
  root: ../KaryaMeshScratch
  generation: v2
  strategy: section
  mode: read_only
  expected_manifest: evidence/day08/corpus-manifest-v2.json
  expected_chunks: var/v2/chunks/corpus.section-1000.jsonl

snapshot:
  output_root: data/snapshots
  verify_sha256: true
  require_full_evidence_for_extraction: true
```

This configuration is **proposed for implementation**, not an existing upstream API.

---

## 5. Data contract: version 1

Use JSON/JSONL with explicit versioned schemas. Retain native source IDs; never merge two document revisions simply because they share a title or `stable_id`.

### 5.1 `DocumentVersion`

Required identity and lineage fields:

- `document_id` (the source `stable_id`, e.g. `CRS-SOP-009`)
- `object_id` (specific source object/revision identity)
- `version` (source revision label)
- `pdf_sha256` (complete 64-character SHA-256)
- `title` (from source metadata, where available)
- `source_generation` (`v2` or explicitly `dissection_fixture`)
- `source_manifest_sha256`
- `effective_at`, `approved_at`, `withdrawn_at`, `supersedes` (if available; retain null vs absent semantics)
- `approval_status` and an explicit `eligibility_status` with its evaluation time
- `acl_policy_ref` / `acl_policy_version` (if present; **missing is not public**). Note: upstream has no per-document policy ref — any document→grant association is a **derived** join and must be labeled `derived`, never presented as source fact.
- `is_simulated` / `simulated_fields` passthrough — upstream lifecycle metadata is simulated and must remain visibly so; never launder simulated approvals into real ones.

Proposed immutable identity: `(document_id, object_id, version, pdf_sha256)`.

### 5.2 `SourceElement`

- `element_id`, `document_version_id`, `kind`, `text`
- `pages`, `source_segments`, `spans`, `section_id`
- `parser_version`, `reconstructor_version`, source artifact SHA-256
- `classification_evidence`, `review_flags`

Keep all element classes in the source layer, even if rule extraction later filters some. A heading can supply missing rule context even if it is not part of the RAG chunk text.

### 5.3 `Section`

- `section_id`, `document_version_id`, `heading_element_id`
- `title`, `number`, `parent_section_id`, `path`, `pages`
- `detector_version`, `evidence`, `uncertain_flags`

### 5.4 `ChunkRef`

- `chunk_id`, `chunk_sha256`, `document_version_id`, `corpus_generation`
- `text`, `section_id`, `section_path_titles`, `pages`
- `elements`, `source_spans`, `inserted`, `review_flags`

For v2, use the upstream content-safe `chunk_uid = chunk_id + '#' + chunk_sha256[:16]` convention wherever a persistent chunk reference is needed. **Never key downstream labels by `chunk_id` alone**; upstream evidence demonstrates some unchanged IDs were associated with changed text across generations.

### 5.5 `EvidenceSpan` (new normalized contract)

- `document_version_id`
- `element_id` / `segment_id` as available
- `page_number` (1-based)
- `source_bbox` when available
- `source_start` / `source_end` in the recorded source segment or element
- `quote_exact`
- `source_text_sha256` and `evidence_validation_status`
- `verification_level`: **`SOURCE_VERIFIED`** (quote validated against the reconstructed segment/element layer, with page/bbox geometry independently confirmed against the PDF-derived source) vs **`CHUNK_VERIFIED`** (validated only against verified v2 chunk text; offsets are exact within the chunk, but PDF-level geometry is not independently confirmed). A `chunk_only` snapshot can never yield `SOURCE_VERIFIED` spans; claiming it is a verification failure.
- optional `chunk_uid` and `chunk_start` / `chunk_end` as a navigation bridge

**Validation requirement:** compare exact quoted bytes/characters to the referenced verified source segment/element, using the documented offset conventions. Do not pass a quote merely because a normalized/fuzzy match was found elsewhere in the document. If normalization is necessary, preserve both the original quote and the normalized form, with an explicit transform/version.

### 5.6 `SnapshotManifest`

Proposed example (illustrative; compute all values from real inputs):

```json
{
  "schema_version": "1.0.0",
  "snapshot_id": "<content-derived-id>",
  "source_project": "KaryaMeshScratch",
  "source_commit": "<actual-git-sha>",
  "corpus_generation": "v2",
  "strategy": "section",
  "source_manifest_sha256": "<verified-sha256>",
  "source_chunks_sha256": "720d36e80e0b7b5599ca936b922c9be36dc2fb26c30d205e2d13fc22f0e4b6aa",
  "document_count": 0,
  "chunk_count": 0,
  "documents": [],
  "artifacts": [],
  "validation_status": "pending"
}
```

`0` counts above are placeholders, **not a successful snapshot**. The implementation must fill verified counts. Store nondeterministic timestamps separately from content-derived identifiers, or canonicalize them so repeat imports of identical source bytes produce the same content identity.

### 5.7 Required distinctions

Do not conflate:

- SOP revision label vs. PDF byte hash vs. RAG corpus generation.
- Paragraph/section boundaries vs. RAG chunk boundaries.
- Source-extracted fact vs. semantic inference vs. human-reviewed decision.
- Candidate rule vs. accepted business rule.
- Authorization to import/analyze vs. authorization to view a source passage at query time.
- A potential cross-SOP conflict vs. a verified contradiction.

---

## 6. Milestone M0 — repository bootstrap

### Deliverables

1. Use the **existing** `tadinve/KaryaMeshGraph` repository at `https://github.com/tadinve/KaryaMeshGraph` (current `main` branch; README present). Clone or open it locally; inspect `git remote -v`, `git status`, and existing files first. Do **not** create a second repository, delete/replace its history, or assume it is private. Its current public visibility requires the safeguards in Section 0.
2. Add this `SPEC.md`, a concise `README.md`, Python package structure, `pyproject.toml`, `uv.lock`, `.gitignore`, and tests directory.
3. Use `uv` to build an isolated environment with pinned dependencies.
4. Provide `config/source.example.yaml` and local configuration instructions.
5. Add a README architecture diagram and M0/M1 commands.
6. Ensure `.gitignore` covers snapshots, source PDFs, real SOP text, `var/`, `.env`, local provider secrets, SQLite, Python caches, and test-generated confidential artifacts.

### M0 tests / exit criteria

- `uv run pytest -q` succeeds on smoke tests.
- Project installs and imports cleanly in a fresh environment.
- `git status` and a staged-file inspection show no accidentally staged PDFs, real SOP text, local secrets, or raw extraction artifacts. Because the remote is public, block commit/push if any such material is detected.
- The RAG upstream repository remains unmodified.

---

## 7. Milestone M1 — read-only upstream artifact integration (**implement first**)

Build a deliberately small **offline CLI**, with no LLM and no KG extraction.

### M1.1 — inspect source

Proposed command:

```bash
uv run karyameshgraph source inspect \
  --source-root ../KaryaMeshScratch \
  --generation v2 \
  --strategy section
```

The command must:

1. Confirm source root is readable and appears to be the expected project; record its Git commit and whether the working tree is clean (a dirty tree is reported, never hidden).
2. Read the frozen manifest and check its declared generation, record count, and artifact paths.
3. Locate the v2 section JSONL under `var/v2/chunks/`.
4. Stream SHA-256 of the file and compare it to the frozen manifest; count JSONL records and unique document-version identities.
5. Check that all selected documents have corresponding `corpus/manifest.json` entries and expected PDF SHA-256 values.
6. Enumerate which full-fidelity element/section/segment exports exist and which are missing. Identify dissection fixtures separately.
7. Read available revision and ACL metadata, distinguishing **available**, **not loaded**, **missing**, and **invalid**; never label missing policy `public`.
8. Print a useful, concise inspection report and optionally save machine-readable JSON under `evidence/` without source passages.
9. Exit non-zero on mismatch, invalid JSON, duplicate incompatible identities, absent required artifacts, or hash failures.

Read from the upstream directory **without creating or changing files there**. Prefer streaming file reads. Do not run `uv sync` or upstream build commands as part of inspection.

### M1.2 — create snapshot

Proposed command:

```bash
uv run karyameshgraph snapshot build \
  --source-root ../KaryaMeshScratch \
  --generation v2 \
  --strategy section \
  --documents CRS-SOP-009,CRS-SOP-018 \
  --out data/snapshots/pilot-v2
```

Implement a snapshot writer that:

- Uses **verified v2** chunks and metadata, never falling back to v1.
- Records exact document/revision and upstream artifact identities.
- Copies or serializes only the chosen local artifacts into a **Git-ignored snapshot directory**.
- Includes source elements/sections/segments when compatible artifacts actually exist.
- Marks provenance capability explicitly (`full`, `chunk_only`, or `missing`), rather than claiming page-level source evidence is verified when only chunks are available.
- Refuses any later **rule extraction** from `chunk_only` snapshots until full source evidence becomes available.
- Writes files atomically to avoid leaving a partially successful snapshot.
- Writes `snapshot-manifest.json` only with a complete verification result, or records an explicit failed/incomplete state in a separate report.

**Pilot policy:** Start with `CRS-SOP-009` and `CRS-SOP-018`, because we have committed dissection examples to inspect. The first snapshot may contain verified v2 chunk data but lack compatible full element exports. That is an **expected, actionable discovery**, not a reason to fabricate data or relabel older dissection results as v2. Expand to five documents only once the contract is demonstrably complete.

### M1.3 — verify snapshot

Proposed command:

```bash
uv run karyameshgraph snapshot verify --snapshot data/snapshots/pilot-v2
```

Verifier must independently recompute hashes and check:

- all required documents have matching corpus/source identities;
- chunk SHA-256 equals the hash of the recorded chunk text using the **upstream's actual hashing convention** (inspect code; do not guess serialization or encoding);
- chunk IDs and `chunk_uid`s do not collide across differing content;
- section/element references resolve where the full source layer exists;
- every declared source span maps back to exactly the correct source text and, where present, PDF page/geometry;
- no v1/v2 or dissection/v2 mixing;
- duplicates, missing references, altered text, or expired/revoked/unauthorized source state are handled by a documented, fail-closed rule;
- a repeat snapshot build from unchanged inputs has the same content-derived identity.

**Access-control nuance — offline simulation mode (the only M1 mode):** The snapshot CLI requires an explicit `--authorization-mode offline_simulation` and a `--principal <id>` that must resolve to an **active** principal in the snapshotted `corpus/authz/identities.json`; an unknown, disabled, or omitted principal fails the build (fail closed). The chosen mode, principal, and policy/registry versions are recorded in `snapshot-manifest.json` under `authorization_context`, which must state `"is_production_authorization": false`. A successful import under `offline_simulation` is **never** proof of production authorization or that any end-user can access the content. The eventual query layer **must reauthorize at query time** and be safe under revocation or revision changes.

### M1.4 — test requirements

Use synthetic small fixtures, plus optional integration tests that run only when a real local upstream checkout is configured.

Required negative tests:

- source checkout missing;
- frozen manifest missing;
- v2 JSONL missing;
- wrong corpus generation;
- wrong SHA-256 for chunks or original PDF;
- truncation or malformed JSONL line;
- duplicate `chunk_id` with conflicting chunk text;
- section reference points to a nonexistent section;
- an evidence span resolves to different text or page;
- an older dissection snapshot is presented as frozen v2;
- missing or invalid lifecycle/ACL inputs cannot accidentally authorize document access;
- a denied document does not leak content through reports or logs;
- verification catches a tampered snapshot;
- source files and git status remain unchanged after the import (test against a temporary fixture checkout).

Do not log full source text in error messages. Return typed errors with useful source IDs and reasons.

### M1 exit gate

- `source inspect`, `snapshot build`, and `snapshot verify` are working and documented.
- All unit and fixture integration tests pass.
- At least one **actual v2** document snapshot is verified if the local runtime data is available; otherwise a synthetic fixture pipeline is verified and missing real artifacts are listed precisely.
- Every imported item is labeled with its actual fidelity and lineage.
- No LLM/API calls, no graph database, and **no upstream changes**.
- Produce `evidence/m1-report.md` (commands, actual test outcomes, inspected files, verified hashes, missing artifacts, known limitations, next step).

---

## 7.5 Milestone M1.5 — deterministic structural graph (no LLM)

Build the Layer-1/Layer-2 graph from verified snapshot data only: the document/section skeleton plus the entities the SOPs' own structure declares. Every edge is deterministic and reproducible without any model call — but **deterministic does not mean explicit**: a relationship the source states unambiguously within one evidence span is `EXPLICIT`; a relationship recovered by interpretation (e.g. carrying a role header across a chunk boundary, resolving a citation through the alias table) is `INFERRED`; where the source does not establish the relationship clearly (merged sections, degraded tables, contaminated text) it is `AMBIGUOUS` or omitted, never silently promoted. All spans in this milestone are `CHUNK_VERIFIED` (§5.5); `SOURCE_VERIFIED` requires the regenerated element layer and is not claimable here.

**Entry gate:** a **full-corpus snapshot** (all 50 documents) built by M1.2 and verified by M1.3 at chunk fidelity. M1's two-document pilot does not satisfy this; expanding to 50 documents is itself a verification exercise and must pass before M1.5 begins. May then begin without a provider approval, since no LLM is involved.

Deliverables:

1. **Section skeleton** per document from `section_path_titles`, filtered against the two family canons (whitelist; the 81 noisy family-B titles are excluded and reported).
2. **Role nodes + `RESPONSIBLE_FOR` edges** from RESPONSIBILITIES sections, using a stateful parse in chunk-index order that carries the last role header across chunk boundaries. Derivation labels: duties under a role header in the same chunk are `EXPLICIT`; duties attributed via a carried-over header are `INFERRED`; the 4 merged RESPONSIBILITIES-AND-PROCEDURES documents and contaminated text (e.g. CRS-SOP-042 footnotes) yield `AMBIGUOUS` or no edge.
3. **Term nodes + `DEFINED_IN` edges** from the 40 documents with definitions sections (3 format variants); terms defined in multiple documents are linked for comparison, never silently merged.
4. **Revision rows** from change-history tables: parsed in the 36 clean documents; the 14 scrambled-table documents are flagged `degraded_table` and deferred to the full element layer.
5. **Citation detection edges** (`CITES` to external standards; candidate cross-document references) plus the **document-identity alias table** (MAPP/ORA-LAB number → `stable_id`), built read-only from PDF front matter. Whether the alias table may be committed to the public repository requires an explicit user decision. **Decision (owner, 2026-11-01): approved** — the alias table (document number ↔ `stable_id` ↔ title/series) may live in the public repo; it holds identifiers and titles only, never source passages.

Exit gate: counts per deliverable reported with denominators; spans spot-verified; zero LLM calls; evidence recorded in `evidence/m1_5-report.md` without source passages.

---

## 8. Milestone M2 — extraction of business-rule candidates (after M1 approval)

Create a **narrow rule-specific extractor**, not an arbitrary concept graph generator. Start with 2–5 SOPs whose source layers have been verified.

**Blocking precondition:** rule extraction consumes reconstructed source elements and sections, which the frozen v2 generation does not persist (§2 Verified findings). Before M2 begins, a **controlled regeneration** of the full element/section layer must run *outside* the frozen upstream tree: same pinned pipeline versions, hash-pinned input PDFs, lineage proven by matching the manifest's recorded candidate fingerprints; the inferred pdfplumber version (0.11.10) is a recorded risk. Until then, snapshots are `chunk_only` and extraction stays blocked, per §7.

### Initial rule schema

```json
{
  "rule_candidate_id": "<stable-content-derived-id>",
  "statement": "<normalized requirement, without invented qualifiers>",
  "rule_type": "obligation|prohibition|permission|condition|exception|definition",
  "modality": "MUST|MUST_NOT|MAY|SHOULD|UNSPECIFIED",
  "actor": "<canonical-or-source-entity-ref>",
  "action": "<verb/action>",
  "object": "<target>",
  "scope": [],
  "preconditions": [],
  "exceptions": [],
  "evidence": ["<EvidenceSpan-id>"],
  "derivation": "EXPLICIT|INFERRED|AMBIGUOUS",
  "verification_status": "PENDING",
  "review_status": "UNREVIEWED",
  "source_document_version_id": "<id>",
  "extraction_version": "<version>"
}
```

The above is a proposed output example, not an existing schema.

### Extraction rules

- Extract **atomic** business rules with the actors, constraints, timing, exceptions and conditions required to interpret them.
- Keep literal evidence and normalized statement distinct.
- Do not convert a descriptive statement into a `MUST` without source evidence.
- Preserve negation, quantifiers, units, temporal windows, and exceptions; keep undecidable cases ambiguous.
- Provide source span(s) for **every rule candidate**, including inferred rules. An inference must cite the observations supporting it, not invent a source sentence.
- Ensure cross-element context (heading, lead-in, substeps, table header/row, definitions) can be included without falsely treating it as part of a quotation.
- Validate JSON structure, types, entity references, source quotations, document version, and eligibility **independently of the LLM**.
- Reject, quarantine, or flag unsupported candidates rather than promoting them to accepted rules.
- Store prompt/model/provider/configuration versions, the source snapshot ID, raw candidate output (locally), and validation results for reproducibility.
- Obtain explicit approval of external model/provider/data handling before sending real SOP text to an API.

### Extraction evaluation

Curate a small human-reviewed gold set before claiming quality. Measure precision, recall, evidence exactness, and modality/exception fidelity independently. Missing rules matter as much as spurious rules. Report denominators and errors; no invented performance numbers.

---

## 9. Milestone M3 — typed knowledge graph

Start with **NetworkX plus versioned JSON/JSONL**. Do not add Neo4j until traversal/query requirements demonstrate a need for it.

### Node types (initial ontology)

- `Document`, `DocumentVersion`, `Section`, `SourceElement`, `EvidenceSpan`
- `RuleCandidate`, `BusinessRule` (approved, if/when reviewed)
- `Actor`, `Role`, `Action`, `Process`, `Asset`, `System`, `Condition`, `Exception`
- `ReviewDecision`

### Edges (typed, directed)

- `DOCUMENT_HAS_VERSION`, `VERSION_HAS_SECTION`, `SECTION_HAS_ELEMENT`
- `RULE_SUPPORTED_BY`, `RULE_APPLIES_TO`, `RULE_REQUIRES_ACTION`, `RULE_HAS_CONDITION`
- `RULE_HAS_EXCEPTION`, `ROLE_RESPONSIBLE_FOR`, `RULE_REFERENCES_RULE`
- `RULE_SUPERSEDES`, `RULE_DEPENDS_ON`, `RULE_POTENTIALLY_CONFLICTS_WITH`

Edges must carry derivation (`EXPLICIT`, `INFERRED`, or `AMBIGUOUS`), evidence references, source version, validation status, and review decision. Preserve edge direction; don't silently collapse different edge types or revisions.

### Graphify relevance

Graphify (the user's fork: `https://github.com/tadinve/graphify`) is a **reference implementation and optional baseline**, not a compulsory runtime dependency. Its NetworkX graph construction, diff, path/explain queries, JSON/HTML export, and community detection may be adapted selectively **after inspecting license and APIs**. Do not replace the business-rule ontology with generic `conceptually_related_to` edges. We need semantic domain correctness over visually dense graphs.

### Query semantics

Every graph query must propagate source-document eligibility and current principal access rights. Cross-document paths cannot expose nodes, relationships, snippets, or even sensitive existence of denied documents. Query results must distinguish *approved facts*, *unreviewed candidates*, and *potential findings*.

---

## 10. Milestone M4 — rule relationship and contradiction candidates

An apparent contradiction requires compatible **actor, process, object, scope, applicability time, document lifecycle, and exceptions**. Two different frequencies or requirements do not alone prove contradiction.

Initial analyzers should produce **review candidates**, e.g.:

- Opposite obligations for same scope/time (`MUST` vs `MUST_NOT`).
- Conflicting thresholds, deadlines, frequencies, or approval requirements.
- A rule with a condition or exception that lacks a definition/reference.
- A rule depending on a superseded/ineligible provision.
- Ambiguous actor, timing, exception, or responsibility.

Each finding stores supporting rule IDs, evidence from both sides, assumed scope match, unresolved context, confidence rationale, and `PENDING_REVIEW` status. Never automatically mark source SOPs incorrect.

---

## 11. Milestone M5 — review, quality gates, and operational safety

Human review decisions should be **versioned, attributed, and auditable**, including reviewer, timestamp, decision (`APPROVED`, `REJECTED`, `NEEDS_CLARIFICATION`), reason, and exact rule/source revision covered. A previous approval must not carry silently to changed source text.

Security evaluation must include revocation, revised SOPs, denied roles, historical revisions with current policy, cache invalidation, and no-content denial audit records. Treat the prior RAG Day 8 security baseline as a reference; **do not claim KG passes it until independently tested**.

Track extraction precision/recall, source-span exactness, document coverage, evidence verification failure counts, relationship quality, contradiction precision, test results, import time and cost. Keep measurements reproducible and clearly scoped.

---

## 12. Build order and decisions that must wait

| Stage | Focus | Completion gate |
|---|---|---|
| **M0** | Bootstrap existing GitHub repo + `uv` project | Repo boots, tests run, no real SOP content or secrets staged |
| **M1** | Read-only verified snapshot contract | Importer, verifier, negative tests, evidence report |
| **M1.5** | Deterministic structural graph (no LLM) | Roles/terms/citations/revisions with exact spans over all 50 docs; alias table |
| **M2** | Rule candidates from verified elements | Human-labeled evaluation + exact evidence validation |
| **M3** | Typed graph + path/explain | Correct node/edge identity, lineage and authorization |
| **M4** | Contradiction/gap candidates | Reviewable evidence; no false claims of certainty |
| **M5** | Reviews, revisions, security, evaluation | Independent tests and measured results |

**Explicitly defer:** Neo4j/cloud database, GraphRAG fusion, autonomous agents, graph UI product, event monitoring, continuous-improvement proposals, and enterprise deployment.

### Open questions to resolve from code/evidence, not by guessing

1. Which v2 source-element and section exports are present on the local machine for **all 50** SOPs? The committed dissection examples are not equivalent to a complete frozen v2 export.
2. What are the exact canonical hashing/encoding rules for chunk SHA-256 and identifiers? Read upstream implementation and tests.
3. Can current revision and ACL metadata be exported read-only as a snapshot without breaking policy semantics? Which fields are authoritative and which are simulated?
4. What original PDF files are available locally, and can they be accessed without modifying RAG source state?
5. What minimal additional **read-only** export procedure is needed for complete source-element fidelity if only chunks are present?

**Resolved 2026-10-31:** all five questions were answered by read-only inspection; the grounded answers are recorded in §2 "Verified findings." `evidence/m1-report.md` must restate them with the exact commands used.

---

## 13. First Claude execution request

**Start now with M0 and M1 only.**

1. Inspect local directories and Git status. Locate `KaryaMeshScratch` and confirm the artifact inventory and v2 hash. If upstream data are absent, do not claim a successful real-data import.
2. Bootstrap the **existing** `tadinve/KaryaMeshGraph` repository as an isolated `uv` Python project; preserve its Git history and confirm its remote before any commit/push. Treat it as public unless the user separately changes visibility.
3. Implement and test the `source inspect` CLI, a minimal snapshot builder, and an independent snapshot verifier. Build the minimal code required; do not scaffold elaborate unimplemented frameworks.
4. Demonstrate a read-only import of `CRS-SOP-009` and `CRS-SOP-018` from real verified v2 artifacts when available. Show source-layer completeness honestly. Use synthetic fixtures for negative tests.
5. Produce `evidence/m1-report.md`: exact files found, commands actually run, tests, verification results, incomplete input artifacts, decisions, and **the next recommended step**.
6. **Stop at the gate.** Do not begin LLM extraction, graph building, or database deployment until M1 evidence is reviewed.

### Required final handoff from Claude

Provide a concise report with:

- Git repository URL and existing remote/branch confirmation (or explicit explanation if the local clone is unavailable).
- Source checkout path and pinned source Git commit, if known.
- Corpus generation and actual verified hashes/counts.
- Exact commands to reproduce the inspection, snapshot, and verification.
- Files changed, tests run, tests passed/failed/skipped.
- Provenance capability of each imported document (`full`/`chunk_only`/`missing`).
- Evidence file locations, known risks, and unresolved blockers.
- A clear **PASS / PARTIAL / FAIL** verdict for M0 and M1 individually.

**Do not claim completion from code existence alone. Demonstrate the contract with tested artifacts.**
