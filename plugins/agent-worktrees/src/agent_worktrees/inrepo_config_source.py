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
**committed**, via binary Git plumbing -- entirely independent of the anchor's
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

from dropin_registry import ScanAuthority

from . import config_acquisition as acquisition, git_ops
from .acquisition_report import AcquisitionReport, failed, observe

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
    dict[tuple[Path, tuple[Path, ...], str], AcquisitionReport[dict[str, Any]]] | None
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
    read from a committed Git blob, never a file on disk.
    """
    return _parse_yaml_text_safe(text, "committed-config")


def _parse_yaml_text_safe(text: str, source: str) -> dict[str, Any]:
    observations = acquisition.current_observations()
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:
        failed(observations, source, "inrepo-yaml", "invalid-yaml", type(exc).__name__)
        return {}
    if raw is not None and not isinstance(raw, dict):
        failed(observations, source, "inrepo-yaml", "invalid-shape", "document must be a mapping")
    else:
        observe(observations, source, "inrepo-yaml", ScanAuthority.COMPLETE)
    return raw if isinstance(raw, dict) else {}


def _unavailable(source: str, stage: str, detail: str) -> None:
    failed(acquisition.current_observations(), source, stage, "committed-source-unavailable", detail)


def _offline_remote_head_branch(
    anchor: Path, remote: str, deadline: float
) -> str | None:
    """The branch ``<remote>/HEAD`` currently points at, read offline from the
    local ref store (never a network call) -- or ``None`` if unset/unknown."""
    timeout = _probe_timeout(deadline)
    if timeout is None:
        return _unavailable(str(anchor), "committed-head", "resolution budget exhausted")
    try:
        proc = git_ops.git(
            "symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD",
            cwd=anchor, check=False, capture=True, timeout=timeout,
        )
    except Exception as exc:
        return _unavailable(str(anchor), "committed-head", type(exc).__name__)
    if proc.returncode != 0:
        if proc.returncode == 1:
            observe(acquisition.current_observations(), str(anchor), "committed-head", ScanAuthority.ABSENT)
            return None
        return _unavailable(str(anchor), "committed-head", "Git did not resolve remote HEAD")
    ref = proc.stdout.strip()
    prefix = f"refs/remotes/{remote}/"
    if not ref.startswith(prefix):
        return _unavailable(str(anchor), "committed-head", "unexpected remote HEAD output")
    observe(acquisition.current_observations(), str(anchor), "committed-head", ScanAuthority.COMPLETE)
    return ref[len(prefix):]


def _offline_remote_branch_exists(
    anchor: Path, remote: str, branch: str, deadline: float
) -> bool:
    """Cheap, offline existence check for ``<remote>/<branch>`` in the local
    ref store (no network)."""
    timeout = _probe_timeout(deadline)
    if timeout is None:
        _unavailable(str(anchor), "committed-branch", "resolution budget exhausted")
        return False
    try:
        proc = git_ops.git(
            "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}",
            cwd=anchor, check=False, capture=True, timeout=timeout,
        )
    except Exception as exc:
        _unavailable(str(anchor), "committed-branch", type(exc).__name__)
        return False
    if proc.returncode not in (0, 1):
        _unavailable(str(anchor), "committed-branch", "Git branch probe failed")
    else:
        observe(acquisition.current_observations(), f"{remote}/{branch}", "committed-branch",
                ScanAuthority.COMPLETE if proc.returncode == 0 else ScanAuthority.ABSENT)
    return proc.returncode == 0


def _read_committed_blob(
    anchor: Path, remote: str, branch: str, rel_path: Path, deadline: float
) -> str | None:
    """Text content of ``rel_path`` as committed on ``<remote>/<branch>`` --
    read via ``git show``, so it never depends on (or touches) the anchor's
    working tree or index. ``None`` if the ref or path doesn't exist."""
    timeout = _probe_timeout(deadline)
    if timeout is None:
        return _unavailable(str(anchor), "committed-blob", "resolution budget exhausted")
    spec = f"{remote}/{branch}:{rel_path.as_posix()}"
    try:
        proc = git_ops.git(
            "show", spec, cwd=anchor, check=False, capture=True, timeout=timeout
        )
    except Exception as exc:
        return _unavailable(spec, "committed-blob", type(exc).__name__)
    if proc.returncode != 0:
        return _unavailable(spec, "committed-blob", "Git blob read failed")
    observe(acquisition.current_observations(), spec, "committed-blob", ScanAuthority.COMPLETE)
    return proc.stdout


