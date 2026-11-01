"""Offline CLI for M1: `source inspect`, `snapshot build`, `snapshot verify`.

No LLM, no network, no writes inside the source root. Exit codes:
0 = success, 1 = typed failure (verification, authorization, missing
artifacts), 2 = usage/configuration error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .errors import KMGError
from .ingest import inspect as inspect_mod
from .ingest import verify as verify_mod
from .ingest.snapshot import build_snapshot
from .ingest.source import SourceLayout


def _load_config(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        print(f"config file not found: {p}", file=sys.stderr)
        raise SystemExit(2)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        print(f"config file is not a mapping: {p}", file=sys.stderr)
        raise SystemExit(2)
    # Reject configurations this implementation cannot honor, instead of
    # silently ignoring them (fail closed on configuration too).
    mode = (data.get("source") or {}).get("mode")
    if mode not in (None, "read_only"):
        print(f"unsupported source.mode {mode!r}: only read_only is implemented",
              file=sys.stderr)
        raise SystemExit(2)
    if (data.get("snapshot") or {}).get("verify_sha256") is False:
        print("snapshot.verify_sha256: false is not supported - verification "
              "is mandatory", file=sys.stderr)
        raise SystemExit(2)
    return data


def _layout(args, cfg: dict) -> SourceLayout:
    src = cfg.get("source", {})
    root = args.source_root or src.get("root")
    if not root:
        print("--source-root (or source.root in --config) is required", file=sys.stderr)
        raise SystemExit(2)
    return SourceLayout(
        Path(root),
        generation=args.generation or src.get("generation", "v2"),
        strategy=args.strategy or src.get("strategy", "section"),
        frozen_manifest_rel=src.get("expected_manifest"),
        expected_chunks_file=src.get("expected_chunks"),
    )


def cmd_source_inspect(args) -> int:
    cfg = _load_config(args.config)
    layout = _layout(args, cfg)
    report = inspect_mod.inspect_source(layout)
    print(inspect_mod.format_report(report))
    if args.json_out:
        inspect_mod.write_report_json(report, Path(args.json_out))
        print(f"  report written: {args.json_out}")
    return 0 if report["ok"] else 1


def cmd_snapshot_build(args) -> int:
    cfg = _load_config(args.config)
    layout = _layout(args, cfg)
    snap = cfg.get("snapshot", {})
    principal = args.principal or snap.get("principal")
    mode = args.authorization_mode or snap.get("authorization_mode")
    if not principal or not mode:
        print("--principal and --authorization-mode are required (SPEC.md M1.3)",
              file=sys.stderr)
        raise SystemExit(2)
    if args.documents.strip().lower() == "all":
        # Full-corpus build (the M1.5 entry gate): every stable_id in the
        # upstream corpus manifest, in sorted order for determinism.
        documents = sorted(layout.load_corpus_manifest().keys())
    else:
        documents = [d.strip() for d in args.documents.split(",") if d.strip()]
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        manifest = build_snapshot(
            layout, documents, Path(args.out),
            principal=principal, authorization_mode=mode, created_at=created_at,
            allow_unignored_out=args.allow_unignored_out,
        )
    except KMGError as e:
        print(f"snapshot build FAILED: {e}", file=sys.stderr)
        return 1
    caps = {d["document_id"]: d["provenance_capability"] for d in manifest["documents"]}
    print(f"snapshot build: {args.out}")
    print(f"  snapshot_id: {manifest['snapshot_id']}")
    print(f"  documents: {manifest['document_count']} ({manifest['chunk_count']} chunks)")
    print(f"  capabilities: {json.dumps(caps, sort_keys=True)}")
    print(f"  validation_status: {manifest['validation_status']}")
    return 0


def cmd_graph_build(args) -> int:
    from .graph.build import build_graph
    try:
        manifest = build_graph(
            Path(args.snapshot), Path(args.out),
            expected_documents=args.expect_documents,
            alias_out=Path(args.alias_out) if args.alias_out else None,
        )
    except KMGError as e:
        print(f"graph build FAILED: {e}", file=sys.stderr)
        return 1
    print(f"graph build: {args.out}")
    print(f"  graph_id: {manifest['graph_id']}")
    print(f"  snapshot_id: {manifest['snapshot_id']}")
    print(f"  documents: {manifest['document_count']}")
    print(f"  nodes: {manifest['node_count']} {json.dumps(manifest['node_kinds'], sort_keys=True)}")
    print(f"  edges: {manifest['edge_count']} {json.dumps(manifest['edge_kinds'], sort_keys=True)}")
    print(f"  derivations: {json.dumps(manifest['derivations'], sort_keys=True)}")
    if args.alias_out:
        print(f"  alias table written: {args.alias_out}")
    return 0


def cmd_graph_verify(args) -> int:
    from .graph import verify as gverify
    report = gverify.verify_graph(Path(args.graph), Path(args.snapshot))
    print(gverify.format_report(report))
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
        print(f"  report written: {args.json_out}")
    return 0 if report["ok"] else 1


def cmd_snapshot_verify(args) -> int:
    report = verify_mod.verify_snapshot(Path(args.snapshot))
    print(verify_mod.format_report(report))
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"  report written: {args.json_out}")
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="karyameshgraph",
        description="KaryaMeshGraph M1: read-only KaryaMeshRAG artifact integration",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_source = sub.add_parser("source", help="upstream source operations")
    source_sub = p_source.add_subparsers(dest="subcommand", required=True)
    p_inspect = source_sub.add_parser("inspect", help="verify upstream artifacts (read-only)")
    p_inspect.add_argument("--source-root")
    p_inspect.add_argument("--generation")
    p_inspect.add_argument("--strategy")
    p_inspect.add_argument("--config")
    p_inspect.add_argument("--json-out", help="write machine-readable report (no source text)")
    p_inspect.set_defaults(func=cmd_source_inspect)

    p_snap = sub.add_parser("snapshot", help="snapshot operations")
    snap_sub = p_snap.add_subparsers(dest="subcommand", required=True)
    p_build = snap_sub.add_parser("build", help="build a verified, atomic snapshot")
    p_build.add_argument("--source-root")
    p_build.add_argument("--generation")
    p_build.add_argument("--strategy")
    p_build.add_argument("--config")
    p_build.add_argument("--documents", required=True,
                         help="comma-separated stable_ids, or 'all' for the full corpus")
    p_build.add_argument("--out", required=True)
    p_build.add_argument("--principal")
    p_build.add_argument("--authorization-mode")
    p_build.add_argument("--allow-unignored-out", action="store_true",
                         help="permit an output path git would commit (dangerous: "
                              "snapshots hold real SOP text and the repo is public)")
    p_build.set_defaults(func=cmd_snapshot_build)

    p_verify = snap_sub.add_parser("verify", help="independently verify a snapshot")
    p_verify.add_argument("--snapshot", required=True)
    p_verify.add_argument("--json-out")
    p_verify.set_defaults(func=cmd_snapshot_verify)

    p_graph = sub.add_parser("graph", help="M1.5 structural graph operations")
    graph_sub = p_graph.add_subparsers(dest="subcommand", required=True)
    p_gbuild = graph_sub.add_parser(
        "build", help="build the deterministic structural graph (no LLM)")
    p_gbuild.add_argument("--snapshot", required=True,
                          help="verified snapshot directory (M1.5 entry gate: full corpus)")
    p_gbuild.add_argument("--out", required=True)
    p_gbuild.add_argument("--expect-documents", type=int, default=50,
                          help="refuse unless the snapshot holds exactly this many "
                               "documents (default 50 = the full-corpus entry gate; "
                               "pass the actual count for fixture/pilot work)")
    p_gbuild.add_argument("--alias-out",
                          help="also write the committable alias table here "
                               "(identifiers and titles only; owner-approved)")
    p_gbuild.set_defaults(func=cmd_graph_build)
    p_gverify = graph_sub.add_parser(
        "verify", help="independently verify a graph against its snapshot")
    p_gverify.add_argument("--graph", required=True)
    p_gverify.add_argument("--snapshot", required=True)
    p_gverify.add_argument("--json-out")
    p_gverify.set_defaults(func=cmd_graph_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KMGError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
