"""Finite process client for the selected Efforts export and readback boundary."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

from agent_logger import configuration_layers
from agent_logger.sync.worktree_state import (
    MAX_MANIFEST_BYTES,
    WorktreeStateError,
    WorktreeStateRevision,
    decode_object,
)
from agent_logger.sync.worktree_state_providers import (
    _SCRIPT_ENTRY,
    EffortsExporter,
    ManagedPlacement,
    select_efforts_exporter,
)


@dataclass(frozen=True)
class EffortsExportClient:
    """Requalify the effective provider before every command, on one shared clock."""

    checkout: Path
    selected: EffortsExporter
    budget: OwnedExecutionBudget

    def _command(self, command: str, arguments: list[str], field: str) -> dict[str, Any]:
        if select_efforts_exporter(self.checkout, budget=self.budget) != self.selected:
            raise WorktreeStateError("effective efforts export provider changed")
        environment = configuration_layers._peer_launch.legacy_peer_environment(dict(os.environ))
        try:
            result = run_caller_owned(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    _SCRIPT_ENTRY,
                    str(self.selected.script),
                    command,
                    *arguments,
                ],
                cwd=self.checkout,
                env=environment,
                timeout=120,
                max_stdout_bytes=(1 << 20) + MAX_MANIFEST_BYTES,
                max_stderr_bytes=4096,
                binary=True,
                budget=self.budget,
            )
            value = decode_object(result.stdout)
        except OwnedCleanupError:
            raise
        except (
            OSError,
            ValueError,
            UnicodeError,
            RecursionError,
            subprocess.SubprocessError,
        ) as error:
            raise WorktreeStateError("selected efforts export command failed") from error
        if (
            result.returncode
            or value.get("schema") != "efforts.agenda-export-cli"
            or type(value.get("version")) is not int
            or value["version"] != 1
            or value.get("ok") is not True
            or not isinstance(value.get(field), dict)
        ):
            raise WorktreeStateError("selected efforts export command refused or is unsupported")
        return value[field]

    def export(
        self,
        directory: Path,
        repository: str,
        placement: ManagedPlacement,
    ) -> WorktreeStateRevision:
        """Capture the owner's actual consistent revision, never invent a revision pin."""
        manifest = self._command(
            "export",
            [
                "--state-home",
                str(placement.state_home),
                "--destination",
                str(directory),
                "--worktree",
                placement.worktree,
                "--store-id",
                placement.store_id,
                "--contract-version",
                "1",
            ],
            "manifest",
        )
        if (
            manifest.get("schema") != "efforts.agenda-export"
            or type(manifest.get("version")) is not int
            or manifest["version"] != 1
            or manifest.get("completeness") != "complete"
            or manifest.get("worktree") != placement.worktree
            or manifest.get("store_id") != placement.store_id
        ):
            raise WorktreeStateError("efforts export changed its selected store identity")
        return WorktreeStateRevision(
            repository,
            placement.worktree,
            placement.creation_nonce,
            placement.store_id,
            manifest.get("revision"),
        )

    def _readback(
        self,
        command: str,
        field: str,
        directory: Path,
        *,
        worktree: str,
        store_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        return self._command(
            command,
            [
                "--destination",
                str(directory),
                "--worktree",
                worktree,
                "--store-id",
                store_id,
                "--expected-revision",
                str(expected_revision),
                "--contract-version",
                "1",
            ],
            field,
        )

    def verify(
        self,
        directory: Path,
        *,
        worktree: str,
        store_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Verify the complete exported snapshot through its selected domain owner."""
        return self._readback(
            "verify",
            "manifest",
            directory,
            worktree=worktree,
            store_id=store_id,
            expected_revision=expected_revision,
        )

    def contributors(
        self,
        directory: Path,
        *,
        worktree: str,
        store_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Read lossless provenance from the same revision-pinned export snapshot."""
        return self._readback(
            "contributors",
            "contributors",
            directory,
            worktree=worktree,
            store_id=store_id,
            expected_revision=expected_revision,
        )
