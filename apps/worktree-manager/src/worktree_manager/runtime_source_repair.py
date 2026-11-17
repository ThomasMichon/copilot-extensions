"""Prepare engine repair without importing the damaged engine application."""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

from .runtime_source import ResolvedSource
from .runtime_source_prepare import prepare, preparer
from .self_install import _runtime_installer, default_root

PIN_ENV = "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"


def prepare_engine(resolved: ResolvedSource) -> tuple[Path, str]:
    record = prepare(
        preparer(), resolved, "agent-worktrees",
        default_root().absolute() / "runtime-sources/prepared",
    )
    payload = Path(record["payload"])
    qualify_engine(payload)
    return payload, json.dumps(record["identity"], sort_keys=True)


def engine_installer(payload: Path, platform: str, *, prepared: bool = False) -> tuple[Path, str | None]:
    """Select the declared prepared entry or preserve the native legacy entry."""
    if prepared:
        from agent_worktrees.update_runtime_source import source_install_entry

        entry = source_install_entry(payload, platform)
        if entry is None:
            raise ValueError("Prepared engine has no native installation-source capability")
        return payload / entry["script"], entry["argument"]
    return payload / "scripts" / ("install.ps1" if platform == "windows" else "install.sh"), None


def qualify_engine(payload: Path, *, prepared: bool = False) -> None:
    """Negotiate native isolated repair before any other runtime is published."""
    declaration = ast.parse(
        (payload / "scripts/versioned_runtime_source.py").read_text(encoding="utf-8"),
    )
    if not any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SOURCE_PIN_ENV"
                for target in node.targets)
        and isinstance(node.value, ast.Constant) and node.value.value == PIN_ENV
        for node in declaration.body
    ):
        raise ValueError("Prepared engine installer does not support transaction source pinning")
    installer, _argument = engine_installer(
        payload, "windows" if os.name == "nt" else "posix", prepared=prepared,
    )
    if "AGENT_WORKTREES_TARGETED_UPDATE" not in installer.read_text(encoding="utf-8"):
        raise ValueError("Prepared engine installer does not support runtime-only repair")


def fresh_engine_plan(source: Path, prepared):
    """Use native installation, not incumbent repair, for unattributable footprints."""
    from dataclasses import replace
    from agent_worktrees import config, reconcile, update_runtime_plan
    from agent_worktrees.installation_updates import isolated_engine_step
    from . import core_install, update_bootstrap

    environment = dict(os.environ)
    _script, _sdk, helper, _api, primitive = update_bootstrap._policy_sdk(source)
    _current, profile = primitive._current_environment(
        environment=environment, os_profile=None, platform=None, wsl_distro=None,
    )
    root = core_install.runtime_dir(Path(profile))
    helper["_ordinary"](root)
    helper["_ordinary"](root / "deploy-manifest.json")
    malformed = getattr(primitive, "MalformedDeploymentManifest", json.JSONDecodeError)
    try:
        captured = update_bootstrap._legacy_owner_selection(helper, primitive, root)
        if captured.binding is not None:
            update_bootstrap._identity(update_bootstrap._Installation(
                root, captured.payload, captured.binding, _sdk,
            ))
    except PermissionError:
        raise
    except (FileNotFoundError, malformed, RuntimeError, KeyError):
        pass
    else:
        return None
    owner = reconcile.core_installed_payload_dir("agent-worktrees")
    if owner is None:
        raise ValueError(
            "Unattributable engine footprint has no registered source; "
            "its native setup owner must establish that source before recovery"
        )
    action = "repair" if root.exists() else "install"
    if action == "repair":
        entry, _argument = engine_installer(
            prepared.payload, "windows" if os.name == "nt" else "posix",
            prepared=prepared.install_source is not None,
        )
        if "repair" not in entry.read_text(encoding="utf-8"):
            raise ValueError("Selected native engine lacks independent repair support")
    step = update_runtime_plan.plan_engine(
        owner, config.detect_platform(), force=True, source=prepared, action=action,
    )
    if step.blocked:
        raise ValueError(f"Native engine installation preflight failed: {step.status}")
    if step.runtime_root != root:
        raise ValueError("Native engine installation scope differs from the inspected legacy root")
    step = isolated_engine_step(step)
    child = dict(step.environment)
    defer = os.environ.get("AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION")
    if defer is None:
        child.pop("AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION", None)
    else:
        child["AGENT_WORKTREES_DEFER_LOCAL_INTEGRATION"] = defer
    return replace(step, environment=child)


def verify_engine(root: Path, pin: str | None) -> None:
    """Require the native lifecycle to have selected the complete pinned slot."""
    if pin is None:
        raise ValueError("Prepared engine repair has no transaction source pin")
    identity = json.loads(pin)
    base_version = identity["runtime_version"]
    manifest_path = root / "deploy-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Prepared engine repair has no ordinary deployment receipt")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime = _runtime_installer().runtime
    selector = root / "current-version"
    if selector.is_symlink() or not selector.is_file():
        raise ValueError("Prepared engine repair has no ordinary selected generation")
    version = selector.read_text(encoding="utf-8").strip()
    slot = runtime.version_dir(root, version)
    if (
        not isinstance(manifest, dict) or manifest.get("service") != "agent-worktrees"
        or manifest.get("runtime_source") != identity["runtime_source"]
        or manifest.get("runtime_version") != version
        or not (
            version == base_version
            or re.fullmatch(re.escape(base_version) + r"\.repair\.[0-9a-f]{32}", version)
        )
        or slot.is_symlink() or not slot.is_dir()
        or not isinstance(manifest.get("venv"), str) or Path(manifest["venv"]) != slot
        or not runtime.is_complete(root, version)
    ):
        raise ValueError("Engine repair did not select the complete pinned service source")
