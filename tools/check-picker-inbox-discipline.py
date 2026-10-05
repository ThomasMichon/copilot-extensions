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
Alias tracking is scope-aware: a nested function/lambda inherits a copy of
its enclosing scope's aliases (a real closure genuinely resolves an
outer-scope name at runtime) while its own parameters always shadow it
regardless of name reuse, and a reassignment inside a conditional
(``if``/``try``/``for``/``while``/``with``) is never allowed to
permanently clear an alias for code after it -- some other branch (or
none) might still leave it aliased, so alias state is conservatively
merged after such a node rather than taking whichever branch happened to
be visited last. The inline escape hatch below is recognized even on the
opening line of a call whose arguments span multiple lines.

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


def _iter_py():
    if not PICKER_TUI_DIR.is_dir():
        return
    # The primitive itself is the one module allowed to call
    # `call_from_thread` -- that call IS the sanctioned wake mechanism
    # `Inbox` wraps for everyone else. Compared by full, resolved path
    # (never a bare basename, and computed fresh from the current
    # `PICKER_TUI_DIR` rather than cached at import time -- tests
    # monkeypatch `PICKER_TUI_DIR` to a throwaway directory): the scan is
    # recursive, so a basename-only check would also silently exempt any
    # future unrelated nested module that merely happens to be named the
    # same (`picker_tui/**/inbox.py`), even though only this one root
    # primitive is sanctioned.
    exempt = (PICKER_TUI_DIR / "inbox.py").resolve()
    for f in sorted(PICKER_TUI_DIR.rglob("*.py")):
        if f.resolve() == exempt:
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


def _iter_direct_statements(stmts: list[ast.stmt]):
    """Yield every statement directly belonging to this scope -- recursing
    into control-flow bodies (``if``/``try``/``for``/``while``/``with``,
    which share the SAME scope as their surrounding code) but never into a
    nested ``def``/``async def``/``lambda`` (a separate scope with its own
    analysis)."""
    for stmt in stmts:
        yield stmt
        if isinstance(stmt, ast.If):
            yield from _iter_direct_statements(stmt.body)
            yield from _iter_direct_statements(stmt.orelse)
        elif isinstance(stmt, ast.Try):
            yield from _iter_direct_statements(stmt.body)
            for handler in stmt.handlers:
                yield from _iter_direct_statements(handler.body)
            yield from _iter_direct_statements(stmt.orelse)
            yield from _iter_direct_statements(stmt.finalbody)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            yield from _iter_direct_statements(stmt.body)
            yield from _iter_direct_statements(stmt.orelse)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            yield from _iter_direct_statements(stmt.body)
        elif isinstance(stmt, getattr(ast, "Match", ())):
            for case in stmt.cases:
                yield from _iter_direct_statements(case.body)


def _direct_assign_targets_and_values(stmt: ast.stmt):
    """``(target, value)`` pairs for every ``Name`` an ``Assign``/
    ``AnnAssign`` in *this exact statement* binds -- tuple/list-unpacking
    targets are expanded element-wise (mirroring ``_record_alias``'s own
    literal-pairing rule; an opaque unpacking yields nothing, the same
    conservative choice made there)."""
    if isinstance(stmt, ast.Assign):
        targets, value = stmt.targets, stmt.value
    elif isinstance(stmt, ast.AnnAssign):
        targets, value = [stmt.target], stmt.value
    else:
        return
    if value is None:
        return
    for target in targets:
        if isinstance(target, (ast.Tuple, ast.List)):
            if (
                isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
                and not any(isinstance(e, ast.Starred) for e in target.elts)
            ):
                for sub_target, sub_value in zip(target.elts, value.elts):
                    if isinstance(sub_target, ast.Name):
                        yield sub_target.id, sub_value
            continue
        if isinstance(target, ast.Name):
            yield target.id, value


