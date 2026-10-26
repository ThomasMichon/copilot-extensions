"""Installed-selection fixtures and read-only fake Manager protocols."""

from __future__ import annotations

import json
import os
import shutil
import sys
import venv
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_machines.fleet_update import CommandResult
from agent_machines.manager_compatibility import ManagerUpdateResult
from agent_machines.manager_selection import (
    BOOTSTRAP_FILES,
    POSIX_SELECTION_STUB,
    RETAINED_POSIX_SELECTION_STUB,
    RETAINED_WINDOWS_SELECTION_STUB,
    WINDOWS_SELECTION_STUB,
)

LEGACY_HELP = (
    "usage: worktree-manager [--version] [--help] <command>\n\ncommands:\n"
    "  --project NAME         launch a registered project\n"
    "  doctor                 read prerequisites\n"
    "  setup                  explicit setup\n"
    "  self-install           explicit installation\n"
    "  update                 update the Manager and harness\n"
    "  projects               list registered projects\n"
    "  repos                  list registered repositories\n"
    "  worktrees              inspect worktrees\n"
    "A bare invocation reads current installation state without changing it.\n"
)
GUARDED_HELP = LEGACY_HELP.replace(
    "  update ",
    "  automatic-updates      consent and ownership\n  update ",
    1,
)
CURRENT_HELP = GUARDED_HELP.replace(
    "  update                 update the Manager and harness\n",
    "  update [--wait|--detach|--supervised-debug]  submit and observe a durable suite update\n"
    "  update --request-status ID [--json]        read a durable request without installing\n"
    "  update --recovery-status|--recover-after-restart [--json]  inspect/settle interrupted work\n",
)


def default_policy() -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.automatic-updates",
        "version": 1,
        "owner": "disabled",
        "provenance": "default",
        "user_disabled": False,
        "interval_seconds": 3600,
        "project": None,
        "external_controller": None,
    }


@dataclass
class InstalledManager:
    home: Path
    root: Path
    command: Path
    slot: Path
    version: str
    retained: bool = False

    @property
    def payload(self) -> Path:
        return self.slot / "payload" if self.retained else self.slot

    @property
    def invocation(self) -> list[str]:
        if not self.retained:
            return [str(self.command)]
        return [
            str(self.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
            "-I", "-S", "-B", "-X", "utf8",
            str(self.slot / ".runtime-bootstrap/runtime_launcher.py"),
            "-m", "worktree_manager",
        ]

    def arguments(self, command: list[str]) -> list[str]:
        assert command[:len(self.invocation)] == self.invocation
        return command[len(self.invocation):]


def installed_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str = "1.2.3",
    retained: bool = False,
    generation: str | None = None,
) -> InstalledManager:
    home = tmp_path / "home"
    root = home / ".worktree-manager"
    generation = generation or version
    slot = root / "versions" / generation
    payload = slot / "payload" if retained else slot
    package = payload / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (root / "current-version").write_text(generation + "\n", encoding="utf-8")
    if retained:
        venv.EnvBuilder(with_pip=False).create(slot)
        scripts = Path(__file__).resolve().parents[3] / "worktree-manager" / "scripts"
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir()
        for name in BOOTSTRAP_FILES:
            shutil.copyfile(scripts / name, bootstrap / name)
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": generation, "completed_at": "2026-01-01T00:00:00Z",
            "pid": 1, "payload_hash": "a" * 64,
        }), encoding="utf-8")
    else:
        (slot / ".install-complete").write_bytes(b"")
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "copilot-extensions-worktree-manager"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (package / "__main__.py").write_text("# selected command fixture\n", encoding="utf-8")
    command = (
        home
        / ".local"
        / "bin"
        / ("worktree-manager.cmd" if os.name == "nt" else "worktree-manager")
    )
    command.parent.mkdir(parents=True)
    stub = (
        RETAINED_WINDOWS_SELECTION_STUB if os.name == "nt" else RETAINED_POSIX_SELECTION_STUB
    ) if retained else (
        WINDOWS_SELECTION_STUB if os.name == "nt" else POSIX_SELECTION_STUB
    )
    command.write_bytes(stub.encode("utf-8"))
    command.chmod(0o755)
    if retained:
        site = slot / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        shutil.copytree(package, site / "worktree_manager")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    return InstalledManager(home, root, command, slot, version, retained)


@dataclass
class ManagerProtocol:
    installation: InstalledManager
    guarded: bool
    calls: list[list[str]] = field(default_factory=list)
    replies: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    before: Callable[[list[str]], None] | None = None
    update: Callable[[list[str]], CommandResult] | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> CommandResult:
        args = self.installation.arguments(argv)
        self.calls.append(list(argv))
        if self.before is not None:
            self.before(args)
        if tuple(args) in self.replies:
            return self.replies[tuple(args)]
        if args == ["--version"]:
            return CommandResult(argv, 0, f"worktree-manager {self.installation.version}\n")
        if args == ["--help"]:
            return CommandResult(argv, 0, CURRENT_HELP if self.guarded else LEGACY_HELP)
        if args == ["automatic-updates", "ownership", "--json"]:
            assert self.guarded, "never use an unknown legacy verb as capability evidence"
            path = self.installation.root / "automatic-updates.json"
            policy = json.loads(path.read_text()) if path.exists() else default_policy()
            return CommandResult(
                argv,
                0,
                json.dumps({"root": str(self.installation.root), "policy": policy}),
            )
        assert args[0] == "update"
        if self.update is not None:
            return self.update(argv)
        return CommandResult(argv, 0, "updated")

    @property
    def updates(self) -> list[list[str]]:
        return [
            command for command in self.calls
            if self.installation.arguments(command)[0] == "update"
        ]


def unit_guarded_manager_update(*, runner: Any, home: Path | None = None) -> ManagerUpdateResult:
    """Existing run-result unit tests isolate negotiation, tested separately."""
    command = ["worktree-manager", "update", "--automatic-trigger", "legacy-agent-machines"]
    outcome = runner(command, timeout=3600)
    return ManagerUpdateResult(
        "ok" if outcome.returncode == 0 else "error",
        outcome.output,
        mode="guarded",
        command=command,
        returncode=outcome.returncode,
    )
