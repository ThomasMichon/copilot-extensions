"""Keep application-root Manager updates on the complete application path."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import TYPE_CHECKING
import uuid

from . import application_footprints, runtime_identity
from .runtime_source import SourceAcquisitionError, acquire
from .runtime_source_config import RuntimeSource
from .runtime_source_prepare import prepare_footprint, preparer

if TYPE_CHECKING:
    from .self_install import SelfUpdateResult


@contextmanager
def _acquisition_root(root: Path, *, disposable: bool = False) -> Iterator[Path]:
    if os.name != "nt" and not disposable:
        yield root
        return
    from agent_procutil import _daemon_instance_owner, python_cache_permissions, scratch

    # Git's worktree metadata still needs ordinary-path headroom on Windows.
    parent = Path(tempfile.gettempdir()).resolve(strict=True)
    python_cache_permissions.check(parent, ancestor=True)
    python_cache_permissions.check_ancestors(parent)
    temporary = parent / (".s-" + uuid.uuid4().hex[:8])
    _daemon_instance_owner.mkdir_private(temporary)
    identity = temporary.stat()
    try:
        yield temporary
    finally:
        if not scratch.remove_scratch(
            temporary, expected_directory_identity=(identity.st_dev, identity.st_ino),
        ):
            raise OSError(f"Temporary Manager source cleanup is incomplete: {temporary}")


def is_application(root: Path) -> bool:
    """Recognize physical or selected app installations without guessing root names."""
    from .self_install import current_version

    running = runtime_identity.running_application_slot()
    if running is not None and running.parent.parent.resolve() == root:
        return True
    selected = current_version(root)
    return bool(
        selected and re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", selected)
        and runtime_identity.is_application_slot(root / "versions" / selected)
    )


def _selection(repo: str, ref: str) -> RuntimeSource:
    if ref == "dev":
        return RuntimeSource("dev", repo=repo, allow_dev=True)
    if ref == "main":
        return RuntimeSource("main", repo=repo)
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", ref):
        return RuntimeSource("exact", repo=repo, ref=ref)
    return RuntimeSource("stable", repo=repo, ref=ref)


def update(root: Path, *, repo: str, ref: str, dry_run: bool) -> SelfUpdateResult:
    """Use canonical acquisition, promotion, copy and setup, never legacy installation."""
    from . import self_install, source_config, update_auth

    previous = self_install.current_version(root)
    version = None
    if shutil.which("git") is None:
        return self_install.SelfUpdateResult(
            action="skipped", previous=previous,
            reason="Manager application-footprint acquisition requires git; no legacy fallback",
        )
    deadline = time.monotonic() + 1800
    base = root / "state"
    try:
        selection = _selection(source_config.absolute_local_source(repo), ref)
        base.mkdir(parents=True, exist_ok=True)
        environment = update_auth.environment(selection.repo, cwd=base)
        with _acquisition_root(base, disposable=True) as acquisition_root:
            output = acquisition_root / "footprints" / uuid.uuid4().hex
            output.parent.mkdir()
            with acquire(
                selection, root=acquisition_root, environment=environment,
                deadline=deadline, compact=True,
            ) as resolved:
                footprint = prepare_footprint(
                    preparer(), resolved, output, deadline=deadline, environment=environment,
                )
            applications = application_footprints.plan(
                footprint, root.parent, selected=("worktree-manager",),
            )
            if len(applications) != 1 or applications[0].name != "worktree-manager":
                raise ValueError("Manager footprint requires an unsupported IPC dependency")
            application = replace(applications[0], root=root)
            declaration = json.loads(application.declaration)
            construction_identity = declaration["construction"].get("identity")
            identity = (
                construction_identity.get("runtime_source")
                if isinstance(construction_identity, dict) else None
            )
            if (
                not isinstance(identity, dict)
                or identity.get("source_commit") != resolved.commit
                or identity.get("source_tree") != resolved.tree
            ):
                raise ValueError("Manager footprint differs from its independent source selection")
            application = application_footprints.select_generation(
                application, previous, qualify=self_install._expected_frontdoor_contents,
                deadline=deadline,
            )
            version = application.version
            if dry_run:
                return self_install.SelfUpdateResult(
                    action="updated", previous=previous, version=version,
                    reason="planned complete application setup; no selection changed",
                )
            application_footprints.install((application,), stage=False, deadline=deadline)
            if self_install.current_version(root) != version:
                raise ValueError("Manager application setup did not select the incoming generation")
            return self_install.SelfUpdateResult(
                action="already-current" if previous == version else "updated",
                previous=previous, version=version,
            )
    except update_auth.AuthRequired as error:
        return self_install.SelfUpdateResult(
            action="auth_required", previous=previous, reason=str(error),
        )
    except (SourceAcquisitionError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        detail = (
            "\n" + error.stderr[-65536:].strip()
            if isinstance(error, subprocess.CalledProcessError) and isinstance(error.stderr, str)
            else ""
        )
        return self_install.SelfUpdateResult(
            action="error", previous=previous, version=version,
            reason=f"Manager application update failed: {error}{detail}",
        )
