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
(``self.app.call_from_thread(...)``, or a call through a locally-assigned
alias, e.g. ``marshal = self.app.call_from_thread; marshal(fn)``) counts.

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


def _param_names(args: ast.arguments) -> set[str]:
    """Every name a function/lambda's own parameter list binds -- always a
    fresh binding, never a continuation of some outer-scope alias of the
    same name, regardless of what that outer scope calls it."""
    names: set[str] = set()
    for group in (args.posonlyargs, args.args, args.kwonlyargs):
        names.update(a.arg for a in group)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


class _CallFinder(ast.NodeVisitor):
    """Collect line numbers where ``call_from_thread`` is called (not just
    referenced -- a bound-method reference with no call is not itself a
    marshalling attempt, though in practice this call is always invoked
    directly), including through a locally-assigned alias (e.g.
    ``marshal = self.app.call_from_thread; marshal(fn)``).

    Aliases are tracked **per lexical scope** (module level, and freshly
    for each function/method/lambda body), but each nested scope starts
    as a *copy* of its immediately enclosing scope's aliases -- so a
    closure can still see an alias from an outer function (e.g.
    ``marshal = app.call_from_thread`` at module/outer-function level,
    then called from a nested ``def worker(): marshal(fn)``), exactly the
    way a real nested-function reference would actually resolve the name
    at runtime. A parameter of the nested function sharing that same name
    is a fresh, unrelated binding regardless of the outer alias, so it is
    explicitly excluded from the copied-in scope. Reassigning an
    already-tracked alias name to something else (a non-``call_from_thread``
    value) clears it within its own scope -- never the enclosing scope --
    so it stops being flagged only from that point on, in that scope.
    """

    def __init__(self) -> None:
        self.hits: list[int] = []
        self._scopes: list[set[str]] = [set()]

    @property
    def _aliases(self) -> set[str]:
        return self._scopes[-1]

    def _visit_new_scope(self, node: ast.AST, shadowed: set[str]) -> None:
        # Inherit a COPY of the enclosing scope's aliases (a real nested
        # function/closure can reference an outer-scope name), minus any
        # name this scope's own parameters rebind -- a parameter is always
        # a fresh binding, never a continuation of an outer alias.
        self._scopes.append(self._aliases - shadowed)
        try:
            self.generic_visit(node)
        finally:
            self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_new_scope(node, _param_names(node.args))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_new_scope(node, _param_names(node.args))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_new_scope(node, _param_names(node.args))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        flagged = False
        if isinstance(func, ast.Attribute):
            flagged = func.attr == _FLAGGED_ATTR
        elif isinstance(func, ast.Name):
            flagged = func.id == _FLAGGED_ATTR or func.id in self._aliases
        if flagged:
            self.hits.append(node.lineno)
        self.generic_visit(node)

    def _record_alias(self, target: ast.expr, value: ast.expr | None) -> None:
        if not isinstance(target, ast.Name) or value is None:
            return
        # `marshal = self.app.call_from_thread` (any attribute chain ending
        # in the flagged attribute) or `marshal = call_from_thread` (an
        # alias of an alias).
        if isinstance(value, ast.Attribute) and value.attr == _FLAGGED_ATTR:
            self._aliases.add(target.id)
        elif isinstance(value, ast.Name) and (
            value.id == _FLAGGED_ATTR or value.id in self._aliases
        ):
            self._aliases.add(target.id)
        else:
            # A non-aliasing reassignment of a previously-tracked name
            # shadows it -- the name no longer refers to call_from_thread
            # from this point on in this scope.
            self._aliases.discard(target.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_alias(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_alias(node.target, node.value)
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
