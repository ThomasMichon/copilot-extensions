#!/usr/bin/env python3
"""Guard Windows background-process launch invariants.

Windows-launch-hardening (ThomasMichon/copilot-extensions#786). Once a plugin
depends on the shared ``agent-procutil`` lib, every Windows console-suppression /
detach flag it needs is available through ``no_window_kwargs`` /
``detached_kwargs`` / ``no_window_flags``. Re-introducing a raw
``subprocess.CREATE_NO_WINDOW`` / ``DETACHED_PROCESS`` / ``CREATE_NEW_PROCESS_GROUP``
/ ``CREATE_BREAKAWAY_FROM_JOB`` literal in that plugin's ``src`` re-opens the
exact drift the lib was created to close, so this guard freezes it.

Independently, ``CREATE_NEW_CONSOLE`` is forbidden in every production plugin
and canonical shared-library source. Windows Default Terminal can surface that
console even when the caller supplies ``SW_HIDE``; background work must use the
appropriate shared no-window primitive instead. A genuinely interactive launch
may use the inline escape hatch below.

**Scope.** The raw-flag rule applies only to plugins whose ``pyproject.toml``
declares ``agent-procutil`` -- adoption is incremental. The unsafe-new-console
rule applies to every ``plugins/*/src``, canonical ``libs/*/src``, shipped
``plugins/*/libs/*/src``, and ``plugins/*/scripts`` tree.

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
import io
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"

_ALLOW = "headless-guard: allow"
_UNSAFE_FLAG = "CREATE_NEW_CONSOLE"
_CREATE_NEW_CONSOLE_VALUE = 0x00000010
_FLAG_NAMES = frozenset({
    "CREATE_NO_WINDOW",
    "CREATE_NEW_CONSOLE",
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


def _production_src_roots() -> list[Path]:
    roots: list[Path] = []
    if PLUGINS_DIR.is_dir():
        for plugin in sorted(PLUGINS_DIR.iterdir()):
            for name in ("src", "scripts"):
                root = plugin / name
                if root.is_dir():
                    roots.append(root)
            libs = plugin / "libs"
            if libs.is_dir():
                for library in sorted(libs.iterdir()):
                    vendored_src = library / "src"
                    if vendored_src.is_dir():
                        roots.append(vendored_src)
    if LIBS_DIR.is_dir():
        for library in sorted(LIBS_DIR.iterdir()):
            src = library / "src"
            if src.is_dir():
                roots.append(src)
    return roots


class _FlagFinder(ast.NodeVisitor):
    """Collect line numbers where a raw process-creation flag is referenced in code."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []
        self.aliases: dict[str, str] = {}

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FLAG_NAMES:
            self.hits.append((node.lineno, node.attr))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        flag = node.id if node.id in _FLAG_NAMES else self.aliases.get(node.id)
        if flag:
            self.hits.append((node.lineno, flag))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in _FLAG_NAMES:
                self.aliases[alias.asname or alias.name] = alias.name
                self.hits.append((node.lineno, alias.name))

    def _record_assignment(
        self,
        target: ast.expr,
        value: ast.expr | None,
        lineno: int,
    ) -> None:
        if not isinstance(target, ast.Name) or value is None:
            return
        flag: str | None = None
        if isinstance(value, ast.Attribute) and value.attr in _FLAG_NAMES:
            flag = value.attr
        elif isinstance(value, ast.Name):
            flag = (
                value.id
                if value.id in _FLAG_NAMES
                else self.aliases.get(value.id)
            )
        elif (
            isinstance(value, ast.Constant)
            and value.value == _CREATE_NEW_CONSOLE_VALUE
            and target.id.upper().endswith(_UNSAFE_FLAG)
        ):
            flag = _UNSAFE_FLAG
        if flag:
            self.aliases[target.id] = flag
            self.hits.append((lineno, flag))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_assignment(target, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_assignment(node.target, node.value, node.lineno)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # getattr(subprocess, "CREATE_NO_WINDOW", ...) -- the flag name as a string
        # literal. A docstring is one big Constant whose value is the prose, so it
        # never equals a bare flag name.
        if isinstance(node.value, str) and node.value in _FLAG_NAMES:
            self.hits.append((node.lineno, node.value))


def _find_flags(f: Path) -> tuple[str, list[tuple[int, str]]]:
    text = f.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(f))
    except SyntaxError:
        return text, []
    finder = _FlagFinder()
    finder.visit(tree)
    return text, sorted(set(finder.hits))


def _allowed(lines: list[str], lineno: int) -> bool:
    line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(line).readline)
        comments = [tok.string for tok in tokens if tok.type == tokenize.COMMENT]
    except (IndentationError, tokenize.TokenError):
        return False
    for comment in comments:
        text = comment.removeprefix("#").strip()
        if not text.startswith(_ALLOW):
            continue
        suffix = text[len(_ALLOW):]
        if not suffix or suffix[0] not in " :":
            continue
        reason = suffix.lstrip(" :").strip()
        if reason:
            return True
    return False


def verify() -> list[str]:
    problems: list[str] = []

    # CREATE_NEW_CONSOLE is unsafe for background work regardless of whether a
    # package has adopted agent-procutil. Scan canonical shared libs as well as
    # plugin source so a vendored primitive cannot bypass the adoption gate.
    for src in _production_src_roots():
        for f in _iter_py(src):
            text, hits = _find_flags(f)
            lines = text.splitlines()
            rel = f.relative_to(REPO).as_posix()
            for lineno, tok in hits:
                if tok != _UNSAFE_FLAG or _allowed(lines, lineno):
                    continue
                line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
                problems.append(
                    f"{rel}:{lineno}: unsafe '{tok}' -- Windows Default Terminal "
                    "may surface it even with SW_HIDE; use a shared no-window "
                    f"primitive, or add '# {_ALLOW} <interactive reason>'  ::  "
                    f"{line.strip()}"
                )

    for plugin in _adopting_plugins():
        src = plugin / "src"
        if not src.is_dir():
            continue
        for f in _iter_py(src):
            text, hits = _find_flags(f)
            if not hits:
                continue
            lines = text.splitlines()
            rel = f.relative_to(REPO).as_posix()
            for lineno, tok in hits:
                if tok == _UNSAFE_FLAG:
                    continue  # The stronger repository-wide rule reports it.
                line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
                if _allowed(lines, lineno):
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
            "\nBackground launches must not allocate a new Windows console, and an "
            "agent-procutil-adopting plugin must not hand-roll process-creation "
            "flags. Route launches through the shared helpers, or mark a genuine "
            f"interactive/low-level exception with '# {_ALLOW} <why>'.",
            file=sys.stderr,
        )
        return 1
    checked = len(_adopting_plugins())
    roots = len(_production_src_roots())
    print(
        "check-headless-launch: OK "
        f"({checked} agent-procutil adopters; {roots} production source trees)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
