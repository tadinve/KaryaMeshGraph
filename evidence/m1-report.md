# M1 Evidence Report — read-only KaryaMeshRAG artifact integration

**Status: FINAL, corrected 2026-11-01 (two review passes, same calendar
day).** All work in this report, including §2c below, was executed on
**2026-11-01, America/Los_Angeles (PST, UTC-8)** — the session's actual
local date/timezone, confirmed against both `date` on the working machine
and the `git log` commit timestamps (`...T13:43:42-08:00` for the first
pass). An earlier draft of the companion `evidence/m1_5-report.md`
mislabeled its second-pass work "2026-11-02"; that was a reasoning slip,
corrected throughout this review (§5 below and the companion report).

Sections 2 and 7 below are updated by a first review pass that found two
real defects in the original M1 implementation (not in the test suite,
which never exercised real PDF bytes end-to-end and never exercised a
denied/default-deny document), and a second review pass (§2c) that aligned
offline ACL evaluation with the upstream KaryaMeshRAG validator and
separated lifecycle eligibility from ACL authorization. All three are
fixed, independently re-verified against the real upstream checkout, and
covered by new regression tests. See **§2a**, **§2b**, and **§2c** for the
corrected findings; the rest of this report (hashing conventions,
snapshot/verify contract) is unchanged and still accurate.

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
   **7 grants — corrected 2026-11-01, see §2b**; the original "8 grants" was
   a transcription error, never matched by the machine-readable
   `evidence/inspect-report.json`, which always recorded 7).
5. **Structural layer** ~80–85% deterministically parseable from chunks;
   in-corpus citation resolution requires the alias table
   (`corpus/manifest.json` `source_document_number` provides it).

## 2a. Correction — PDF byte verification (2026-11-01)

**Finding:** `src/karyameshgraph/ingest/inspect.py` built each original PDF's
path as `layout.root / "data" / "originals" / entry["file"]`, but
`corpus/manifest.json` entries carry `file` as a **root-relative** path
(e.g. `"data/originals/CRS-SOP-001.pdf"`), not a bare filename. The join
silently doubled the prefix (`data/originals/data/originals/...`), so every
real PDF read as missing. The §2 point 3 claim above ("all 50 PDFs present")
was never actually exercised by code — it was a directory listing, not a
hash-verified read.

**Before the fix** (`evidence/inspect-report.json`, 2026-11-01 run):
`corpus_manifest.pdf_bytes = {"verified": 0, "missing": [CRS-SOP-001 .. 050],
"mismatched": []}` — 0/50 verified, 50/50 reported missing, despite the
files existing on disk at the correct (single-prefix) path.

**Fix:** resolve `layout.root / entry["file"]` directly (one join, no
re-prepended prefix). Regression coverage added:
[tests/unit/test_source_layout.py](../tests/unit/test_source_layout.py)
(direct repro: resolves correctly under the fix, not under the historical
buggy construction) and an assertion in
[tests/integration/test_pipeline.py](../tests/integration/test_pipeline.py)
that `pdf_bytes.verified == len(DOCS)` on every inspect run. The two
synthetic fixtures (`tests/synthetic.py`, `tests/structural_fixture.py`) were
also corrected: their `corpus_entries["file"]` previously used a bare
filename, which is why the fixture suite never caught the real bug (the
double-prefixed and bare-filename cases happened to collide under the old
buggy code).

**Re-verified against the real upstream checkout (2026-11-01):**

```bash
uv run karyameshgraph source inspect --source-root ../KaryaMeshScratch \
  --generation v2 --strategy section --json-out evidence/inspect-report.json
```

`corpus_manifest.pdf_bytes = {"verified": 50, "missing": [], "mismatched":
[]}` — **all 50 original PDFs independently hash-verified against
`corpus/manifest.json`**; verdict **OK**. This corrects §2 point 3: PDF
presence is now actually demonstrated by a recomputed SHA-256 over the raw
file bytes, not inferred from the fact that files existed somewhere on disk.

## 2b. Correction — document-level ACL enforcement (2026-11-01)

**Finding:** the snapshot builder validated that the principal was an
*active* entry in `corpus/authz/identities.json`, then recorded whichever
ACL grants happened to reference a requested document as informational
`derived_grants` (`SPEC.md §5.1`) — but it never evaluated those grants to
decide whether the principal could actually see that document's content.
An active principal with **zero matching grants**, or with only an
**explicit deny**, was silently treated the same as one with an explicit
allow: the chunk content was copied into the snapshot regardless. This is
exactly the gap SPEC.md M5 and the review flagged — "do not silently treat
an active principal as authorized for every document."

