"""Shared two-phase lazy-dispatch mechanism for an ``agent-*`` CLI's
monolithic ``__main__.py`` entry root.

**The problem** (see the ``agent-cli-lazy-dispatch`` effort's own Journal
for the full root-cause investigation, first done against
``agent-worktrees``): a typical entry root eagerly imports every one of its
CLI submodules at import time, purely to register every subcommand's
argparse subparser -- regardless of which single subcommand was actually
requested. For a hook-invoked command (a session-start/pre-tool-use hook a
Copilot CLI extension can't simply wait out) this import cost is paid on
every invocation, even for a subcommand whose own real work is trivial.

**The fix, in two phases:**

1. **argv-peek**: before building the full argparse tree, peek at
   ``sys.argv``'s first token. If it names a subcommand this entry root has
   pre-registered in a ``dispatch_table`` (command -> ``(module_name,
   handler_attr_name)``), route straight to :func:`dispatch_lazy` instead of
   the eager full build. Any other invocation (unknown command, ``--help``,
   no args) falls through to the caller's own existing eager path
   unchanged -- this mechanism never has to explain a "no route" case.
2. **lazy-import-by-table**: :func:`dispatch_lazy` imports and registers
   *only* the one CLI submodule that owns the requested command's parser
   and handler, builds a parser scoped to just that one subcommand (so its
   ``--help``/error output is byte-identical to what the eager full build
   would have produced for that same subcommand), and calls its handler.

**A known follow-on hazard, and how this module addresses it:** a CLI
submodule frequently reaches back into its own entry root for a helper
another sibling submodule actually owns (rather than importing that sibling
directly), via a shared ``_core()`` accessor. If the entry root hasn't
finished its own full eager import block yet (skipped by the fast path
above), that reach-back can raise ``AttributeError``/``NameError`` for a
name only bound during the eager block -- a live regression this exact
class shipped at least twice in ``agent-worktrees`` before being fully
decoupled (module-by-module, and function-by-function within
``__main__.py`` itself) across that effort's Stage A-D. :func:`core_helper`
and :func:`self_override` are the two small, generic pieces of that
decoupling pattern, factored out here since every future adopter of this
lazy-dispatch mechanism will eventually need the identical fix:

* A CLI submodule that needs a name actually owned by a **sibling**
  submodule (not its own entry root) should import that sibling directly
  and resolve the name through :func:`core_helper` -- preferring a
  monkeypatched override already sitting on the entry root (a common test
  idiom: ``monkeypatch.setattr(m, name, fake)``) over the direct import,
  so the sibling-decoupling refactor itself never silently defeats an
  existing test.
* An entry-root-*native* function shared across several CLI submodules
  (not itself owned by any one of them) that bare-references a name only
  bound by the eager block should instead do a function-scoped lazy import
  of the real owning module and resolve the name through
  :func:`self_override` -- the entry-root-side mirror of the same pattern,
  since Python's own LEGB scoping lets a name assigned anywhere in a
  function's body shadow an outer/global name of the same spelling for
  that function's *entire* body, safely, even before the assignment line
  textually runs.

Deliberately NOT in scope here (stays plugin-local): the AST-based
``_core()``-cluster classifier each adopting plugin's own
``tests/_core_cluster_scan.py`` uses to compute/verify its own
``_CLUSTER_FREE_MODULES`` set. That classifier walks each plugin's own
``__main__.py`` AST structure and its own eager-import-block shape, which
isn't a generic cross-plugin mechanism -- only the *dispatch* mechanism
above, and the two small decoupling helpers, are.
"""
from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable, Mapping

__all__ = ["core_helper", "dispatch_lazy", "self_override"]


