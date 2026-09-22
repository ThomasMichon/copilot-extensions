"""Per-task git worktree lifecycle inside a live fleet container.

restricted-venue-targets Phase 5/7: today a container gets exactly ONE
static repo checkout, materialized once at creation time (``_materialize_repo``
in :mod:`fleet`) and exposed as the fixed ``workspace_folder``. That single
checkout is shared across every dispatched task, which is exactly the
resumed/shared-state contamination class the effort exists to close for a
*restricted* fleet used for concurrent, isolated per-task work (e.g. the
Intelligence Dampener containerized reviewer).

This module adds the missing "exact-worktree lifecycle verb, routed to the
owning provider" primitive: create (and later remove) a genuinely fresh
``git worktree`` under the container's already-provisioned ``workspace_folder``
tmpfs, scoped to one task id. No new mount, writable root, or host projection
is introduced -- the worktree lives entirely inside the existing size-bounded
tmpfs the restricted policy already grants (see
``lifecycle.restricted_policy_errors``), so this is safe for a restricted
fleet member as-is.
"""

from __future__ import annotations

import posixpath
import re

from .resolver import LiveExecTarget, resolve_live_exec_target

# Task ids are caller-supplied (e.g. an agent-dispatch task id) and become a
# literal path segment plus a `git worktree add` ref-adjacent argument passed
# through a single `bash -lc` string -- validate strictly to rule out path
# traversal or shell injection via a hostile id.
_SAFE_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# A git ref/branch name/SHA. Deliberately permissive of the characters git
# refs allow (slashes for e.g. `pr/foo`), but still rules out shell
# metacharacters and leading dashes (which `git` could otherwise interpret as
# an option).
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class WorkspaceError(RuntimeError):
    """A per-task workspace could not be created, found, or removed."""


def _validate_task_id(task_id: str) -> str:
    task_id = task_id.strip()
    if not _SAFE_TASK_ID_RE.fullmatch(task_id):
        raise WorkspaceError(
            f"task_id {task_id!r} is not a safe identifier "
            "(expected 1-64 chars, alnum/underscore/hyphen, not leading with one)"
        )
    return task_id


def _validate_ref(ref: str) -> str:
    ref = ref.strip()
    if not _SAFE_REF_RE.fullmatch(ref) or ".." in ref:
        raise WorkspaceError(f"ref {ref!r} is not a safe git ref")
    return ref


def _task_workspace_path(target: LiveExecTarget, task_id: str) -> str:
    return posixpath.join(target.workspace_folder, "tasks", task_id)


def create_task_workspace(
    name: str,
    task_id: str,
    ref: str,
    *,
    docker_exec=None,
) -> str:
    """Create a fresh ``git worktree`` for one task inside container ``name``.

    Runs ``git worktree add <task-path> <ref>`` against the container's
    already-materialized base checkout (``target.workspace_folder``), as the
    fleet's ``exec_user``. Returns the concrete in-container path. Raises
    :class:`WorkspaceError` on any validation or exec failure; never silently
    falls back to the shared base checkout.
    """
    from .lifecycle import _docker as default_docker_exec

    docker_exec = docker_exec or default_docker_exec
    task_id = _validate_task_id(task_id)
    ref = _validate_ref(ref)
    target = resolve_live_exec_target(name)
    path = _task_workspace_path(target, task_id)

    script = (
        f"set -euo pipefail; "
        f"cd {target.workspace_folder} && "
        f"git worktree add --force {path} {ref}"
    )
    result = docker_exec(
        ["exec", "-u", target.user, name, "bash", "-lc", script],
        timeout=120,
    )
    if result.returncode != 0:
        raise WorkspaceError(
            f"git worktree add failed for task {task_id!r} in {name!r}: "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return path


def remove_task_workspace(
    name: str,
    task_id: str,
    *,
    docker_exec=None,
) -> None:
    """Remove one task's worktree and prune its metadata.

    Idempotent: removing an already-absent worktree is not an error (`git
    worktree remove --force` on a missing path fails, so this checks first via
    `git worktree list --porcelain` and no-ops if the path isn't registered).
    """
    from .lifecycle import _docker as default_docker_exec

    docker_exec = docker_exec or default_docker_exec
    task_id = _validate_task_id(task_id)
    target = resolve_live_exec_target(name)
    path = _task_workspace_path(target, task_id)

    list_script = f"cd {target.workspace_folder} && git worktree list --porcelain"
    listed = docker_exec(
        ["exec", "-u", target.user, name, "bash", "-lc", list_script],
        timeout=60,
    )
    if listed.returncode != 0:
        raise WorkspaceError(
            f"git worktree list failed in {name!r}: "
            f"{(listed.stderr or listed.stdout).strip()}"
        )
    registered_paths = {
        line.split(" ", 1)[1]
        for line in (listed.stdout or "").splitlines()
        if line.startswith("worktree ")
    }
    if path not in registered_paths:
        return

    script = (
        f"set -euo pipefail; "
        f"cd {target.workspace_folder} && "
        f"git worktree remove --force {path} && "
        f"git worktree prune"
    )
    result = docker_exec(
        ["exec", "-u", target.user, name, "bash", "-lc", script],
        timeout=120,
    )
    if result.returncode != 0:
        raise WorkspaceError(
            f"git worktree remove failed for task {task_id!r} in {name!r}: "
            f"{(result.stderr or result.stdout).strip()}"
        )