Separately, the original "8 grants" claim in §2 point 4 never matched the
checked-in `evidence/inspect-report.json`, which always recorded 7 — the
real upstream `corpus/authz/acl-policy.json` has exactly 7 grant entries
(`G-001, G-010, G-011, G-020, G-021, G-022, G-023`). The "8" was a
transcription slip, not a parsing bug.

**Fix:** a new module,
[src/karyameshgraph/ingest/authz.py](../src/karyameshgraph/ingest/authz.py),
evaluates the upstream policy's own documented resolution order
(`explicit deny` > `allow` > `default deny`) per requested document, using
the import's `created_at` as the "current policy time" (never a historical
as-of, per the policy's own note; never live wall-clock time, since this
remains offline simulation). `snapshot build`
([src/karyameshgraph/ingest/snapshot.py](../src/karyameshgraph/ingest/snapshot.py))
now refuses the **entire** build — before any chunk line is even read — if
any requested document does not resolve to an explicit `allow`. A missing or
invalid ACL policy matches no grants and therefore resolves every document
to `default_deny`: it is still never treated as public. Each surviving
document's `acl.authorization_decision` (effect, resolution,
matched_grant_ids) is recorded in `documents.json`, and `snapshot verify`
([src/karyameshgraph/ingest/verify.py](../src/karyameshgraph/ingest/verify.py))
**independently recomputes** every document's decision from the snapshotted
policy/identity artifacts rather than trusting the stored value, catching a
tampered decision or a document that should never have been included.

This stays explicitly **offline simulation**: `authorization_context`
still states `is_production_authorization: false`; the policy-evaluation
time (`policy_time`) is recorded in the manifest (excluded from the
content-derived `snapshot_id`, like `created_at`) but never baked into the
hashed per-document record, so repeat builds at different wall-clock
instants with an unchanged allow/deny outcome still produce the identical
`snapshot_id`. A successful import decision is still never proof of
production authorization; the eventual query layer must reauthorize at
query time.

**New regression tests**
([tests/integration/test_negative.py](../tests/integration/test_negative.py)):
missing/invalid policy denies all documents and leaks nothing
(`test_missing_acl_policy_denies_import`); an explicit deny blocks only the
denied document while an unaffected document can still be imported
(`test_explicit_deny_blocks_denied_document_import`); no matching grant at
all is a default deny, never an implicit allow
(`test_default_deny_when_no_grant_matches`); a withdrawn document's
*lifecycle* ineligibility is independent of its *ACL* decision
(`test_withdrawn_document_blocked_under_current_mode` /
`test_withdrawn_document_importable_under_authorized_archive_mode` — renamed
and split further in §2c below); the decision is recorded, excluded from
content identity, and independently re-verified, with tamper detection
exercised directly
(`test_authorization_decision_recorded_and_independently_verified`).
Disabled-principal and unknown-principal coverage already existed
(`test_unknown_and_disabled_principals_fail`) and is unchanged.

**Re-verified against the real upstream checkout:** both the pilot
(`CRS-SOP-009`, `CRS-SOP-018`) and full 50-document snapshot build
successfully under principal `compliance.officer`, whose `compliance` group
holds grant `G-001` (`document_id: "*"`, `effect: allow`) — every document's
`acl.authorization_decision` resolves to `{"effect": "allow", "resolution":
"allow", "matched_grant_ids": ["G-001"]}`. Both snapshots independently
verify OK under `snapshot verify`. `evidence/inspect-report.json`'s `acl`
section now also records the content SHA-256 of `identities.json`
(`961199b9921c6dcc29174722c536892544cdd5965b50bfb7702b1d4049654a92`) and
`acl-policy.json`
(`8ab997554f15eaf5670d7891d1e9237655d30210293bf97ba9621e541c2310e6`)
alongside their version strings, so a silent content edit under an
unchanged version string is independently detectable.

## 2c. Second review pass (2026-11-01) — ACL validation alignment and
lifecycle/ACL separation

Reviewing commit `163a275`, the owner identified that §2b's ACL enforcement,
while real, was not as strict as the upstream KaryaMeshRAG validator, and
that an ACL `allow` was being treated as sufficient on its own, without a
separate lifecycle-eligibility gate. Both are now fixed.

