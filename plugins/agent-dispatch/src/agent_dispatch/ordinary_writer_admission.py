"""Qualify Bridge's ordinary writer without claiming the rest of the cohort."""

from __future__ import annotations

import copy
import json
import re

from .ordinary_agent_facts import AgentIdentity, identity
from .queue_records import TaskError
from .queue_task_relationships import valid_record_writer

REQUIREMENT = "worktree_writer_requirement"


def _writer_identity(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"installation_root", "runtime_generation"}
        and isinstance(value["installation_root"], str) and bool(value["installation_root"].strip())
        and isinstance(value["runtime_generation"], str)
        and re.fullmatch(r"[0-9a-f]{32}", value["runtime_generation"]) is not None
    )


def writer_requirement(value: object) -> dict:
    if not (
        isinstance(value, dict) and set(value) == {"version", "identity", "creation_nonce"}
        and type(value["version"]) is int and value["version"] == 1
        and isinstance(value["creation_nonce"], str) and value["creation_nonce"].strip()
        and _writer_identity(value["identity"])
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
    if retained is None and row.get("task_relationship_event_id") is not None:
        raise TaskError("historical relationship has no original writer requirement")
    if retained is None and (row["agent_info"] or row["session_handle"]):
        raise TaskError("historical ordinary agent has no original writer requirement")
    return json.dumps(requirement, sort_keys=True)


def prepared_requirement(
    row: dict, *, worktree: str, project: str | None, path: str | None,
    creation_nonce: str | None, value: dict | None,
) -> str | None:
    """Retain the expectation with preparation, before its first relationship."""
    retained = row[REQUIREMENT]
    if value is None and retained is None:
        return None
    if value is not None and (
        not row["task_relationship_required"] or creation_nonce is None
    ):
        raise TaskError("prepared writer requirement needs its governed workspace incarnation")
    requirement = writer_requirement(value if value is not None else json.loads(retained))
    if creation_nonce is not None and creation_nonce != requirement["creation_nonce"]:
        raise TaskError("prepared writer requirement differs from its workspace incarnation")
    return allocation_requirement(
        {**row, "worktree": worktree, "worktree_path": path}, project, requirement,
    )


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

    @classmethod
    def _health(cls, run) -> dict:
        health = cls._read(run, ["status"])
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

    @classmethod
    def _create_health(cls, run) -> dict:
        support = cls._read(run, ["version", "--contracts-json"])
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
        return cls._health(run)

    @classmethod
    def selected_writer(cls, run) -> dict:
        """Observe an expectation before preparation, not a closed-cohort permit."""
        selected = cls._create_health(run).get("worktree_record_writer")
        if not _writer_identity(selected):
            raise TaskError("ordinary Bridge selected writer identity is unconfirmed")
        return copy.deepcopy(selected)

    def admit_create(self, run, *, retained: bool = False) -> None:
        """A future journal cannot admit the earlier first relationship."""
        health = self._create_health(run)
        if not retained and health.get("worktree_record_writer") != self.requirement["identity"]:
            raise TaskError("ordinary Bridge selected writer differs from the admitted writer")

    def inspect_session(self, run, info: dict) -> dict:
        """Requalify only the exact session's retained journal, never a new selection."""
        actual = identity(info)
        return self._inspect_identity(run, actual)

    def inspect_workspace(self, run, *, native_session_id: str) -> dict:
        """Discover an existing handle, then qualify its exact original native journal."""
        if not isinstance(native_session_id, str) or not native_session_id.strip():
            raise TaskError("existing Bridge writer requires its exact native session")
        response = self._read(run, ["status", native_session_id, "--full"])
        if (
            not all(isinstance(response.get(key), str) and response[key] for key in (
                "session_id", "acp_session_id", "durable_session_id",
                "worktree_id", "project", "target_dir",
            ))
            or response["durable_session_id"] != response["acp_session_id"]
            or response["acp_session_id"] != native_session_id
            or any(response.get(key) != value for key, value in {
                "worktree_id": self.workspace["worktree_id"],
                "project": self.workspace["project"], "target_dir": self.workspace["worktree_path"],
            }.items())
        ):
            raise TaskError("existing Bridge native workspace identity is unconfirmed")
        return self._inspect_identity(run, AgentIdentity(
            response["session_id"], response["acp_session_id"], response["worktree_id"],
            response["project"], response["target_dir"],
        ))

    def _inspect_identity(self, run, actual: AgentIdentity) -> dict:
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
