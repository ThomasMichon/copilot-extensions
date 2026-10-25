"""Negotiate read-only Manager capability before a consented legacy fleet update."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from zdd.cutover_lock import CutoverLockedError

from .automatic_update_ownership import (
    LEGACY_FLEET_TRIGGER,
    _unique_object,
    read_update_ownership,
    suite_update_transaction,
    validate_ownership_payload,
)
from .manager_selection import (
    ManagerGenerationChanged,
    ManagerSelection,
    ManagerSelectionRefused,
    read_manager_selection,
    require_same_selection,
)


class CommandOutcome(Protocol):
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str: ...


class ManagerCapabilityRefused(RuntimeError):
    """A probe is not positive evidence of either supported protocol."""


class OwnershipChanged(RuntimeError):
    """Another owner or an explicit disable superseded standalone admission."""


@dataclass(frozen=True)
class ManagerUpdateResult:
    status: str
    detail: str
    mode: str | None = None
    command: list[str] | None = None
    returncode: int | None = None


Runner = Callable[..., CommandOutcome]


def _require_standalone(home: Path | None) -> None:
    ownership = read_update_ownership(home)
    if ownership.state == "refused":
        raise ManagerCapabilityRefused(ownership.detail)
    if ownership.state != "standalone":
        raise OwnershipChanged(ownership.detail + "; no legacy automatic update")


def _probe(
    selection: ManagerSelection,
    args: list[str],
    *,
    runner: Runner,
    home: Path | None,
) -> str:
    require_same_selection(selection, home)
    result = runner([*selection.invocation, *args], timeout=60)
    require_same_selection(selection, home)
    if result.returncode != 0 or result.stderr.strip():
        raise ManagerCapabilityRefused(
            f"read-only Manager probe {args!r} failed ({result.returncode}): "
            f"{result.output[:4096]}"
        )
    if len(result.stdout) > 65536:
        raise ManagerCapabilityRefused("read-only Manager probe exceeded its output bound")
    return result.stdout


def _help_protocol(text: str) -> str:
    lines = text.splitlines()
    if (
        not lines
        or lines[0] != "usage: worktree-manager [--version] [--help] <command>"
        or "commands:" not in lines
        or "A bare invocation reads current installation state without changing it." not in lines
        or not re.search(r"(?m)^  --project NAME[ \t]+\S", text)
    ):
        raise ManagerCapabilityRefused("Manager global --help is not a known read-only protocol")
    for name in ("doctor", "setup", "self-install", "projects", "repos", "worktrees"):
        if len(re.findall(rf"(?m)^  {re.escape(name)}[ \t]+\S", text)) != 1:
            raise ManagerCapabilityRefused(f"ambiguous Manager global --help entry: {name}")
    updates = re.findall(r"(?m)^  update[ \t]+(\S.*)$", text)
    single_update = len(updates) == 1 and not updates[0].startswith(
        ("--request-status", "--recovery-status", "--recover-for-repair")
    )
    durable_prefixes = (
        "[--wait|--detach|--supervised-debug]  ",
        "--request-status ID [--json]  ",
        "--recovery-status|--recover-after-restart [--json]  ",
        "--recover-for-repair [--json]  ",
    )
    known_durable = len(updates) in (3, 4) and all(
        update.startswith(prefix) for update, prefix in zip(updates, durable_prefixes)
    )
    if not single_update and not known_durable:
        raise ManagerCapabilityRefused("ambiguous Manager global --help entry: update")
    automatic = re.findall(r"(?m)^  automatic-updates[ \t]+\S", text)
    if len(automatic) == 1:
        return "guarded"
    if automatic or "automatic-updates" in text:
        raise ManagerCapabilityRefused("ambiguous Manager automatic-update capability")
    return "legacy"


def _negotiate(selection: ManagerSelection, *, runner: Runner, home: Path | None) -> str:
    version = _probe(selection, ["--version"], runner=runner, home=home)
    if version.strip() != f"worktree-manager {selection.version}":
        raise ManagerCapabilityRefused("Manager --version disagrees with the installed selection")
    mode = _help_protocol(_probe(selection, ["--help"], runner=runner, home=home))
    if mode == "legacy":
        return mode
    if not selection.retained_command:
        raise ManagerCapabilityRefused("guarded Manager requires an immutable retained runtime")
    text = _probe(
        selection,
        ["automatic-updates", "ownership", "--json"],
        runner=runner,
        home=home,
    )
    try:
        reply = json.loads(text, object_pairs_hook=_unique_object)
        if not isinstance(reply, dict) or set(reply) != {"root", "policy"}:
            raise ValueError("ownership reply must contain exactly root and policy")
        root = reply["root"]
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError("ownership root must be an absolute path")
        if Path(root) != selection.root:
            raise ValueError("ownership root disagrees with the installed standard root")
        owner = validate_ownership_payload(reply["policy"])
        _require_standalone(home)
        if (
            owner != "disabled"
            or reply["policy"]["user_disabled"]
            or reply["policy"]["provenance"] != "default"
        ):
            raise ValueError("ownership reply disagrees with the absent local policy")
    except (ValueError, TypeError, OSError) as exc:
        raise ManagerCapabilityRefused(f"Manager ownership reply refused: {exc}") from exc
    return mode


def _execute(
    selection: ManagerSelection,
    mode: str,
    *,
    runner: Runner,
    home: Path | None,
) -> ManagerUpdateResult:
    _require_standalone(home)
    require_same_selection(selection, home)
    command = [*selection.invocation, "update"]
    if mode == "guarded":
        command.extend(["--automatic-trigger", LEGACY_FLEET_TRIGGER])
    outcome = runner(command, timeout=3600)
    status = (
        "ok" if outcome.returncode == 0 else "deferred" if outcome.returncode == 5 else "error"
    )
    return ManagerUpdateResult(
        status,
        f"{mode} Manager exit {outcome.returncode}: {outcome.output or 'update completed'}",
        mode=mode,
        command=command,
        returncode=outcome.returncode,
    )


def run_compatible_manager_update(
    *,
    runner: Runner,
    home: Path | None = None,
) -> ManagerUpdateResult:
    """No fallback after an update; only a proven legacy protocol can omit the marker."""
    try:
        _require_standalone(home)
        selection = read_manager_selection(home)
        mode = _negotiate(selection, runner=runner, home=home)
        if mode == "guarded":
            return _execute(selection, mode, runner=runner, home=home)
        with suite_update_transaction(home):
            _require_standalone(home)
            require_same_selection(selection, home)
            if _negotiate(selection, runner=runner, home=home) != "legacy":
                raise ManagerGenerationChanged("Manager capability changed before legacy dispatch")
            return _execute(selection, "legacy", runner=runner, home=home)
    except (OwnershipChanged, ManagerGenerationChanged, CutoverLockedError) as exc:
        return ManagerUpdateResult("deferred", f"legacy fleet update deferred: {exc}")
    except (
        ManagerSelectionRefused,
        ManagerCapabilityRefused,
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
    ) as exc:
        return ManagerUpdateResult("error", f"Manager compatibility refused: {exc}")
