#!/usr/bin/env bash
# Symlink the KaryaMeshScratch (RAG) artifacts we read into data/upstream/kms/,
# mirroring upstream relative paths. Read-only: nothing is written upstream.
# Usage: KMS_ROOT=/path/to/KaryaMeshScratch scripts/link_upstream.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KMS_ROOT="$(cd "${KMS_ROOT:-$REPO_ROOT/../KaryaMeshScratch}" && pwd)"
DEST="$REPO_ROOT/data/upstream/kms"

PATHS=(
  corpus                 # manifest, provenance, review, authz
  evidence/day08         # frozen v2 corpus manifest
  evidence/dissect       # dissection fixtures (CRS-SOP-009, -018)
  var/v2                 # frozen v2 chunks
  data/originals         # source PDFs
  data/mock_dms          # revision / lifecycle metadata
)

for p in "${PATHS[@]}"; do
  [ -e "$KMS_ROOT/$p" ] || { echo "missing upstream: $p" >&2; exit 1; }
  mkdir -p "$DEST/$(dirname "$p")"
  ln -sfn "$KMS_ROOT/$p" "$DEST/$p"
done

echo "linked ${#PATHS[@]} paths from $KMS_ROOT -> $DEST"
