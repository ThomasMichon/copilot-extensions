"""Command-line policy and toolchain lifetime for the Python artifact builder."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from build_toolchain_lock import ArtifactBuildError, resolve_toolchain_lock, sanitize_subprocess_env


def _core_in_toolchain(args, toolchain, venv: Path) -> dict:
    command = [
        str(toolchain.venv_python), str(Path(__file__).with_name("build_python_artifacts.py")),
        "core", "--core", "--out-dir", str(args.out_dir),
        "--toolchain-venv", str(venv), "--python", args.python or toolchain.record["provenance"]["python"],
        "--toolchain-source", args.toolchain_source,
    ]
    if args.toolchain_min_age_days is not None:
        command.extend(["--toolchain-min-age-days", str(args.toolchain_min_age_days)])
    result = subprocess.run(
        command, env=sanitize_subprocess_env(), capture_output=True, text=True, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise ArtifactBuildError(f"core build in locked toolchain failed:\n{result.stderr}")
    try:
        manifest = json.loads(result.stdout)
        if not isinstance(manifest, dict) or manifest.get("build_toolchain") != toolchain.record:
            raise ValueError("core builder changed its locked toolchain")
        return manifest
    except (ValueError, TypeError) as exc:
        raise ArtifactBuildError("invalid core manifest from locked toolchain") from exc


def main(builder, description: str, *, standalone_builder=None, core_builder=None) -> int:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("plugin", help="plugin name under plugins/")
    kind = ap.add_mutually_exclusive_group()
    kind.add_argument("--standalone", action="store_true", help="build a registered standalone component")
    kind.add_argument("--core", action="store_true", help="build the consolidated first-party core")
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
    if args.core:
        if args.plugin != "core" or core_builder is None:
            ap.error("--core requires the core composition name")
        def builder(_name, **kwargs):
            return core_builder(**kwargs)
    if args.toolchain_min_age_days is not None and args.toolchain_source != "seasoned-public":
        ap.error("--toolchain-min-age-days requires --toolchain-source seasoned-public")
    try:
        if args.toolchain_venv is None and args.toolchain_source == "governed" and not args.core:
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
            if args.core and tuple(map(int, toolchain.marker_environment["python_version"].split("."))) < (3, 11):
                raise ArtifactBuildError("core construction requires Python 3.11 or newer")
            # Compare venv roots, not executable targets: POSIX venv Python may be a symlink.
            if args.core and Path(sys.prefix).resolve() != toolchain.venv_python.parent.parent.resolve():
                manifest = _core_in_toolchain(args, toolchain, venv)
            else:
                manifest = builder(
                    args.plugin, out_dir=args.out_dir, python=args.python, toolchain=toolchain
                )
    except (ArtifactBuildError, OSError) as exc:
        print(f"build-python-artifacts: FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0
