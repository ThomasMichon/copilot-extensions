"""Optional accepted defaults composed into the existing confirmed binding transaction."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess

from agent_procutil.owned_process import OwnedCleanupError, OwnedExecutionBudget, run_caller_owned

from . import machines_runtime, repos_knowledge_preferences as native, state_root
from .repos_configuration_sources import ConfigurationError

SCHEMA = "agent-machines.preferences.repositories"


def _source(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - {"state_dir", "local_overrides"} or "state_dir" not in value:
        raise ConfigurationError("preference-source-invalid", "Configure an explicit local accepted preference source.")
    if any(not isinstance(path, str) or not path or not Path(path).is_absolute() for path in value.values()):
        raise ConfigurationError("preference-source-invalid", "Preference input paths must be explicit and absolute.")
    return dict(value)


def _supported(python: Path) -> bool:
    root = python.parent.parent
    pattern = (
        "Lib/site-packages/agent_machines/preferences-repositories-v1.json" if os.name == "nt"
        else "lib/python*/site-packages/agent_machines/preferences-repositories-v1.json"
    )
    paths = list(root.glob(pattern))
    if len(paths) != 1:
        return False
    path = paths[0]
    if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 65536:
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return (
        isinstance(data, dict) and data.get("schema") == SCHEMA
        and type(data.get("version")) is int and data["version"] == 1
        and type(data.get("accepted_projection")) is int and data["accepted_projection"] == 1
        and data.get("read_only") is True and data.get("target_identity") == "caller-native"
    )


def _read(
    source: dict, machine: str, harness: str, *, budget: OwnedExecutionBudget | None = None,
) -> tuple[dict, list[str]]:
    try:
        local_budget = OwnedExecutionBudget.from_timeout(8, cleanup_reserve=1)
        budget = (
            OwnedExecutionBudget(min(local_budget.deadline, budget.execution_deadline), 1)
            if budget is not None else local_budget
        )
        prefix, environment, python = machines_runtime.selected(budget=budget)
        if not _supported(python):
            raise ConfigurationError(
                "preference-owner-unsupported", "The selected Machines runtime lacks accepted repository projection.",
            )
        args = [
            *prefix, "preferences", "repositories", "--state-dir", source["state_dir"],
            "--machine", machine, "--harness", harness,
        ]
        if "local_overrides" in source:
            args += ["--local-overrides", source["local_overrides"]]
        child = run_caller_owned(
            args, timeout=7, budget=budget, env=environment,
            max_stdout_bytes=256 * 1024, max_stderr_bytes=65536,
        )
        value = json.loads(child.stdout)
        if (
            child.returncode or not isinstance(value, dict) or value.get("schema") != SCHEMA
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("read_only") is not True or value.get("configuration_applied") is not False
            or value.get("binding_changed") is not False
            or value.get("scope") != {
                "machine": machine, "harness_repository_id": harness, "identity_resolution": "not-performed",
            }
            or not isinstance(value.get("input_revision"), str)
            or re.fullmatch(r"[a-f0-9]{64}", value["input_revision"]) is None
            or not isinstance(value.get("knowledge_preference"), dict)
        ):
            raise ValueError("invalid projection")
        preference = value["knowledge_preference"]
        if preference.get("status") not in {"absent", "suppressed", "suggested"}:
            raise ValueError("invalid preference status")
        target = preference.get("knowledge_repository_id")
        if preference["status"] == "suggested":
            if not isinstance(target, str) or re.fullmatch(r"repo-[a-f0-9]{24}", target) is None:
                raise ValueError("invalid native identity")
        elif target is not None:
            raise ValueError("invalid absent preference")
        return value, prefix
    except OwnedCleanupError:
        raise
    except ConfigurationError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise ConfigurationError(
            "preference-owner-unavailable", "Accepted preferences are unavailable; inspect the selected Machines owner.",
        ) from error


def observe(project: str, *, budget: OwnedExecutionBudget | None = None) -> dict:
    config = native._binding_config(project)
    selected = getattr(config, "knowledge_preferences", None)
    if selected is None:
        return {"status": "unconfigured"}
    source = _source(selected)
    if config.knowledge_repo.strip():
        current = state_root.resolve_state_root(
            config, repo_override=config.knowledge_repo.strip(), cwd=config.default_repo.anchor,
        )
        if not current.bound or not current.path or current.error:
            raise ConfigurationError("binding-unresolved", "The explicit local binding needs owner repair.")
        return {"status": "binding-preserved"}
    catalog = native.identities()
    harnesses = [entry for entry in catalog["entries"] if entry["repository"] == project]
    if len(harnesses) != 1:
        raise ConfigurationError("context-mismatch", "The selected project needs a native repository identity.")
    harness = native._selected(catalog, harnesses[0]["repository_id"])
    projection, runtime = _read(
        source, config.machine, harness["repository_id"], **({"budget": budget} if budget is not None else {}),
    )
    preference = projection["knowledge_preference"]
    result = {"status": preference["status"], "projection": projection}
    if preference["status"] != "suggested":
        return result
    resolution = native.resolve(project, harness["repository_id"], preference["knowledge_repository_id"])
    return {
        **result, "status": resolution["status"], "resolution": resolution,
        "selection": {"source": source, "runtime": runtime, "projection": projection},
    }


def revalidate(project: str, selection: dict, *, budget: OwnedExecutionBudget | None = None) -> None:
    current = observe(project, **({"budget": budget} if budget is not None else {}))
    config = native._binding_config(project)
    if (
        current.get("status") != "suggested" or current.get("selection") != selection
        or getattr(config, "knowledge_preferences", None) != selection["source"]
    ):
        raise ConfigurationError("preview-conflict", "Accepted preference inputs changed; preview again.")


def preview(
    project: str, harness_id: str, knowledge_id: str, *, budget: OwnedExecutionBudget | None = None,
) -> dict:
    budget = budget or OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2)
    current = observe(project, budget=budget)
    resolution = current.get("resolution", {})
    if (
        current["status"] != "suggested" or resolution["harness"]["repository_id"] != harness_id
        or resolution["knowledge"]["repository_id"] != knowledge_id
    ):
        raise ConfigurationError("preview-conflict", "The selected accepted default changed; refresh the list.")
    proposed = native.preview(
        project, harness_id, knowledge_id, require_preference_freshness=True, budget=budget,
    )
    revalidate(project, current["selection"], budget=budget)
    token = hashlib.sha256(json.dumps({
        "native_token": proposed["token"], "selection": current["selection"],
    }, sort_keys=True).encode()).hexdigest()
    return {
        **proposed, "token": token,
        "accepted_default": current["projection"], "default_selection": current["selection"],
    }


def apply(project: str, harness_id: str, knowledge_id: str, token: str) -> dict:
    from .repos_knowledge_configuration import invoke

    if not isinstance(token, str) or re.fullmatch(r"[a-f0-9]{64}", token) is None:
        raise ConfigurationError("missing-preview", "A fresh accepted-default preview is required.")
    budget = OwnedExecutionBudget.from_timeout(26, cleanup_reserve=2)
    proposed = preview(project, harness_id, knowledge_id, budget=budget)
    if not hmac.compare_digest(token, proposed["token"]):
        raise ConfigurationError("preview-conflict", "Accepted preference inputs changed; preview again.")
    return invoke(
        project, proposed["knowledge"]["repository"], "apply", proposed["native_preview"]["token"],
        portable_resolution={
            key: value for key, value in proposed.items()
            if key not in {"token", "native_preview", "accepted_default", "default_selection"}
        },
        preference_selection=proposed["default_selection"],
        budget=budget,
    )
