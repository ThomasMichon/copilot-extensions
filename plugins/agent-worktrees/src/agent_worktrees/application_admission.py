"""Opted-in New admission consumes the selected Machines owner's required read."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

from agent_procutil.owned_process import OwnedCleanupError, OwnedExecutionBudget, run_caller_owned

from .launch_preflight import LaunchPreflight, LaunchPreflightError

SCHEMA = "copilot-extensions.installation-repair"


def _supports_required_read(python: Path) -> bool:
    environment = python.parent.parent
    pattern = (
        "Lib/site-packages/agent_machines/installation-repair-v1.json"
        if os.name == "nt" else
        "lib/python*/site-packages/agent_machines/installation-repair-v1.json"
    )
    try:
        paths = list(environment.glob(pattern))
        if len(paths) != 1:
            return False
        path = paths[0]
        if not path.resolve().is_relative_to(environment.resolve()) or path.stat().st_size > 65536:
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict) and data.get("schema") == SCHEMA
        and type(data.get("version")) is int and data["version"] == 1
        and type(data.get("required_selection")) is int and data["required_selection"] == 1
        and type(data.get("caller_owned_readiness")) is int and data["caller_owned_readiness"] == 1
        and data.get("read_only") is True
        and data.get("target_identity") == "project-root"
    )


def _selected_runtime(*, budget: OwnedExecutionBudget | None = None) -> tuple[list[str], dict[str, str]]:
    from . import _peer_launch, peer_launch_adapter, reconcile

    if "COPILOT_EXTENSIONS_CONTEXT" in os.environ:
        own = peer_launch_adapter.validate_context()
        if own is None:
            raise ValueError("Machines observation requires a valid installation context")
        prefix, python = _peer_launch.pinned_launch_prefix(
            "agent-worktrees", Path(own["pluginRoot"]), own["installReceipt"], "agent-machines",
        )
        environment = dict(os.environ)
    else:
        payload = reconcile.core_installed_payload_dir("agent-machines")
        if payload is None:
            raise ValueError("Machines payload is unavailable; use Installation & Updates")
        selected = reconcile.resolve_runtime_installation(
            "agent-machines", payload, **({"budget": budget} if budget is not None else {}),
        )
        if selected.actual_mode != "legacy" or selected.status != "ready" or selected.context is not None:
            raise ValueError("Machines observation requires the selected installation's native context")
        root = selected.runtime_root
        python = _peer_launch._peer_python_inprocess({
            "pluginId": "agent-machines", "pluginRoot": str(root),
            "versionsRoot": str(root / "versions"), "payloadRoot": str(payload),
        })
        if python is None:
            raise ValueError("Machines has no observable completed runtime")
        prefix = [str(python), "-I", "-B", "-X", "utf8", "-m", "agent_machines"]
        environment = _peer_launch.legacy_peer_environment(dict(os.environ))
    if not _supports_required_read(python):
        raise ValueError("The selected Machines runtime does not support required application observation")
    return prefix, environment


def _ready(result: subprocess.CompletedProcess, source: str) -> bool:
    try:
        data = json.loads(result.stdout)
        if (
            result.returncode != 0 or not isinstance(data, dict) or data.get("schema") != SCHEMA
            or type(data.get("version")) is not int or data["version"] != 1
            or data.get("requiredSelection") is not True or data.get("apply") is not False
            or data.get("callerOwnedReadiness") is not True
            or data.get("status") != "observed" or data.get("requiredSatisfied") is not True
            or data.get("interrupted") is not False or data["selectionRefusal"] is not None
            or data.get("refusedOwners") != {} or data.get("discoveryFindings") != []
            or Path(data["projectRoot"]).resolve() != Path(source).resolve()
            or not isinstance(data.get("restarts"), dict)
        ):
            return False
        selected, outcomes = data["selected"], data["outcomes"]
        pending = data["restarts"]["pending"]
        if (
            not isinstance(selected, list) or not isinstance(outcomes, list)
            or any(not isinstance(item, str) or not item for item in selected)
            or len(set(selected)) != len(selected)
            or len(selected) != len(outcomes)
            or not isinstance(pending, list)
            or any(
                not isinstance(row, dict) or not isinstance(row.get("module"), str)
                or row["module"] in selected for row in pending
            )
        ):
            return False
        observed = []
        for row in outcomes:
            if (
                not isinstance(row, dict) or row.get("selected") is not True
                or row.get("attempted") is not False or row.get("installer") is not None
                or row.get("observedAfter") is not None
                or row.get("restartRequired") is not False or row.get("blockedBy") != []
                or not isinstance(row.get("observedBefore"), dict)
                or row["observedBefore"].get("state") not in {"ready", "configuration-empty"}
            ):
                return False
            observed.append(row["module"])
        return sorted(selected) == sorted(observed)
    except (ValueError, KeyError, TypeError, OSError):
        return False


def new_application_error(config, *, source: str) -> str | None:
    return new_application_preflight(config, source=source).error


def new_application_preflight(config, *, source: str, checked_profile: str | None = None) -> LaunchPreflight:
    """Observe required applications against a stable prospective tool roster."""
    from . import copilot_plugin_profile as tool
    from .knowledge_plugins import KnowledgePluginError

    if not config.default_repo.require_application_readiness:
        return LaunchPreflight()
    reason = "Required application observation is incomplete or not ready."
    stage = "profile selection"
    try:
        budget = OwnedExecutionBudget.from_timeout(60, cleanup_reserve=5)
        profile_input = tool.observe_for_admission(config, deadline=budget.execution_deadline)
        if checked_profile is not None and profile_input != checked_profile:
            raise KnowledgePluginError("project tool profile changed after application admission")
        if time.monotonic() >= budget.execution_deadline:
            raise TimeoutError("Required application profile selection exhausted its budget")
        stage = "runtime selection"
        prefix, environment = _selected_runtime(budget=budget)
        remaining = budget.execution_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Required application selection exhausted its budget")
        stage = "observation"
        result = run_caller_owned(
            [*prefix, "installation-repair", "--project-root", source, "--required", "--caller-owned-readiness"],
            timeout=remaining, max_stdout_bytes=1024 * 1024, max_stderr_bytes=65536,
            cwd=source, env=environment, budget=budget,
        )
        stage = "profile revalidation"
        if (
            _ready(result, source)
            and tool.observe_for_admission(config, deadline=budget.execution_deadline) == profile_input
            and time.monotonic() < budget.deadline
        ):
            return LaunchPreflight(checked_profile=profile_input)
    except OwnedCleanupError:
        raise
    except KnowledgePluginError as exc:
        reason = str(exc)
    except (TimeoutError, subprocess.TimeoutExpired):
        reason = f"Required application {stage} exceeded the launch admission budget."
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        reason = f"Required application {stage} is unavailable or unsupported."
    boundary = "launch" if checked_profile is not None else "allocation"
    retry = "this existing worktree" if checked_profile is not None else "New"
    return LaunchPreflight(policy_error=(
        f"New authoring requires application readiness before {boundary}. " + reason
        + " Open Configuration > Application Readiness for the selected repository; "
        f"use Installation & Updates if its owner is unavailable, then retry {retry}. "
        "Admission does not install applications, acquire credentials or confirm restarts."
    ))


def require_received_applications(config, checkout: str, preflight: LaunchPreflight | None) -> None:
    """Observe the actual prepared New roster without acquiring a source-only or recovery gate."""
    checked_profile = getattr(preflight, "checked_profile", None)
    if checked_profile is None:
        return
    result = new_application_preflight(config, source=checkout, checked_profile=checked_profile)
    if result.error:
        raise LaunchPreflightError(result.error)
