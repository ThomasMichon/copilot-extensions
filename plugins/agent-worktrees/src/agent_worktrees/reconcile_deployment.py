"""Installation-owned publication admission for plugin reconciliation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _matching_path(value: Any, expected: Path | None) -> bool:
    from .reconcile import _same_path

    if expected is None:
        return value is None
    return (
        isinstance(value, str) and bool(value)
        and Path(value).is_absolute() and _same_path(Path(value), expected)
    )


def publication_diagnostic(
    plugin: str, payload: Path, root: Path, *, context: Path | None,
) -> dict[str, Any] | None:
    """Check the addressed installation without changing its deployment policy."""
    from . import reconcile

    def denied(reason: str, message: str) -> dict[str, Any]:
        return {
            "service": plugin, "phase": "runtime", "reason": reason,
            "message": message,
        }

    policy = root / "runtime-deployment.json"
    try:
        policy.lstat()
    except FileNotFoundError:
        # Older standalone payloads need no new helper merely to retain their
        # existing behavior. A present policy always requires canonical proof.
        return None
    except OSError as error:
        return denied("deployment-policy-invalid", str(error))
    try:
        helper = reconcile._installation_context_helper(plugin, payload)
        if helper is None:
            raise ValueError(
                "The payload lacks installation-owned deployment-policy support; "
                "update the plugin adapter explicitly before publishing this runtime."
            )
        result = reconcile._run_installation_mode_helper(
            plugin, payload, helper, reconcile.runtime_dir(plugin),
            context=context, action="probe-plugin-publication",
        )
        if (
            result.get("schema") != reconcile._INSTALLATION_RESOLUTION_SCHEMA
            or result.get("version") != 1
            or result.get("marketplaceId") != reconcile.CORE_MARKETPLACE_ID
            or result.get("pluginId") != plugin
            or result.get("status") not in (
                ("ready", "deactivation-required") if context is not None else ("ready",)
            )
            or result.get("actualMode") != ("namespaced" if context else "legacy")
            or not _matching_path(result.get("runtimeRoot"), root)
            or not _matching_path(result.get("context"), context)
        ):
            raise ValueError("The deployment probe did not confirm the ready installation.")
        deployment = result.get("deployment")
        if not isinstance(deployment, dict) or not _matching_path(deployment.get("path"), policy):
            raise ValueError("The deployment probe returned an invalid policy location.")
        if deployment.get("allowPluginPublication") is True:
            if (
                deployment.get("state") not in {"missing", "valid"}
                or deployment.get("managed") is not False
                or deployment.get("reason") not in {
                    "deployment-default-standalone", "deployment-explicit-standalone",
                }
            ):
                raise ValueError("The deployment probe returned an invalid permission.")
            return None
        reason = deployment.get("reason")
        if reason not in {
            "deployment-managed", "deployment-policy-invalid",
            "deployment-policy-version-unsupported",
        }:
            raise ValueError("The deployment probe returned an invalid refusal.")
        message = " ".join(
            value for key in ("diagnostic", "recovery")
            if isinstance(value := deployment.get(key), str) and value
        )
        if not message:
            raise ValueError("The deployment probe omitted its recovery diagnostic.")
        return denied(reason, message)
    except (OSError, ValueError) as error:
        return denied("deployment-policy-unavailable", str(error))


def execution_diagnostic(
    update: dict[str, Any], *, staged: bool = False,
) -> dict[str, Any] | None:
    """Revalidate runtime steps, retaining the stager's own Worktrees discovery."""
    from . import reconcile

    if update.get("phase") != "runtime":
        return None
    plugin = str(update.get("service", ""))
    try:
        if staged:
            if plugin != reconcile.SELF_PLUGIN:
                raise ValueError("Staged payload discovery is only supported for agent-worktrees.")
            from .update_stage import discover_plugin_dir

            payload, _layout = discover_plugin_dir()
        else:
            payload = reconcile.core_installed_payload_dir(plugin)
        if payload is None:
            raise ValueError("The owning plugin payload is no longer installed.")
        environment, root = reconcile.runtime_installer_environment(plugin, payload)
        # Do not let a plan prepared for another root or receipt borrow the
        # currently selected installation's permission.
        for field, actual in (("payload_root", payload), ("runtime_root", root)):
            expected = update.get(field)
            if expected is not None and (
                not isinstance(expected, str)
                or not reconcile._same_path(Path(expected), actual)
            ):
                raise ValueError(f"The runtime plan's {field} changed; rebuild the plan.")
        expected_context = update.get("environment", {}).get("COPILOT_EXTENSIONS_CONTEXT")
        if expected_context != environment.get("COPILOT_EXTENSIONS_CONTEXT"):
            raise ValueError("The runtime plan's installation context changed; rebuild the plan.")
        return publication_diagnostic(
            plugin, payload, root,
            context=Path(expected_context) if expected_context else None,
        )
    except (OSError, ValueError) as error:
        return {
            "service": plugin, "phase": "runtime",
            "reason": "deployment-policy-unavailable", "message": str(error),
        }
