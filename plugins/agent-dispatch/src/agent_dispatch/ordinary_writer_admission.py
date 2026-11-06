"""Qualify Bridge's ordinary writer without claiming the rest of the cohort."""

from __future__ import annotations

import copy
import json
import re

from .ordinary_agent_facts import identity
from .queue_records import TaskError
from .queue_task_relationships import valid_record_writer

REQUIREMENT = "worktree_writer_requirement"


def writer_requirement(value: object) -> dict:
    if not (
        isinstance(value, dict) and set(value) == {"version", "identity", "creation_nonce"}
        and type(value["version"]) is int and value["version"] == 1
        and isinstance(value["creation_nonce"], str) and value["creation_nonce"].strip()
        and isinstance(value["identity"], dict)
        and set(value["identity"]) == {"installation_root", "runtime_generation"}
        and isinstance(value["identity"]["installation_root"], str)
        and value["identity"]["installation_root"].strip()
        and isinstance(value["identity"]["runtime_generation"], str)
        and re.fullmatch(r"[0-9a-f]{32}", value["identity"]["runtime_generation"])
    ):
        raise TaskError("ordinary Bridge writer requirement is unconfirmed")
    return copy.deepcopy(value)


def retain_requirement(previous: dict | None, incoming: dict) -> None:
    """Storage cannot remove, change or retrofit an original writer requirement."""
    if REQUIREMENT in incoming:
        writer_requirement(incoming[REQUIREMENT])
    if previous is not None and (
        (REQUIREMENT in previous) != (REQUIREMENT in incoming)
        or previous.get(REQUIREMENT) != incoming.get(REQUIREMENT)
    ):
        raise TaskError("ordinary Bridge original writer requirement cannot change")


def allocation_requirement(row: dict, project: str, value: dict | None) -> str | None:
    retained = row[REQUIREMENT]
    if value is None:
        return retained
    requirement = OrdinaryWriterAdmission(value, {**row, "allocation_project": project}).requirement
    if row["driver"] != "ordinary-bridge" or not row["creation_key"]:
        raise TaskError("ordinary Bridge writer requirement lacks its original creation key")
    if retained is not None and json.loads(retained) != requirement:
        raise TaskError("ordinary Bridge original writer requirement cannot change")
    if retained is None and (row["agent_info"] or row["session_handle"]):
        raise TaskError("historical ordinary agent has no original writer requirement")
    return json.dumps(requirement, sort_keys=True)


