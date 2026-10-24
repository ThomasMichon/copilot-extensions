"""Compose trusted provider projection with the existing supervisor and Bridge."""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import time

import httpx

from .bridge import BridgeUnavailable
from .client import DispatchError
from .client_transport import validate_task_url
from .ordinary_bridge import _json_result
from .procutil import agent_bridge_launch_prefix, no_window_kwargs
from .restricted_delivery import DRIVER

log = logging.getLogger(__name__)


def configuration(value: object) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    if (
        not isinstance(value, dict)
        or set(value) != {"command", "name", "instance_id", "coordinator_url"}
        or not isinstance(value["command"], list) or not value["command"]
        or any(not isinstance(part, str) or not part or "\0" in part for part in value["command"])
        or not isinstance(value["name"], str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value["name"]) is None
        or not isinstance(value["instance_id"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["instance_id"]) is None
        or not isinstance(value["coordinator_url"], str)
    ):
        raise ValueError("restricted_worker requires provider command, name, physical instance_id and coordinator_url")
    validate_task_url(value["coordinator_url"])
    return {**value, "command": list(value["command"])}


def validate_lane(spec: dict) -> None:
    configuration(spec["restricted_worker"])
    if (
        not spec.get("labels") or spec.get("embody_backend", "headless") != "headless"
        or type(spec.get("max_concurrent", 1)) is not int or spec.get("max_concurrent", 1) != 1
        or any(spec.get(field) for field in (
            "fleet", "pool", "cli_labels", "script_labels", "headless_labels",
            "ci_repair_bindings", "no_pair",
        ))
    ):
        raise ValueError("restricted_worker requires an explicit label-gated, single-container headless lane")


def _run(command: list[str], payload: str | None = None) -> dict:
    try:
        result = subprocess.run(
            command, input=payload, capture_output=True, text=True, check=False,
            timeout=180, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Provider input can contain a grant. Never include subprocess arguments
        # or captured output in errors returned to the queue.
        raise BridgeUnavailable("restricted delivery subprocess failed") from exc
    if result.returncode:
        raise BridgeUnavailable("restricted delivery command refused or failed")
    return _json_result(result)


def _provider(spec: dict, action: str, body: dict) -> dict:
    return _run([
        *spec["command"], action, spec["name"],
        "--expected-container-id", spec["instance_id"],
    ], json.dumps(body))


def _save(client, key: str, info: dict) -> None:
    client.record_restricted_delivery(key, info)


def _same_task(task: dict, info: dict) -> bool:
    return (
        task.get("status") in {"claimed", "started"}
        and not task.get("hold_reason")
        and task.get("owner") == info["worker_id"]
        and task.get("generation") == info["task_generation"]
        and task.get("owner_session_id") in {None, info.get("native_session_id")}
    )


def _grant(client, task_id: str, info: dict) -> dict:
    grant = client.task_grant(
        task_id, info["worker_id"], info["native_session_id"],
        expected_generation=info["task_generation"], expected_epoch=info["coordinator_epoch"],
    )
    if (
        not isinstance(grant, dict) or any(grant.get(field) != expected for field, expected in {
            "task_id": task_id, "worker_id": info["worker_id"],
            "owner_session_id": info["native_session_id"],
            "generation": info["task_generation"], "epoch": info["coordinator_epoch"],
        }.items())
        or not isinstance(grant.get("task_token"), str) or not grant["task_token"]
        or type(grant.get("expires_at")) not in (float, int)
        or not math.isfinite(grant["expires_at"])
        or grant["expires_at"] <= time.time()
    ):
        raise BridgeUnavailable("restricted task grant identity or expiry is unproven")
    return grant


def _project(spec: dict, client, task_id: str, info: dict) -> dict:
    grant = _grant(client, task_id, info)
    projected = _provider(spec, "dispatch-project", {
        "binding_id": info["binding_id"], "owner_session_id": info["native_session_id"],
        "task_token": grant["task_token"], "expires_at": grant["expires_at"],
    })
    if any(projected.get(field) != expected for field, expected in {
        "container_id": spec["instance_id"], "binding_id": info["binding_id"],
        "task_id": task_id, "worker_id": info["worker_id"],
        "generation": info["task_generation"], "coordinator_epoch": info["coordinator_epoch"],
        "coordinator_url": spec["coordinator_url"], "owner_session_id": info["native_session_id"],
        "expires_at": grant["expires_at"], "agent": info["agent"],
    }.items()):
        raise BridgeUnavailable("restricted provider credential publication is unproven")
    return grant


def _native_create(prefix: list[str], key: str, agent: str, spec: dict, charter: str | None) -> tuple[dict, bool]:
    argv = [*prefix, "--json", "create", agent, "--provider-instance", "--creation-key", key]
    if charter:
        argv += ["--charter", charter]
    created = _run(argv)
    connection = created.get("connection")
    connection = connection if isinstance(connection, dict) else {}
    venue = connection.get("venue")
    venue = venue if isinstance(venue, dict) else {}
    if (
        type(created.get("ready")) is not bool
        or (created.get("ready"), created.get("status")) not in (
            (True, "awaiting_first_prompt"), (False, "stopped"),
        )
        or not isinstance(created.get("session_id"), str) or not created["session_id"]
        or not isinstance(connection.get("native_session_id"), str) or not connection["native_session_id"]
        or connection.get("worktree_id")
        or venue.get("instance_id") != spec["instance_id"]
        or venue.get("target_id") != agent or venue.get("dispatch_only") is not True
    ):
        raise BridgeUnavailable("restricted provider native startup identity is unproven")
    return {
        "session_id": created["session_id"], "native_session_id": connection["native_session_id"],
    }, created["ready"]


def _resume_unprompted(prefix: list[str], info: dict) -> None:
    status = _run([*prefix, "--json", "status", info["session_id"]])
    if (
        status.get("session_id") != info["session_id"]
        or type(status.get("turn_count")) is not int or status["turn_count"] != 0
        or status.get("status") not in {"idle", "stopped"}
    ):
        raise BridgeUnavailable("restricted original conversation is not provably unprompted")
    if status["status"] == "stopped":
        resumed = _run([*prefix, "--json", "resume", info["session_id"], "--strict"])
        if resumed.get("session_id") != info["session_id"] or resumed.get("status") != "idle":
            raise BridgeUnavailable("restricted strict resume did not retain the original bridge handle")


def start(task: dict, *, spec: dict, charter: str | None = None) -> dict:
    client, key = task["_spawn_client"], task["spawn_reservation_key"]
    prefix = agent_bridge_launch_prefix()
    if not prefix:
        raise BridgeUnavailable("restricted startup requires agent-bridge")
    reservation = client.get_reservation(key)
    if (
        reservation.get("driver") != DRIVER or reservation.get("state") != "reserving"
        or reservation.get("release_requested") or reservation.get("conversation_retired")
        or reservation.get("worktree") or reservation.get("allocation_project")
    ):
        raise BridgeUnavailable("restricted startup reservation is not admitted")
    if not (reservation.get("agent_info") or {}).get("delivered"):
        admission = _provider(spec, "dispatch-check-hosting", {})
        if (
            type(admission.get("version")) is not int or admission["version"] != 1
            or admission.get("allowed") is not True or admission.get("checked") is not True
            or admission.get("container_id") != spec["instance_id"]
        ):
            raise BridgeUnavailable("Restricted provider repository hosting receiver is unsupported")
    facts = {field: spec[field] for field in ("name", "instance_id", "coordinator_url")}
    current = client.get(task["id"])
    info = reservation.get("agent_info")
    if info is None:
        worker = f"dispatch-{key}"
        if current.get("status") == "queued" and not current.get("owner"):
            generation = current.get("generation")
            current = client.claim(
                worker, task_id=task["id"], repo=current.get("repo"),
            )
            if current is None:
                return {
                    "deferred": True, "expected_generation": generation,
                    "error": "restricted claim did not acquire this task",
                }
        if (
            not isinstance(current, dict) or current.get("id") != task["id"]
            or current.get("status") != "claimed" or current.get("owner") != worker
            or current.get("owner_session_id") is not None
            or type(current.get("generation")) is not int or current.get("hold_reason")
        ):
            raise BridgeUnavailable("restricted supervisor did not acquire this task")
        info = {
            "kind": DRIVER, "worker_id": worker, "task_generation": current["generation"],
            "coordinator_epoch": client.directory_fencing_epoch(), "configuration": facts,
            "charter": charter,
        }
        _save(client, key, info)
    if (
        info.get("configuration") != facts or info.get("kind") != DRIVER or info.get("charter") != charter
        or not _same_task(current, info)
    ):
        raise BridgeUnavailable("restricted startup task incarnation or configured placement changed")
    if info.get("delivered"):
        return {"session": f"local-body:{info['session_id']}"}
    if info.get("send_attempted"):
        raise BridgeUnavailable("restricted first-send acknowledgement is uncertain; retain the original conversation for repair")
    if "binding_id" not in info:
        prepared = _provider(spec, "dispatch-prepare", {
            "task_id": task["id"], "worker_id": info["worker_id"],
            "generation": info["task_generation"], "coordinator_epoch": info["coordinator_epoch"],
            "coordinator_url": spec["coordinator_url"],
        })
        binding_id = prepared.get("binding_id")
        if (
            not isinstance(binding_id, str) or re.fullmatch(r"[0-9a-f]{32}", binding_id) is None
            or prepared.get("container_id") != spec["instance_id"]
            or any(prepared.get(field) != expected for field, expected in {
                "task_id": task["id"], "worker_id": info["worker_id"],
                "generation": info["task_generation"], "coordinator_epoch": info["coordinator_epoch"],
                "coordinator_url": spec["coordinator_url"],
            }.items())
        ):
            raise BridgeUnavailable("restricted provider preparation identity is unproven")
        agent = f"container:dispatch/{spec['instance_id']}/{binding_id}/{spec['name']}"
        if prepared.get("agent") != agent:
            raise BridgeUnavailable("restricted provider returned a different prepared target")
        info = {**info, "binding_id": binding_id, "agent": agent}
        _save(client, key, info)
    retained_native = "session_id" in info
    if retained_native:
        _resume_unprompted(prefix, info)
    native, ready = _native_create(prefix, reservation["creation_key"], info["agent"], spec, charter)
    if retained_native:
        if any(info.get(field) != value for field, value in native.items()):
            raise BridgeUnavailable("restricted startup cannot replace its original native conversation")
    else:
        info = {**info, **native}
        _save(client, key, info)
    if not ready:
        if retained_native:
            raise BridgeUnavailable("restricted original conversation did not become ready after strict resume")
        _resume_unprompted(prefix, info)
        confirmed, ready = _native_create(
            prefix, reservation["creation_key"], info["agent"], spec, charter,
        )
        if not ready or confirmed != native:
            raise BridgeUnavailable("restricted startup cannot replace its original native conversation")
    client.bind_owner_session(
        task["id"], info["worker_id"], info["native_session_id"],
        expected_generation=info["task_generation"],
    )
    _project(spec, client, task["id"], info)
    info = {**info, "send_attempted": True}
    _save(client, key, info)
    prompt = (
        f"Execute only your assigned task {task['id']}. Your supervisor already claimed it "
        "and bound this native conversation. Use the projected task-only agent-dispatch "
        f"commands: `agent-dispatch start {task['id']}`, then "
        f"`agent-dispatch show {task['id']}` for its assignment prompt and "
        f"`agent-dispatch payload {task['id']} --raw` when a payload is present. "
        "Carry out the assigned work; report progress and completion through the same task-only "
        "commands. Do not claim, enumerate, renew credentials, change identity or use fleet authority."
    )
    sent = _run([
        *prefix, "--json", "send", info["session_id"], "--expected-native-session-id",
        info["native_session_id"], "--prompt-file", "-", "--no-wait",
    ], prompt)
    if (
        sent.get("session_id") != info["session_id"]
        or sent.get("native_session_id") != info["native_session_id"]
        or sent.get("outcome") != "delivered"
    ):
        raise BridgeUnavailable("restricted native-bound first send is unproven")
    _save(client, key, {**info, "delivered": True})
    return {"session": f"local-body:{info['session_id']}"}


def make_spawn(value: object, *, charter: str | None = None):
    spec = configuration(value)

    def spawn(task: dict) -> tuple[bool, dict]:
        try:
            handle = start(task, spec=spec, charter=charter)
            return not handle.get("deferred"), handle
        except (DispatchError, BridgeUnavailable, httpx.HTTPError) as exc:
            return False, {"error": str(exc), "retained": True}

    def renew(client, reservation: dict, task: dict, native: str, *, heartbeat: bool = False) -> None:
        info = reservation.get("agent_info") or {}
        if (
            info.get("kind") != DRIVER or not info.get("delivered")
            or info.get("charter") != charter
            or info.get("configuration") != {field: spec[field] for field in ("name", "instance_id", "coordinator_url")}
            or not _same_task(task, info) or native != info.get("native_session_id")
            or task.get("owner_session_id") != native
        ):
            raise BridgeUnavailable("restricted renewal incarnation or native identity changed")
        grant = _project(spec, client, task["id"], info)
        if heartbeat:
            client.scoped_heartbeat(task["id"], info["worker_id"], grant["task_token"])

    spawn.restricted_bridge_for = lambda _task: True
    spawn.renew_restricted = renew
    spawn.requires_reusable_worktree = False
    return spawn
