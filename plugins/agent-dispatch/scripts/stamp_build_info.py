#!/usr/bin/env python3
"""Stamp ``_build_info.py`` in a deployed ``agent_dispatch`` package.

Mirrors agent-worktrees' ``stamp_build_info``: ``pyproject.toml`` is the single
source of truth for the version; this bakes it -- plus git commit/branch,
build timestamp, and source path -- into the *deployed* package's
``_build_info.py`` so the runtime reports its version and provenance without
depending on ``importlib.metadata``.

Invoked by ``install.ps1`` / ``install.sh`` right after ``pip install`` copies
the package into the runtime slot. Stdlib-only (runs under the slot's venv
python, no third-party deps). Best-effort: any failure leaves the committed
placeholder in place, so ``_resolve_version`` simply falls through to metadata.

Usage::

    python stamp_build_info.py --package-dir <deployed agent_dispatch dir> \
        --plugin-dir <.../plugins/agent-dispatch> [--git-dir <repo root>]
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _read_pyproject_version(plugin_dir: Path) -> str:
    """Version from the ``[project]`` table of pyproject.toml, or ``""``.

    Scoped to ``[project]`` so a ``version`` key in some later ``[tool.*]`` table
    can't be mistaken for the package version. Returns an empty string on any
    failure so the caller stamps an empty version -- which makes
    ``_resolve_version`` fall through to package metadata rather than misreport a
    bogus literal.
    """
    pyproject = plugin_dir / "pyproject.toml"
    try:
        lines = pyproject.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    in_project = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project and stripped.startswith("version"):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _git(git_dir: Path | None, *args: str) -> str:
    if not git_dir:
        return "unknown"
    try:
        r = subprocess.run(  # noqa: S603 -- fixed git argv, best-effort provenance
            ["git", "-C", str(git_dir), *args],  # noqa: S607 -- git resolved from PATH
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def stamp(package_dir: Path, plugin_dir: Path, git_dir: Path | None) -> Path:
    version = _read_pyproject_version(plugin_dir)
    commit = _git(git_dir, "rev-parse", "HEAD")
    branch = _git(git_dir, "rev-parse", "--abbrev-ref", "HEAD")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source = str(git_dir or plugin_dir).replace("\\", "/")
    # Emit each value with ``!r`` so quotes/backslashes/newlines in a value
    # (odd branch name, unusual install path) can never produce invalid Python.
    content = (
        '"""Build provenance -- auto-generated at deploy time. Do not edit."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "BUILD_INFO: dict[str, str] = {\n"
        f"    'version': {version!r},\n"
        f"    'commit': {commit!r},\n"
        f"    'branch': {branch!r},\n"
        f"    'build_timestamp': {ts!r},\n"
        f"    'source': {source!r},\n"
        "}\n"
    )
    out = package_dir / "_build_info.py"
    out.write_text(content, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stamp agent_dispatch _build_info.py at deploy time")
    ap.add_argument(
        "--package-dir", required=True,
        help="the deployed agent_dispatch package directory (where _build_info.py lives)",
    )
    ap.add_argument(
        "--plugin-dir", required=True,
        help="the agent-dispatch plugin directory (reads pyproject.toml for the version)",
    )
    ap.add_argument(
        "--git-dir", default=None,
        help="repo root for git provenance (commit/branch); optional",
    )
    args = ap.parse_args(argv)
    out = stamp(
        Path(args.package_dir),
        Path(args.plugin_dir),
        Path(args.git_dir) if args.git_dir else None,
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
