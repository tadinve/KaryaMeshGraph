# M1 Evidence Report — read-only KaryaMeshRAG artifact integration

**Status: FINAL — all results below are from actual executed commands
(test suite and pilot import run by the project owner on 2026-11-01;
output pasted verbatim into the session and transcribed here).**

## 1. Repository and environment

- Target repo: `tadinve/KaryaMeshGraph` (public), remote confirmed via
  `git remote -v` → `git@github.com:tadinve/KaryaMeshGraph.git`.
- Upstream source: local KaryaMeshRAG checkout at `../KaryaMeshScratch`
  (`tadinve/KaryaMeshScratch`), read-only. Last commit `c069874c`
  (2026-10-21); working tree **dirty** (4 modified + 4 untracked on
  2026-10-31; 10 entries at the 2026-11-01 pilot run — the drift itself is
  why snapshots record commit + dirty state instead of assuming stability).
- Environment: `uv` project, Python 3.14 (`~/venv/kmg`). M0/M1 code depends
  only on pyyaml (dev: pytest); fastapi/httpx/uvicorn were added to
  pyproject on 2026-11-01 for future service work and are unused by M0/M1.

## 2. Resolved open questions (SPEC §12) — verified 2026-10-31

Five read-only probes executed on 2026-10-31 (shasum/python3 one-liners over
the local checkout; no upstream file modified):

