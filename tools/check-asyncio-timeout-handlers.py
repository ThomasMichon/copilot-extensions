#!/usr/bin/env python3
"""Guard against ``asyncio.wait_for`` timeouts that escape on Python <=3.10.

Why this exists
---------------
The agent-bridge runtime venv is created with ``uv venv --python 3.10`` (see
``plugins/agent-bridge/scripts/install.sh``), so the daemon commonly runs on
**Python 3.10**. On Python <=3.10 ``asyncio.wait_for`` raises
**``asyncio.TimeoutError``** -- a ``concurrent.futures.TimeoutError`` -- which is
**NOT** the builtin ``TimeoutError`` (they were only unified in 3.11). So a
handler written as ``except TimeoutError`` (or ``except (TimeoutError, OSError)``,
or ``contextlib.suppress(TimeoutError)``) around an ``asyncio.wait_for`` **fails
to catch its own timeout on 3.10**, and the exception escapes.

This is exactly the class of bug behind dotfiles#1549 (the credential-relay
reverse-forward degraded to "auth-light" because a routine stderr-poll timeout
escaped) and its follow-up. The dev-box/CI interpreters are 3.11/3.12, where the
two exception types are identical, so such bugs pass every local test and only
bite the 3.10 daemon.

What it flags
-------------
For each ``asyncio.wait_for(...)`` call, this guard finds the innermost handler
scope that guards it -- a ``try/except`` whose ``try`` body contains the call, or
a ``with contextlib.suppress(...)`` whose body contains it -- and flags it when
that handler **explicitly names the builtin ``TimeoutError``** (a clear intent to
swallow the timeout) but does **not** also name ``asyncio.TimeoutError`` and is
**not** a broad catch (``Exception`` / ``BaseException`` / bare ``except:``).

Naming the builtin ``TimeoutError`` is the precise "I mean to catch this timeout"
signal, so the fix is always the same: add ``asyncio.TimeoutError`` to the
handler (``UP041`` stays quiet under a ``py310`` target). Handlers that only
catch unrelated errors (e.g. ``except OSError`` without ``TimeoutError``) are NOT
flagged -- they may not intend to swallow the timeout at all; this guard stays
precise to avoid false positives.

Usage::

    python tools/check-asyncio-timeout-handlers.py          # verify (CI / pre-push)
    python tools/check-asyncio-timeout-handlers.py --list     # show every guarded wait_for

Exit 0 = clean, 1 = a vulnerable handler was found.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"

_IGNORE_PARTS = {"build", "dist", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "tests"}


def _py_files() -> list[Path]:
    out: list[Path] = []
    if not PLUGINS_DIR.is_dir():
        return out
    for f in PLUGINS_DIR.rglob("*.py"):
        if _IGNORE_PARTS & set(f.relative_to(PLUGINS_DIR).parts):
            continue
        if ".egg-info" in str(f):
            continue
        out.append(f)
    return out


def _is_asyncio_wait_for(node: ast.AST) -> bool:
    """True for a ``Call`` to ``asyncio.wait_for`` (attribute form)."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr == "wait_for"
        and isinstance(fn.value, ast.Name)
        and fn.value.id == "asyncio"
    )


def _is_suppress_with(node: ast.AST) -> bool:
    """True for a ``with contextlib.suppress(...)`` / ``with suppress(...)``."""
    if not isinstance(node, ast.With):
        return False
    for item in node.items:
        call = item.context_expr
        if not isinstance(call, ast.Call):
            continue
        fn = call.func
        if isinstance(fn, ast.Attribute) and fn.attr == "suppress":
            return True
        if isinstance(fn, ast.Name) and fn.id == "suppress":
            return True
    return False


def _exc_names(expr: ast.expr | None) -> set[str]:
    """Flatten an exception spec (Name / Attribute / Tuple) to dotted names."""
    names: set[str] = set()
    if expr is None:
        names.add("<bare>")
        return names
    targets = expr.elts if isinstance(expr, ast.Tuple) else [expr]
    for t in targets:
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, ast.Attribute):
            base = t.value.id if isinstance(t.value, ast.Name) else "?"
            names.add(f"{base}.{t.attr}")
    return names


def _suppress_names(node: ast.With) -> set[str]:
    names: set[str] = set()
    for item in node.items:
        call = item.context_expr
        if isinstance(call, ast.Call):
            fn = call.func
            is_suppress = (isinstance(fn, ast.Attribute) and fn.attr == "suppress") or (
                isinstance(fn, ast.Name) and fn.id == "suppress"
            )
            if is_suppress:
                for a in call.args:
                    names |= _exc_names(a)
    return names


_BROAD = {"Exception", "BaseException", "<bare>"}
_SAFE = _BROAD | {"asyncio.TimeoutError", "concurrent.futures.TimeoutError"}


