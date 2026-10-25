"""Read-only installation facts and one-plugin updates under existing custody."""

from __future__ import annotations

import dataclasses
import contextlib
import json
import re
import sys

from . import config, output, reconcile, update_acquisition, update_events, update_options, update_runtime, update_runtime_plan

TARGET_CONTRACT = 1


def _target_manifest(payload):
    manifest = json.loads((payload / "plugin.json").read_text("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Plugin manifest must be an object")
    return manifest


def _target_scope(payload, *, manifest=None):
    manifest = _target_manifest(payload) if manifest is None else manifest
    scope = manifest.get("runtimeScope", "none")
    if not isinstance(scope, str) or scope not in reconcile.VALID_SCOPES:
        raise ValueError("Invalid runtimeScope; repair the selected payload")
    return scope


def target_plan(name, target):
    payload = reconcile.core_installed_payload_dir(name)
    if payload is not None:
        _target_scope(payload)
    platform = config.detect_platform()
    if name == reconcile.SELF_PLUGIN:
        payload = reconcile.core_installed_payload_dir(name)
        if payload is None:
            raise ValueError("Engine payload is missing; use the explicit setup/repair flow")
        step = update_runtime_plan.plan_engine(payload, platform, force=False)
        # Old installers recover Manager as a finishing effect. Never pass them
        # a scoped request: the provider must advertise the narrow contract.
        for extension in ("sh", "ps1"):
            script = payload / "scripts" / f"install.{extension}"
            if not script.is_file() or "AGENT_WORKTREES_TARGETED_UPDATE" not in script.read_text("utf-8"):
                raise ValueError("Installed engine installer lacks targeted-update support; update the suite first")
        return dataclasses.replace(step, environment={
            **step.environment, "AGENT_WORKTREES_TARGETED_UPDATE": "1",
        })
    engine = reconcile.core_installed_payload_dir(reconcile.SELF_PLUGIN)
    if engine is not None and name in update_options._module_dirs(engine) and _target_scope(payload) != "none":
        step = update_runtime_plan.plan_module(
            name, payload, platform, force=False, gate_context=target.context,
        )
        return dataclasses.replace(step, fallback_argv=())
    return update_runtime_plan.plan_registered(
        name, platform, force=False, gate_context=target.context,
    )


def _target(name, targets):
    if name == reconcile.SELF_PLUGIN and reconcile.core_installed_payload_dir(name) is not None:
        return update_runtime._RegisteredPluginTarget(
            update_runtime._invocation_update_context(), update_runtime._PluginActivation.ACTIVE,
        )
    target = targets.get(name)
    payload = reconcile.core_installed_payload_dir(name)
    if target is None and payload is not None and _target_scope(payload) == "none":
        return update_runtime._RegisteredPluginTarget(None, update_runtime._PluginActivation.INACTIVE)
    return target


def inventory():
    targets = update_runtime._registered_plugin_targets()
    names = set(targets) | {name for name, _ in reconcile.discover_core_payload_dirs()}
    names.add(reconcile.SELF_PLUGIN)
    rows = []
    for name in sorted(names):
        target = targets.get(name)
        payload = reconcile.core_installed_payload_dir(name)
        row = {
            "id": name, "title": name, "enabled": target.activation.value if target else "unknown",
            "installed": payload is not None, "payload": "missing", "selected": "unknown",
            "running": "unknown", "source": f"{name}@{reconcile.MARKETPLACE}",
            "kind": "unknown", "updatable": False, "limitation": "",
        }
        if payload is None:
            row["limitation"] = "Payload missing; use setup/enablement before updating"
        else:
            row["payload"] = "unknown"
            try:
                manifest = _target_manifest(payload)
                version = manifest.get("version")
                row["payload"] = version if isinstance(version, str) and version else "unknown"
                scope = _target_scope(payload, manifest=manifest)
                row["kind"] = scope
                if scope == "none":
                    row.update(selected="not applicable", running="not applicable")
                else:
                    resolved = reconcile.resolve_runtime_installation(name, payload)
                    row["selected"] = reconcile.runtime_deployed_version(name, root=resolved.runtime_root) or "missing"
                    row["running"] = reconcile.runtime_running_version(name, root=resolved.runtime_root) or "unknown / not observed"
                    row["source"] += f"; {resolved.actual_mode}: {resolved.reason}"
                selected_target = _target(name, targets)
                if selected_target is None or (not selected_target.required and scope != "none"):
                    raise ValueError("Not enabled; enable through configuration before updating its runtime")
                plan = target_plan(name, selected_target)
                if plan.blocked or (plan.eligible is False and scope != "none"):
                    raise ValueError(plan.status)
                row["updatable"] = True
            except (OSError, ValueError) as error:
                row["limitation"] = str(error)
        rows.append(row)
    return {
        "contract": TARGET_CONTRACT, "entries": rows,
        "execution_source": str(config.install_dir().resolve()),
    }


def print_inventory():
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = inventory()
        print(json.dumps(result))
        return 0
    except (OSError, ValueError) as error:
        output.err(f"Installation inventory: {error}")
        return 1


def update_target(name: str) -> int:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", name):
        output.err("Invalid plugin target")
        return 1
    try:
        targets = update_runtime._registered_plugin_targets()
        target = _target(name, targets)
        payload = reconcile.core_installed_payload_dir(name)
        if target is None or (not target.required and (
            payload is None or _target_scope(payload) != "none"
        )):
            raise ValueError(f"{name}: not enabled or not registered; use setup/enablement")
        if reconcile.core_installed_payload_dir(name) is None:
            raise ValueError(f"{name}: payload absent; use setup, not an implicit installation")
        before = target_plan(name, target)
        if before.blocked or (not before.eligible and before.status != "payload-only"):
            raise ValueError(before.status)
        if not update_runtime._refresh_marketplace(reconcile.MARKETPLACE, cwd=target.context):
            raise ValueError("Selected marketplace refresh failed; resolve its reported source/authentication failure and retry")
        status = (
            update_acquisition.acquire(
                name, reconcile.MARKETPLACE, reconcile.resolve_copilot() or "copilot",
                cwd=target.context, engine=True,
            )
            if name == reconcile.SELF_PLUGIN else
            update_runtime._update_one_plugin_payload(name, reconcile.MARKETPLACE, cwd=target.context)
        )
        update_events.record("payload_refresh", f"{name}@{reconcile.MARKETPLACE}", status)
        if not status.startswith("OK"):
            return 1
        acquired = reconcile.core_installed_payload_dir(name)
        if acquired is None:
            raise ValueError("Acquired payload is unavailable; inspect acquisition and retry")
        if not target.required and _target_scope(acquired) != "none":
            raise ValueError("Acquired release now requires a runtime; enable through configuration before updating its runtime")
        step = target_plan(name, target)
        if step.blocked or (not step.eligible and step.status != "payload-only"):
            raise ValueError(step.status)
        if name == reconcile.SELF_PLUGIN and not step.status:
            return update_runtime._run_engine_installer(
                list(step.argv), step.payload, step.deadline_s, step.environment,
                runtime_root=step.runtime_root, target_version=step.target_version,
            )
        status = update_runtime._execute_runtime_step(step)
        ok = status.startswith(("OK", "SKIPPED")) or status == "payload-only"
        update_events.record(step.phase, name, status, changed=status.startswith("OK"))
        (output.ok if ok else output.err)(f"{name}: {status}")
        return 0 if ok else 1
    except (OSError, ValueError) as error:
        output.err(f"Targeted update blocked: {error}")
        update_events.record("runtime_preflight", name, f"blocked: {error}")
        return 1
