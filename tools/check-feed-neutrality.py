#!/usr/bin/env python3
"""Guard against a hardcoded public package-feed URL landing outside a real
override mechanism.

This repo is cloned and run on machines whose default package feed is
network-blocked and replaced with an internal mirror (see #2389 / the
aperture-labs "feed-neutral-build-config" effort that motivated this guard,
and ``tools/clean-room``'s own ``--block-public-feeds`` mode, which reproduces
that condition in the validation harness). A build/install/CI file that
hardcodes ``pypi.org``, ``files.pythonhosted.org``, ``registry.npmjs.org``, or
``download.pytorch.org`` as the *only* usable endpoint breaks on such a
machine.

**Scope.** Config/Dockerfile/install-script/CI-workflow files only:
``pyproject.toml``, ``uv.toml``, ``.npmrc``, ``package.json``,
``Dockerfile*``, ``*.sh``, ``*.ps1``, and ``.github/workflows/*.yml``/``.yaml``.
Never ``package-lock.json`` (npm's lockfile is inherently saturated with
resolved public URLs by design -- that is a distinct, out-of-scope concern).

**What counts as a violation.** A line containing a known public feed
hostname as part of a URL, unless it sits behind one of:

1. A Dockerfile ``ARG NAME=<url>`` default declaration (quoted or unquoted) --
   overridable with ``--build-arg NAME=<internal-url>``.
2. A shell/POSIX default-value parameter expansion, e.g.
   ``VAR="${VAR:-<url>}"`` -- overridable by exporting the var.
3. A PowerShell null-coalescing default, e.g. ``$var = $x ?? '<url>'``.
4. A line whose stripped content starts with ``#`` (a comment).
5. An inline ``# feed-guard: allow <reason>`` (or PowerShell ``# feed-guard:
   allow <reason>``) escape hatch, matching this repo's established
   ``<guard-name>: allow <why>`` convention (see
   ``check-headless-launch.py``) for a deliberate, reviewed exception (e.g.
   ``agent_codespaces.platform_preflight``'s intentional public-npm repair
   path for a CodeSpace whose *private* feed default is broken -- the mirror
   image of this guard's usual concern, not a violation of it).

Usage::

    python tools/check-feed-neutrality.py           # whole tracked tree
    python tools/check-feed-neutrality.py --base <ref>   # PR diff only
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PUBLIC_FEED_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "download.pytorch.org",
)

_SCAN_GLOBS = (
    "pyproject.toml",
    "uv.toml",
    ".npmrc",
    "package.json",
    "Dockerfile",
    "Dockerfile.*",
    "*.sh",
    "*.ps1",
)
_SCAN_DIR_EXCLUDE_PARTS = {"node_modules", ".venv", ".venv-test", "build", "dist", "__pycache__"}

_ALLOW = "feed-guard: allow"

_URL_RE = re.compile(r"https?://([A-Za-z0-9.\-]+)(?::\d+)?(?:/[^\s\"'`)]*)?")
_DOCKER_ARG_PREFIX_RE = re.compile(r"ARG\s+\w+\s*=\s*['\"]?$")
_SHELL_DEFAULT_PREFIX_RE = re.compile(r"\$\{?\w+:-\s*['\"]?$")
_PS_COALESCE_PREFIX_RE = re.compile(r"\?\?\s*['\"]?$")


def _iter_candidate_files() -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in _SCAN_GLOBS:
        for f in REPO.rglob(pattern):
            if _SCAN_DIR_EXCLUDE_PARTS & set(f.relative_to(REPO).parts):
                continue
            if f in seen:
                continue
            seen.add(f)
            files.append(f)
    workflows = REPO / ".github" / "workflows"
    if workflows.is_dir():
        for pattern in ("*.yml", "*.yaml"):
            for f in workflows.glob(pattern):
                if f not in seen:
                    seen.add(f)
                    files.append(f)
    return sorted(files)


def _diff_files(base: str) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACM", f"{base}...HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return _iter_candidate_files()
    changed = {REPO / line for line in out.splitlines() if line.strip()}
    return sorted(f for f in _iter_candidate_files() if f in changed)


def _match_is_exempt(line: str, match: re.Match, allow_comment: bool) -> bool:
    if allow_comment:
        return True
    prefix = line[: match.start()]
    if _DOCKER_ARG_PREFIX_RE.search(prefix):
        return True
    if _SHELL_DEFAULT_PREFIX_RE.search(prefix):
        return True
    if _PS_COALESCE_PREFIX_RE.search(prefix):
        return True
    return False


def _line_has_allow_comment(line: str) -> bool:
    # Search every '#' from the right so an in-URL '#' (e.g. a pip VCS/egg
    # fragment like "...#egg=x") before the real trailing comment cannot
    # shadow a genuine allow-comment later on the same line.
    start = len(line)
    while True:
        idx = line.rfind("#", 0, start)
        if idx == -1:
            return False
        text = line[idx + 1:].strip()
        if text.startswith(_ALLOW):
            suffix = text[len(_ALLOW):]
            if suffix and suffix[0] in " :" and suffix.lstrip(" :").strip():
                return True
        start = idx


def scan_text(text: str, *, rel: str) -> list[str]:
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        allow_comment = _line_has_allow_comment(line)
        for match in _URL_RE.finditer(line):
            host = match.group(1).lower()
            if host not in PUBLIC_FEED_HOSTS:
                continue
            if _match_is_exempt(line, match, allow_comment):
                continue
            problems.append(
                f"{rel}:{lineno}: hardcoded public feed host '{host}' -- "
                f"wrap it behind a Dockerfile ARG default, a shell "
                f"\"${{VAR:-<url>}}\" expansion, a PowerShell '?? <url>' "
                f"default, or add '# {_ALLOW} <why>'  ::  {stripped}"
            )
    return problems


def verify(*, base: str | None = None) -> list[str]:
    files = _diff_files(base) if base else _iter_candidate_files()
    problems: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(REPO).as_posix()
        problems.extend(scan_text(text, rel=rel))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="only check files changed vs this ref (PR diff)")
    args = ap.parse_args()
    problems = verify(base=args.base)
    if problems:
        print("check-feed-neutrality: FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nA hardcoded public package-feed URL breaks on a machine whose "
            "default feed is network-blocked. Parameterize it (Dockerfile ARG, "
            f"shell/PowerShell default expansion), or mark a deliberate "
            f"exception with '# {_ALLOW} <why>'.",
            file=sys.stderr,
        )
        return 1
    scope = "PR diff" if args.base else "whole tree"
    print(f"check-feed-neutrality: OK ({scope}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
