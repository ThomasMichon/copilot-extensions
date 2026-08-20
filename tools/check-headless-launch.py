#!/usr/bin/env python3
"""Guard: an agent-procutil-adopting plugin must not hand-roll process-creation flags.

Windows-launch-hardening (ThomasMichon/copilot-extensions#786). Once a plugin
depends on the shared ``agent-procutil`` lib, every Windows console-suppression /
detach flag it needs is available through ``no_window_kwargs`` /
``detached_kwargs`` / ``no_window_flags``. Re-introducing a raw
``subprocess.CREATE_NO_WINDOW`` / ``DETACHED_PROCESS`` / ``CREATE_NEW_PROCESS_GROUP``
/ ``CREATE_BREAKAWAY_FROM_JOB`` literal in that plugin's ``src`` re-opens the
exact drift the lib was created to close, so this guard freezes it.

**Scope.** Only plugins whose ``pyproject.toml`` declares a dependency on
``agent-procutil`` are checked -- adoption is incremental, and a plugin that has
not migrated yet is not a failure here. (Repo-wide adoption of the remaining
plugins is tracked on #786.)

**AST-based**, so a docstring or comment that merely *names* a flag is never
flagged -- only real code (an attribute access like ``subprocess.CREATE_NO_WINDOW``,
a bare ``Name`` reference, or a ``getattr(subprocess, "CREATE_NO_WINDOW", ...)``
string) counts.

A genuinely-intentional low-level exception (e.g. the ``winjob`` job-object
primitive, or a deliberately-different detach) carries an inline
``# headless-guard: allow <why>`` comment on the offending line and is skipped.

Usage::

    python tools/check-headless-launch.py          # verify (CI / pre-push)
    python tools/check-headless-launch.py --list    # show the adopting plugins it checks
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"

_ALLOW = "headless-guard: allow"
_FLAG_NAMES = frozenset({
    "CREATE_NO_WINDOW",
    "DETACHED_PROCESS",
    "CREATE_NEW_PROCESS_GROUP",
    "CREATE_BREAKAWAY_FROM_JOB",
})
_SKIP_DIR_PARTS = {"libs", "tests", "build", "__pycache__", ".venv", ".venv-test", "dist"}


def _adopting_plugins() -> list[Path]:
    """Plugins whose pyproject.toml depends on agent-procutil."""
    out: list[Path] = []
    if not PLUGINS_DIR.is_dir():
        return out
    for plugin in sorted(PLUGINS_DIR.iterdir()):
        pp = plugin / "pyproject.toml"
        if pp.is_file() and "agent-procutil" in pp.read_text(encoding="utf-8"):
            out.append(plugin)
    return out


def _iter_py(src: Path):
    for f in src.rglob("*.py"):
        if _SKIP_DIR_PARTS & set(f.relative_to(src).parts):
            continue
        yield f


class _FlagFinder(ast.NodeVisitor):
    """Collect line numbers where a raw process-creation flag is referenced in code."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FLAG_NAMES:
            self.hits.append((node.lineno, node.attr))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FLAG_NAMES:
            self.hits.append((node.lineno, node.id))

    def visit_Constant(self, node: ast.Constant) -> None:
        # getattr(subprocess, "CREATE_NO_WINDOW", ...) -- the flag name as a string
        # literal. A docstring is one big Constant whose value is the prose, so it
        # never equals a bare flag name.
        if isinstance(node.value, str) and node.value in _FLAG_NAMES:
            self.hits.append((node.lineno, node.value))


def verify() -> list[str]:
    problems: list[str] = []
    for plugin in _adopting_plugins():
        src = plugin / "src"
        if not src.is_dir():
            continue
        for f in _iter_py(src):
            text = f.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text, filename=str(f))
            except SyntaxError:
                continue
            finder = _FlagFinder()
            finder.visit(tree)
            if not finder.hits:
                continue
            lines = text.splitlines()
            rel = f.relative_to(REPO).as_posix()
            for lineno, tok in sorted(set(finder.hits)):
                line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
                if _ALLOW in line:
                    continue
                problems.append(
                    f"{rel}:{lineno}: raw '{tok}' -- use agent_procutil "
                    f"(no_window_kwargs / detached_kwargs / no_window_flags), or add "
                    f"'# {_ALLOW} <why>'  ::  {line.strip()}"
                )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                    help="print the agent-procutil-adopting plugins this guard checks")
    args = ap.parse_args()
    if args.list:
        for p in _adopting_plugins():
            print(p.relative_to(REPO).as_posix())
        return 0
    problems = verify()
    if problems:
        print("check-headless-launch: FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nAn agent-procutil-adopting plugin must not hand-roll process-creation "
            "flags. Route them through agent_procutil's helpers, or mark a genuine "
            f"low-level exception with an inline '# {_ALLOW} <why>' comment.",
            file=sys.stderr,
        )
        return 1
    checked = len(_adopting_plugins())
    print(f"check-headless-launch: OK ({checked} agent-procutil-adopting plugins clean).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
