"""Read-only access to a local KaryaMeshRAG (KaryaMeshScratch) checkout.

This module NEVER writes inside the source root (SPEC.md §0.2, §3). All
readers stream or load files and classify what they find as
available / missing / invalid - they do not invent absent artifacts, and
missing access-control metadata is never interpreted as public.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from ..errors import (
    FrozenManifestError,
    GenerationMismatchError,
    MalformedRecordError,
    SourceRootError,
)

STATUS_AVAILABLE = "available"
STATUS_MISSING = "missing"
STATUS_INVALID = "invalid"
STATUS_NOT_LOADED = "not_loaded"


def sha256_file(path: Path, chunk_bytes: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk_bytes):
            h.update(block)
    return h.hexdigest()


class SourceLayout:
    """Locates and reads the upstream artifacts this project depends on.

    Depends only on the published artifact shapes (manifests, JSONL chunk
    records, authz/mock-DMS JSON), never on upstream Python internals.
    """

    FROZEN_MANIFEST_REL = Path("evidence/day08/corpus-manifest-v2.json")

    def __init__(
        self,
        root: Path,
        generation: str = "v2",
        strategy: str = "section",
        frozen_manifest_rel: str | None = None,
        expected_chunks_file: str | None = None,
    ):
        self.root = Path(root)
        self.generation = generation
        self.strategy = strategy
        self.frozen_manifest_rel = (
            Path(frozen_manifest_rel) if frozen_manifest_rel else self.FROZEN_MANIFEST_REL
        )
        # Basename cross-check against the manifest fingerprint (config
        # `expected_chunks`); disagreement is a failure, not a silent override.
        self.expected_chunks_file = (
            Path(expected_chunks_file).name if expected_chunks_file else None
        )

    # -- existence / identity -------------------------------------------------

    def check_root(self) -> None:
        """Fail closed when the root is absent or not the expected project."""
        if not self.root.is_dir():
            raise SourceRootError("source root does not exist", root=str(self.root))
        markers = [self.root / "corpus" / "manifest.json", self.root / "src" / "karyamesh"]
        missing = [str(m.relative_to(self.root)) for m in markers if not m.exists()]
        if missing:
            raise SourceRootError(
                "source root does not look like a KaryaMeshRAG checkout",
                root=str(self.root),
                missing_markers=missing,
            )

    def git_state(self) -> dict:
        """Upstream commit + clean/dirty state, read-only. A dirty tree is
        reported, never hidden (SPEC.md M1.1)."""
        try:
            commit = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            status = subprocess.run(
                ["git", "-C", str(self.root), "status", "--porcelain"],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"status": STATUS_NOT_LOADED}
        if commit.returncode != 0 or status.returncode != 0:
            return {"status": STATUS_NOT_LOADED}
        dirty_lines = [l for l in status.stdout.splitlines() if l.strip()]
        return {
            "status": STATUS_AVAILABLE,
            "commit": commit.stdout.strip(),
            "dirty": bool(dirty_lines),
            "dirty_entries": len(dirty_lines),
        }

    # -- frozen manifest + chunks ---------------------------------------------

    @property
    def frozen_manifest_path(self) -> Path:
        return self.root / self.frozen_manifest_rel

    def load_frozen_manifest(self) -> dict:
        path = self.frozen_manifest_path
        if not path.is_file():
            raise FrozenManifestError("frozen corpus manifest missing", path=str(path))
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise FrozenManifestError(
                "frozen corpus manifest is not valid JSON", path=str(path), reason=str(e)
            ) from None
        declared = manifest.get("corpus_generation")
        if declared != self.generation:
            raise GenerationMismatchError(
                "frozen manifest declares a different corpus generation",
                declared=declared, requested=self.generation,
            )
        fp = (manifest.get("fingerprints") or {}).get(self.strategy)
        if not fp or not fp.get("sha256") or not fp.get("file"):
            raise FrozenManifestError(
                "frozen manifest lacks a usable fingerprint for the strategy",
                strategy=self.strategy,
            )
        if self.expected_chunks_file and fp["file"] != self.expected_chunks_file:
            raise FrozenManifestError(
                "configured expected_chunks disagrees with the manifest fingerprint",
                expected=self.expected_chunks_file, fingerprint=fp["file"],
            )
        return manifest

    def chunks_path(self, manifest: dict) -> Path:
        fp = manifest["fingerprints"][self.strategy]
        return self.root / "var" / self.generation / "chunks" / fp["file"]

    def stream_chunk_records(self, chunks_path: Path):
        """Yield ``(line_no, raw_line_bytes, record)``; strict parsing.

        Raw bytes are preserved so a snapshot copies lines verbatim and the
        file-level hashes of the copied artifacts stay reproducible.
        """
        with open(chunks_path, "rb") as f:
            for line_no, raw in enumerate(f, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError as e:
                    raise MalformedRecordError(
                        "chunk JSONL line is not valid JSON",
                        path=str(chunks_path), line=line_no, reason=e.msg,
                    ) from None
                if not isinstance(rec, dict):
                    raise MalformedRecordError(
                        "chunk JSONL line is not an object",
                        path=str(chunks_path), line=line_no,
                    )
                yield line_no, raw, rec

    # -- corpus + revision + ACL metadata --------------------------------------

    def load_corpus_manifest(self) -> dict[str, dict]:
        """corpus/manifest.json → {stable_id: entry}. Typed failures on
        invalid JSON, a non-list top level, non-object entries, duplicates."""
        path = self.root / "corpus" / "manifest.json"
        if not path.is_file():
            raise SourceRootError("corpus/manifest.json missing", path=str(path))
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise MalformedRecordError(
                "corpus manifest is not valid JSON", path=str(path), reason=str(e)
            ) from None
        if not isinstance(entries, list):
            raise MalformedRecordError(
                "corpus manifest top level is not a list", path=str(path)
            )
        out: dict[str, dict] = {}
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or not entry.get("stable_id"):
                raise MalformedRecordError(
                    "corpus manifest entry is not an object with stable_id",
                    path=str(path), index=i,
                )
            sid = entry["stable_id"]
            if sid in out:
                raise MalformedRecordError(
                    "duplicate stable_id in corpus manifest", stable_id=sid
                )
            out[sid] = entry
        return out

    def _load_json_classified(self, rel: str, required_keys: tuple[str, ...]) -> tuple[str, dict | list | None, str | None]:
        """Load a metadata JSON file → (status, data, detail)."""
        path = self.root / rel
        if not path.is_file():
            return STATUS_MISSING, None, f"{rel} not found"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return STATUS_INVALID, None, f"{rel}: {e}"
        if isinstance(data, dict):
            missing = [k for k in required_keys if k not in data]
            if missing:
                return STATUS_INVALID, None, f"{rel}: missing keys {missing}"
        return STATUS_AVAILABLE, data, None

    def load_mock_dms_documents(self):
        return self._load_json_classified("data/mock_dms/documents.json", ())

    def load_identities(self):
        return self._load_json_classified(
            "corpus/authz/identities.json", ("registry_version", "principals")
        )

    def load_acl_policy(self):
        return self._load_json_classified(
            "corpus/authz/acl-policy.json", ("policy_version", "grants")
        )

    # -- full-fidelity enumeration ----------------------------------------------

    def enumerate_full_fidelity(self, frozen_versions: dict) -> list[dict]:
        """List persisted segment/classified/element/section artifacts per
        document and label their lineage.

        The document id comes from the artifact's location (the dissection
        directory name, or the filename stem under var/extract), never from a
        generic stage filename like ``05_elements``. Dissection fixtures are
        identified separately (SPEC.md M1.1.6). ``v2_compatible`` is granted
        only to an *elements* artifact whose version stamps all match the
        frozen manifest's pipeline versions - today that is true for none.
        """
        found: list[dict] = []
        extract_dir = self.root / "var" / "extract"
        if extract_dir.is_dir():
            for f in sorted(extract_dir.glob("*.jsonl")):
                doc = f.name.split(".")[0]
                layer = ("elements" if f.name.endswith(".elements.jsonl")
                         else "classified" if f.name.endswith(".classified.jsonl")
                         else "segments")
                found.append(self._classify_artifact(f, "var_extract", doc, layer, frozen_versions))
        dissect_dir = self.root / "evidence" / "dissect"
        if dissect_dir.is_dir():
            for d in sorted(p for p in dissect_dir.iterdir() if p.is_dir()):
                for name, layer in (("02_extract.jsonl", "segments"),
                                    ("03_classify.jsonl", "classified"),
                                    ("05_elements.jsonl", "elements"),
                                    ("07_sections.json", "sections")):
                    f = d / name
                    if f.is_file():
                        found.append(self._classify_artifact(
                            f, "dissection_fixture", d.name, layer, frozen_versions))
        return found

    def _classify_artifact(self, path: Path, kind: str, document: str,
                           layer: str, frozen_versions: dict) -> dict:
        stamps: dict = {}
        try:
            with open(path, encoding="utf-8") as f:
                first = f.readline().strip()
            if first:
                head = json.loads(first)
                if isinstance(head, dict):
                    for key in ("classifier", "reconstructor", "extractor"):
                        if key in head:
                            stamps[key] = head[key]
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        comparable = {k: v for k, v in stamps.items() if k in ("classifier", "reconstructor")}
        v2_compatible = (
            layer == "elements"
            and "reconstructor" in comparable
            and all(frozen_versions.get(k) == v for k, v in comparable.items())
        )
        return {
            "path": str(path.relative_to(self.root)),
            "kind": kind,
            "document": document,
            "layer": layer,
            "version_stamps": stamps,
            "v2_compatible": v2_compatible,
        }
