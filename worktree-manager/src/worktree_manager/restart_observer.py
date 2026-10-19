"""Bounded black-box sampling owned by the existing active mux daemon."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID

from machine_transport import detect_platform

from . import engine_client
from .mux_mapping_registry import MuxMappingRegistry
from .restart_evidence import (
    NATIVE_BINDING_PROOF, PositiveEvidence, RecoveryEvidenceError, RecoveryJournal,
)


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HostEpoch:
    """Native execution-environment identity; never inferred from inherited env."""

    platform: str
    environment_id: str
    boot_id: str


def native_epoch() -> HostEpoch:
    """Read a provable Linux/WSL epoch, refusing unqualified native platforms."""
    if not sys.platform.startswith("linux"):
        raise RecoveryEvidenceError("Native restart evidence is not qualified on this platform.")
    if any(Path(marker).exists() for marker in ("/.dockerenv", "/run/.containerenv")):
        raise RecoveryEvidenceError("Container restart epochs are not qualified.")
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8")
        if any(tag in cgroup for tag in ("/docker", "/kubepods", "containerd", "/lxc", "libpod")):
            raise RecoveryEvidenceError("Container restart epochs are not qualified.")
        platform = detect_platform()
        boot_id = str(UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
        environment = Path("/etc/machine-id").read_text(encoding="ascii").strip()
        if len(environment) != 32 or any(c not in "0123456789abcdef" for c in environment):
            raise ValueError("invalid native environment identity")
        if platform == "wsl":
            # A distro can restart while its shared WSL kernel stays running.
            process = Path("/proc/1/stat").read_text(encoding="ascii")
            start = process.rsplit(")", 1)[1].split()[19]
            if not start.isdecimal():
                raise ValueError("invalid native init identity")
            boot_id += ":" + start
        return HostEpoch(platform, environment, boot_id)
    except (OSError, ValueError, IndexError) as exc:
        raise RecoveryEvidenceError("Native boot/environment identity is unavailable.") from exc


def _owner_probe(project: str, worktree_id: str) -> dict:
    return engine_client.run_json(
        project,
        ["session-role", "--hosting-evidence", "--worktree-id", worktree_id, "--json"],
        timeout=4,
    )


def _mapping_identity(entry: dict) -> tuple:
    return tuple(entry.get(key) for key in (
        "project", "worktree_id", "worktree_path", "mux_session",
        "session_incarnation", "mapping_revision", "live",
    ))


class RestartObserver:
    """At most one owner probe per 20 seconds, inside the active daemon lease."""

    def __init__(
        self, root: Path, *, epoch: HostEpoch | None = None,
        owner_probe: Callable[[str, str], dict] = _owner_probe,
    ) -> None:
        self.journal = RecoveryJournal(root)
        self.epoch = epoch
        self.owner_probe = owner_probe
        self.last_result: dict = {"applied": False, "reason": "not-observed"}
        self.startup_result: dict = {"candidates": [], "automatic_eligible": False}
        self._next_attempt_at = 0.0
        self._last_key: tuple[str, str] | None = None
        self._startup_complete = False
        self._storage_recovery = False

    def startup(self, *, recover_existing: bool = False) -> dict:
        """Freeze previous-epoch evidence before ordinary mux reconciliation.

        Inspection creates no launch intent, claims or enrollment. Incomplete
        admission remains explicitly unconfirmed, including sudden power loss.
        """
        self._startup_complete = False
        self._storage_recovery = recover_existing
        rows = []
        try:
            rows = self.journal.snapshot(recover_existing=recover_existing)
            if self.epoch is None:
                self.epoch = native_epoch()
            candidates = []
            for row in rows:
                evidence = row["last_positive"]
                same_environment = (
                    evidence["platform"] == self.epoch.platform
                    and evidence["environment_id"] == self.epoch.environment_id
                )
                reason = (
                    "explicitly-closed" if row["disposition"] == "intentional"
                    else "foreign-environment" if not same_environment
                    else "same-boot" if evidence["boot_id"] == self.epoch.boot_id
                    else "previous-boot-unconfirmed"
                )
                candidates.append({**row, "reason": reason})
            self.startup_result = {"candidates": candidates, "automatic_eligible": False}
            self._startup_complete = True
        except RecoveryEvidenceError as exc:
            self.startup_result = {
                "candidates": [{**row, "reason": "epoch-unconfirmed"} for row in rows],
                "automatic_eligible": False, "blocker": str(exc),
            }
            log.warning("Restart evidence startup blocked: %s", exc)
        return self.startup_result

    def observe(self, registry: MuxMappingRegistry, *, now: float | None = None) -> None:
        """Persist only independently owner-confirmed, identity-fenced evidence."""
        started = time.monotonic() if now is None else now
        if started < self._next_attempt_at:
            return
        self._next_attempt_at = started + 20.0
        try:
            if not self._startup_complete:
                self.startup(recover_existing=self._storage_recovery)
            if not self._startup_complete or self.epoch is None:
                self.last_result = {"applied": False, "reason": "startup-blocked"}
                return
            entries = sorted(
                (key, entry) for key, entry in registry.snapshot().items()
                if entry["live"] and entry.get("session_incarnation")
            )
            if not entries:
                self.last_result = {"applied": False, "reason": "no-live-mapping"}
                return
            key, entry = next(
                (item for item in entries if self._last_key is None or item[0] > self._last_key),
                entries[0],
            )
            self._last_key = key
            owner = self.owner_probe(*key)
            if not isinstance(owner, dict) or owner.get("confirmed") is not True:
                self.last_result = {"applied": False, "reason": "owner-unconfirmed"}
                return
            if (
                owner.get("schema") != "native-hosting-evidence"
                or type(owner.get("version")) is not int or owner["version"] != 1
                or owner.get("worktree_id") != entry["worktree_id"]
                or owner.get("worktree_path") != entry.get("worktree_path")
                or owner.get("mux_session") != entry["mux_session"]
                or owner.get("session_incarnation") != entry["session_incarnation"]
                or owner.get("platform") != self.epoch.platform
                or owner.get("liveness") != "live"
                or owner.get("binding_proof") != NATIVE_BINDING_PROOF
            ):
                raise ValueError("Native hosting owner returned incompatible evidence")
            current = registry.get(*key)
            if current is None or _mapping_identity(current) != _mapping_identity(entry):
                self.last_result = {"applied": False, "reason": "mapping-changed"}
                return
            rows = self.journal.snapshot()
            previous = next(
                (row["last_positive"] for row in rows if (
                    row["last_positive"]["project"], row["last_positive"]["worktree_id"],
                ) == key), None,
            )
            sequence = 1 if previous is None else previous["observation_sequence"] + 1
            evidence = PositiveEvidence(
                project=entry["project"], worktree_id=entry["worktree_id"],
                worktree_path=entry["worktree_path"],
                native_session_id=owner["native_session_id"],
                native_incarnation=owner["native_incarnation"],
                binding_proof=owner["binding_proof"],
                owner_revision=owner["owner_revision"],
                session_incarnation=entry["session_incarnation"],
                mapping_revision=entry["mapping_revision"], platform=self.epoch.platform,
                environment_id=self.epoch.environment_id, boot_id=self.epoch.boot_id,
                observed_at=time.time(), observation_sequence=sequence,
            )
            self.last_result = self.journal.observe(evidence)
        except (engine_client.EngineError, RecoveryEvidenceError, OSError, ValueError, KeyError) as exc:
            self.last_result = {"applied": False, "reason": "evidence-unavailable", "error": str(exc)}
            log.warning("Restart evidence observation failed: %s", exc)
        finally:
            # Real slow probes do not pull the next attempt closer; test clocks
            # use explicit monotonic values without sleeping.
            if now is None:
                self._next_attempt_at = time.monotonic() + 20.0
