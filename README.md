# KaryaMeshGraph

The **"Remember" layer of [KaryaMesh](https://www.karyamesh.com)** ("Connected
intelligence for enterprise artifacts"): a provenance-backed business-rule
knowledge graph built from the 50 FDA SOPs already processed by
**KaryaMeshRAG** ([`tadinve/KaryaMeshScratch`](https://github.com/tadinve/KaryaMeshScratch)).

Every assertion traces to an exact approved document revision and evidence
span; nothing an LLM produces becomes a fact until independently verified and
human-reviewed. See [SPEC.md](SPEC.md) for the full contract.

```text
KaryaMeshRAG (read-only; frozen v2 corpus)          KaryaMeshGraph (this repo)
  frozen manifest ─┐
  section chunks  ─┤   source inspect        snapshot build        snapshot verify
  corpus identity ─┼──▶ verify hashes,  ──▶  atomic, verified ──▶  independent
  revision + ACL  ─┘   counts, lineage       chunk_only import     recomputation
                                                    │
                                   (M1.5+) structural graph → rule candidates
                                           → typed KG → contradiction review
```

## Status

M0 (bootstrap) + M1 (read-only artifact integration) implemented; later
milestones (structural graph, LLM rule extraction, typed graph, contradiction
candidates, review workflow) are specified in SPEC.md and gated on M1 review.

## Setup

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/venv/kmg"   # or your preferred location
uv sync
cp config/source.example.yaml config/source.yaml  # then set source.root
```

## M1 commands

```bash
# 1. Verify the upstream KaryaMeshRAG checkout (read-only, exits non-zero on mismatch)
uv run karyameshgraph source inspect --source-root ../KaryaMeshScratch \
  --generation v2 --strategy section --json-out evidence/inspect-report.json

# 2. Build the pilot snapshot (atomic; fail-closed; offline authorization simulation)
uv run karyameshgraph snapshot build --source-root ../KaryaMeshScratch \
  --generation v2 --strategy section \
  --documents CRS-SOP-009,CRS-SOP-018 --out data/snapshots/pilot-v2 \
  --principal compliance.officer --authorization-mode offline_simulation

# 3. Independently verify the snapshot (recomputes every hash)
uv run karyameshgraph snapshot verify --snapshot data/snapshots/pilot-v2

# Tests (synthetic fixtures; add KMG_REAL_SOURCE_ROOT for real-source tests)
uv run pytest -q
KMG_REAL_SOURCE_ROOT=../KaryaMeshScratch uv run pytest -q
```

## Data policy (public repository)

`data/` is gitignored and holds all real SOP content locally (upstream links,
snapshots). Committed content is limited to source code, synthetic test
fixtures, and redacted evidence reports - never PDFs, SOP text, prompts
containing SOP passages, or secrets. The upstream KaryaMeshRAG checkout is
never modified.
