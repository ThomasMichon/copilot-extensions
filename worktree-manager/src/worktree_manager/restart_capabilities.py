"""Read-only restart capability reporting; configuration is never admission."""

from __future__ import annotations

from pathlib import Path

from .manager_config import ManagerConfigError, RestartRecoveryConfig, load_config
from .restart_observer import RestartObserver


def recovery_capabilities(config: RestartRecoveryConfig, platform: str | None) -> dict:
    """Describe local requests without claiming a connected platform adapter."""
    notifications: dict = {
        "requested": config.shutdown_notifications,
        "status": "disabled",
    }
    if config.shutdown_notifications:
        notifications.update(
            status="unconfirmed" if platform == "linux" else "blocked",
            blocker=(
                "Read-only inspection does not establish an active logind delay inhibitor."
                if platform == "linux" else
                "Native shutdown notifications are not qualified for this environment; "
                "WSL teardown and Windows shutdown are not logind shutdown."
            ),
        )
    automatic: dict = {
        "requested": config.automatic_restore,
        "status": "disabled",
    }
    if config.automatic_restore:
        automatic.update(
            status="blocked",
            blocker="Owner admission and acknowledged native restart Resume are not qualified.",
        )
    return {"shutdown_notifications": notifications, "automatic_restore": automatic}


def inspect_recovery(root: Path) -> dict:
    """Inspect evidence/configuration without creating state or contacting logind."""
    observer = RestartObserver(root)
    result = observer.startup()
    try:
        config = load_config(root).restart_recovery
    except ManagerConfigError as exc:
        result["blocker"] = str(exc)
        return result
    capabilities = recovery_capabilities(
        config, observer.epoch.platform if observer.epoch is not None else None,
    )
    result["capabilities"] = capabilities
    blockers = [value["blocker"] for value in capabilities.values() if "blocker" in value]
    if blockers:
        result["blocker"] = "; ".join(filter(None, [result.get("blocker"), *blockers]))
    return result
