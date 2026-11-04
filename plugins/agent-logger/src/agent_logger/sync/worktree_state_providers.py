"""Passive exact-repository placement discovery through the selected owner."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs
from plugin_activation import normalize_remote, resolve_active_plugins

from agent_logger import configuration_layers
from agent_logger.aggregate import Admission
from agent_logger.sync.worktree_state import WorktreeStateError, decode_object

INVENTORY_CAPABILITY = "worktree-data-project-inventory-v1"
MAX_INVENTORY_BYTES = 1 << 20
MAX_PROJECT_WORKTREES = 128


@dataclass(frozen=True)
class ManagedPlacement:
    """Owner-qualified location, not validated contents or execution authority."""

    worktree: str
    creation_nonce: str
    store_id: str
    lane: str
    placement_generation: int
    state_home: Path
    canonical_record: str


@dataclass(frozen=True)
class ProjectInventory:
    """One complete passive owner observation for an admitted repository."""

    repository: str
    project: str
    placements: tuple[ManagedPlacement, ...]


@dataclass(frozen=True)
class EffortsExporter:
    """The exact activated script that negotiated complete export and provenance."""

    source: str
    script: Path


def _read(
    prefix: list[str],
    arguments: list[str],
    root: Path,
    environment: dict[str, str] | None,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [*prefix, *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise WorktreeStateError("selected worktree-data owner read failed") from error
    if result.returncode:
        detail = " ".join(result.stderr[:4096].decode("utf-8", errors="replace").split())[:1024]
        raise WorktreeStateError(
            f"selected worktree-data owner refused or is unsupported "
            f"(exit {result.returncode}): {detail}"
        )
    if len(result.stdout) > MAX_INVENTORY_BYTES:
        raise WorktreeStateError("complete owner inventory exceeds its byte budget")
    try:
        value = decode_object(result.stdout)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise WorktreeStateError("selected worktree-data owner returned invalid JSON") from error
    if (
        value.get("schema") != "agent-worktrees.worktree-data"
        or type(value.get("version")) is not int
        or value["version"] != 1
        or value.get("ok") is not True
    ):
        raise WorktreeStateError("selected worktree-data owner returned an unsupported response")
    return value


def _fields(value: Any, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise WorktreeStateError("owner inventory is partial or unsupported")
    return value


def _segment(value: Any) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}",
            value,
        )
        is None
    ):
        raise WorktreeStateError("owner inventory has an invalid identity")
    return value


def _scope(value: Any, project: str, marketplace: str | None) -> dict[str, Any]:
    scope = _fields(
        value,
        {
            "project",
            "repository",
            "repository_id",
            "marketplace_id",
            "project_root_device",
            "project_root_inode",
        },
    )
    if (
        scope["project"] != project
        or scope["repository"] != project
        or scope["marketplace_id"] != marketplace
        or any(
            type(scope[key]) is not int or scope[key] < 0
            for key in (
                "project_root_device",
                "project_root_inode",
            )
        )
    ):
        raise WorktreeStateError("owner inventory belongs to another project or installation")
    _segment(scope["repository_id"])
    return scope


def _placements(
    entries: Any,
    scope: dict[str, Any] | None,
) -> tuple[ManagedPlacement, ...]:
    if not isinstance(entries, list) or len(entries) > MAX_PROJECT_WORKTREES:
        raise WorktreeStateError("complete owner inventory exceeds its worktree count budget")
    if entries and scope is None:
        raise WorktreeStateError("registered worktrees require an owner-qualified scope")
    result: list[ManagedPlacement] = []
    seen_worktrees: set[str] = set()
    for raw in entries:
        worktree = _fields(
            raw,
            {
                "schema",
                "version",
                "scope",
                "worktree_id",
                "creation_nonce",
                "placements",
                "canonical_record",
                "live_authority",
            },
        )
        identity = _segment(worktree["worktree_id"])
        nonce = _segment(worktree["creation_nonce"])
        if (
            worktree["schema"] != "copilot-extensions.worktree-data"
            or type(worktree["version"]) is not int
            or worktree["version"] != 1
            or worktree["scope"] != scope
            or worktree["live_authority"] is not False
            or worktree["canonical_record"] not in ("matching", "missing")
            or identity in seen_worktrees
        ):
            raise WorktreeStateError("owner worktree inventory changed its qualified identity")
        seen_worktrees.add(identity)
        placements = worktree["placements"]
        if not isinstance(placements, list) or not 1 <= len(placements) <= 32:
            raise WorktreeStateError("owner worktree has an incomplete placement inventory")
        stores: set[str] = set()
        lanes: set[str] = set()
        for raw_placement in placements:
            placement = _fields(
                raw_placement,
                {
                    "scope",
                    "worktree_id",
                    "creation_nonce",
                    "domain",
                    "lane",
                    "store_id",
                    "placement_generation",
                    "state_home",
                    "state_home_allocated",
                    "store_content_validated",
                    "live_authority",
                },
            )
            store, lane = placement["store_id"], _segment(placement["lane"])
            home = placement["state_home"]
            if (
                placement["scope"] != scope
                or placement["worktree_id"] != identity
                or placement["creation_nonce"] != nonce
                or placement["domain"] != "agendas"
                or placement["state_home_allocated"] is not True
                or placement["store_content_validated"] is not False
                or placement["live_authority"] is not False
                or type(placement["placement_generation"]) is not int
                or placement["placement_generation"] < 1
                or not isinstance(store, str)
                or re.fullmatch("[0-9a-f]{32}", store) is None
                or store in stores
                or lane in lanes
                or not isinstance(home, str)
                or "\0" in home
                or not Path(home).is_absolute()
                or ".." in Path(home).parts
            ):
                raise WorktreeStateError("owner placement is incomplete or changes its source")
            stores.add(store)
            lanes.add(lane)
            result.append(
                ManagedPlacement(
                    identity,
                    nonce,
                    store,
                    lane,
                    placement["placement_generation"],
                    Path(home),
                    worktree["canonical_record"],
                )
            )
    return tuple(result)


def read_project_inventory(admission: Admission, home: Path) -> ProjectInventory:
    """Negotiate and read the actual admitted checkout's complete retained inventory.

    The aggregate input owner supplies the admitted checkout. No project alias is
    inferred from its basename, provenance path or the caller's ambient identity.
    """
    if not admission.enabled or admission.quarantine_reason:
        raise WorktreeStateError("repository admission is disabled or quarantined")
    root = admission.authoritative_checkout
    if ".." in root.parts:
        raise WorktreeStateError("admitted checkout contains parent traversal")
    try:
        prefix, environment, own = configuration_layers.worktrees_owner_command(home)
    except (OSError, ValueError, RuntimeError) as error:
        raise WorktreeStateError(str(error)) from error
    if not prefix:
        raise WorktreeStateError("selected worktree-data owner is unavailable")
    capabilities = _read(
        prefix,
        ["worktree-data-read", "--capabilities", "--json"],
        root,
        environment,
    )
    baseline = capabilities.get("capabilities")
    optional = capabilities.get("optional_capabilities")
    if (
        not isinstance(baseline, list)
        or "worktree-data-v1" not in baseline
        or not isinstance(optional, list)
        or INVENTORY_CAPABILITY not in optional
        or any(not isinstance(item, str) for item in [*baseline, *optional])
    ):
        raise WorktreeStateError("selected owner lacks worktree-data-project-inventory-v1")
    reply = _read(prefix, ["worktree-data-read", "--inventory", "--json"], root, environment)
    project = _segment(reply.get("project"))
    if reply.get("capability") != INVENTORY_CAPABILITY:
        raise WorktreeStateError("selected owner did not return the negotiated inventory")
    data = _fields(
        reply.get("data"),
        {
            "project",
            "repository_identity",
            "scope",
            "worktrees",
            "live_authority",
        },
    )
    if (
        data["project"] != project
        or data["live_authority"] is not False
        or data["repository_identity"] != normalize_remote("https://" + admission.repository)
    ):
        raise WorktreeStateError("selected owner inventory differs from the admitted repository")
    marketplace = own["marketplaceId"] if own is not None else None
    scope = None if data["scope"] is None else _scope(data["scope"], project, marketplace)
    return ProjectInventory(admission.repository, project, _placements(data["worktrees"], scope))


_SCRIPT_ENTRY = (
    "import runpy,sys; from pathlib import Path; script=sys.argv.pop(1); "
    "sys.path.insert(0,str(Path(script).parent)); runpy.run_path(script,run_name='__main__')"
)


def select_efforts_exporter(root: Path) -> EffortsExporter:
    """Select only this checkout's effective Efforts activation, then negotiate info.

    No other repository's activation, package version or rich-reader availability
    establishes support for the selected export provider.
    """
    if not root.is_absolute() or ".." in root.parts:
        raise WorktreeStateError("efforts selection requires the exact absolute checkout")
    try:
        report = resolve_active_plugins(plugin_names=("efforts",), project_root=root)
        if (
            report.authority.value != "complete"
            or len(report.decisions) != 1
            or len(report.active) != 1
        ):
            raise WorktreeStateError("one unambiguous enabled efforts provider is required")
        plugin = next(iter(report.active.values()))
        selected = plugin.root_for_scope("project:current") or plugin.root_for_scope("global")
        if selected is None or plugin.name != "efforts":
            raise WorktreeStateError("efforts provider has no selected activation scope")
        script = selected / "skills/driving-effort-agendas/scripts/agenda_export_cli.py"
        if (
            not script.is_file()
            or script.resolve() != script
            or any(
                member.is_symlink() or getattr(member.lstat(), "st_reparse_tag", 0)
                for member in (selected, script, *script.parents)
                if member == selected or selected in member.parents
            )
        ):
            raise WorktreeStateError("selected efforts export path is missing or indirect")
        environment = configuration_layers._peer_launch.legacy_peer_environment(dict(os.environ))
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _SCRIPT_ENTRY, str(script), "info"],
            cwd=root,
            env=environment,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
            **no_window_kwargs(),
        )
        if result.returncode or len(result.stdout) > 65536:
            raise WorktreeStateError("selected efforts export info refused or exceeded its bound")
        value = decode_object(result.stdout)
        capabilities, optional = value.get("capabilities"), value.get("optional_capabilities")
        if (
            value.get("schema") != "efforts.agenda-export-cli"
            or type(value.get("version")) is not int
            or value["version"] != 1
            or value.get("ok") is not True
            or not isinstance(capabilities, list)
            or "agenda-complete-export-v1" not in capabilities
            or not isinstance(optional, list)
            or "agenda-export-contributors-v1" not in optional
            or any(not isinstance(item, str) for item in [*capabilities, *optional])
        ):
            raise WorktreeStateError("selected efforts provider lacks complete export provenance")
        return EffortsExporter(plugin.source, script)
    except WorktreeStateError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise WorktreeStateError("selected efforts export provider is unavailable") from error