class OrdinaryWriterAdmission:
    """Expected writer and original workspace, not a closed-cohort ready permit."""

    def __init__(self, value: dict, reservation: dict) -> None:
        self.requirement = writer_requirement(value)
        self.workspace = {
            "worktree_id": reservation.get("worktree"),
            "project": reservation.get("allocation_project"),
            "worktree_path": reservation.get("worktree_path"),
        }
        if (
            any(not isinstance(value, str) or not value.strip() for value in self.workspace.values())
            or reservation.get("creation_nonce") not in (None, self.requirement["creation_nonce"])
        ):
            raise TaskError("ordinary Bridge writer requires its original prepared workspace")

    @staticmethod
    def _read(run, arguments: list[str]) -> dict:
        result = run(["--json", *arguments], read_only=True)
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise TaskError("ordinary Bridge writer observation is unconfirmed") from exc
        if result.returncode or not isinstance(value, dict):
            raise TaskError("ordinary Bridge writer observation is unavailable")
        return value

    def _health(self, run) -> dict:
        health = self._read(run, ["status"])
        capabilities = health.get("capabilities")
        slot = health.get("slot")
        active = slot.get("active") if isinstance(slot, dict) else None
        if not (
            health.get("service") == "agent-bridge" and health.get("status") == "ok"
            and health.get("ready") is True and health.get("draining") is False
            and isinstance(capabilities, dict)
            and all(type(capabilities.get(key)) is int and capabilities[key] == 1 for key in (
                "worktree_record_delegation", "retained_workspace_creation_writer",
                "retained_workspace_creation_writer_inspection",
            ))
            and isinstance(slot, dict) and slot.get("role") == "active"
            and type(slot.get("pid")) is int and slot["pid"] > 0
            and isinstance(active, dict) and type(active.get("pid")) is int
            and active["pid"] == slot["pid"]
            and type(active.get("generation")) is int and active["generation"] > 0
        ):
            raise TaskError("actual ordinary Bridge writer support is unconfirmed")
        return health

    def check_record(self, record: dict) -> None:
        if not isinstance(record, dict) or any(
            record.get(key) != value for key, value in {
                "id": self.workspace["worktree_id"], "repo": self.workspace["project"],
                "path": self.workspace["worktree_path"],
                "creation_nonce": self.requirement["creation_nonce"],
            }.items()
        ):
            raise TaskError("ordinary Bridge writer workspace incarnation changed")

    def admit_create(self, run, *, retained: bool = False) -> None:
        """A future journal cannot admit the earlier first relationship."""
        support = self._read(run, ["version", "--contracts-json"])
        contracts = support.get("contracts")
        required = {
            "agent-bridge.create": {"keyed-worktree-writer"},
            "agent-bridge.status": {"health-json", "exact-session-worktree-writer"},
            "agent-bridge.resume": {"keyed-worktree-writer"},
        }
        if not (
            support.get("schema") == "agent-bridge.cli-contracts"
            and type(support.get("version")) is int and support["version"] == 1
            and isinstance(contracts, dict)
            and all(
                isinstance(contracts.get(key), dict)
                and type(contracts[key].get("version")) is int and contracts[key]["version"] == 1
                and isinstance(contracts[key].get("capabilities"), list)
                and all(item in contracts[key]["capabilities"] for item in capabilities)
                for key, capabilities in required.items()
            )
        ):
            raise TaskError("executing ordinary Bridge CLI writer support is unconfirmed")
        health = self._health(run)
        if not retained and health.get("worktree_record_writer") != self.requirement["identity"]:
            raise TaskError("ordinary Bridge selected writer differs from the admitted writer")

    def inspect_session(self, run, info: dict) -> dict:
        """Requalify only the exact session's retained journal, never a new selection."""
        actual = identity(info)
        health = self._health(run)
        response = self._read(run, ["status", actual.bridge, "--full", "--worktree-writer"])
        observed = response.get("worktree_writer_observation")
        instance = observed.get("bridge_instance") if isinstance(observed, dict) else None
        retained = writer_requirement(observed.get("worktree_writer") if isinstance(observed, dict) else None)
        if not (
            isinstance(observed, dict)
            and observed.get("schema") == "copilot-extensions.creation-worktree-writer"
            and type(observed.get("version")) is int and observed["version"] == 1
            and observed.get("session_id") == response.get("session_id") == actual.bridge
            and observed.get("native_session_id") == response.get("acp_session_id") == actual.native
            and response.get("durable_session_id") == actual.native
            and observed.get("worktree_id") == response.get("worktree_id") == actual.worktree
            and observed.get("project") == response.get("project") == actual.project
            and observed.get("worktree_path") == response.get("target_dir") == actual.path
            and all(observed.get(key) == value for key, value in self.workspace.items())
            and retained == self.requirement
            and observed.get("writer_scope") == "executing-process"
            and valid_record_writer(observed.get("writer_contract"))
            and type(observed.get("retained_session_writes")) is int
            and observed["retained_session_writes"] == 1
            and isinstance(instance, dict)
            and isinstance(instance.get("installation_root"), str)
            and instance["installation_root"].strip()
            and type(instance.get("pid")) is int and instance["pid"] == health["slot"]["pid"]
            and type(instance.get("generation")) is int
            and instance["generation"] == health["slot"]["active"]["generation"]
        ):
            raise TaskError("original ordinary Bridge writer journal is unconfirmed")
        return response
