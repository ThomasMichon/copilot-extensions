#!/usr/bin/env python3
"""Run a registered standalone distribution's tests with host containment."""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _devcontainer_host_admission import acquire
from plugin_test_containment import ContainmentError, Limits, isolated_environment, run_contained
from standalone_consumers import STANDALONE_CONSUMERS

REPO = Path(__file__).resolve().parent.parent


def default_python(component: str) -> Path:
    root = REPO / ".test-venvs" / sys.platform / component
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare(component: str, python: Path) -> None:
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    root = python.parent.parent
    if not python.is_file():
        subprocess.run(
            ["uv", "venv", str(root)], cwd=REPO, check=True,
            capture_output=True, text=True, **flags,
        )
    sources: list[str] = []
    if component == "agent-index-service":
        sources = [
            str(REPO / "libs" / "zdd"),
            str(REPO / "libs" / "agent-procutil"),
            str(REPO / "plugins" / "agent-index" / "libs" / "dropin-registry"),
            str(REPO / "plugins" / "agent-index") + "[store,server]",
        ]
    extra = "native,test" if component == "agent-index-service" else "dev"
    sources.append(str(REPO / component) + f"[{extra}]")
    with tempfile.TemporaryDirectory(prefix="standalone-dependencies-") as temporary:
        overrides: list[str] = []
        if component == "agent-index-service":
            # dev freezes source versions; test the local compatibility seam,
            # not an unrelated released wheel chosen to satisfy that floor.
            override = Path(temporary) / "overrides.txt"
            core = (REPO / "plugins" / "agent-index").resolve().as_uri()
            override.write_text(f"agent-index[store,server] @ {core}\n", encoding="utf-8")
            overrides = ["--override", str(override)]
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), *overrides, *sources],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
            **flags,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=STANDALONE_CONSUMERS)
    parser.add_argument("--python", type=Path, help="test interpreter (default: managed venv)")
    parser.add_argument("--prepare", action="store_true", help="provision declared test dependencies")
    parser.add_argument("--smoke", action="store_true", help="run controller contracts without real deployment cycles")
    parser.add_argument("--admission-wait", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if args.smoke and args.component != "agent-index-service":
        parser.error("--smoke is only defined for agent-index-service")
    python = (args.python or default_python(args.component)).resolve()
    root = REPO / args.component
    if args.prepare and not python.is_relative_to((REPO / ".test-venvs").resolve()):
        parser.error("--prepare may only provision an interpreter under repository .test-venvs")
    if not (root / "tests").is_dir() or (not args.prepare and not python.is_file()):
        parser.error("component tests and a prepared interpreter are required; use --prepare")
    limits = Limits(wall_seconds=args.timeout)
    try:
        limits.validate()
    except ValueError as exc:
        parser.error(str(exc))
    lease = acquire(args.admission_wait)
    try:
        if args.prepare:
            prepare(args.component, python)
        with tempfile.TemporaryDirectory(prefix=f"{args.component}-tests-") as temporary:
            sandbox = Path(temporary)
            env = isolated_environment(os.environ, sandbox)
            tests = (
                [root / "tests" / "test_cli.py", root / "tests" / "test_config.py"]
                if args.smoke else [root / "tests"]
            )
            return run_contained(
                [str(python), "-I", "-m", "pytest", "-q", *map(str, tests),
                 "--basetemp", str(sandbox / "pytest")],
                cwd=root,
                env=env,
                sandbox=sandbox,
                limits=limits,
            )
    except (ContainmentError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"standalone tests: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            for output in (exc.stdout, exc.stderr):
                if output:
                    print(output, file=sys.stderr)
        return 1
    finally:
        lease.release()


if __name__ == "__main__":
    raise SystemExit(main())