def dispatch_lazy(
    command: str,
    args_list: list[str],
    *,
    dispatch_table: Mapping[str, tuple[str, str]],
    package: str,
    prog: str,
    ensure_cluster_loaded: Callable[[], None],
    cluster_free_modules: frozenset[str] = frozenset(),
    command_map: Mapping[str, Callable[[argparse.Namespace], int]] | None = None,
    register_cli_modules: frozenset[str] = frozenset(),
) -> int:
    """Fast-path dispatch for one ``dispatch_table``-delegated subcommand.

    Imports and registers only the ONE module that owns ``command``'s
    parser and handler (per ``dispatch_table``), instead of the caller's own
    eager full-surface build. The resulting parser/help text for this one
    subcommand is identical to what the eager full build would have
    produced for it, since it's built from the exact same module.

    Args:
        command: The subcommand name (``dispatch_table``'s key), typically
            ``args_list[0]``.
        args_list: The full argv slice to parse (including ``command``
            itself), e.g. ``["get", "--json", "worktree-id"]``.
        dispatch_table: ``command -> (module_name, handler_attr_name)``,
            covering every fast-tracked subcommand this entry root
            supports.
        package: The entry root's own package name (``__package__``), used
            to import ``f"{package}.{module_name}"``.
        prog: The ``prog`` name the scoped parser should report (matching
            the eager full build's own ``prog``, so ``--help``/usage output
            stays byte-identical).
        ensure_cluster_loaded: Called (with no arguments) before importing
            ``module_name`` when it is not a member of
            ``cluster_free_modules`` -- the caller's own "run my eager
            full-surface import block, if not already done" entry point.
            Required, not optional: silently defaulting to a no-op here
            would let a not-yet-decoupled module's `_core()` cross-reaches
            raise at runtime instead of loading its dependencies first.
        cluster_free_modules: The subset of ``dispatch_table``'s module
            names verified (by the caller's own classifier/tests) to need
            nothing from ``ensure_cluster_loaded``'s eager block. Empty by
            default -- the safe, fully-conservative starting point (every
            command pays the eager-load cost) a caller can narrow over
            time as it decouples individual modules; never widen this
            without the same runtime-exercised verification this effort's
            own Journal describes (a static-analysis-only pass has shipped
            a live regression before).
        command_map: An optional ``command -> handler`` mapping to prefer
            over ``getattr(module, handler_attr)`` -- lets a caller (or a
            test that monkeypatches its own already-populated command map)
            override dispatch without bypassing this fast path.
        register_cli_modules: Module names that expose ``register_cli(sub)``
            instead of the default ``add_parsers(sub)`` convention for
            registering their subparser onto ``sub``.

    Returns:
        The handler's own return code (an ``int`` exit status).
    """
    module_name, handler_attr = dispatch_table[command]
    if module_name not in cluster_free_modules:
        ensure_cluster_loaded()
    module = importlib.import_module(f"{package}.{module_name}")
    parser = argparse.ArgumentParser(
        prog=prog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    if module_name in register_cli_modules:
        module.register_cli(sub)
    else:
        module.add_parsers(sub)
    args = parser.parse_args(args_list)
    handler = (command_map or {}).get(command)
    if handler is None:
        handler = getattr(module, handler_attr)
    return handler(args)


def self_override(module_globals: Mapping[str, object], name: str, local):
    """Prefer a pre-bound/monkeypatched ``name`` in ``module_globals`` (a
    caller's own ``globals()``) over ``local`` (a direct sibling-module
    import).

    This is the entry-root's own mirror of :func:`core_helper`: an
    entry-root-native function that used to reach a deferred-only name
    through the entry root's own eager-loaded globals, now fixed to import
    the real owning module directly instead, must not silently defeat a
    test (or any other caller) that monkeypatches the bare name directly
    onto the entry-root module -- e.g. ``monkeypatch.setattr(m,
    "some_name", fake)`` -- since a plain sibling import can never see that
    override. Call with the entry root's own ``globals()`` (never a copy)
    so a later monkeypatch onto the live module dict is still visible.
    """
    candidate = module_globals.get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def core_helper(core_module: object, name: str, local):
    """Prefer an already-bound callable ``name`` on ``core_module`` (a CLI
    submodule's own entry-root accessor, e.g. its ``_core()``) over
    ``local`` (a direct sibling-module import).

    A CLI submodule that needs a name actually owned by a sibling
    submodule, not its own entry root, should import that sibling directly
    and resolve the name through this helper rather than reaching into the
    entry root for it -- the entry root may not have finished its own
    eager-loaded surface (skipped for a fast-tracked command), so a plain
    ``core_module._some_name`` attribute access can raise before this
    decoupling. Once the entry root's real work IS loaded (a monkeypatched
    override for a test, or the eager surface having genuinely finished),
    this still prefers that live binding over the direct import, so a test
    intercepting the entry root's own name is never silently bypassed.
    """
    candidate = vars(core_module).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local
