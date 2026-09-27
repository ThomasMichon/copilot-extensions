#!/usr/bin/env python3
"""Guard: no sibling plugin may import an ``agent_worktrees`` tracking-record
WRITE function directly.

``agent-worktrees-authoritative-daemon`` effort, Phase 4: the resident
daemon (via ``tracking_write.py``'s verb registry) is meant to become the
SOLE path that mutates a worktree's tracking YAML, with agent-worktrees'
own CLI commands as its only direct callers. A sibling plugin importing one
of these functions itself would let it write worktree state independent of
whatever the daemon's own lock/cache/verb machinery is doing --
reintroducing exactly the cross-process atomicity/staleness hazard this
effort exists to close, even though the survey that founded this effort
(see the effort's own README § "How this was found") found zero such
violations across every existing sibling plugin at the time. This guard
makes that finding an enforced invariant instead of a point-in-time grep
that could go stale silently as new call sites are added.

**Scope: every ``plugins/*`` directory except ``agent-worktrees`` itself**
-- NOT filtered by an ``agent-*`` name prefix (this repo ships several
non-``agent-*``-named plugins too, e.g. ``visions``,
``customizing-copilot``); the invariant this guard enforces has nothing to
do with a plugin's naming convention, so scanning by name would create a
blind spot for exactly the plugins that don't happen to match it. This
mirrors the effort's own surveyed scope and Plan language in spirit ("any
sibling plugin imports a tracking.py write function") even though that
prose used "sibling plugin" loosely; the actual enforced scope is
everything under ``plugins/`` but the owning plugin. It deliberately does
NOT cover ``worktree-manager/`` (a separate, non-plugin, out-of-plugin
control-plane app under ``worktree-manager-control-plane``, outside
``plugins/`` entirely): that app's Picker (``data_local.py``, via its own
in-process ``_engine_runtime`` bridge) already calls several of these same
write functions directly today (``stamp_bound_live``, ``stamp_mux_live``,
``stamp_session_state``) -- a KNOWN, already-tracked architectural gap, not
an oversight this guard should silently flag or block. See
`efforts/active/worktree-manager-control-plane/README.md` Phase 3d ("Design
the `data_local.py` hot path -- needs a new batched verb") for its own
tracked resolution, explicitly sequenced after that effort's Phase 3c.
Extending this guard's scope to cover that app is a deliberate, separate,
cross-effort decision -- not something to fold in here without that
coordination.

The read-only accessors this guard does NOT flag
(``load_record_by_id``, ``find_worktree_id_by_cwd``,
``find_worktree_id_by_session``, ``load_record``, ``list_records``, and
similar) remain a sanctioned, documented surface, consistent with every
sibling plugin's actual (all read-only) usage found by the effort's
original survey.

**Maintenance note:** :data:`WRITE_FUNCTIONS` is a hand-curated denylist,
not derived automatically -- update it (and :data:`TRACKING_MODULES` if a
new module needs protecting) in the same PR that adds a new
tracking-record write function (a function whose body persists a
``WorktreeRecord``, directly or via the async stamp queue, including a
new daemon verb handler) to any of the currently-protected modules:
``tracking.py`` / ``tracking_lifecycle.py`` / ``tracking_claims.py`` /
``tracking_session_registry.py`` / ``tracking_controller_relations.py`` /
``tracking_write.py`` / the 6 ``tracking_*_write.py`` verb-handler
modules.

Exit code 0 = no sibling-plugin direct-write imports found, 1 = found one.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
OWNING_PLUGIN = "agent-worktrees"

#: Every ``agent_worktrees.tracking`` / ``tracking_lifecycle`` /
#: ``tracking_claims`` / ``tracking_session_registry`` function whose body
#: persists a ``WorktreeRecord`` (directly via ``save_record``/
#: ``_save_record_unlocked``, or indirectly via the async stamp queue).
#: See this module's own docstring for the update-alongside-new-writes rule.
WRITE_FUNCTIONS = frozenset({
    # tracking.py
    "save_record",
    "_save_record_unlocked",
    "retire_record",
    "update_status",
    "set_disposition",
    "mark_resumed",
    "stamp_frozen_attribution",
    "stamp_mux_live",
    "stamp_bound_live",
    "stamp_session_state",
    "flush_stamp_writes",
    "_stamp_liveness",
    "_apply_session_state_stamp",
    # tracking_lifecycle.py
    "open_handoff",
    "link_handoff",
    "associate_handoff_candidate",
    "set_head_session",
    "conclude_session",
    "link_succession",
    "create_new_record",
    "create_new_record_if_absent",
    # tracking_claims.py
    "add_resource_claim",
    "settle_resource_claim",
    "release_resource_claim",
    "add_follow_up",
    "resolve_follow_up",
    "dismiss_follow_up",
    "sweep_abandoned_obligations",
    "release_all_resources",
    "release_at_rest_resources",
    "load_or_create_anchor_record",
    # tracking_session_registry.py
    "register_session",
    "deregister_session",
    "seal_worktree_identity",
    # tracking_controller_relations.py (also re-exported by tracking.py
    # itself, see that module's own `# noqa: F401 -- re-export for tests`
    # imports -- a sibling importing either module's copy is a violation)
    "backfill_legacy_controller_relations",
    "set_controller_relation",
    "end_controller_relation",
    "remove_controller_relation",
    # tracking_*_write.py -- the daemon's own verb-handler modules
    # (tracking_write.py's registry dispatches to these; each acquires
    # _RecordLock, mutates the record, and calls tracking.save_record
    # itself). Importing one of these directly is the SAME bypass as
    # importing tracking.save_record -- it just skips the verb-dispatch
    # layer on the way there.
    "apply_claim_add",
    "apply_claim_release",
    "apply_claim_settle",
    "apply_status_disposition",
    "apply_follow_up_add",
    "apply_follow_up_resolve",
    "apply_follow_up_dismiss",
    "apply_session_deregister",
    "apply_session_conclude",
    "apply_session_link_succession",
    "apply_session_register",
    # tracking_write.py -- its own DIRECT-EXECUTION entry points.
    # `run_direct(verb, args, reason=...)` and `compute(kind, payload)`
    # both load and invoke a registered verb's handler (one of the
    # apply_* functions above) IN-PROCESS, bypassing the daemon exactly
    # as directly as importing the handler itself would -- a sibling
    # calling `tracking_write.run_direct("claim_add", {...}, reason=...)`
    # never needs to name `apply_claim_add` at all. `dispatch` and
    # `write_with_boot` are deliberately NOT here: those two ARE the
    # sanctioned daemon-mediated API (they try the resident daemon first
    # and only fall back to `run_direct` when it's unreachable) -- the
    # thing a sibling plugin SHOULD call if it ever needs to trigger a
    # verb at all.
    "run_direct",
    "compute",
})

#: `tracking.py`'s module-level ``_STAMP_QUEUE`` singleton (a
#: ``_StampWriteQueue`` instance) exposes these write-triggering methods:
#: ``submit``/``submit_mux`` enqueue a real persisted write (applied via
#: `_apply_session_state_stamp`/`_stamp_liveness` on a background writer
#: thread), and ``_apply`` is the same queue's own direct, synchronous
#: apply path (normally only called by the queue's internal worker/
#: `flush`). `tracking.stamp_mux_live`/`stamp_bound_live`/
#: `stamp_session_state` already funnel through this queue and are
#: themselves denylisted above; a sibling reaching straight for
#: `tracking._STAMP_QUEUE.submit(...)` (or `._apply(...)` directly)
#: bypasses those wrappers (and their own best-effort/throttle semantics)
#: while still triggering the SAME persisted write, so the queue object's
#: own write-triggering methods need their own three-level attribute-chain
#: check (module -> `_STAMP_QUEUE` -> method), distinct from the plain
#: module -> function chains above.
STAMP_QUEUE_ATTR = "_STAMP_QUEUE"
STAMP_QUEUE_WRITE_METHODS = frozenset({"submit", "submit_mux", "_apply"})

#: The module names a write function could be imported from -- all of these
#: re-export (or ARE) the same underlying write surface this guard protects.
TRACKING_MODULES = frozenset({
    "tracking",
    "tracking_lifecycle",
    "tracking_claims",
    "tracking_session_registry",
    "tracking_controller_relations",
    "tracking_write",
    # The daemon's own verb-handler modules (see the WRITE_FUNCTIONS
    # comment above for why these belong here too).
    "tracking_claim_write",
    "tracking_disposition_write",
    "tracking_followup_write",
    "tracking_session_deregistration_write",
    "tracking_session_lifecycle_write",
    "tracking_session_registration_write",
})


class Violation:
    def __init__(self, path: Path, line: int, detail: str, *, repo_root: Path = REPO) -> None:
        self.path = path
        self.line = line
        self.detail = detail
        self._repo_root = repo_root

    def __str__(self) -> str:
        try:
            rel = self.path.relative_to(self._repo_root)
        except ValueError:
            rel = self.path
        return f"{rel}:{self.line}: {self.detail}"


def _sibling_plugin_py_files(plugins_dir: Path) -> list[Path]:
    if not plugins_dir.is_dir():
        return []
    files: list[Path] = []
    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name == OWNING_PLUGIN:
            continue
        files.extend(sorted(plugin_dir.rglob("*.py")))
    return files


def _check_file(path: Path, *, repo_root: Path = REPO) -> list[Violation]:
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Violation(
            path, 0,
            f"could not be read to scan for a direct tracking-write import "
            f"({exc}) -- fix or exclude this file rather than letting the "
            f"guard silently skip it",
            repo_root=repo_root,
        )]
    except UnicodeDecodeError as exc:
        return [Violation(
            path, 0,
            f"is not valid UTF-8, so it could not be scanned for a direct "
            f"tracking-write import ({exc}) -- fix its encoding or exclude "
            f"it rather than letting the guard silently skip it",
            repo_root=repo_root,
        )]
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [Violation(
            path, exc.lineno or 0,
            f"has a syntax error, so it could not be scanned for a direct "
            f"tracking-write import ({exc.msg}) -- fix the syntax error "
            f"rather than letting the guard silently skip this file",
            repo_root=repo_root,
        )]

    violations: list[Violation] = []
    # Local names bound to a whole tracking module (from either
    # `from agent_worktrees import tracking` or `import agent_worktrees.tracking
    # as tracking`) -- any attribute access on one of these matching
    # WRITE_FUNCTIONS is a violation.
    module_aliases: dict[str, int] = {}
    # Local names bound to the WHOLE `agent_worktrees` package (`import
    # agent_worktrees` or `import agent_worktrees as aw`) -- a two-level
    # attribute chain off one of these (`aw.tracking.save_record(...)`) is
    # the same violation as a module alias's own single-level access.
    package_aliases: set[str] = set()
    # Local names bound directly to the `_STAMP_QUEUE` singleton itself
    # (`queue = tracking._STAMP_QUEUE`, or a direct
    # `from agent_worktrees.tracking import _STAMP_QUEUE`) -- calling a
    # write-triggering method on one of these is the same violation as
    # the inline `<module>._STAMP_QUEUE.<method>(...)` chain.
    queue_aliases: dict[str, int] = {}
    # Local names bound to the `importlib` MODULE itself -- the plain
    # literal name `importlib` is included unconditionally (the common,
    # unaliased form), and `import importlib as il` adds its own alias.
    # Used to recognize `il.import_module(...)` alongside the literal
    # `importlib.import_module(...)` form. Local names bound to
    # `import_module` ITSELF (`from importlib import import_module`,
    # optionally `as X`) let a sibling call it completely bare.
    importlib_aliases: set[str] = {"importlib"}
    import_module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or "importlib")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module == "importlib"
        ):
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_aliases.add(alias.asname or alias.name)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "agent_worktrees":
                for alias in node.names:
                    if alias.name == "*":
                        violations.append(Violation(
                            path, node.lineno,
                            "wildcard-imports `agent_worktrees` "
                            "(`from agent_worktrees import *`) -- this "
                            "cannot be verified safe against the write "
                            "denylist and is rejected outright",
                            repo_root=repo_root,
                        ))
                    elif alias.name in TRACKING_MODULES:
                        module_aliases[alias.asname or alias.name] = node.lineno
                    elif alias.name in WRITE_FUNCTIONS:
                        violations.append(Violation(
                            path, node.lineno,
                            f"imports write function `{alias.name}` directly "
                            f"from `agent_worktrees` "
                            f"(`from agent_worktrees import {alias.name}`)",
                            repo_root=repo_root,
                        ))
            elif node.module in {
                f"agent_worktrees.{m}" for m in TRACKING_MODULES
            }:
                owning = node.module.rsplit(".", 1)[-1]
                for alias in node.names:
                    if alias.name == "*":
                        violations.append(Violation(
                            path, node.lineno,
                            f"wildcard-imports `agent_worktrees.{owning}` "
                            f"(`from agent_worktrees.{owning} import *`) -- "
                            "this cannot be verified safe against the "
                            "write denylist and is rejected outright",
                            repo_root=repo_root,
                        ))
                    elif alias.name == STAMP_QUEUE_ATTR:
                        queue_aliases[alias.asname or alias.name] = node.lineno
                    elif alias.name in WRITE_FUNCTIONS:
                        violations.append(Violation(
                            path, node.lineno,
                            f"imports write function `{alias.name}` directly "
                            f"from `agent_worktrees.{owning}`",
                            repo_root=repo_root,
                        ))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] != "agent_worktrees":
                    continue
                if len(parts) == 1:
                    package_aliases.add(alias.asname or parts[0])
                elif len(parts) == 2 and parts[1] in TRACKING_MODULES:
                    if alias.asname:
                        # `import agent_worktrees.tracking as X` binds X to
                        # the submodule directly -- a genuine module alias.
                        module_aliases[alias.asname] = node.lineno
                    else:
                        # `import agent_worktrees.tracking` (no `as`) binds
                        # only the top-level package name `agent_worktrees`
                        # in the local namespace (Python's own import
                        # semantics) -- NOT `tracking`. The submodule is
                        # only reachable via `agent_worktrees.tracking...`,
                        # i.e. the two-level package-alias chain below.
                        package_aliases.add("agent_worktrees")

    # A literal dynamic import (`importlib.import_module("agent_worktrees
    # .tracking")`, `__import__("agent_worktrees.tracking")`, or either
    # via an aliased `importlib`/`import_module` binding -- `import
    # importlib as il; il.import_module(...)` or `from importlib import
    # import_module; import_module(...)`) resolves to a tracked module or
    # the package itself just as statically as an ordinary `import`
    # statement does, provided the argument is a plain string constant --
    # so it gets the SAME alias treatment via the reassignment-propagation
    # pass below (a non-literal argument, e.g. a variable or an f-string,
    # is undecidable statically and is not attempted, mirroring the
    # getattr/`__dict__` boundary documented further down).
    def _dynamic_import_alias_kind(expr: ast.expr) -> tuple[str, str] | None:
        """Returns ``("module", "<submodule-name>")`` or
        ``("package", "agent_worktrees")`` if ``expr`` is a literal-string
        dynamic import of a tracked target; otherwise ``None``."""
        is_import_module_call = (
            isinstance(expr, ast.Call)
            and (
                (
                    isinstance(expr.func, ast.Attribute)
                    and expr.func.attr == "import_module"
                    and isinstance(expr.func.value, ast.Name)
                    and expr.func.value.id in importlib_aliases
                )
                or (
                    isinstance(expr.func, ast.Name)
                    and expr.func.id in import_module_aliases
                )
            )
            and expr.args
            and isinstance(expr.args[0], ast.Constant)
            and isinstance(expr.args[0].value, str)
        )
        is_dunder_import_call = (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id == "__import__"
            and expr.args
            and isinstance(expr.args[0], ast.Constant)
            and isinstance(expr.args[0].value, str)
        )
        if not (is_import_module_call or is_dunder_import_call):
            return None
        target = expr.args[0].value
        if target == "agent_worktrees":
            return ("package", "agent_worktrees")
        prefix = "agent_worktrees."
        if target.startswith(prefix) and target[len(prefix):] in TRACKING_MODULES:
            return ("module", target[len(prefix):])
        return None

    # Propagate through simple reassignment so a trivial rename can't
    # evade the alias tracking above. Three RHS shapes are recognized on
    # a single-target plain `Assign` OR a single-target `AnnAssign` (e.g.
    # `writer_module: object = tracking` -- an annotation adds no actual
    # indirection, so it must be treated identically to the plain form):
    # a bare Name (`writer_module = tracking`, `wt = agent_worktrees`)
    # copies that name's own alias membership onto the LHS; a two-level
    # Attribute off a package alias (`tracking_module = aw.tracking`)
    # resolves to the SAME thing a `tracking_module.save_record(...)`
    # call already catches, so it's folded into `module_aliases` too
    # rather than needing its own parallel check downstream; and a
    # literal-string dynamic import (see above) resolves the same way an
    # ordinary `import` statement would. Iterated to a fixed point since
    # a chain (`a = tracking; b = a; c = b`, or `x = aw.tracking; y = x`)
    # needs more than one pass to fully propagate.
    def _simple_assignments():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                yield node.targets[0].id, node.value, node.lineno
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                yield node.target.id, node.value, node.lineno

    def _module_expr_label(expr: ast.expr) -> str | None:
        """If ``expr`` resolves to a tracked module (a module alias, a
        two-level package-alias chain like ``aw.tracking``, or an inline
        literal-string dynamic import), return a human-readable label
        for it; otherwise ``None``."""
        if isinstance(expr, ast.Name) and expr.id in module_aliases:
            return expr.id
        if (
            isinstance(expr, ast.Attribute)
            and isinstance(expr.value, ast.Name)
            and expr.value.id in package_aliases
            and expr.attr in TRACKING_MODULES
        ):
            return f"{expr.value.id}.{expr.attr}"
        dynamic = _dynamic_import_alias_kind(expr)
        if dynamic is not None:
            return expr.args[0].value
        return None

    changed = True
    while changed:
        changed = False
        for target, value, lineno in _simple_assignments():
            if isinstance(value, ast.Name):
                source = value.id
                if source in module_aliases and target not in module_aliases:
                    module_aliases[target] = lineno
                    changed = True
                if source in package_aliases and target not in package_aliases:
                    package_aliases.add(target)
                    changed = True
                if source in queue_aliases and target not in queue_aliases:
                    queue_aliases[target] = lineno
                    changed = True
            elif (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id in package_aliases
                and value.attr in TRACKING_MODULES
                and target not in module_aliases
            ):
                module_aliases[target] = lineno
                changed = True
            elif (
                isinstance(value, ast.Attribute)
                and value.attr == STAMP_QUEUE_ATTR
                and _module_expr_label(value.value) is not None
                and target not in queue_aliases
            ):
                # `queue = tracking._STAMP_QUEUE` (or `aw.tracking.
                # _STAMP_QUEUE`) -- binds the QUEUE OBJECT itself to
                # `target`, not a module, so it goes into `queue_aliases`
                # rather than `module_aliases`.
                queue_aliases[target] = lineno
                changed = True
            else:
                dynamic = _dynamic_import_alias_kind(value)
                if dynamic is not None:
                    kind, name = dynamic
                    if kind == "package" and target not in package_aliases:
                        package_aliases.add(target)
                        changed = True
                    elif kind == "module" and target not in module_aliases:
                        module_aliases[target] = lineno
                        changed = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in WRITE_FUNCTIONS:
            label = _module_expr_label(node.value)
            if label is not None:
                violations.append(Violation(
                    path, node.lineno,
                    f"calls write function `{label}.{node.attr}` directly",
                    repo_root=repo_root,
                ))
        # `<module>._STAMP_QUEUE.submit(...)` (or `.submit_mux`/`._apply`)
        # -- a three-level chain bypassing stamp_mux_live/stamp_bound_live/
        # stamp_session_state's own wrappers while triggering the exact
        # same persisted write. See STAMP_QUEUE_WRITE_METHODS' own comment.
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in STAMP_QUEUE_WRITE_METHODS
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == STAMP_QUEUE_ATTR
        ):
            label = _module_expr_label(node.value.value)
            if label is not None:
                violations.append(Violation(
                    path, node.lineno,
                    f"calls write-triggering queue method "
                    f"`{label}.{STAMP_QUEUE_ATTR}.{node.attr}` directly",
                    repo_root=repo_root,
                ))
        # `queue.submit(...)` (or `.submit_mux`/`._apply`) via a name
        # already bound directly to the `_STAMP_QUEUE` object itself
        # (`queue = tracking._STAMP_QUEUE`, or a direct
        # `from agent_worktrees.tracking import _STAMP_QUEUE`) -- the
        # same violation as the inline three-level chain above, just
        # through an intermediate alias.
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in STAMP_QUEUE_WRITE_METHODS
            and isinstance(node.value, ast.Name)
            and node.value.id in queue_aliases
        ):
            violations.append(Violation(
                path, node.lineno,
                f"calls write-triggering queue method "
                f"`{node.value.id}.{node.attr}` directly",
                repo_root=repo_root,
            ))
        # Reflective access with a literal write-function name still
        # resolves statically even though it isn't an ast.Attribute:
        # `getattr(tracking, "save_record")` and
        # `tracking.__dict__["save_record"]` both name the exact function
        # being fetched in a plain string constant. This is deliberately
        # NOT a general reflection-proof analysis (a non-literal name,
        # e.g. `getattr(tracking, some_variable)`, is undecidable
        # statically and is not attempted) -- it closes the specific,
        # easy, literal-string bypass of the ast.Attribute check above.
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in WRITE_FUNCTIONS
        ):
            label = _module_expr_label(node.args[0])
            if label is not None:
                violations.append(Violation(
                    path, node.lineno,
                    f"reflectively accesses write function "
                    f"`getattr({label}, {node.args[1].value!r})`",
                    repo_root=repo_root,
                ))
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "__dict__"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and node.slice.value in WRITE_FUNCTIONS
        ):
            label = _module_expr_label(node.value.value)
            if label is not None:
                violations.append(Violation(
                    path, node.lineno,
                    f"reflectively accesses write function "
                    f"`{label}.__dict__[{node.slice.value!r}]`",
                    repo_root=repo_root,
                ))
    return violations


def find_violations(repo_root: Path = REPO) -> list[Violation]:
    violations: list[Violation] = []
    for path in _sibling_plugin_py_files(repo_root / "plugins"):
        violations.extend(_check_file(path, repo_root=repo_root))
    return violations


def main() -> int:
    violations = find_violations()
    if not violations:
        print(
            "check-no-sibling-tracking-writes: OK (no sibling plugin imports "
            "an agent_worktrees tracking write function)."
        )
        return 0
    print(
        "check-no-sibling-tracking-writes: FAILED -- a sibling plugin "
        "imports an agent_worktrees tracking-record WRITE function directly:"
    )
    for v in violations:
        print(f"  - {v}")
    print(
        "\nOnly agent-worktrees itself may write a worktree's tracking YAML "
        "(agent-worktrees-authoritative-daemon effort, no-writer-bypasses-the-"
        "daemon). A sibling plugin that needs to change worktree state must "
        "call agent-worktrees' own CLI/verb surface, not `tracking.py`'s "
        "write functions directly. The existing read-only accessors "
        "(load_record_by_id, find_worktree_id_by_cwd, "
        "find_worktree_id_by_session, load_record, list_records, ...) remain "
        "a sanctioned, documented surface."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
