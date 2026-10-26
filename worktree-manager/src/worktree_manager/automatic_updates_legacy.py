"""Read-only legacy trigger inventory before adopting an exclusive update owner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agent_procutil import no_window_kwargs

from .automatic_updates_policy import PolicyError

LEGACY_NAME = "agent-machines-fleet-update-sweep"
SELF_NAME = "agent-machines-self-update-sweep"


def installed_inventory() -> dict | None:
    """Use Agent Machines' public read-only seam when it is installed."""
    from . import agent_plugin_runtime
    from .automatic_updates_process import update_environment
    from .self_install import default_root

    base = agent_plugin_runtime.resolve_installed_plugin_command("agent-machines")
    if base is None:
        root = agent_plugin_runtime.legacy_plugin_root("agent-machines")
        if any((root / name).exists() for name in ("current-version", "deploy-manifest.json")):
            raise PolicyError("Agent Machines installation exists but its inventory runtime is unavailable")
        return None
    completed = subprocess.run(
        [*base, "fleet-update", "inventory", "--json"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=90, check=False,
        env=update_environment(default_root()), **no_window_kwargs(),
    )
    if completed.returncode != 0:
        raise PolicyError(
            "installed Agent Machines cannot provide legacy update inventory; "
            "update it through the canonical installer before enrollment: "
            + completed.stderr.strip()[:2000]
        )
    try:
        result = json.loads(completed.stdout)
    except ValueError as exc:
        raise PolicyError("Agent Machines returned invalid legacy update inventory") from exc
    if (
        not isinstance(result, dict)
        or result.get("schema") != "copilot-extensions.legacy-update-inventory"
        or type(result.get("version")) is not int or result["version"] != 1
        or type(result.get("ok")) is not bool or type(result.get("adoption_ready")) is not bool
        or not isinstance(result.get("legacy_artifacts"), list)
    ):
        raise PolicyError("unsupported Agent Machines legacy update inventory contract")
    artifacts = result["legacy_artifacts"]
    if (
        len(artifacts) != 2
        or any(not isinstance(item, dict) for item in artifacts)
        or any(not isinstance(item.get("kind"), str) for item in artifacts)
        or {item.get("kind") for item in artifacts} != {"fleet", "self"}
    ):
        raise PolicyError("Agent Machines legacy inventory omitted a required update path")
    if (
        result["ok"] is not True or result["adoption_ready"] is not True
        or any(
            item.get("blocks_adoption") is not False
            or item.get("in_flight") is not False
            or item.get("retirement_required") is not False
            or type(item.get("present")) is not bool
            or (item.get("kind") == "fleet" and item.get("present") is not False)
            for item in artifacts
        )
    ):
        details = "; ".join(str(item.get("detail", "unknown")) for item in artifacts)
        raise PolicyError("legacy update handover is blocked: " + details[:4000])
    return result


def legacy_triggers() -> list[str]:
    if sys.platform == "win32":
        script = (
            "$ErrorActionPreference='Stop'; "
            "try { $tasks=@(Get-ScheduledTask -ErrorAction Stop | "
            "Where-Object {$_.TaskName -in @('agent-machines-fleet-update-sweep',"
            "'agent-machines-self-update-sweep')}); "
            "@($tasks | Select-Object TaskName,TaskPath,State) | "
            "ConvertTo-Json -Compress } catch { [Console]::Error.WriteLine($_); exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, check=False,
            **no_window_kwargs(),
        )
        if result.returncode != 0:
            raise PolicyError("could not inventory legacy scheduled updates: " + result.stderr.strip())
        try:
            value = json.loads(result.stdout) if result.stdout.strip() else []
        except ValueError as exc:
            raise PolicyError("invalid legacy task inventory") from exc
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise PolicyError("invalid legacy task inventory")
        blockers = []
        for task in value:
            name, state = task.get("TaskName"), task.get("State")
            if name not in (LEGACY_NAME, SELF_NAME) or state not in ("Ready", "Disabled", "Running", "Queued", 1, 2, 3, 4):
                raise PolicyError("legacy task state could not be verified")
            if name == LEGACY_NAME or state in ("Running", "Queued", 2, 4):
                blockers.append(f"{task.get('TaskPath', '')}{name} ({state})")
        return blockers
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    directories = dict.fromkeys((
        config / "systemd" / "user", Path.home() / ".config" / "systemd" / "user",
    ))
    paths = [
        directory / f"{LEGACY_NAME}.{suffix}"
        for directory in directories for suffix in ("timer", "service")
    ]
    installed = [str(path) for path in paths if path.exists() or path.is_symlink()]
    if installed:
        return installed
    # Loaded timers and running services can outlive deleted unit files.
    for unit in (f"{LEGACY_NAME}.timer", f"{LEGACY_NAME}.service", f"{SELF_NAME}.service"):
        try:
            result = subprocess.run(
                ["systemctl", "--user", "show", unit,
                 "--property=LoadState,ActiveState", "--no-pager"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except FileNotFoundError:
            return installed
        except (OSError, subprocess.SubprocessError) as error:
            raise PolicyError(f"legacy user-unit inventory failed for {unit}: {error}") from error
        if result.returncode != 0:
            raise PolicyError(
                f"legacy user-unit inventory is unavailable for {unit}: " + result.stderr.strip()
            )
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if properties.get("ActiveState") in ("active", "activating", "deactivating", "reloading"):
            installed.append(f"{unit} ({properties['ActiveState']})")
        elif (
            properties.get("ActiveState") not in ("inactive", "failed")
            or properties.get("LoadState") not in ("not-found", "loaded", "masked")
        ):
            raise PolicyError(f"legacy unit activity could not be verified: {unit}")
    return installed


def require_legacy_retired() -> None:
    if installed_inventory() is not None:
        return
    # An orphaned old manual run needs its owning inventory implementation.
    legacy_state = Path.home() / ".agent-machines"  # marketplace-isolation: allow legacy-compatibility
    for kind in ("self-update", "fleet-update"):
        lock = legacy_state / kind / "sweep.lock.json"
        if lock.exists() or lock.is_symlink():
            raise PolicyError(f"orphaned legacy update lock requires verification: {lock}")
    triggers = legacy_triggers()
    if triggers:
        raise PolicyError(
            "legacy fleet-update triggers require explicit retirement/in-flight checks before "
            "enrollment: " + ", ".join(triggers)
        )
