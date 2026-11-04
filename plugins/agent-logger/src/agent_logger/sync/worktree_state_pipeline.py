"""Policy-first composition of managed placement, consistent export and local collection."""

from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from agent_procutil.owned_budget import OwnedExecutionBudget

from agent_logger.aggregate import Admission, ResolvedPlan
from agent_logger.config import Config
from agent_logger.sync.publication_directory import publication_directory
from agent_logger.sync.targets.base import Target
from agent_logger.sync.worktree_state import (
    MAX_MANIFEST_BYTES,
    WorktreeStateError,
    WorktreeStateReceipt,
    admit_repository_collection,
)
from agent_logger.sync.worktree_state_archive import collect_worktree_state
from agent_logger.sync.worktree_state_exporter import EffortsExportClient
from agent_logger.sync.worktree_state_providers import (
    ManagedPlacement,
    ProjectInventory,
    read_project_inventory,
    select_efforts_exporter,
)


def _requalify(
    admission: Admission,
    home: Path,
    inventory: ProjectInventory,
    placement: ManagedPlacement,
    budget: OwnedExecutionBudget,
) -> None:
    current = read_project_inventory(admission, home, budget=budget)
    if (
        current.repository != inventory.repository
        or current.project != inventory.project
        or current.owner_repository_identity != inventory.owner_repository_identity
        or current.scope != inventory.scope
        or placement not in current.placements
    ):
        raise WorktreeStateError("selected managed placement or owner scope changed")


@contextmanager
def _export_directory(
    cfg: Config,
    destination: Path,
    placement: ManagedPlacement,
    budget: OwnedExecutionBudget,
) -> Iterator[Path]:
    home = cfg.home.expanduser().absolute()
    base = home / "worktree-state-exports"
    for other in (destination, placement.state_home):
        other = other.expanduser().absolute()
        if base == other or base in other.parents or other in base.parents:
            raise WorktreeStateError("export staging must be disjoint from source and collection")
    if time.monotonic() >= budget.execution_deadline:
        raise WorktreeStateError("worktree export exhausted its shared time budget")
    with publication_directory(home) as owner:
        if owner.fd is None:
            raise WorktreeStateError("private export staging requires a qualified native adapter")
        try:
            os.mkdir(base.name, mode=0o700, dir_fd=owner.fd)
        except FileExistsError:
            pass
        with publication_directory(base, create=False) as parent:
            info = os.fstat(parent.fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise WorktreeStateError(
                    "export staging parent must be private and owner-controlled"
                )
            name = uuid4().hex
            os.mkdir(name, mode=0o700, dir_fd=parent.fd)
            directory = base / name
            original = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
            try:
                yield directory
                with publication_directory(directory, create=False) as staged:
                    held = os.fstat(staged.fd)
                    if (held.st_dev, held.st_ino) != (original.st_dev, original.st_ino):
                        raise WorktreeStateError("export staging identity changed before cleanup")
                    members = set(staged.names())
                    required = {"manifest.json", "agenda.sqlite3"}
                    if not required <= members <= required | {"manifest.pending.json"}:
                        raise WorktreeStateError("unexpected export staging content; retained")
                    if "manifest.pending.json" in members:
                        with staged.open("manifest.json", protected=True) as published:
                            raw = published.read(MAX_MANIFEST_BYTES + 1)
                        with staged.open("manifest.pending.json", protected=True) as prepared:
                            if (
                                len(raw) > MAX_MANIFEST_BYTES
                                or prepared.read(
                                    MAX_MANIFEST_BYTES + 1,
                                )
                                != raw
                            ):
                                raise WorktreeStateError(
                                    "prepared export manifest changed; retained"
                                )
                    for member in sorted(members):
                        with staged.open(member, protected=True):
                            pass
                    for member in sorted(members):
                        staged.unlink(member)
                    os.fsync(staged.fd)
                current = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    raise WorktreeStateError("export staging identity changed before cleanup")
                os.rmdir(name, dir_fd=parent.fd)
                os.fsync(parent.fd)
            except BaseException:
                print(
                    f"session-sync: worktree export staging retained: {directory}", file=sys.stderr
                )
                raise


def collect_managed_worktree_state(
    cfg: Config,
    target: Target,
    plan: ResolvedPlan,
    admission: Admission,
    inventory: ProjectInventory,
    placement: ManagedPlacement,
    budget: OwnedExecutionBudget,
) -> WorktreeStateReceipt:
    """Collect one currently admitted store; never allocate or mutate its source.

    The caller supplies one compile's typed admission and passive owner inventory.
    Both source placement and destination policy are requalified before export and
    after the consistent cut. Failed or uncertain staging is retained, not swept.
    """
    root = admit_repository_collection(cfg, target, plan, admission.repository)
    if inventory.repository != admission.repository or placement not in inventory.placements:
        raise WorktreeStateError("managed placement is not from the admitted repository inventory")
    _requalify(admission, cfg.home, inventory, placement, budget)
    selected = select_efforts_exporter(admission.authoritative_checkout, budget=budget)
    client = EffortsExportClient(admission.authoritative_checkout, selected, budget)
    admit_repository_collection(cfg, target, plan, admission.repository)
    with _export_directory(cfg, root, placement, budget) as directory:
        identity = client.export(directory, admission.repository, placement)
        _requalify(admission, cfg.home, inventory, placement, budget)
        admit_repository_collection(cfg, target, plan, admission.repository)
        return collect_worktree_state(
            cfg,
            target,
            plan,
            directory,
            identity,
            (),
            client.verify,
            contributors=client.contributors,
            budget=budget,
        )
