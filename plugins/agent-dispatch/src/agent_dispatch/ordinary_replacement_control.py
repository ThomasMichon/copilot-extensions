"""Control-only authorization with receiving runtime and ground-owner checks."""

from dataclasses import asdict
import json
import subprocess

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import ordinary_replacement as replacement
from .bridge import BridgeCarriedSessionBusy
from .ordinary_assignment import read_owner
from .procutil import agent_bridge_launch_prefix, agent_worktrees_environment, agent_worktrees_launch_prefix, no_window_kwargs
from .queue_records import TaskError

OWNER_CONTRACT = "agent-worktrees/conditional-head-replacement"


class AuthorizeReplacementBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reservation_key: str = Field(min_length=1)
    worktree: str = Field(min_length=1)
    expected_generation: int = Field(ge=0, lt=2**63 - 1)
    expected_head: str = Field(min_length=1)
    expected_head_revision: int = Field(ge=1, lt=2**63 - 1)
    expected_lifecycle_revision: int = Field(ge=1, lt=2**63 - 1)
    permitted: bool = False
    confirmed_no_live_holder: bool = False
    reason: str = Field(min_length=1)


def _run(argv: list[str], *, data: dict | None = None, env: dict | None = None) -> dict:
    result = subprocess.run(
        argv, input=json.dumps(data) if data is not None else None,
        check=False, capture_output=True, text=True, timeout=30,
        **({"env": env} if env is not None else {}), **no_window_kwargs(),
    )
    if result.returncode:
        raise TaskError(f"replacement readiness refused: {result.stderr or result.stdout}")
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise TaskError("replacement readiness returned invalid JSON") from exc
    if not isinstance(response, dict):
        raise TaskError("replacement readiness returned no object")
    return response


def _version(response: dict, contract: str) -> bool:
    return (
        response.get("contract") == contract
        and type(response.get("version")) is int and response["version"] == 1
    )


def verify_receivers(target: dict, project: str) -> None:
    bridge, worktrees = agent_bridge_launch_prefix(), agent_worktrees_launch_prefix()
    if not bridge or not worktrees:
        raise TaskError("replacement requires both receiving runtime capabilities")
    ready = _run([*bridge, "native-replacement-readiness"])
    if (
        not _version(ready, replacement.CONTRACT) or ready.get("supported") is not True
        or type(ready.get("required_protocol")) is not int or ready["required_protocol"] < replacement.BRIDGE_PROTOCOL
    ):
        raise TaskError("replacement requires Bridge payload and daemon protocol 52")
    prefix = [*worktrees, "--project", project, "replace-dead-head"]
    env = agent_worktrees_environment()
    capabilities = _run([*prefix, "capabilities"], env=env)
    if (
        not _version(capabilities, OWNER_CONTRACT)
        or type(capabilities.get("preflight")) is not int or capabilities["preflight"] != 1
        or type(capabilities.get("candidate_observation")) is not int or capabilities["candidate_observation"] != 1
        or not isinstance(capabilities.get("versions"), list)
        or not any(type(v) is int and v == 1 for v in capabilities["versions"])
    ):
        raise TaskError("replacement requires Worktrees preflight and candidate observation version 1")
    checked = _run(
        [*prefix, "check", "--stdin"], env=env,
        data={"contract": OWNER_CONTRACT, "version": 1, **target},
    )
    if (
        not _version(checked, OWNER_CONTRACT) or checked.get("eligible") is not True
        or checked.get("target") != target
    ):
        raise TaskError("replacement requires the exact stopped ground head")


def authorize(queue, task_id: str, body: AuthorizeReplacementBody) -> dict:
    if not body.permitted or not body.confirmed_no_live_holder or not body.reason.strip():
        raise TaskError("replacement requires explicit permission, no-live-holder attestation and reason")
    reservation = queue.get_reservation(body.reservation_key)
    if reservation is None or reservation.task_id != task_id or reservation.worktree != body.worktree:
        raise TaskError("replacement reservation does not match this task and workspace")
    record = read_owner(asdict(reservation), task_id, timeout=30)
    replacement._exact_head(
        record, body.expected_head, body.expected_head_revision, body.expected_lifecycle_revision,
    )
    verify_receivers({
        "worktree_id": body.worktree, "worktree_path": record["path"],
        "creation_nonce": record["creation_nonce"], "expected_head": body.expected_head,
        "expected_head_revision": body.expected_head_revision,
        "expected_lifecycle_revision": body.expected_lifecycle_revision,
    }, record["repo"])
    grant = replacement.authorize(queue, task_id, **body.model_dump(), worktree_record=record)
    return {"authorization": grant, "launched": False}


def register(app, queue, control_auth) -> None:
    @app.post(
        "/spawn-reservations/tasks/{task_id}/authorize-replacement",
        dependencies=[Depends(control_auth)],
    )
    def authorize_replacement(task_id: str, body: AuthorizeReplacementBody) -> dict:
        try:
            return authorize(queue, task_id, body)
        except (TaskError, BridgeCarriedSessionBusy) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, detail="native replacement receiver readiness unavailable") from exc
