"""Observe explicit runtime participation, not empty-work service readiness."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from agent_procutil.owned_process import run_caller_owned
from installer_readiness import (
    ConfigurationEmpty, MarketplaceProvenance, PluginInstallation,
    ReadinessState, parse_readiness,
)
from installer_readiness.manifest import _payload_path, discover_installation

from . import reconcile
from .installation_probe import strict_json_object

if TYPE_CHECKING:
    from .update_runtime_plan import RuntimeUpdateStep


INACTIVE = "SKIPPED (configuration-empty)"


def _manifest(step: RuntimeUpdateStep) -> dict:
    manifest = json.loads(
        (step.artifact / "plugin.json").read_text(encoding="utf-8"),
        object_pairs_hook=strict_json_object,
    )
    if not isinstance(manifest, dict):
        raise ValueError("plugin manifest must be an object")
    return manifest


def activation_declared(step: RuntimeUpdateStep) -> bool:
    """Keep nonparticipating owners' existing repository/role selection."""
    return "runtimeActivation" in _manifest(step)


def configuration_empty(step: RuntimeUpdateStep, platform_name: str) -> bool:
    """Observe inactivity, never certify an installed or serving generation."""
    manifest = _manifest(step)
    if "runtimeActivation" not in manifest:
        return False
    activation = manifest["runtimeActivation"]
    if (
        not isinstance(activation, dict)
        or set(activation) != {"schema", "version", "module", "path"}
        or activation["schema"] != "copilot-extensions.runtime-activation"
        or type(activation["version"]) is not int or activation["version"] != 1
        or not isinstance(activation["module"], str)
    ):
        raise ValueError("invalid runtime-activation declaration")
    reader = _payload_path(step.artifact, activation["path"], "runtime activation reader")
    if reader.suffix != ".py":
        raise ValueError("runtime activation reader must be a Python script")
    # The update portfolio has already selected and admitted this core payload.
    provenance = MarketplaceProvenance(
        reconcile.CORE_MARKETPLACE_ID,
        "sha256:" + hashlib.sha256(reconcile._CORE_SOURCE_RECORD.encode()).hexdigest(),
        "github", "github:thomasmichon/copilot-extensions",
    )
    modules, _, findings, _ = discover_installation(
        PluginInstallation(step.name, step.artifact, provenance),
    )
    if findings:
        raise ValueError("invalid installer-readiness declaration")
    if len({module.module_id for module in modules}) != len(modules):
        raise ValueError("duplicate installer-readiness module identity")
    if len(modules) != 1 or modules[0].module_id != activation["module"]:
        raise ValueError("runtime activation must address the sole declared module")
    module = modules[0]
    if module.configuration_empty is not ConfigurationEmpty.SATISFIED:
        return False
    if step.runtime_root is None:
        raise ValueError("runtime activation requires the selected runtime root")
    environment = dict(step.environment)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    project = Path(environment["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"])
    result = run_caller_owned(
        [sys.executable, "-I", "-B", str(reader), "--cwd", str(project),
         "--runtime-root", str(step.runtime_root),
         "--context", environment.get("COPILOT_EXTENSIONS_CONTEXT", "")],
        cwd=project, env=environment, timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    value = json.loads(result.stdout, object_pairs_hook=strict_json_object)
    if result.returncode == 0 and isinstance(value, dict) and value.get("state") == "configured":
        # Activation uses the existing envelope, but not service-readiness states.
        observation = parse_readiness({**value, "state": "ready"})
        if observation.module_id != module.module_id:
            raise ValueError("runtime-activation module identity mismatch")
        return False
    observation = parse_readiness(value)
    if observation.module_id != module.module_id:
        raise ValueError("runtime-activation module identity mismatch")
    if result.returncode != 2 or observation.state is not ReadinessState.CONFIGURATION_EMPTY:
        raise ValueError("runtime-activation probe did not report a valid participation decision")
    return True