1. **v2 is chunk-only.** `var/v2/` = chunks + bm25 + embeddings only; 0/50
   documents have a persisted v2-compatible element/section layer. The only
   element artifacts are v1-era (`var/extract/CRS-SOP-005.*`,
   `evidence/dissect/CRS-SOP-009`, `-018`; version stamps 2026-10-16/17.x vs
   v2's 2026-10-19.x).
2. **Hashing conventions** (verified empirically on 2 live records):
   `chunk_sha256 = sha256(utf8(text))`; `chunk_uid = chunk_id + '#' +
   sha[:16]` (`src/karyamesh/chunk/fixed.py:58-62`); `chunk_id` embeds
   `pdf_sha256[:12]`; file fingerprints = sha256 of raw bytes.
3. **Frozen v2 verified on disk:** `var/v2/chunks/corpus.section-1000.jsonl`
   sha256 = `720d36e80e0b7b5599ca936b922c9be36dc2fb26c30d205e2d13fc22f0e4b6aa`
   (matches `evidence/day08/corpus-manifest-v2.json` fingerprint); 1,752
   records; 50 documents; exactly one identity tuple per document; PDF hashes
   agree across v2 manifest / corpus manifest / chunk records / PDF bytes;
   all 50 PDFs present via the `data/originals` symlink.
4. **Revision/ACL metadata:** all lifecycle fields simulated
   (`is_simulated: true`); `supersedes` NULL for all 50 rows; no per-document
   `acl_policy_ref` (doc→grant join is derived); registry
   `identities-2026-11-01.1`, policy `acl-2026-11-01.1` (4 principals,
   8 grants).
5. **Structural layer** ~80–85% deterministically parseable from chunks;
   in-corpus citation resolution requires the alias table
   (`corpus/manifest.json` `source_document_number` provides it).

## 3. Commands to reproduce (M1 CLI)

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/venv/kmg"

uv run karyameshgraph source inspect --source-root ../KaryaMeshScratch \
  --generation v2 --strategy section --json-out evidence/inspect-report.json

uv run karyameshgraph snapshot build --source-root ../KaryaMeshScratch \
  --generation v2 --strategy section \
  --documents CRS-SOP-009,CRS-SOP-018 --out data/snapshots/pilot-v2 \
  --principal compliance.officer --authorization-mode offline_simulation

uv run karyameshgraph snapshot verify --snapshot data/snapshots/pilot-v2

uv run pytest -q
KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run pytest -q
```

## 4. Test results — executed 2026-11-01

- Fixture-only run (`uv run pytest -q`):
  **45 passed, 2 skipped in 1.65s** (the 2 skips are the real-source
  integration tests, gated on `KMG_REAL_SOURCE_ROOT`).
- Real-source run (`KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run
  pytest -q`): **47 passed in 1.77s** — zero failures, zero skips.
- The suite covers: hashing/uid conventions, record validation, snapshot_id
  determinism, inspect→build→verify roundtrip on the synthetic fixture tree
  (source tree untouched, byte-identical repeat builds), ~24 negative tests
  (tampered bytes, duplicate identities, span violations, fixture mixing,
  authorization failures, post-build tampering caught by the independent
  verifier), and the real-corpus assertions (frozen sha `720d36e8…`,
  1,752 records / 50 documents, chunk_only).
- No test error message or report contains source text (asserted directly
  via the `SYNTH_MARKER` leak tests).

## 5. Real pilot import — executed 2026-11-01

All three CLI commands ran against the live `../KaryaMeshScratch` checkout:

1. **`source inspect`** → verdict **OK**. Upstream git `c069874cdb2d`,
   tree **DIRTY (10 entries)** — recorded, not hidden. Chunks: 1,752
   records / 50 documents, sha256 `720d36e80e0b7b55..` **[MATCH]** against
   the frozen fingerprint. Full-fidelity layer: **chunk_only**
   (0 v2-compatible documents). Revision metadata: available
   (documents=50, `all_simulated=True`). ACL: identities=available,
   policy=available. JSON report: `evidence/inspect-report.json`
   (7.5 KB; verified to contain hashes/counts/verdicts only — no strings
   over 120 chars, no source passages).
2. **`snapshot build`** (CRS-SOP-009, CRS-SOP-018; principal
   `compliance.officer`; mode `offline_simulation`) →
   `snapshot_id: ksnap-4b613b9d7d12ab67a5985152015d94341aa701e58fd26c0db5e31909a9833644`,
   2 documents (60 chunks), capabilities
   `{"CRS-SOP-009": "chunk_only", "CRS-SOP-018": "chunk_only"}`,
   `validation_status: verified` (builder's internal verify gate passed
   before the atomic rename).
3. **`snapshot verify`** (independent re-verification from on-disk bytes) →
   verdict **OK**; same snapshot_id recomputed; 2 documents (60 chunks),
   both `chunk_only`.

The snapshot lives under `data/snapshots/pilot-v2` — a gitignored path
(enforced by the builder's gitignore guard), so no SOP text can be staged.

## 6. Known limitations

- Every snapshot is `chunk_only`: evidence spans are `CHUNK_VERIFIED` at
  best; `SOURCE_VERIFIED` requires the controlled element-layer regeneration
  (SPEC §8 blocking precondition). `extraction_eligible` is false for all
  imported documents; M2 is blocked by design.
- Importing element/section artifacts is not implemented; the builder
  refuses loudly if v2-compatible full layers ever appear.
- Lifecycle metadata is simulated upstream and labeled as such end-to-end;
  `offline_simulation` authorization is never production authorization.
- The exact pdfplumber version behind v2 is inferred (0.11.10), not recorded
  upstream — a recorded risk for the regeneration task.
- Upstream git tree was dirty at inspection; snapshots record commit + dirty
  flag rather than assuming a clean tree.

## 7. Verdicts

- **M0: PASS.** Repo scaffolding, uv environment, CLI entry point, typed
  error model, and config contract all in place; `uv run pytest -q` green
  (45 passed, 2 skipped); `.gitignore` verified via `git check-ignore`
  (`data/`, `init.sh`, `config/source.yaml` all ignored); `git status`
  shows only code/config/docs — no SOP content stageable. Nothing is
  committed yet; the staged-file inspection repeats before every actual
  commit (SPEC §0.8).
- **M1: PASS.** All four deliverables — source inspector, snapshot builder,
  independent verifier, negative-test suite — implemented and exercised
  against the real upstream corpus: 47/47 tests pass with
  `KMG_REAL_SOURCE_ROOT` set; inspect verdict OK with frozen-fingerprint
  MATCH; pilot build + independent verify both OK with a deterministic
  content-derived snapshot_id. The trustworthy connection from the RAG
  artifacts to the knowledge-graph input layer is demonstrated end to end.

## 8. Recommended next step

Review this report at the M1 gate. Then: expand the verified snapshot to all
50 documents (the M1.5 entry gate), build the deterministic structural graph
(M1.5), and plan the controlled element-layer regeneration that unblocks M2.
