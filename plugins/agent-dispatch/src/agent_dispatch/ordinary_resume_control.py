"""Use ordinary Bridge resume, then a separately retained current-control send."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any, Protocol

from .bridge import BridgeCarriedSessionBusy
from .ordinary_agent_facts import identity, validate_resumed
from .ordinary_control_adoption import ADOPTION, HISTORY
from .ordinary_control_invocation import CONTROL
from .ordinary_invocation import native_payload, same_facts
from .ordinary_writer_admission import REQUIREMENT, OrdinaryWriterAdmission
from .queue_records import TaskError


class Runner(Protocol):
    def __call__(
        self, arguments: list[str], *, prompt_input: str | None = None, read_only: bool = False,
    ) -> subprocess.CompletedProcess[str]: ...


def _read(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    from .ordinary_bridge import _json_result
    return _json_result(result)


def _support(run: Runner, client: Any) -> None:
    support = client.pool_placement_contract()
    for capability in ("ordinary_control_adoption_versions", "ordinary_control_delivery_versions"):
        versions = support.get(capability)
        if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
            raise TaskError(f"coordinator lacks {capability} version 1")
    local = run(["--json", "version", "--contracts-json"], read_only=True)
    declared = _read(local)
    contracts = declared.get("contracts")
    if local.returncode or (
        declared.get("schema") != "agent-bridge.cli-contracts"
        or type(declared.get("version")) is not int or declared["version"] != 1
        or not isinstance(contracts, dict)
        or any(
            not isinstance(contracts.get(name), dict)
            or type(contracts[name].get("version")) is not int or contracts[name]["version"] != 1
            or not isinstance(contracts[name].get("capabilities"), list)
            or capability not in contracts[name].get("capabilities", [])
            for name, capability in (
                ("agent-bridge.resume", "exact-worktree-target"),
                ("agent-bridge.status", "exact-session-absence"),
            )
        )
    ):
        raise TaskError("executing ordinary Bridge CLI lacks exact retained resume support")
    health = OrdinaryWriterAdmission._health(run)
    if any(
        type(health["capabilities"].get(name)) is not int or health["capabilities"][name] != 1
        for name in (
            "retained_worktree_resume", "retained_native_metadata_adoption",
            "retained_native_session_recovery",
            "retained_keyed_native_admission",
        )
    ):
        raise TaskError("receiving ordinary Bridge lacks retained resume support")


def resume(
    run: Runner, client: Any, reservation: dict[str, Any], previous: dict[str, Any],
    writer: OrdinaryWriterAdmission, *, read_record: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Only proven absent metadata or an already-adopted control uses this path."""
    _support(run, client)
    actual = identity(previous)
    record = read_record()
    if record.get("last_session_id") != actual.native:
        raise TaskError("ordinary retained resume native head changed")
    observed = run(["--json", "status", actual.bridge, "--full"], read_only=True)
    status = _read(observed)
    absent = observed.returncode == 1 and status == {
        "outcome": "refused_unavailable", "reason": "native_session_unavailable",
        "target": actual.bridge,
    }
    if HISTORY not in previous and not absent:
        raise BridgeCarriedSessionBusy("ordinary receipt history is unavailable; retained control is not proven absent")
    if HISTORY in previous and (
        observed.returncode or status.get("session_id") != actual.bridge
    ):
        raise BridgeCarriedSessionBusy("the adopted ordinary control is unavailable; no second epoch created")
    if absent or status.get("status") == "stopped":
        result = run([
            "--json", "resume", "--worktree-id", actual.worktree,
            "--strict", "--require-agent-hosting",
        ])
        status = _read(result)
        if result.returncode:
            raise BridgeCarriedSessionBusy("ordinary retained resume outcome is unproven; inspect its original owner")
    validate_resumed(status, project=actual.project)
    current = {**status, "fact_kind": "resumed", REQUIREMENT: writer.requirement}
    restored = identity(current)
    if (
        (restored.native, restored.worktree, restored.project, restored.path)
        != (actual.native, actual.worktree, actual.project, actual.path)
        or absent and restored.bridge == actual.bridge
        or not absent and restored.bridge != actual.bridge
    ):
        raise TaskError("ordinary retained resume changed original native/workspace identity")
    boundary = current.get(ADOPTION, previous.get(ADOPTION))
    if not isinstance(boundary, dict):
        raise TaskError("ordinary retained resume lacks its owner-qualified adoption")
    current[ADOPTION] = boundary
    writer.inspect_session(run, current)
    if HISTORY in previous:
        current[HISTORY] = previous[HISTORY]
    return current


def deliver(
    run: Runner, client: Any, task: dict[str, Any], reservation: dict[str, Any],
    info: dict[str, Any], *, observe_only: bool = False,
) -> dict[str, Any] | None:
    """Receipt-first delivery of only the persisted new operation, never its birth key."""
    from .ordinary_assignment import current_task
    from .ordinary_control_invocation import prepare

    actual = identity(info)
    invocation = info[CONTROL]["invocation"]
    expected = prepare(
        task, reservation, info, turn=invocation["binding"]["expected_turn_index"],
    )
    if not same_facts(info[CONTROL], expected):
        raise TaskError("ordinary current-control invocation admission changed")
    arguments = [
        "--json", "send", actual.bridge, "--expected-native-session-id", actual.native,
        "--native-invocation-file", "-", "--no-wait", "--no-resume",
    ]
    payload = json.dumps(native_payload(invocation), ensure_ascii=False)
    result = run([*arguments, "--receipt-only"], prompt_input=payload, read_only=True)
    response = _read(result)
    if result.returncode:
        if result.returncode != 69 or (
            response.get("outcome") != "refused_unavailable"
            or response.get("reason") != "native_receipt_missing"
        ):
            raise BridgeCarriedSessionBusy("ordinary current-control receipt is unknown; no send replayed")
        if observe_only:
            return None
        current_task(client, task)
        stored = client.get_reservation(reservation["key"])
        if (
            stored.get("state") != "reserving" or stored.get("release_requested")
            or stored.get("startup_fenced") or stored.get("conversation_retired") is not False
            or stored.get("startup_revision") != reservation["startup_revision"]
            or stored.get("ordinary_assignment_error")
            or not isinstance(stored.get("agent_info"), dict)
            or not same_facts(stored["agent_info"].get(CONTROL), info[CONTROL])
        ):
            raise TaskError("ordinary current-control admission changed before send")
        result = run(arguments, prompt_input=payload)
        response = _read(result)
    if result.returncode or (
        response.get("session_id") != actual.bridge
        or response.get("native_session_id") != actual.native
        or response.get("invocation_key") != invocation["invocation_key"]
        or response.get("outcome") != "delivered"
        or type(response.get("version")) is not int or response["version"] != 1
        or response.get("admission") != "accepted" or response.get("queued") is not False
        or type(response.get("turn_index")) is not int
        or response["turn_index"] != invocation["binding"]["expected_turn_index"]
    ):
        raise BridgeCarriedSessionBusy("ordinary current-control admission is unproven")
    return {
        "session": f"local-body:{actual.bridge}", "worktree": actual.worktree,
        "native_session_id": actual.native, "mode": "acp", "session_info": response,
    }