**1. ACL policy structural validation now mirrors upstream exactly.** The
real upstream implementation (`src/karyamesh/authz/policy.py:AclPolicy` in
the `KaryaMeshScratch` checkout) was located and read; this project's new
`validate_acl_policy()`
([ingest/authz.py](../src/karyameshgraph/ingest/authz.py)) mirrors its
validation field-for-field: unknown effect, unknown/incomplete subject
type, a target that is not *exactly one* of `document_id`/`document_series`,
a duplicate `grant_id`, a non-ISO-8601 or timezone-naive timestamp, and
`expires_at`/`revoked_at` not following `granted_at`. **A single bad grant
now invalidates the entire policy** (default-deny for every document) -
never a silent per-grant skip. `source inspect` downgrades `acl.policy.status`
to `invalid` and lists the failures (capped) in
`evidence/inspect-report.json`; `snapshot build` raises `AuthorizationError`
and refuses the whole import before touching any chunk line; `snapshot
verify` independently re-validates the snapshotted policy artifact
(structural checks only - intentionally not the referential "is this a
known document" check, since the snapshot only embeds its own partial
document selection, documented at the call site).

Two real fail-open bugs from the first pass were also found and fixed while
implementing this:
- `evaluate_document_access` silently fell back to live
  `datetime.now(timezone.utc)` when the import's `policy_time_iso` failed
  to parse - breaking reproducibility and potentially resurrecting access
  that the real, intended policy time would have denied. It now fails
  closed with a dedicated `invalid_policy_time` resolution.
- `_grant_active` silently *ignored* a malformed `expires_at`/`revoked_at`,
  treating the grant as unrestricted rather than denying it. It now treats
  any grant carrying an unparseable timestamp as inactive.

**2. Lifecycle eligibility is now a separate, independently enforced axis.**
An ACL `allow` never again implies a document is a currently eligible
(approved, not withdrawn) SOP. `build_snapshot` gained `document_mode`
(`"current"` default, or `"historical_archive"`) and `archive_mode_authorized`:
`"current"` requires every requested document to ALSO resolve
`eligibility_for(...) == "eligible_offline_simulated"`, refusing the whole
import (`LifecycleIneligibleError`) otherwise - even though the real
upstream wildcard grant (`G-001`) would still ACL-allow a withdrawn
document like `CRS-SOP-050`. `"historical_archive"` deliberately bypasses
only the eligibility gate (the ACL gate still applies), and is refused
outright unless the caller also passes `archive_mode_authorized=True` -
this is exactly the deliberate, explicitly authorized archival-import
boundary the review asked for, mirroring the upstream secure-retrieval
design's "authorized pool = eligible ∧ allowed". `snapshot verify`
independently recomputes eligibility per document under `"current"` mode
and checks that `"historical_archive"` is only ever recorded alongside
`archive_mode_authorized: true`.

**Confirmed against the real upstream checkout:** `CRS-SOP-050`'s real
`mock_dms` lifecycle record is `WITHDRAWN`
(`withdrawn_at: "2026-10-12T09:00:00Z"`). Requesting it under the default
`document_mode="current"` now refuses
(`LifecycleIneligibleError: ineligible_documents=['CRS-SOP-050']`) even
though `compliance.officer`'s wildcard ACL grant still allows it. The M1.5
full-corpus structural graph (`evidence/m1_5-report.md`) is exactly the
deliberate archival case: it deliberately imports all 50 documents,
including the withdrawn one, under `document_mode="historical_archive"`
with `archive_mode_authorized=True` - recorded explicitly in
`authorization_context`, never silently mixed with "currently eligible for
production access."

**New regression tests:** 14 new tests across
[tests/unit/test_authz.py](../tests/unit/test_authz.py) (structural
validation of every malformed-field case; expired/revoked-grant evaluation;
the malformed-`policy_time` fail-closed fix; a dedicated test pinning that
there is no hidden "historical policy-time bypass" - the decision changes
only at the documented `revoked_at`/`expires_at` boundary, nowhere else) and
[tests/integration/test_negative.py](../tests/integration/test_negative.py)
(malformed real-shaped policies: unknown effect, unknown subject type,
duplicate grant_id, naive timestamp, backwards validity interval, missing
field; expired-grant and revoked-grant denial at the snapshot-build level;
post-build policy tampering caught by the independent verifier; the
withdrawn-document current-vs-archive-mode split). Real-corpus coverage
added in
[tests/integration/test_real_source.py](../tests/integration/test_real_source.py):
`test_withdrawn_document_blocked_under_current_mode_real` against the real
`CRS-SOP-050`.

**Test counts after this pass:** fixture-only `uv run pytest -q`: **104
passed, 4 skipped** (0 failed). Real-source
`KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run pytest -q`: **108 passed**
(0 failed, 0 skipped).

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