def _handler_names_for_try(try_node: ast.Try) -> set[str]:
    names: set[str] = set()
    for h in try_node.handlers:
        names |= _exc_names(h.type)
    return names


def _is_vulnerable(caught: set[str]) -> bool:
    """A handler is vulnerable iff it explicitly names builtin ``TimeoutError``
    but neither ``asyncio.TimeoutError`` nor a broad catch."""
    if caught & _SAFE:
        return False
    return "TimeoutError" in caught


def _build_parent_field_map(tree: ast.AST) -> dict[int, tuple[ast.AST, str]]:
    """Map id(child) -> (parent, field-name-on-parent)."""
    m: dict[int, tuple[ast.AST, str]] = {}
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            items = value if isinstance(value, list) else [value]
            for child in items:
                if isinstance(child, ast.AST):
                    m[id(child)] = (parent, field)
    return m


def _enclosing_scopes(
    call: ast.AST, pmap: dict[int, tuple[ast.AST, str]]
) -> list[tuple[ast.AST, set[str], str]]:
    """Ordered inner->outer list of handler scopes a raised timeout would pass
    through: ``try`` blocks *with* except handlers whose ``body`` contains the
    call, and ``with contextlib.suppress(...)`` blocks whose ``body`` contains
    it. A ``try/finally`` with no except handlers does NOT catch, so it is
    skipped (the exception propagates through it to the next outer handler --
    this is what makes ``manager.py``'s inner-``finally`` / outer-``except``
    shape a real, catchable case). Each entry is (node, caught-names, kind)."""
    scopes: list[tuple[ast.AST, set[str], str]] = []
    cur: ast.AST = call
    while id(cur) in pmap:
        parent, field = pmap[id(cur)]
        if isinstance(parent, ast.Try) and field == "body" and parent.handlers:
            scopes.append((parent, _handler_names_for_try(parent), "except"))
        elif _is_suppress_with(parent) and field == "body":
            scopes.append((parent, _suppress_names(parent), "contextlib.suppress"))
        cur = parent
    return scopes


def _classify(call: ast.AST, pmap: dict[int, tuple[ast.AST, str]]) -> tuple[str, set[str]] | None:
    """Return (kind, caught) for the scope that makes ``call`` vulnerable, or
    ``None`` if a raised timeout is either safely caught first or never meets a
    ``TimeoutError``-naming handler at all.

    Walks scopes inner->outer: the first that safely catches the 3.10 timeout
    (``asyncio.TimeoutError`` or a broad catch) clears it; the first that names
    the builtin ``TimeoutError`` without that safety is the bug."""
    for _node, caught, kind in _enclosing_scopes(call, pmap):
        if caught & _SAFE:
            return None
        if "TimeoutError" in caught:
            return kind, caught
    return None


def _analyze(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    pmap = _build_parent_field_map(tree)
    rel = path.relative_to(REPO).as_posix()
    problems: list[str] = []
    for node in ast.walk(tree):
        if not _is_asyncio_wait_for(node):
            continue
        verdict = _classify(node, pmap)
        if verdict is None:
            continue
        kind, caught = verdict
        problems.append(
            f"{rel}:{node.lineno}: asyncio.wait_for guarded by {kind} "
            f"({', '.join(sorted(caught))}) missing asyncio.TimeoutError "
            "-- escapes on Python <=3.10"
        )
    return problems


def _list_all() -> None:
    for path in _py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        pmap = _build_parent_field_map(tree)
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if not _is_asyncio_wait_for(node):
                continue
            scopes = _enclosing_scopes(node, pmap)
            if not scopes:
                print(f"{rel}:{node.lineno}: wait_for (unguarded)")
                continue
            verdict = _classify(node, pmap)
            desc = "; ".join(
                f"{kind}: {', '.join(sorted(caught)) or '-'}" for _n, caught, kind in scopes
            )
            flag = "  <== VULNERABLE" if verdict is not None else ""
            print(f"{rel}:{node.lineno}: wait_for [{desc}]{flag}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="print every asyncio.wait_for + its guarding handler and exit")
    args = ap.parse_args(argv)

    if args.list:
        _list_all()
        return 0

    problems: list[str] = []
    for path in _py_files():
        problems.extend(_analyze(path))

    if problems:
        print("check-asyncio-timeout-handlers: FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nOn Python <=3.10 (the agent-bridge daemon's pinned interpreter) "
            "asyncio.wait_for raises asyncio.TimeoutError, which is NOT the "
            "builtin TimeoutError. Add asyncio.TimeoutError to each flagged "
            "handler so the timeout is caught (dotfiles#1549).",
            file=sys.stderr,
        )
        return 1
    print("check-asyncio-timeout-handlers: OK (no vulnerable wait_for handlers).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
