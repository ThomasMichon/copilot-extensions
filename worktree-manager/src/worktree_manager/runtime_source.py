"""Acquire one immutable source choice from a Manager-owned standalone checkout.

Acquisition never executes fetched code, updates the checked-out files on reuse,
or installs a runtime. The caller holds the existing construction lease while
consuming the exact selected commit through canonical source preparation.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
import math
from pathlib import Path
import time

from agent_procutil import no_window_kwargs

from .runtime_source_config import RuntimeSource
from .runtime_identity import running_payload_dir, runtime_scripts
from .source_config import absolute_local_source, default_root

_GIT_ROUTING_ENV = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE", "GIT_REPLACE_REF_BASE", "GIT_CONFIG",
}


class SourceAcquisitionError(RuntimeError):
    """The configured runtime source could not be acquired without substitution."""


@dataclass(frozen=True)
class ResolvedSource:
    selection: RuntimeSource
    checkout: Path
    commit: str
    tree: str
    branch_tip: str
    main_head: str | None = None

    def document(self) -> dict:
        document = {
            "selection": self.selection.document(), "checkout": str(self.checkout),
            "commit": self.commit, "tree": self.tree, "branch_tip": self.branch_tip,
            "branch": self.selection.branch,
        }
        if self.main_head is not None:
            document["main_head"] = self.main_head
        return document


def _ordinary(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (
            candidate.exists() and getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise SourceAcquisitionError(f"Runtime source path must not be linked: {candidate}")


def _remaining(deadline: float | None, cap: float = 120) -> float:
    if deadline is None:
        return cap
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise SourceAcquisitionError("Runtime source requires a finite deadline")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SourceAcquisitionError("Runtime source deadline exceeded")
    return min(cap, remaining)


def _git(
    checkout: Path, *arguments: str, deadline: float | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    environment = {
        key: value for key, value in (os.environ if environment is None else environment).items()
        if key.upper() not in _GIT_ROUTING_ENV
    }
    environment.update(
        GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
    )
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
             "-c", "core.autocrlf=false",
             "-c", "core.longpaths=true",
             "-c", f"core.hooksPath={os.devnull}", "-c", "gc.auto=0",
             "-C", str(checkout), *arguments],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=environment, timeout=_remaining(deadline), check=False, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SourceAcquisitionError(f"Runtime source Git operation failed: {error}") from error
    _remaining(deadline)
    if result.returncode:
        raise SourceAcquisitionError(
            f"Runtime source Git operation failed: {result.stderr.strip() or result.returncode}"
        )
    return result.stdout.strip()


def _validate(
    checkout: Path, owner: dict[str, str], *, deadline: float | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    _remaining(deadline)
    git_command = _git if deadline is None and environment is None else partial(
        _git, deadline=deadline, environment=environment,
    )
    _ordinary(checkout)
    git = checkout / ".git"
    if not git.is_dir():
        raise SourceAcquisitionError("Runtime source must be an owned standalone checkout")
    _ordinary(git)
    marker = git / "runtime-source-owner.json"
    _ordinary(marker)
    try:
        actual = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SourceAcquisitionError("Runtime source checkout has no valid Manager ownership") from error
    if actual != owner:
        raise SourceAcquisitionError("Runtime source checkout belongs to a different selection owner")
    if Path(git_command(checkout, "rev-parse", "--show-toplevel")).resolve() != checkout.resolve():
        raise SourceAcquisitionError("Runtime source checkout is not the exact standalone root")
    if git_command(checkout, "config", "--local", "--get-all", "remote.origin.url") != owner["repo"]:
        raise SourceAcquisitionError("Runtime source origin differs from the configured repository")
    for name in ("info/grafts", "objects/info/alternates", "shallow"):
        if (git / name).exists():
            raise SourceAcquisitionError("Runtime source requires complete, independent Git history")
    head = git_command(checkout, "rev-parse", "--verify", "HEAD")
    if git_command(checkout, "status", "--porcelain=v1", "--untracked-files=all", "--ignored"):
        raise SourceAcquisitionError("Runtime source contains modified, untracked or ignored files")
    return head


@contextmanager
def acquire(
    selection: RuntimeSource, *, root: Path | None = None, deadline: float | None = None,
    environment: dict[str, str] | None = None,
    branch_repository: str | None = None,
) -> Iterator[ResolvedSource]:
    # The payload owns the lease library; acquisition needs no installer or CLI.
    scripts = runtime_scripts(Path(__file__).resolve().parent, running_payload_dir)
    sys.path.insert(0, str(scripts))
    from versioned_runtime import (
        construction_lease, _AV_RETRY_ATTEMPTS, _AV_RETRY_BACKOFF, _is_transient_lock,
    )

    _remaining(deadline)
    git_command = _git if deadline is None and environment is None else partial(
        _git, deadline=deadline, environment=environment,
    )
    if selection.channel == "marketplace":
        raise SourceAcquisitionError("Marketplace runtime delivery has no independent source checkout")
    installation = (root or default_root()).absolute()
    source_root = installation / "runtime-sources"
    _ordinary(source_root)
    repo = absolute_local_source(selection.repo)
    authority = None
    if branch_repository is not None:
        RuntimeSource("main", repo=branch_repository)
        authority = absolute_local_source(branch_repository)
    digest = hashlib.sha256((repo + ("\n" + authority if authority else "")).encode("utf-8")).hexdigest()
    checkout = source_root / digest / "checkout"
    owner = {"schema": "worktree-manager.runtime-source", "repo": repo}
    if authority is not None:
        owner["branch_repository"] = authority
    acquired_authority = False
    source_root.mkdir(parents=True, exist_ok=True)
    with construction_lease(source_root, digest, timeout=_remaining(deadline, 60)):
        _ordinary(checkout)
        if not checkout.exists():
            checkout.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".acquire-", dir=checkout.parent) as temporary:
                staging = Path(temporary) / "checkout"
                git_command(
                    Path(temporary), "clone", "--no-hardlinks", "--no-checkout",
                    "--origin", "origin", "--", repo, "checkout",
                )
                _ordinary(staging / ".git")
                if authority is not None:
                    git_command(
                        staging, "fetch", "--no-tags", "--", authority,
                        f"+refs/heads/{selection.branch}:refs/remotes/origin/{selection.branch}",
                    )
                    acquired_authority = True
                initial = selection.branch if authority is not None else "main"
                git_command(staging, "checkout", "--detach", f"refs/remotes/origin/{initial}", "--")
                marker = staging / ".git" / "runtime-source-owner.json"
                _ordinary(marker)
                marker.write_text(json.dumps(owner, sort_keys=True) + "\n", encoding="utf-8")
                initial_head = _validate(staging, owner, deadline=deadline, environment=environment)
                retry_until = time.monotonic() + _remaining(deadline, 15)
                for attempt in range(_AV_RETRY_ATTEMPTS):
                    _remaining(deadline)
                    _ordinary(checkout)
                    if checkout.exists() or checkout.is_symlink():
                        raise SourceAcquisitionError("Runtime source checkout appeared during initialization")
                    _remaining(deadline)
                    if time.monotonic() >= retry_until:
                        raise SourceAcquisitionError("Runtime source checkout retry deadline exceeded")
                    try:
                        staging.rename(checkout)
                        break
                    except OSError as error:
                        if (
                            os.name != "nt" or not _is_transient_lock(error)
                            or attempt == _AV_RETRY_ATTEMPTS - 1
                        ):
                            raise
                        time.sleep(max(0, min(
                            _AV_RETRY_BACKOFF * (attempt + 1), _remaining(deadline),
                            retry_until - time.monotonic(),
                        )))
                        if _validate(staging, owner, deadline=deadline, environment=environment) != initial_head:
                            raise SourceAcquisitionError("Runtime source checkout moved during initialization")
        head = _validate(checkout, owner, deadline=deadline, environment=environment)
        branch = selection.branch
        remote_ref = f"refs/remotes/origin/{branch}"
        main_ref = "refs/remotes/origin/main"
        if not acquired_authority:
            refspecs = [f"+refs/heads/{branch}:{remote_ref}"]
            if authority is None and branch != "main":
                refspecs.append(f"+refs/heads/main:{main_ref}")
            git_command(
                checkout, "fetch", "--no-tags", *(["--", authority] if authority else ["origin"]),
                *refspecs,
            )
        if authority is not None and branch != "main":
            git_command(checkout, "fetch", "--no-tags", "origin", f"+refs/heads/main:{main_ref}")
        tip = git_command(checkout, "rev-parse", "--verify", f"{remote_ref}^{{commit}}")
        main_head = tip if branch == "main" else git_command(
            checkout, "rev-parse", "--verify", f"{main_ref}^{{commit}}",
        )
        if selection.channel == "stable":
            tag = f"refs/tags/{selection.ref}"
            git_command(
                checkout, "fetch", "--no-tags", *(["--", authority] if authority else ["origin"]),
                f"+{tag}:{tag}",
            )
            reference = tag
        else:
            reference = selection.ref if selection.channel == "exact" else tip
        commit = git_command(checkout, "rev-parse", "--verify", f"{reference}^{{commit}}")
        if selection.channel == "exact" and commit != selection.ref:
            raise SourceAcquisitionError("Runtime source did not resolve to the exact requested commit")
        if git_command(checkout, "merge-base", commit, tip) != commit:
            raise SourceAcquisitionError(f"Runtime source selection is not merged into fetched {branch}")
        tree = git_command(checkout, "rev-parse", "--verify", f"{commit}^{{tree}}")
        if _validate(checkout, owner, deadline=deadline, environment=environment) != head:
            raise SourceAcquisitionError("Runtime source checkout moved during acquisition")
        yield ResolvedSource(selection, checkout, commit, tree, tip, main_head)
        if _validate(checkout, owner, deadline=deadline, environment=environment) != head:
            raise SourceAcquisitionError("Runtime source checkout changed while consuming the selection")
