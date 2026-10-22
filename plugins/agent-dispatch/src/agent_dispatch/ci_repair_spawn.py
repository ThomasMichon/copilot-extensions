"""Task-selected CI repair execution on explicitly allowlisted container venues."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from contextlib import nullcontext
from typing import TYPE_CHECKING

from .producers.ci_repair import AdmissionRefused, _invoke, execution, failure
from .queue_records import TaskError

if TYPE_CHECKING:
    from .coordinator_execution_sender import CoordinatorExecutionSender


def reservation_authorized(conn, task, reserved_by: str | None, *, transition: dict | None = None) -> bool:
    """Fence reservations to the persisted, validated specialized lane."""
    if not reserved_by or reserved_by != task.origin_ref:
        return False
    row = conn.execute(
        "SELECT kind,spec,status FROM registrations WHERE id = ?", (reserved_by,)
    ).fetchone()
    if row is None or row["kind"] != "supervised-lane" or row["status"] != "active":
        return False
    try:
        spec = json.loads(row["spec"])
        from .registrations import validate_registration

        validate_registration("supervised-lane", spec)
        selected = execution(json.loads(task.prompt)["execution"])
        from .identity import canonicalize_remote
        lane_matches = spec.get("all_repos") is True or canonicalize_remote(spec.get("repo")) == task.repo
        if selected not in spec.get("ci_repair_bindings", []) or not lane_matches:
            return False
        request = {"failure": json.loads(task.prompt)["failure"], "execution": selected,
                   **(transition or {"phase": "reserve"})}
        result = _invoke(spec["ci_repair_admission_command"], {}, runner=subprocess.run,
                         cwd=None, request=request)
        return isinstance(result, dict) and result.get("allowed") is True and all(
            result.get(key) == value for key, value in request.items()
        )
    except (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired):
        return False


def authority_snapshot(conn, task) -> tuple:
    """Capture task, registration and latest session identity for compare-before-write."""
    registration = conn.execute("SELECT * FROM registrations WHERE id = ?", (task.origin_ref,)).fetchone()
    reservation = conn.execute(
        "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt DESC LIMIT 1",
        (task.id,),
    ).fetchone()
    return repr(task), tuple(registration) if registration else None, tuple(reservation) if reservation else None


def claim_admissions(queue, worker_id, caps, repo, task_id) -> dict:
    """Probe only eligible CI tasks outside any coordinator write transaction."""
    admitted = {}
    if "ci-repair-admitted" not in caps or not task_id:
        return admitted
    with queue._connect() as conn:
        rows = conn.execute("SELECT id FROM tasks WHERE source = 'ci-repair' AND status = 'queued'").fetchall()
        for row in rows:
            task = queue._fetch(conn, row["id"])
            if (task_id and task.id != task_id) or (repo and task.repo != repo):
                continue
            reservation = conn.execute(
                "SELECT session_handle FROM spawn_reservations WHERE task_id = ? AND state = 'spawned' ORDER BY attempt DESC LIMIT 1",
                (task.id,),
            ).fetchone()
            if not reservation or not reservation["session_handle"]:
                continue
            snapshot = authority_snapshot(conn, task)
            if reservation_authorized(conn, task, task.origin_ref, transition={
                "phase": "claim", "worker_id": worker_id, "session_handle": reservation["session_handle"],
            }):
                admitted[task.id] = snapshot
    return admitted

def validate_bindings(bindings: list[dict]) -> None:
    """Require an explicit non-empty execution allowlist."""
    if not isinstance(bindings, list) or not bindings:
        raise AdmissionRefused("ci_repair_bindings must be a non-empty list")
    for binding in bindings:
        execution(binding)


def validate_admission_command(command: object) -> None:
    """Reject missing or malformed launch-time adapter argv."""
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise AdmissionRefused("launch admission command must be a non-empty argv list")


def cli_bindings(bindings: object, command: object) -> tuple[list[dict], list[str]]:
    """Parse CLI JSON once and report malformed configuration without a traceback."""
    try:
        parsed_bindings = json.loads(bindings) if isinstance(bindings, str) else bindings
        parsed_command = json.loads(command) if isinstance(command, str) else command
        validate_bindings(parsed_bindings)
        validate_admission_command(parsed_command)
        return parsed_bindings, parsed_command
    except (ValueError, TypeError) as exc:
        raise SystemExit(f"Invalid CI repair bindings/admission command: {exc}") from exc


def make_ci_repair_spawn(
    bindings: list[dict],
    *,
    route: str = "",
    admission_command: list[str] | None = None,
    admission_runner=subprocess.run,
    execution_sender: CoordinatorExecutionSender | None = None,
):
    """Select only admitted task overlays; never allocate a host worktree."""
    from . import bridge, embody

    validate_bindings(bindings)
    if admission_command is not None:
        validate_admission_command(admission_command)

    def spawn(task: dict) -> tuple[bool, dict]:
        try:
            if task.get("source") != "ci-repair" or task.get("require_verification") is not True:
                raise AdmissionRefused("not an admitted CI repair task")
            payload = json.loads(task.get("prompt") or "")
            if payload.get("schema") != "agent-dispatch.ci-repair.v1":
                raise AdmissionRefused("invalid CI repair task schema")
            selected = execution(payload.get("execution"))
            if selected not in bindings:
                raise AdmissionRefused("CI repair execution is not allowlisted")
            evidence = failure(payload.get("failure"), task.get("target_repo") or task.get("repo"))
            if (
                task.get("dedup_key")
                != f"ci-repair:{evidence['repository']}:{evidence['signature']}"
            ):
                raise AdmissionRefused("CI repair task identity mismatch")
            if not admission_command:
                raise AdmissionRefused("CI repair launch has no independent admission adapter")
            verdict = _invoke(
                admission_command,
                {},
                runner=admission_runner,
                cwd=None,
                request={"failure": evidence, "execution": selected, "phase": "spawn"},
            )
            if not isinstance(verdict, dict) or verdict.get("allowed") is not True:
                raise AdmissionRefused("CI repair launch admission refused")
            if verdict.get("phase") != "spawn":
                raise AdmissionRefused("CI repair launch response is not bound to spawn phase")
            if (
                failure(verdict.get("failure"), evidence["repository"]) != evidence
                or execution(verdict.get("execution")) != selected
                or verdict.get("inference_auth_ref") == verdict.get("repository_auth_ref")
            ):
                raise AdmissionRefused("CI repair launch authority mismatch")
            for key in ("policy_ref", "inference_auth_ref", "repository_auth_ref", "source_ref"):
                if not isinstance(verdict.get(key), str) or not verdict[key].strip():
                    raise AdmissionRefused(f"CI repair launch missing {key}")
        except (ValueError, TypeError, AttributeError, OSError, subprocess.TimeoutExpired) as exc:
            return False, {"error": str(exc)}
        worker_id = f"ci-repair-{uuid.uuid4().hex[:8]}"
        seed = embody.autopilot_worker_prompt(
            task["id"],
            worker_id=worker_id,
            route=route,
            repo=task.get("repo"),
            explicit_worker_identity=True,
        )
        seed = seed.replace(
            f"claim --task {task['id']}", f"claim --task {task['id']} --capability ci-repair-admitted"
        )
        custody = (
            execution_sender.attempt("launch")
            if execution_sender is not None
            else nullcontext(None)
        )
        sid: str | None = None
        try:
            with custody as attempt:
                try:
                    result = bridge.spawn_worker(
                        task["id"],
                        agent=selected["venue"],
                        charter=selected["charter"],
                        target_dir=selected["workspace"],
                        worker_id=worker_id,
                        prompt="Remain idle. Do not claim or execute any task until the coordinator sends an admitted task turn.",
                        wait=False,
                        json_output=True,
                        timeout=30,
                    )
                except (bridge.BridgeUnavailable, OSError, subprocess.TimeoutExpired) as exc:
                    return False, {"error": str(exc)}
                if result.returncode != 0:
                    return False, {"error": result.stderr.strip() or "CI repair spawn failed"}
                sid = embody.parse_fleet_body_session(result)
                if not sid:
                    return False, {"error": "CI repair spawn omitted session identity"}
                if attempt is not None:
                    attempt.sent()
        except (TaskError, OSError, sqlite3.Error) as exc:
            return False, {
                "error": str(exc),
                **({"session": f"local-body:{sid}", "worktree": None} if sid else {}),
            }
        return True, {"session": f"local-body:{sid}", "worktree": None, "task_seed": seed}

    spawn.requires_reusable_worktree = False
    return spawn
