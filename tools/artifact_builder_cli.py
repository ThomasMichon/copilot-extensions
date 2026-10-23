"""Command-line policy and toolchain lifetime for the Python artifact builder."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
from pathlib import Path

from build_toolchain_lock import ArtifactBuildError, resolve_toolchain_lock


def main(builder, description: str, *, standalone_builder=None) -> int:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("plugin", help="plugin name under plugins/")
    ap.add_argument("--standalone", action="store_true", help="build a registered standalone component")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--python", help="interpreter used to create the locked toolchain venv")
    ap.add_argument("--toolchain-venv", type=Path, help="share one locked toolchain across builds")
    ap.add_argument(
        "--toolchain-source", choices=("governed", "seasoned-public"), default="governed",
        help="seasoned-public is an explicit build-tooling exception for public promotion runners only",
    )
    ap.add_argument("--toolchain-min-age-days", type=int, default=None)
    args = ap.parse_args()
    if args.standalone:
        if standalone_builder is None:
            ap.error("standalone artifact builder is unavailable")
        builder = standalone_builder
    if args.toolchain_min_age_days is not None and args.toolchain_source != "seasoned-public":
        ap.error("--toolchain-min-age-days requires --toolchain-source seasoned-public")
    try:
        if args.toolchain_venv is None and args.toolchain_source == "governed":
            manifest = builder(
                args.plugin, out_dir=args.out_dir, python=args.python, toolchain=None
            )
            print(json.dumps(manifest, indent=2, sort_keys=True))
            return 0
        with contextlib.ExitStack() as stack:
            venv = args.toolchain_venv
            if venv is None:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory(
                    prefix="build-python-artifacts-toolchain-"
                )))
                venv = root / "venv"
            toolchain = resolve_toolchain_lock(
                venv, python=args.python, source_policy=args.toolchain_source,
                min_age_days=args.toolchain_min_age_days,
            )
            manifest = builder(
                args.plugin, out_dir=args.out_dir, python=args.python, toolchain=toolchain
            )
    except ArtifactBuildError as exc:
        print(f"build-python-artifacts: FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0