def _committed_candidates(
    anchor: Path, remote: str, branch: str, candidates: tuple[Path, ...], deadline: float,
) -> frozenset[str] | None:
    """Find exact candidate paths in one fresh probe; unknown results retain all reads."""
    if not candidates:
        return frozenset()
    timeout = _probe_timeout(deadline)
    if timeout is None:
        return _unavailable(str(anchor), "committed-presence", "presence result unavailable")
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
        return _unavailable(str(anchor), "committed-presence", "presence result unavailable")
    if proc.returncode != 0:
        logging.getLogger("agent-worktrees").warning(
            "Committed-config presence probe failed (git exit %s); retaining bounded ordered reads.",
            proc.returncode,
        )
        return _unavailable(str(anchor), "committed-presence", "presence result unavailable")
    if not isinstance(proc.stdout, str):
        logging.getLogger("agent-worktrees").warning(
            "Committed-config presence probe returned non-text output; retaining bounded ordered reads.",
        )
        return _unavailable(str(anchor), "committed-presence", "presence result unavailable")
    expected = {candidate.as_posix() for candidate in candidates}
    found: set[str] = set()
    records = proc.stdout.split("\0")
    if records.pop() != "":
        logging.getLogger("agent-worktrees").warning(
            "Committed-config presence probe returned malformed output; retaining bounded ordered reads.",
        )
        return _unavailable(str(anchor), "committed-presence", "presence result unavailable")
    for record in records:
        metadata, separator, path = record.partition("\t")
        if not separator or len(metadata.split()) != 3 or path not in expected:
            logging.getLogger("agent-worktrees").warning(
                "Committed-config presence probe returned malformed output; retaining bounded ordered reads.",
            )
            return _unavailable(str(anchor), "committed-presence", "presence result unavailable")
        found.add(path)
    observe(acquisition.current_observations(), f"{remote}/{branch}", "committed-presence",
            ScanAuthority.COMPLETE if found else ScanAuthority.ABSENT)
    return frozenset(found)


def _read_committed_batch(
    anchor: Path, remote: str, branch: str, candidates: tuple[Path, ...], deadline: float,
) -> dict[Path, str] | None:
    timeout = _probe_timeout(deadline)
    if timeout is None:
        return _unavailable(str(anchor), "committed-batch", "batch result unavailable")
    specs = [f"{remote}/{branch}:{candidate.as_posix()}".encode("utf-8") for candidate in candidates]
    try:
        result = git_ops.git(
            "cat-file", "--batch", cwd=anchor, check=False, capture=True, timeout=timeout,
            input_data=b"".join(spec + b"\n" for spec in specs),
        )
    except (OSError, ValueError, subprocess.SubprocessError, git_ops.GitError):
        return _unavailable(str(anchor), "committed-batch", "batch result unavailable")
    if result.returncode != 0 or not isinstance(result.stdout, bytes):
        return _unavailable(str(anchor), "committed-batch", "batch result unavailable")
    data, position, found = result.stdout, 0, {}
    for candidate, spec in zip(candidates, specs):
        end = data.find(b"\n", position)
        if end < 0:
            return _unavailable(str(anchor), "committed-batch", "batch result unavailable")
        header, position = data[position:end], end + 1
        if header == spec + b" missing":
            continue
        fields = header.split()
        if (
            len(fields) != 3 or fields[1] != b"blob"
            or len(fields[0]) not in (40, 64)
            or any(c not in b"0123456789abcdef" for c in fields[0])
            or not fields[2].isdigit()
        ):
            return _unavailable(str(anchor), "committed-batch", "batch result unavailable")
        try:
            size = int(fields[2])
        except ValueError:
            return _unavailable(str(anchor), "committed-batch", "batch result unavailable")
        end = position + size
        if data[end:end + 1] != b"\n":
            return _unavailable(str(anchor), "committed-batch", "batch result unavailable")
        found[candidate] = data[position:end].decode("utf-8", errors="replace")
        if "\ufffd" in found[candidate]:
            _unavailable(f"{remote}/{branch}:{candidate.as_posix()}", "committed-decoding",
                         "replacement text cannot certify lossless UTF-8 acquisition")
        position = end + 1
    if position != len(data):
        return _unavailable(str(anchor), "committed-batch", "batch result unavailable")
    observe(acquisition.current_observations(), f"{remote}/{branch}", "committed-batch",
            ScanAuthority.COMPLETE if found else ScanAuthority.ABSENT)
    return found


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
            with acquisition.capture_config_observations() as observations:
                value = _resolve_committed_config(anchor, rel_path_candidates, remote, parser)
            snapshots[key] = AcquisitionReport("committed-config", value, tuple(observations))
        report = snapshots[key]
        observations = acquisition.current_observations()
        if observations is not None:
            observations.extend(report.observations)
        return copy.deepcopy(report.value)
    return _resolve_committed_config(anchor, rel_path_candidates, remote, parser)


