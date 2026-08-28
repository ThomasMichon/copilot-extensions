#!/usr/bin/env python3
"""Guard: one uniform, marker-only way to resolve a versioned runtime's python.

Every versioned-runtime plugin must resolve and spawn its interpreter the same
way -- the junction-free `current-version` marker chain (see
`libs/versioned-runtime/resolve-runtime.sh` and `versioned_runtime.resolve_python`).
This guard flags launch/spawn paths that diverge:

* resolving through a `venv`/`.venv` **link** (a reparse point on Windows that
  RedirectionGuard blocks) instead of the marker;
* falling back to a **PATH python** (`python3`/`python`) -- a service could come
  up under the system interpreter;
* referencing **another plugin's** runtime venv.

Legitimate exceptions -- a *durable* runtime's own venv outside the versioned
tree (see `docs/patterns/durable-vs-versioned-runtime.md`), a dev-only helper, or
a bootstrap that predates any slot -- opt out with an inline
``# runtime-resolution: allow`` (`# runtime-resolution: allow <reason>`) on the
offending line.

Usage::

    python tools/check-runtime-resolution.py            # report (exit 0)
    python tools/check-runtime-resolution.py --strict    # fail on any violation

This is **report-only** during the uniform-runtime-resolution migration (effort
`uniform-runtime-resolution`, #765); it becomes ``--strict`` in CI once every
plugin has migrated.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS = REPO / "plugins"

ALLOW = "runtime-resolution: allow"

# Scanned files: install/lifecycle scripts and deployed binstubs.
_SCAN_GLOBS = ("scripts/*.sh", "scripts/*.ps1", "bin/*")

# Resolving a python THROUGH a venv/.venv link (versioned runtimes must use the
# marker). `engine`/durable venvs are excluded by path; anything else opts out
# with the inline allow marker.
_LINK_PY = re.compile(
    r"""(?<![\w.])\.?venv[\\/](?:bin[\\/]python(?:3(?:\.\d+)?)?|Scripts[\\/]python(?:w)?\.exe)""",
    re.IGNORECASE,
)
# Resolving a plugin's **console script** through the same link (e.g.
# `$LINK_DIR/bin/agent-bridge`). Same defect as `_LINK_PY`, different spelling,
# and the one that actually shipped: `activate --no-link` leaves no link at all,
# so every such call is unreachable and the caller fails closed. Matches a
# non-python executable directly under a venv link's `bin/`|`Scripts/`.
_LINK_SCRIPT = re.compile(
    r"""(?:(?<![\w.])\.?venv|\$\{?LINK_DIR\}?)[\\/](?:bin|Scripts)[\\/](?!python)[\w.-]+""",
    re.IGNORECASE,
)
# A PATH python bound as a **launch** interpreter (never allowed) -- a launch
# variable or an exec target set to a bare `python`/`python3`. This deliberately
# does NOT flag bootstrap discovery (`py="$(command -v python3)"` to *build* the
# venv), which is legitimate: you need some python to create the versioned venv.
_PATH_PY = re.compile(
    r"""(?:^|[\s;])(?:_py|pybin|py_?bin|launch_?py|run_?host|runhost)\s*=\s*"""
    r"""["']?python3?["']?\s*(?:$|["'#;])"""
    r"""|(?:^|[\s;&])exec\s+["']?python3?["']?[\s"']""",
    re.IGNORECASE,
)
# Another plugin's runtime venv (cross-plugin runtime coupling).
_CROSS = re.compile(r"""\.agent-[a-z-]+[\\/]\.?venv[\\/]""", re.IGNORECASE)

# Path fragments that are legitimately a durable/dev/bootstrap venv, not the
# versioned runtime -- never flagged. Matched against BOTH the file path (to skip
# a whole dev-only helper file, e.g. preview-picker, whose printed setup
# instructions mention `.venv`) AND each line's content (to skip an inline durable
# engine venv reference).
_EXCLUDE_PATH = ("engine/.venv", "engine\\.venv", "preview-picker")


def _iter_targets() -> list[Path]:
    out: list[Path] = []
    if not PLUGINS.is_dir():
        return out
    for plugin in sorted(PLUGINS.iterdir()):
        if not plugin.is_dir():
            continue
        for pat in _SCAN_GLOBS:
            out.extend(sorted(plugin.glob(pat)))
    return out


def _strip_ps_block_comments(line: str, in_block: bool) -> tuple[str, bool]:
    """Remove PowerShell ``<# ... #>`` block-comment spans from a line.

    Returns the code with comment spans blanked out plus the updated in-block
    state (block comments may span multiple lines). Handles inline ``<# .. #>``,
    an opener with no closer (rest of line is comment), and multiple spans.
    """
    out: list[str] = []
    i = 0
    while i < len(line):
        if in_block:
            end = line.find("#>", i)
            if end == -1:
                return "".join(out), True
            in_block = False
            i = end + 2
        else:
            start = line.find("<#", i)
            if start == -1:
                out.append(line[i:])
                return "".join(out), False
            out.append(line[i:start])
            in_block = True
            i = start + 2
    return "".join(out), in_block


def _violations(path: Path) -> list[tuple[int, str, str]]:
    # Skip whole dev-only/durable helper files by path (e.g. preview-picker's
    # printed setup instructions, an engine durable venv helper).
    if any(x in path.as_posix().lower() for x in _EXCLUDE_PATH):
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    is_ps = path.suffix.lower() in (".ps1", ".psm1", ".psd1")
    found: list[tuple[int, str, str]] = []
    in_block = False
    for n, line in enumerate(text.splitlines(), 1):
        # Strip PowerShell block comments (<# .. #>) -- the line-based `#` skip
        # below only catches single-line comments, so a launch-looking path
        # quoted inside block-comment prose would otherwise be a false positive.
        code = line
        if is_ps:
            code, in_block = _strip_ps_block_comments(line, in_block)
        if ALLOW in line:
            continue
        stripped = code.strip()
        if not stripped:
            continue
        # Skip pure comments (documentation of the rule itself, etc.).
        if stripped.startswith("#"):
            continue
        low = code.lower()
        if any(x in low for x in _EXCLUDE_PATH):
            continue
        report = line.strip()[:160]
        if _LINK_PY.search(code):
            found.append((n, "venv-link", report))
        elif _LINK_SCRIPT.search(code):
            found.append((n, "venv-link-script", report))
        elif _CROSS.search(code):
            found.append((n, "cross-plugin-venv", report))
        elif _PATH_PY.search(code):
            found.append((n, "path-python", report))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any violation is found (CI mode)")
    args = ap.parse_args(argv)

    total = 0
    by_plugin: dict[str, int] = {}
    for path in _iter_targets():
        vio = _violations(path)
        if not vio:
            continue
        rel = path.relative_to(REPO)
        plugin = path.relative_to(PLUGINS).parts[0]
        by_plugin[plugin] = by_plugin.get(plugin, 0) + len(vio)
        total += len(vio)
        for n, kind, snippet in vio:
            print(f"{rel}:{n}: [{kind}] {snippet}")

    if total == 0:
        print("check-runtime-resolution: OK (no non-canonical launch paths).")
        return 0

    print()
    print(f"check-runtime-resolution: {total} non-canonical launch path(s) "
          f"across {len(by_plugin)} plugin(s):")
    for plugin in sorted(by_plugin):
        print(f"  {plugin}: {by_plugin[plugin]}")
    print("Migrate to the marker-only resolver (resolve-runtime.sh / "
          "versioned_runtime.resolve_python), or annotate a legitimate durable/"
          f"dev venv with '# {ALLOW}'. Effort: uniform-runtime-resolution (#765).")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
