"""Opted-in New admission consumes the selected Machines owner's required read."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

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
        and data.get("read_only") is True
        and data.get("target_identity") == "project-root"
    )


def _selected_runtime() -> tuple[list[str], dict[str, str]]:
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
        selected = reconcile.resolve_runtime_installation("agent-machines", payload)
        if selected.actual_mode != "legacy" or selected.status != "ready" or selected.context is not None:
            raise ValueError("Machines observation requires the selected installation's native context")
        root = selected.runtime_root
        python = _peer_launch._peer_python_inprocess({
            "pluginId": "agent-machines", "pluginRoot": str(root),
            "versionsRoot": str(root / "versions"), "payloadRoot": str(payload),
        })
        if python is None:
            raise ValueError("Machines has no observable completed runtime")
        identity = _peer_launch.retained_runtime_identity(root, python, "agent_machines")
        prefix = _peer_launch.retained_runtime_prefix(
            root, python, "agent_machines", runtime_identity=identity,
        )
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
    if not config.default_repo.require_application_readiness:
        return None
    reason = "Required application observation is incomplete or not ready."
    try:
        prefix, environment = _selected_runtime()
        result = run_caller_owned(
            [*prefix, "installation-repair", "--project-root", source, "--required"],
            timeout=60, max_stdout_bytes=1024 * 1024, max_stderr_bytes=65536,
            cwd=source, env=environment,
        )
        if _ready(result, source):
            return None
    except OwnedCleanupError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        reason = "Required application observation is unavailable or unsupported by the selected runtime."
    return (
        "New authoring requires application readiness before allocation. " + reason
        + " Open Configuration > Application Readiness for the selected repository; "
        "use Installation & Updates if its owner is unavailable, then retry New. "
        "Admission does not install applications, acquire credentials or confirm restarts."
    )
