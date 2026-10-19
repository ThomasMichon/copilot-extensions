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


def _owner_probe(project: str, worktree_id: str, native_session_id: str | None) -> dict:
    args = ["session-role", "--hosting-evidence", "--worktree-id", worktree_id, "--json"]
    if native_session_id is not None:
        args.extend(("--session-id", native_session_id))
    return engine_client.run_json(
        project, args, timeout=4,
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
        owner_probe: Callable[[str, str, str | None], dict] = _owner_probe,
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
                candidates.append({
                    **row, "reason": reason,
                    "capture_binding_qualified": PositiveEvidence(**evidence).recovery_binding_qualified,
                })
            self.startup_result = {"candidates": candidates, "automatic_eligible": False}
            self._startup_complete = True
        except RecoveryEvidenceError as exc:
            self.startup_result = {
                "candidates": [{**row, "reason": "epoch-unconfirmed"} for row in rows],
                "automatic_eligible": False, "blocker": str(exc),
            }
            log.warning("Restart evidence startup blocked: %s", exc)
        return self.startup_result

    def _lifecycle_exit(self, owner: dict, row: dict) -> dict:
        evidence = PositiveEvidence(**row["last_positive"])
        if (
            not evidence.lifecycle_qualified
            or owner.get("lifecycle_capability") != "source-bound-v1"
        ):
            return {"applied": False, "reason": "lifecycle-unqualified"}
        lifecycle = owner.get("lifecycle")
        if (
            not isinstance(lifecycle, dict)
            or lifecycle.get("schema") != "native-activation-evidence"
            or type(lifecycle.get("version")) is not int or lifecycle["version"] != 1
            or lifecycle.get("native_session_id") != evidence.native_session_id
            or type(lifecycle.get("owner_revision")) is not int
            or lifecycle["owner_revision"] < evidence.owner_revision
        ):
            raise ValueError("Native lifecycle owner returned incompatible evidence")
        activations = lifecycle.get("activations")
        if not isinstance(activations, list) or len(activations) > 256:
            raise ValueError("Invalid native lifecycle activation budget")
        ordinals = [
            item.get("ordinal") for item in activations if isinstance(item, dict)
        ]
        if (
            len(ordinals) != len(activations)
            or any(type(value) is not int or value <= 0 for value in ordinals)
            or len(set(ordinals)) != len(ordinals)
        ):
            raise ValueError("Invalid native lifecycle activation identity")
        matches = [
            item for item in activations
            if item.get("source_identity") == evidence.source_identity
        ]
        if (
            len(matches) != 1 or matches[0]["ordinal"] != evidence.activation_ordinal
            or matches[0].get("source_ambiguous") is not False
        ):
            return {"applied": False, "reason": "lifecycle-unconfirmed"}
        ended = matches[0].get("ended_at")
        if ended is None:
            return {"applied": False, "reason": "activation-open"}
        if not isinstance(ended, str) or not ended or len(ended) > 128:
            raise ValueError("Invalid native lifecycle end observation")
        for field, limit in (("end_reason", 64), ("end_source", 128)):
            value = matches[0].get(field)
            if value is not None and (
                not isinstance(value, str) or not value or len(value) > limit
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"Invalid native lifecycle {field}")
        reason = "unknown"
        detail = "native-end-cause-unconfirmed"
        if (
            matches[0].get("end_reason") == "user_exit"
            and matches[0].get("end_source") in {"hook", "hook:exit"}
        ):
            reason = "intentional"
            detail = "native-session-end"
        return self.journal.record_exit(
            evidence, reason=reason, detail=detail,
            end_consumed=matches[0].get("end_source") in {"hook", "hook:exit"},
        )

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
            mappings = registry.snapshot()
            entries = {
                key: entry for key, entry in mappings.items()
                if entry["live"] and entry.get("session_incarnation")
            }
            rows = {
                (row["last_positive"]["project"], row["last_positive"]["worktree_id"]): row
                for row in self.journal.snapshot()
            }
            keys = sorted(entries.keys() | {
                key for key, row in rows.items()
                if row["disposition"] != "intentional" and not row["end_consumed"]
            })
            if not keys:
                self.last_result = {"applied": False, "reason": "no-live-mapping"}
                return
            key = next(
                (item for item in keys if self._last_key is None or item > self._last_key),
                keys[0],
            )
            self._last_key = key
            entry = entries.get(key)
            previous_row = rows.get(key)
            previous = previous_row["last_positive"] if previous_row is not None else None
            owner = self.owner_probe(*key, previous["native_session_id"] if previous else None)
            if not isinstance(owner, dict):
                self.last_result = {"applied": False, "reason": "owner-unconfirmed"}
                return
            if (
                owner.get("schema") != "native-hosting-evidence"
                or type(owner.get("version")) is not int or owner["version"] != 1
                or owner.get("worktree_id") != key[1]
                or owner.get("worktree_path") != (entry or previous)["worktree_path"]
                or owner.get("platform") != self.epoch.platform
            ):
                raise ValueError("Native hosting owner returned incompatible evidence")
            exit_result = (
                self._lifecycle_exit(owner, previous_row)
                if previous_row is not None else None
            )
            if owner.get("confirmed") is not True or entry is None:
                self.last_result = (
                    exit_result if exit_result and exit_result.get("applied")
                    else {"applied": False, "reason": "owner-unconfirmed"}
                )
                return
            if (
                owner.get("mux_session") != entry["mux_session"]
                or owner.get("session_incarnation") != entry["session_incarnation"]
                or owner.get("liveness") != "live"
                or owner.get("binding_proof") != NATIVE_BINDING_PROOF
            ):
                raise ValueError("Native hosting owner returned incompatible evidence")
            current = registry.get(*key)
            if current is None or _mapping_identity(current) != _mapping_identity(entry):
                self.last_result = {"applied": False, "reason": "mapping-changed"}
                return
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
                activation_ordinal=owner.get("activation_ordinal", 0),
                source_identity=owner.get("source_identity"),
                lifecycle_qualified=owner.get("lifecycle_qualified", False),
                worktree_incarnation=(
                    owner.get("worktree_incarnation")
                    if owner.get("recovery_binding_capability") == "worktree-head-v1" else None
                ),
                head_revision=(
                    owner.get("head_revision")
                    if owner.get("recovery_binding_capability") == "worktree-head-v1" else None
                ),
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
