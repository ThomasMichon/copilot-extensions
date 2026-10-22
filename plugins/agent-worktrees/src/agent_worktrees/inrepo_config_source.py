"""Resolve a repo's committed in-repo config independent of the anchor's
working tree.

``config.py``'s ``_load_inrepo_config`` used to read
``.agent-worktrees/config.yaml`` straight off the anchor's checked-out files.
The anchor is a read-only mirror by design (agents never edit it in place --
see the ``anchor-write-guard``), but nothing kept its *checked-out branch* in
sync with the repo's real default: an anchor left on a stale or wrong local
branch silently fed a stale or wrong ``default_branch`` (and every other
in-repo setting) into every command that resolves the repo, including where
``create``/``create-pr`` fork worktrees and target PRs from.

:func:`load_inrepo_config_from_committed_ref` reads the config as actually
**committed**, via ``git show`` -- entirely independent of the anchor's
working tree or index.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import logging
import subprocess
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

import yaml

from . import git_ops

#: Per-probe cap: a single git launch can be slow on a loaded host.
_OFFLINE_GIT_TIMEOUT = 15
#: Budget for one ``load_config()`` (all its resolutions, up to ~8 probes each),
#: so the sum stays well inside callers' own timeouts however slow each launch is.
_RESOLUTION_BUDGET = 30.0


#: The deadline shared by every resolution inside one ``load_config()`` (which
#: resolves several repos and anchors), so the whole load stays in budget.
_shared_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_agent_worktrees_inrepo_resolution_deadline", default=None
)
_shared_snapshots: contextvars.ContextVar[
    dict[tuple[Path, tuple[Path, ...], str], dict[str, Any]] | None
] = contextvars.ContextVar("_agent_worktrees_inrepo_config_snapshots", default=None)


@contextlib.contextmanager
def resolution_budget(seconds: float | None = None, *, reuse_snapshots: bool = False):
    """Share one probe budget across every committed-config resolution in this
    block. Nested blocks keep the outermost deadline.

    One config load may reuse committed answers across its own overlay/base
    reads. The scope ends before a later policy revalidation or source fetch.
    """
    if _shared_deadline.get() is not None:
        yield
        return
    token = _shared_deadline.set(time.monotonic() + (_RESOLUTION_BUDGET if seconds is None else seconds))
    snapshots = _shared_snapshots.set({} if reuse_snapshots else None)
    try:
        yield
    finally:
        _shared_snapshots.reset(snapshots)
        _shared_deadline.reset(token)


def _probe_timeout(deadline: float) -> float | None:
    """This probe's timeout: the remaining budget, capped per probe; ``None``
    once the budget is spent (the probe is then skipped as unavailable)."""
    remaining = deadline - time.monotonic()
    return min(_OFFLINE_GIT_TIMEOUT, remaining) if remaining > 0 else None


def parse_yaml_text_safe(text: str) -> dict[str, Any]:
    """Parse a YAML string into a dict, returning ``{}`` on any problem.

    The blob-reading sibling of ``config._load_yaml_safe`` -- for content
    read via ``git show`` (a string), never a file on disk.
    """
    try:
        raw = yaml.safe_load(text)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _offline_remote_head_branch(
    anchor: Path, remote: str, deadline: float
) -> str | None:
    """The branch ``<remote>/HEAD`` currently points at, read offline from the
    local ref store (never a network call) -- or ``None`` if unset/unknown."""
    timeout = _probe_timeout(deadline)
    if timeout is None:
        return None
    try:
        proc = git_ops.git(
            "symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD",
            cwd=anchor, check=False, capture=True, timeout=timeout,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    ref = proc.stdout.strip()
    prefix = f"refs/remotes/{remote}/"
    return ref[len(prefix):] if ref.startswith(prefix) else None


def _offline_remote_branch_exists(
    anchor: Path, remote: str, branch: str, deadline: float
) -> bool:
    """Cheap, offline existence check for ``<remote>/<branch>`` in the local
    ref store (no network)."""
    timeout = _probe_timeout(deadline)
    if timeout is None:
        return False
    try:
        proc = git_ops.git(
            "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}",
            cwd=anchor, check=False, capture=True, timeout=timeout,
        )
    except Exception:
        return False
    return proc.returncode == 0


def _read_committed_blob(
    anchor: Path, remote: str, branch: str, rel_path: Path, deadline: float
) -> str | None:
    """Text content of ``rel_path`` as committed on ``<remote>/<branch>`` --
    read via ``git show``, so it never depends on (or touches) the anchor's
    working tree or index. ``None`` if the ref or path doesn't exist."""
    timeout = _probe_timeout(deadline)
    if timeout is None:
        return None
    spec = f"{remote}/{branch}:{rel_path.as_posix()}"
    try:
        proc = git_ops.git(
            "show", spec, cwd=anchor, check=False, capture=True, timeout=timeout
        )
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _committed_candidates(
    anchor: Path, remote: str, branch: str, candidates: tuple[Path, ...], deadline: float,
) -> frozenset[str] | None:
    """Find exact candidate paths in one fresh probe; unknown results retain all reads."""
    if not candidates:
        return frozenset()
    timeout = _probe_timeout(deadline)
    if timeout is None:
        return None
    try:
        proc = git_ops.git(
            "--literal-pathspecs", "ls-tree", "-z", "--full-tree", f"{remote}/{branch}", "--",
            *(candidate.as_posix() for candidate in candidates),
            cwd=anchor, check=False, capture=True, timeout=timeout,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired, git_ops.GitError) as exc:
        logging.getLogger("agent-worktrees").warning(
            "Committed-config presence probe unavailable (%s); retaining bounded ordered reads.",
            type(exc).__name__,
        )
        return None
    if proc.returncode != 0:
        logging.getLogger("agent-worktrees").warning(
            "Committed-config presence probe failed (git exit %s); retaining bounded ordered reads.",
            proc.returncode,
        )
        return None
    if not isinstance(proc.stdout, str):
        logging.getLogger("agent-worktrees").warning(
            "Committed-config presence probe returned non-text output; retaining bounded ordered reads.",
        )
        return None
    expected = {candidate.as_posix() for candidate in candidates}
    found: set[str] = set()
    records = proc.stdout.split("\0")
    if records.pop() != "":
        logging.getLogger("agent-worktrees").warning(
            "Committed-config presence probe returned malformed output; retaining bounded ordered reads.",
        )
        return None
    for record in records:
        metadata, separator, path = record.partition("\t")
        if not separator or len(metadata.split()) != 3 or path not in expected:
            logging.getLogger("agent-worktrees").warning(
                "Committed-config presence probe returned malformed output; retaining bounded ordered reads.",
            )
            return None
        found.add(path)
    return frozenset(found)


