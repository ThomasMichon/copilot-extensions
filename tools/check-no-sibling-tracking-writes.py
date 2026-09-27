#!/usr/bin/env python3
"""Guard: no sibling ``agent-*`` plugin may import an ``agent_worktrees``
tracking-record WRITE function directly.

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

**Scope: sibling ``agent-*`` PLUGINS only** (``plugins/*`` other than
``agent-worktrees`` itself) -- this mirrors the effort's own surveyed scope
and Plan language exactly ("any sibling plugin imports a tracking.py write
function"). It deliberately does NOT cover ``worktree-manager/`` (a
separate, non-plugin, out-of-plugin control-plane app under
``worktree-manager-control-plane``): that app's Picker (``data_local.py``,
via its own in-process ``_engine_runtime`` bridge) already calls several of
these same write functions directly today (``stamp_bound_live``,
``stamp_mux_live``, ``stamp_session_state``) -- a KNOWN, already-tracked
architectural gap, not an oversight this guard should silently flag or
block. See `efforts/active/worktree-manager-control-plane/README.md`
Phase 3d ("Design the `data_local.py` hot path -- needs a new batched
verb") for its own tracked resolution, explicitly sequenced after that
effort's Phase 3c. Extending this guard's scope to cover that app is a
deliberate, separate, cross-effort decision -- not something to fold in
here without that coordination.

The read-only accessors this guard does NOT flag
(``load_record_by_id``, ``find_worktree_id_by_cwd``,
``find_worktree_id_by_session``, ``load_record``, ``list_records``, and
similar) remain a sanctioned, documented surface, consistent with every
sibling plugin's actual (all read-only) usage found by the effort's
original survey.

**Maintenance note:** :data:`WRITE_FUNCTIONS` is a hand-curated denylist,
not derived automatically -- update it in the same PR that adds a new
tracking-record write function (a function whose body persists a
``WorktreeRecord``, directly or via the async stamp queue) to
``tracking.py`` / ``tracking_lifecycle.py`` / ``tracking_claims.py`` /
``tracking_session_registry.py``.

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
})

#: The module names a write function could be imported from -- all of these
#: re-export (or ARE) the same underlying write surface this guard protects.
TRACKING_MODULES = frozenset({
    "tracking",
    "tracking_lifecycle",
    "tracking_claims",
    "tracking_session_registry",
    "tracking_controller_relations",
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

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in WRITE_FUNCTIONS:
            continue
        # Single-level: `tracking.save_record(...)` via a module alias.
        if isinstance(node.value, ast.Name) and node.value.id in module_aliases:
            violations.append(Violation(
                path, node.lineno,
                f"calls write function `{node.value.id}.{node.attr}` directly",
                repo_root=repo_root,
            ))
        # Two-level: `aw.tracking.save_record(...)` via a package alias
        # (covers both `import agent_worktrees` and `import agent_worktrees
        # as aw`, including the unaliased `agent_worktrees.tracking...` form).
        elif (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id in package_aliases
            and node.value.attr in TRACKING_MODULES
        ):
            violations.append(Violation(
                path, node.lineno,
                f"calls write function `{node.value.value.id}.{node.value.attr}."
                f"{node.attr}` directly",
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
        "check-no-sibling-tracking-writes: FAILED -- a sibling agent-* plugin "
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