def _resolve_committed_config(
    anchor: Path, rel_path_candidates: tuple[Path, ...], remote: str,
    parser: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    deadline = _shared_deadline.get() or time.monotonic() + _RESOLUTION_BUDGET
    head_branch = _offline_remote_head_branch(anchor, remote, deadline)
    if head_branch is None:
        return {}

    def _parse(text: str, branch: str, candidate: Path) -> dict[str, Any]:
        source = f"{remote}/{branch}:{candidate.as_posix()}"
        if parser is parse_yaml_text_safe:
            return _parse_yaml_text_safe(text, source)
        try:
            value = parser(text)
        except Exception as exc:
            _unavailable(source, "inrepo-yaml", type(exc).__name__)
            raise
        acquisition.opaque_source("committed-config-parser")
        return value

    def _read_config_on(branch: str) -> dict[str, Any] | None:
        batch = _read_committed_batch(anchor, remote, branch, rel_path_candidates, deadline)
        if batch is not None:
            for candidate in rel_path_candidates:
                if candidate in batch:
                    return _parse(batch[candidate], branch, candidate)
            return None
        logging.getLogger("agent-worktrees").warning(
            "Committed-config batch unavailable; retaining bounded ordered reads.",
        )
        present = _committed_candidates(anchor, remote, branch, rel_path_candidates, deadline)
        if present == frozenset():
            return None
        for rel_path in rel_path_candidates:
            if present is not None and rel_path.as_posix() not in present:
                continue
            text = _read_committed_blob(anchor, remote, branch, rel_path, deadline)
            if text is not None:
                return _parse(text, branch, rel_path)
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
        from .repos_configuration_sources import _mapping_observed, mapping

        observations = acquisition.current_observations()
        start = len(observations) if observations is not None else 0
        try:
            value = mapping(path)
        except Exception as exc:
            failed(acquisition.current_observations(), str(path), "inrepo-file", "unreadable-source", type(exc).__name__)
            raise
        if observations is not None and not _mapping_observed(path, observations[start:]):
            acquisition.opaque_source("strict-config-file")
        return value
    from .config import _load_yaml_safe

    return _load_yaml_safe(path)


def load_config_base(
    anchor: Path, candidates: tuple[Path, ...], *, strict: bool = False,
) -> dict[str, Any]:
    observations = acquisition.current_observations()
    start = len(observations) if observations is not None else 0
    parser = parse_yaml_text_safe
    if strict:
        from .repos_configuration_sources import parse_mapping

        parser = parse_mapping
    merged = load_inrepo_config_from_committed_ref(anchor, candidates, parser=parser)
    if not merged:
        base = next((anchor / rel for rel in candidates
                     if acquisition.source_exists(anchor / rel, "inrepo-disk")), None)
        merged = load_config_file(base, strict=strict) if base is not None else {}
    if observations is not None:
        if len(observations) == start:
            acquisition.opaque_source("inrepo-config-base")
        # Repeating an original verdict marks completion without upgrading absence.
        observe(observations, str(anchor), "inrepo-config-base", observations[-1].authority)
    return merged


def load_config_base_report(
    anchor: Path, candidates: tuple[Path, ...], *, strict: bool = False,
) -> AcquisitionReport[dict[str, Any]]:
    """Report committed/disk base acquisition, excluding marketplace overlays."""
    with acquisition.capture_config_observations() as observations:
        value = load_config_base(anchor, candidates, strict=strict)
        if not any(o.source == str(anchor) and o.stage == "inrepo-config-base" for o in observations):
            acquisition.opaque_source("inrepo-config-base")
    return AcquisitionReport("inrepo-config-base", value, tuple(observations))
