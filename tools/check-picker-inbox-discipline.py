#!/usr/bin/env python3
"""Guard the Picker's inbox-only cross-thread marshalling invariant.

Per the Picker render-flow invariant (``plugins/agent-worktrees/docs/
architecture.md``'s "never block on cross-process/IO" section): no
background producer of a UI update may marshal that update back to the
render thread any way other than ``Inbox.post()``
(``worktree-manager/src/worktree_manager/production_picker/picker_tui/
inbox.py``). Before ``Inbox`` existed, several call sites hand-rolled their
own ``app.call_from_thread(...)`` + thread/cancellation bookkeeping -- each
a fresh place to get the thread-safety, the error handling, or the
diagnosability contract (see #5220) subtly wrong. ``Inbox`` is now the one
sanctioned mechanism (addressed slots, coalesced wake, home-thread
immediate-apply shortcut, a ``bool`` wake-success signal); a raw
``call_from_thread(`` call anywhere in the Picker's own source re-opens
exactly the drift ``Inbox`` was built to close.

**Scope.** Only ``Inbox`` itself (``inbox.py``) may call
``call_from_thread`` -- that module IS the primitive. Everything else under
``picker_tui/`` must route through ``Inbox``/``background.run_background``
instead.

**AST-based**, so a docstring or comment that merely *names*
``call_from_thread`` is never flagged -- only a real call
(``self.app.call_from_thread(...)``, a bound/aliased reference, etc.)
counts.

A genuinely-intentional low-level exception carries an inline
``# inbox-guard: allow <why>`` comment on the offending line and is
skipped -- expect this to be rare; prefer extending ``Inbox`` itself over
adding an exception here.

Usage::

    python tools/check-picker-inbox-discipline.py          # verify (CI / pre-push)
    python tools/check-picker-inbox-discipline.py --list    # show the files it scans
"""
from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PICKER_TUI_DIR = (
    REPO / "worktree-manager" / "src" / "worktree_manager" / "production_picker"
    / "picker_tui"
)

_ALLOW = "inbox-guard: allow"
_FLAGGED_ATTR = "call_from_thread"
# The primitive itself is the one module allowed to call it -- that call IS
# the sanctioned wake mechanism Inbox wraps for everyone else.
_EXEMPT_FILENAMES = frozenset({"inbox.py"})


def _iter_py():
    if not PICKER_TUI_DIR.is_dir():
        return
    for f in sorted(PICKER_TUI_DIR.rglob("*.py")):
        if f.name in _EXEMPT_FILENAMES:
            continue
        yield f


class _CallFinder(ast.NodeVisitor):
    """Collect line numbers where ``call_from_thread`` is called (not just
    referenced -- a bound-method reference with no call is not itself a
    marshalling attempt, though in practice this call is always invoked
    directly)."""

    def __init__(self) -> None:
        self.hits: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name == _FLAGGED_ATTR:
            self.hits.append(node.lineno)
        self.generic_visit(node)


def _find_calls(f: Path) -> tuple[str, list[int]]:
    text = f.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(f))
    finder = _CallFinder()
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
    for f in _iter_py():
        try:
            text, hits = _find_calls(f)
        except SyntaxError as exc:
            rel = f.relative_to(REPO).as_posix()
            problems.append(
                f"{rel}:{exc.lineno or 1}: cannot parse Picker source; "
                f"inbox discipline cannot be verified: {exc.msg}"
            )
            continue
        if not hits:
            continue
        lines = text.splitlines()
        rel = f.relative_to(REPO).as_posix()
        for lineno in hits:
            if _allowed(lines, lineno):
                continue
            line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            problems.append(
                f"{rel}:{lineno}: raw 'call_from_thread(' -- route this "
                "through Inbox.post()/background.run_background() instead, "
                f"or add '# {_ALLOW} <why>'  ::  {line.strip()}"
            )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true",
                     help="print the files this guard scans")
    args = ap.parse_args()
    if args.list:
        for f in _iter_py():
            print(f.relative_to(REPO).as_posix())
        return 0
    if not PICKER_TUI_DIR.is_dir():
        print(
            "check-picker-inbox-discipline: SKIPPED (picker_tui not present "
            "in this checkout)."
        )
        return 0
    problems = verify()
    if problems:
        print("check-picker-inbox-discipline: FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nEvery background producer of a Picker UI update must marshal "
            "back to the render thread through Inbox.post() (or "
            "background.run_background(), which already does) -- never a "
            "raw app.call_from_thread(...). Mark a genuinely-intentional "
            f"low-level exception with '# {_ALLOW} <why>'.",
            file=sys.stderr,
        )
        return 1
    print(f"check-picker-inbox-discipline: OK ({len(list(_iter_py()))} files scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
