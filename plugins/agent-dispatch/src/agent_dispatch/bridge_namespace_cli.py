"""``dispatch:`` namespace-provider CLI commands for agent-bridge (#3389).

Fixes the upward `agent-bridge` -> `agent-dispatch` dependency the
`a-la-carte-independence` plugin-stack layering rule forbids: agent-bridge
used to import an `agent_dispatch_client.py` HTTP client and call the
coordinator directly (`resolve_dispatch_url()`/`fetch_task`/
`fetch_attachments`). Now agent-dispatch is the one that knows how to reach
its own coordinator -- it registers a `dispatch` namespace-provider manifest
into agent-bridge's `providers.d` registry (the same provider-manifest
sub-pattern already proven for agent-codespaces/agent-containers) and
exposes these two commands over the process-boundary contract agent-bridge
already drives every other provider through (`namespace-list`/
`namespace-resolve`, exit 3 = not-found, exit 4 = bad-state).

``namespace-resolve <task_id>`` answers "which worktree is this task bound
to" using the exact same claimant logic ``task_lifecycle_cli._cmd_claimant``
already exposes as a CLI verb, but returns agent-bridge's `type: "worktree"`
namespace-resolve contract (see `agent_bridge.agent_registry_namespace`)
instead of a raw spawn_command -- agent-dispatch only needs to name *which*
worktree a task is bound to; agent-bridge's own already-correct worktree
transport resolution (SSH topology, `agent-worktrees resolve`, etc.) builds
the rest. The task's raw record + durable attachment history are carried
through as opaque `venue` passthrough data (`venue["task"]`/
`venue["attachments"]`) so agent-bridge's own `dispatch_task_resolution
.candidate_session_ids()` ranking -- deliberately pure and agent-bridge-
owned -- keeps working unchanged, just fed from this process boundary
instead of a direct HTTP fetch.

Scope (2026-09-23, `agent-fabric-endpoint-discovery` Phase 2.5a, extended
same day): a task bound to a worktree (claimed, started, or pinned via
``target_worktree``) resolves `type: "worktree"`. A task with no worktree
binding at all -- the normal, permanent state for a completed *headless*
body (a board-sweep/review worker never binds a worktree; its `owner`
clears to `None` on completion) -- falls back to `type: "session"` when the
task's own `owner_session_id` (or an attachment's `session_id`) is durably
recorded, carrying the same `task`/`attachments` venue payload for the
caller's own *resolve-by-any-origin-reference* ranking
(`agent_bridge.dispatch_task_resolution.candidate_session_ids`). A truly
unassigned task with no worktree binding AND no recorded session at all,
or a task bound to a *different* machine's coordinator (cross-machine
dispatch), is still bad-state (exit 4) -- real claim-on-resolve for a
never-started task and cross-machine reach remain explicitly out of scope
here (see Phase 2.5b/c in the effort README), not silently guessed at.
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import DispatchError
from .loop_commands import _resolve_cli_module

_NS_NOT_FOUND_EXIT = 3
_NS_BAD_STATE_EXIT = 4


def _core():
    return _resolve_cli_module()


def _cmd_namespace_list() -> int:
    """Print `[]` -- dispatch tasks are reached by id, never browsed as a
    static namespace listing (unlike a bounded codespace/container fleet, the
    task set is unbounded and churns constantly); agent-bridge treats an
    empty list as "nothing to offer for bare-name resolution", not an error.
    """
    print(json.dumps([]))
    return 0


def _cmd_namespace_resolve(args: argparse.Namespace) -> int:
    """Print a JSON `{"type": "worktree"|"session", ...}` spec resolving a
    dispatch-task id to where it can be reached.

    ``name`` is `<task_id>` optionally suffixed `@<venue>` (agent-bridge's
    general `<name>@<venue>` addressing) -- a single local coordinator has
    no second venue to disambiguate between yet, so a present `@<venue>`
    is validated to name this coordinator's own machine, never silently
    ignored.
    """
    task_id, requested_machine = _split_name(args.name)
    core = _core()
    try:
        with core._client(args) as c:
            task = c.get(task_id)
            attachments = c.attachments(task_id)
    except DispatchError as exc:
        if exc.status_code == 404:
            print(f"dispatch task {task_id} not found", file=sys.stderr)
            return _NS_NOT_FOUND_EXIT
        print(str(exc), file=sys.stderr)
        return _NS_BAD_STATE_EXIT

    status = task.get("status")
    owner = task.get("owner")
    claimed = bool(owner) and status in (
        "claimed", "started", "suspended", "completed",
    )
    if claimed:
        machine, worktree_id = core._split_owner(owner)
    else:
        machine = task.get("target_machine")
        worktree_id = task.get("target_worktree")

    if not worktree_id:
        # A headless body (e.g. a board-sweep/review worker) never binds a
        # worktree at all -- its ownership record clears on completion
        # (`owner` -> None), but the durable `owner_session_id` (the ACP
        # session that actually did the work) and any prior attachment
        # history survive independently of worktree binding. A caller
        # holding only the task id should still resolve to that session --
        # this is exactly *resolve-by-any-origin-reference*
        # (`visions/plugins/agent-bridge`), just without a worktree hop.
        # Real claim-on-resolve for a genuinely unassigned task (no session
        # ever recorded at all) remains out of scope -- see Phase 2.5b/c.
        session_id = task.get("owner_session_id")
        if not session_id and isinstance(attachments, list):
            for entry in attachments:
                if isinstance(entry, dict) and entry.get("session_id"):
                    session_id = entry["session_id"]
                    break
        if not session_id:
            print(
                f"dispatch task {task_id} is not yet bound to a worktree "
                "and carries no resolvable session "
                "(claim-on-resolve is not implemented -- see Phase 2.5b/c)",
                file=sys.stderr,
            )
            return _NS_BAD_STATE_EXIT

        local_machine = _local_machine_name()
        if machine and local_machine and machine != local_machine:
            print(
                f"dispatch task {task_id} is bound to machine {machine!r}, "
                f"not this coordinator's machine {local_machine!r} -- "
                "cross-machine dispatch-task resolution is not implemented",
                file=sys.stderr,
            )
            return _NS_BAD_STATE_EXIT
        if requested_machine and local_machine and requested_machine != local_machine:
            print(
                f"requested venue {requested_machine!r} does not match this "
                f"coordinator's machine {local_machine!r}",
                file=sys.stderr,
            )
            return _NS_BAD_STATE_EXIT

        spec = {
            "type": "session",
            "venue": {
                "provider": "agent-dispatch",
                "target_id": task_id,
                "task": task,
                "attachments": attachments,
            },
        }
        print(json.dumps(spec))
        return 0

    local_machine = _local_machine_name()
    if machine and local_machine and machine != local_machine:
        print(
            f"dispatch task {task_id} is bound to machine {machine!r}, not "
            f"this coordinator's machine {local_machine!r} -- cross-machine "
            "dispatch-task resolution is not implemented",
            file=sys.stderr,
        )
        return _NS_BAD_STATE_EXIT
    if requested_machine and local_machine and requested_machine != local_machine:
        print(
            f"requested venue {requested_machine!r} does not match this "
            f"coordinator's machine {local_machine!r}",
            file=sys.stderr,
        )
        return _NS_BAD_STATE_EXIT

    spec = {
        "type": "worktree",
        "worktree_id": worktree_id,
        "venue": {
            "provider": "agent-dispatch",
            "target_id": task_id,
            "task": task,
            "attachments": attachments,
        },
    }
    print(json.dumps(spec))
    return 0


def _split_name(name: str) -> tuple[str, str | None]:
    """`<task_id>` or `<task_id>@<venue>` -> `(task_id, venue_or_none)`."""
    task_id, sep, venue = name.partition("@")
    return task_id, (venue or None) if sep else None


def _cmd_namespace_ensure_ready(_args: argparse.Namespace) -> int:
    """Always exit 0 -- unlike a stopped CodeSpace/container, a dispatch
    task has no separate "wake it up" step distinct from resolving it;
    `namespace-resolve` itself already reports not-found/not-bound."""
    return 0



def _local_machine_name() -> str | None:
    """This coordinator's own machine name, independent of CWD.

    Cross-machine validation degrades to "not checked" (``None``) rather
    than raising when the local machine name can't be resolved (e.g. a
    standalone box with no agent-worktrees machine registry) -- an
    unresolvable local identity must never itself become a false bad-state
    for an otherwise-valid local resolution.
    """
    from .identity import resolve_identity

    machine, _worktree = resolve_identity()
    return machine
