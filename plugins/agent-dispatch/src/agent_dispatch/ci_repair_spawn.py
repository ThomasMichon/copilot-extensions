"""Task-selected CI repair execution on explicitly allowlisted container venues."""

from __future__ import annotations

import json
import subprocess
import uuid

from .producers.ci_repair import AdmissionRefused, _invoke, execution, failure


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


def make_ci_repair_spawn(
    bindings: list[dict],
    *,
    route: str = "",
    admission_command: list[str] | None = None,
    admission_runner=subprocess.run,
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
        try:
            result = bridge.spawn_worker(
                task["id"],
                agent=selected["venue"],
                charter=selected["charter"],
                target_dir=selected["workspace"],
                worker_id=worker_id,
                prompt=seed,
                wait=False,
                json_output=True,
            )
        except (bridge.BridgeUnavailable, OSError, subprocess.TimeoutExpired) as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            return False, {"error": result.stderr.strip() or "CI repair spawn failed"}
        sid = embody.parse_fleet_body_session(result)
        if not sid:
            return False, {"error": "CI repair spawn omitted session identity"}
        return True, {"session": f"local-body:{sid}", "worktree": None}

    spawn.requires_reusable_worktree = False
    return spawn