def _direct_aliases_in_body(body: list[ast.stmt]) -> set[str]:
    """Every name that will EVENTUALLY be a ``call_from_thread`` alias
    somewhere directly in *body* (never inside a nested function/lambda's
    own body), regardless of textual order or control flow.

    A nested function is a real Python closure: it resolves a free
    variable from the enclosing scope at CALL time, not at its own
    *definition* time. So a closure defined BEFORE a same-scope alias
    assignment can still observe that alias perfectly well, as long as it
    is actually called after the assignment runs -- which the guard has
    no way to rule out. Pre-scanning the whole body up front (a fixed
    point over possibly-chained aliases, e.g. ``also = marshal``) lets a
    nested scope see every alias its enclosing scope will EVER hold,
    regardless of where in the source it happens to be assigned relative
    to the nested ``def``.
    """
    found: set[str] = set()
    changed = True
    while changed:
        changed = False
        for stmt in _iter_direct_statements(body):
            for name, value in _direct_assign_targets_and_values(stmt):
                if name in found:
                    continue
                is_alias = (
                    isinstance(value, ast.Attribute) and value.attr == _FLAGGED_ATTR
                ) or (
                    isinstance(value, ast.Name)
                    and (value.id == _FLAGGED_ATTR or value.id in found)
                )
                if is_alias:
                    found.add(name)
                    changed = True
            # A walrus binding can appear anywhere *within* a statement's
            # own expressions (a condition, a call argument, ...), not
            # only as a statement of its own -- `ast.walk` over the whole
            # statement finds it regardless of position. This can also
            # walk into a nested lambda's own body (a separate scope) in
            # rare cases; erring toward finding one extra alias there is
            # the same safe direction this guard takes everywhere else.
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.NamedExpr) and isinstance(
                    sub.target, ast.Name
                ):
                    value = sub.value
                    is_alias = (
                        isinstance(value, ast.Attribute)
                        and value.attr == _FLAGGED_ATTR
                    ) or (
                        isinstance(value, ast.Name)
                        and (value.id == _FLAGGED_ATTR or value.id in found)
                    )
                    if is_alias and sub.target.id not in found:
                        found.add(sub.target.id)
                        changed = True
    return found


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
    at runtime. This holds even when the alias is assigned LATER in the
    enclosing scope's source than the nested ``def`` -- a real closure
    resolves a free variable at CALL time, not at its own definition
    time, so each scope is pre-scanned (``_direct_aliases_in_body``) for
    every alias it will EVENTUALLY hold before any nested scope is ever
    constructed from it. A parameter of the nested function sharing that
    same name is a fresh, unrelated binding regardless of the outer
    alias, so it is explicitly excluded from the copied-in scope.
    Reassigning an already-tracked alias name to something else (a
    non-``call_from_thread`` value) clears it within its own scope --
    never the enclosing scope -- so it stops being flagged only from
    that point on, in that scope.
    """

    def __init__(self) -> None:
        self.hits: list[int] = []
        self._scopes: list[set[str]] = [set()]

    @property
    def _aliases(self) -> set[str]:
        return self._scopes[-1]

    def _visit_new_scope(
        self, node: ast.AST, shadowed: set[str], body: list[ast.stmt] | None = None
    ) -> None:
        # Inherit a COPY of the enclosing scope's aliases (a real nested
        # function/closure can reference an outer-scope name), minus any
        # name this scope's own parameters rebind -- a parameter is always
        # a fresh binding, never a continuation of an outer alias. Also
        # pre-scan THIS scope's own body (if it has one -- a lambda's
        # "body" is a single expression, never an assignment) for every
        # alias it will eventually hold, so a nested function defined
        # BEFORE a same-scope alias assignment can still see it -- a real
        # closure resolves a free variable at CALL time, not at its own
        # definition time.
        seed = self._aliases - shadowed
        if body is not None:
            seed = seed | (_direct_aliases_in_body(body) - shadowed)
        self._scopes.append(seed)
        try:
            self.generic_visit(node)
        finally:
            self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_new_scope(node, _param_names(node.args), node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_new_scope(node, _param_names(node.args), node.body)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_new_scope(node, _param_names(node.args))

    def visit_Module(self, node: ast.Module) -> None:
        # Seed the module-level scope the same way: a module-level alias
        # assigned AFTER a nested function's own definition is still
        # visible to it at call time.
        self._scopes[0] |= _direct_aliases_in_body(node.body)
        self.generic_visit(node)

    def _visit_conditionally(self, node: ast.AST) -> None:
        """Visit a control-flow node with a single body (``with``) whose
        body may or may not actually execute at runtime -- a reassignment
        inside it must never be allowed to permanently clear an alias that
        held before it, since the body might raise partway through or
        (conceptually) not run at all. Calls inside are still visited and
        flagged normally; only the ALIAS STATE afterward is conservatively
        merged: anything aliased before the node, or newly aliased inside
        it, stays aliased after -- only a plain, unconditional (outside any
        such node) reassignment ever actually clears an alias.
        """
        before = set(self._aliases)
        self.generic_visit(node)
        self._scopes[-1] = before | self._aliases

    def _merge_branches(self, *branches: list[ast.stmt]) -> None:
        """Visit each MUTUALLY EXCLUSIVE branch (an ``if``/``else`` body, a
        ``try``'s body and each ``except`` handler, ...) independently,
        every one starting from the SAME pre-branch alias state, then
        merge the results by union.

        Visiting branches sequentially against one shared, mutable alias
        set (the naive approach) lets an EARLIER branch's reassignment
        hide a real call in a LATER, independently-reachable branch --
        e.g. ``if use_safe: marshal = safe`` clearing ``marshal`` before
        an ``else: marshal(fn)`` is ever visited, even though at runtime
        the ``else`` branch running means that reassignment never
        happened. Each branch must instead see the alias state as it
        actually was at the point control flow forked, not whatever an
        unrelated sibling branch happened to leave behind.
        """
        start = set(self._aliases)
        merged = set(start)
        for stmts in branches:
            self._scopes[-1] = set(start)
            for stmt in stmts:
                self.visit(stmt)
            merged |= self._aliases
        self._scopes[-1] = merged

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._merge_branches(node.body, node.orelse)

    def visit_Try(self, node: ast.Try) -> None:
        # The try body may raise partway through -- a handler can
        # therefore observe any PREFIX of the body's own assignments (an
        # exception after the 3rd statement still leaves the first two's
        # aliasing effects live when the handler runs). Conservatively
        # let every handler start from the union of the pre-try state and
        # the FULLY-completed body's state, rather than just the pre-try
        # state alone -- erring toward seeing MORE of the body's aliases,
        # never fewer.
        start = set(self._aliases)
        self._scopes[-1] = set(start)
        for stmt in node.body:
            self.visit(stmt)
        body_state = set(self._aliases)
        handler_start = start | body_state
        merged = set(body_state)
        for h in node.handlers:
            self._scopes[-1] = set(handler_start)
            for stmt in h.body:
                self.visit(stmt)
            merged |= self._aliases
        self._scopes[-1] = merged
        if node.orelse:
            # `else` is NOT a further alternative alongside the handlers
            # -- it runs only as a CONTINUATION after the body completes
            # fully, successfully, with no exception at all, so it must
            # be analyzed against the body's own resulting alias state
            # (not the handler-merged state, and not the pre-try state).
            merged_without_else = set(self._scopes[-1])
            self._scopes[-1] = set(body_state)
            for stmt in node.orelse:
                self.visit(stmt)
            self._scopes[-1] = merged_without_else | self._aliases
        # `finally` always runs regardless of which branch above executed,
        # after whichever one did -- visit it sequentially against the
        # merged outcome, not as another alternative branch.
        for stmt in node.finalbody:
            self.visit(stmt)

    def _visit_loop(self, iter_or_test: ast.expr, body: list[ast.stmt],
                     orelse: list[ast.stmt]) -> None:
        """Shared ``for``/``while`` handling: the body may run zero or
        more times, and ``else`` runs after it completes without
        ``break`` -- NOT as an alternative fork to the body (a loop
        ``else`` is not mutually exclusive with its body; it commonly
        runs immediately after it). ``else`` is analyzed from the union
        of "the body never ran" and "the body's own resulting state"
        (one pass through the body is enough to capture its aliasing
        effect -- re-running the same statements again doesn't change
        that fixed point), and the overall exit state (for code after the
        loop) is the union of all three possible outcomes.
        """
        self.visit(iter_or_test)
        start = set(self._aliases)
        self._scopes[-1] = set(start)
        for stmt in body:
            self.visit(stmt)
        body_state = set(self._aliases)
        else_state: set[str] = set()
        if orelse:
            self._scopes[-1] = start | body_state
            for stmt in orelse:
                self.visit(stmt)
            else_state = set(self._aliases)
        self._scopes[-1] = start | body_state | else_state

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node.iter, node.body, node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node.iter, node.body, node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node.test, node.body, node.orelse)

    def visit_With(self, node: ast.With) -> None:
        self._visit_conditionally(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_conditionally(node)

    def visit_Match(self, node: ast.AST) -> None:
        # Each `case` is a mutually exclusive alternative (like `if`/
        # `elif`/`else`) -- visited independently from the SAME
        # pre-match alias state, then merged by union, exactly as
        # `_merge_branches` already does for `if`/`else`. A guard
        # expression (`case ... if cond:`) runs as part of deciding
        # whether that specific case is taken, so it's visited within
        # that case's own branch, not the subject's.
        self.visit(node.subject)
        start = set(self._aliases)
        merged = set(start)
        for case in node.cases:
            self._scopes[-1] = set(start)
            if case.guard is not None:
                self.visit(case.guard)
            for stmt in case.body:
                self.visit(stmt)
            merged |= self._aliases
        self._scopes[-1] = merged

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
        if value is None:
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            # `marshal, other = self.app.call_from_thread, None` -- a
            # tuple/list-unpacking assignment. Only analyzed when the
            # right-hand side is ITSELF a literal tuple/list of the same
            # length (so each target pairs unambiguously with its own
            # value); a starred target, a mismatched length, or unpacking
            # an opaque expression (e.g. `a, b = get_something()`) is left
            # untouched rather than guessed at.
            if (
                isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
                and not any(isinstance(e, ast.Starred) for e in target.elts)
            ):
                for sub_target, sub_value in zip(target.elts, value.elts):
                    self._record_alias(sub_target, sub_value)
            return
        if not isinstance(target, ast.Name):
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
            # from this point on in this scope. (If this assignment is
            # inside an `if`/`try`/`for`/`while`/`with` body,
            # `_visit_conditionally` conservatively restores the alias
            # afterward anyway, since some other branch might not have
            # reassigned it.)
            self._aliases.discard(target.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_alias(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_alias(node.target, node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        # A walrus binding (`if marshal := self.app.call_from_thread:
        # marshal(fn)`) is an alias assignment too, just embedded inside
        # an expression rather than a standalone statement.
        self._record_alias(node.target, node.value)
        self.generic_visit(node)


def _find_calls(f: Path) -> tuple[str, list[int]]:
    text = f.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(f))
    finder = _CallFinder()
    finder.visit(tree)
    return text, sorted(set(finder.hits))


def _comments_by_line(text: str) -> dict[int, list[str]]:
    """Map each 1-based source line number to the comment token string(s)
    starting on it, tokenizing the WHOLE file at once -- not one isolated
    physical line. A multiline call (e.g. ``call_from_thread(  # inbox-guard:
    allow <why>`` whose arguments continue on later lines) has an unmatched
    open parenthesis on its own first line alone, which raises a
    ``TokenError`` if tokenized in isolation -- silently defeating the
    escape hatch for any call that isn't entirely on one line. The full
    file is already known to parse (``ast.parse`` succeeded before this is
    ever called), so tokenizing all of it is always well-formed.
    """
    by_line: dict[int, list[str]] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                by_line.setdefault(tok.start[0], []).append(tok.string)
    except (IndentationError, tokenize.TokenError):
        return by_line
    return by_line


def _allowed(comments_by_line: dict[int, list[str]], lineno: int) -> bool:
    for comment in comments_by_line.get(lineno, []):
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
        comments_by_line = _comments_by_line(text)
        lines = text.splitlines()
        rel = f.relative_to(REPO).as_posix()
        for lineno in hits:
            if _allowed(comments_by_line, lineno):
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