> **Note (2026-11-01, second pass):** the counts above are from the
> *original* M1 implementation, before the §2a/§2b/§2c corrections. The
> current, final counts are in §2c: fixture-only **104 passed, 4 skipped**;
> real-source **108 passed**, 0 failed, 0 skipped.

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

> **Note (2026-11-01, second pass):** `snapshot_id` above is from the
> *original* build, before the PDF-path fix and the ACL/lifecycle fields
> were added to every document record (which are content-hashed). The
> current pilot `snapshot_id` is
> `ksnap-bd90f343864e084e75d614f17993d93e310e3391c2610a08bac832aa40bb75c4`
> (re-verified OK, §2c); the ID changing is expected and correct — the
> content genuinely changed (the document records now carry a real,
> enforced `authorization_decision`).

## 6. Known limitations

- Every snapshot is `chunk_only`: evidence spans are `CHUNK_VERIFIED` at
  best; `SOURCE_VERIFIED` requires the controlled element-layer regeneration
  (SPEC §8 blocking precondition). `extraction_eligible` is false for all
  imported documents; M2 is blocked by design.
- Importing element/section artifacts is not implemented; the builder
  refuses loudly if v2-compatible full layers ever appear.
- Lifecycle metadata is simulated upstream and labeled as such end-to-end;
  `offline_simulation` authorization is never production authorization —
  including the new document-level ACL enforcement (§2b): the decision is
  evaluated once, at import time, against the snapshotted policy, and is
  never re-checked at query time.
- `policy_time` for ACL evaluation is the import's `created_at` parameter,
  not live wall-clock time; a caller who passes a stale `created_at` gets a
  stale (but still fail-closed) authorization decision. This is acceptable
  for an offline, reproducible import tool, but is a recorded constraint
  for whoever eventually builds the production query-time reauthorization
  layer.
- The exact pdfplumber version behind v2 is inferred (0.11.10), not recorded
  upstream — a recorded risk for the regeneration task.
- Upstream git tree was dirty at inspection; snapshots record commit + dirty
  flag rather than assuming a clean tree.
- (2026-11-01) The local macOS/Drive working copy periodically regenerates
  stray `Icon` resource-fork files inside `data/` subdirectories (outside
  this project's control). `snapshot verify`'s undeclared-file tamper check
  correctly flags these as failures; they are not snapshot corruption and
  are simply deleted before re-verifying. `.gitignore` already excludes
  `Icon?`, so this never affects what is committed.

## 7. Verdicts

- **M0: PASS.** Repo scaffolding, uv environment, CLI entry point, typed
  error model, and config contract all in place; `uv run pytest -q` green
  (45 passed, 2 skipped); `.gitignore` verified via `git check-ignore`
  (`data/`, `init.sh`, `config/source.yaml` all ignored); `git status`
  shows only code/config/docs — no SOP content stageable. Nothing is
  committed yet; the staged-file inspection repeats before every actual
  commit (SPEC §0.8).
- **M1: PASS (corrected 2026-11-01).** All four deliverables — source
  inspector, snapshot builder, independent verifier, negative-test suite —
  implemented and exercised against the real upstream corpus. The original
  PASS verdict rested on two defects that have now been found, fixed, and
  independently re-verified: the PDF byte-verification path bug (§2a, fixed
  — 50/50 PDFs now independently hash-verified, were 0/50) and the
  authorization gap where an active principal was implicitly authorized for
  every document regardless of the ACL policy (§2b, fixed — document-level
  ACL is now genuinely enforced, with independent re-verification and a
  default-deny posture for missing/invalid policy). With both fixes in
  place: 72/72 tests pass with `KMG_REAL_SOURCE_ROOT` set (0 failed, 0
  skipped; see `evidence/m1_5-report.md` §5 for the full count including
  M1.5's additions); inspect verdict OK with frozen-fingerprint MATCH and
  50/50 PDF bytes verified; pilot and full-corpus builds + independent
  verify both OK under principal `compliance.officer` with deterministic
  content-derived `snapshot_id`s. The trustworthy, authorization-enforced
  connection from the RAG artifacts to the knowledge-graph input layer is
  now demonstrated end to end.

## 8. Recommended next step

M1 corrections are in place and M1.5 (deterministic structural graph) is
now complete — see `evidence/m1_5-report.md` for its full evidence and
verdict. Per SPEC.md M2's blocking precondition, do **not** begin M2 (rule
extraction) until a controlled regeneration of the full element/section
layer runs outside the frozen upstream tree; until then all snapshots stay
`chunk_only` and extraction stays blocked by design.