def load_inrepo_config_from_committed_ref(
    anchor: Path, rel_path_candidates: tuple[Path, ...], remote: str = "origin",
    *, parser: Callable[[str], dict[str, Any]] = parse_yaml_text_safe,
) -> dict[str, Any]:
    """The in-repo config as actually **committed** on the resolved default
    branch, checked in ``rel_path_candidates`` order (first match wins) --
    independent of whatever the anchor's working tree has checked out.

    Resolution is two-hop and stays fully offline (no ``ls-remote`` -- this
    sits on the hot ``load_config`` path):

    1. Start from ``<remote>/HEAD``'s branch -- git's own recorded default,
       independent of any local checkout.
    2. Read that branch's committed config. If it declares a *different*
       ``default_branch`` that also exists as ``<remote>/<that name>``, the
       declaring branch is naming a more current source for its own setting
       (the dev/main split this exists for -- ``dev`` is the contribution
       branch, periodically promoted to ``main``) -- hop once more and read
       from there instead.

    Returns ``{}`` when nothing can be resolved this way (no remote, no
    fetched tracking refs, or the anchor isn't a git repo at all); callers
    fall back to the working-tree read in that case.
    """
    snapshots = _shared_snapshots.get()
    if snapshots is not None and parser is parse_yaml_text_safe:
        key = (anchor, rel_path_candidates, remote)
        if key not in snapshots:
            snapshots[key] = _resolve_committed_config(
                anchor, rel_path_candidates, remote, parser,
            )
        return copy.deepcopy(snapshots[key])
    return _resolve_committed_config(anchor, rel_path_candidates, remote, parser)


def _resolve_committed_config(
    anchor: Path, rel_path_candidates: tuple[Path, ...], remote: str,
    parser: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    deadline = _shared_deadline.get() or time.monotonic() + _RESOLUTION_BUDGET
    head_branch = _offline_remote_head_branch(anchor, remote, deadline)
    if head_branch is None:
        return {}

    def _read_config_on(branch: str) -> dict[str, Any] | None:
        present = _committed_candidates(anchor, remote, branch, rel_path_candidates, deadline)
        if present == frozenset():
            return None
        for rel_path in rel_path_candidates:
            if present is not None and rel_path.as_posix() not in present:
                continue
            text = _read_committed_blob(anchor, remote, branch, rel_path, deadline)
            if text is not None:
                return parser(text)
        return None

    data = _read_config_on(head_branch)
    if data is None:
        return {}

    declared = data.get("default_branch")
    if (
        isinstance(declared, str)
        and declared
        and declared != head_branch
        and _offline_remote_branch_exists(anchor, remote, declared, deadline)
    ):
        hopped = _read_config_on(declared)
        if hopped is not None:
            data = hopped
    return data


def load_config_file(path: Path, *, strict: bool = False) -> dict[str, Any]:
    if strict:
        from .repos_configuration_sources import mapping

        return mapping(path)
    from .config import _load_yaml_safe

    return _load_yaml_safe(path)


def load_config_base(
    anchor: Path, candidates: tuple[Path, ...], *, strict: bool = False,
) -> dict[str, Any]:
    parser = parse_yaml_text_safe
    if strict:
        from .repos_configuration_sources import parse_mapping

        parser = parse_mapping
    merged = load_inrepo_config_from_committed_ref(anchor, candidates, parser=parser)
    if not merged:
        base = next((anchor / rel for rel in candidates if (anchor / rel).exists()), None)
        merged = load_config_file(base, strict=strict) if base is not None else {}
    return merged
