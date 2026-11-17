"""General user-owned Worktree Manager configuration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only
    import tomli as tomllib

from .source_config import config_path


class ManagerConfigError(RuntimeError):
    """The user-owned Manager configuration is invalid."""


@dataclass(frozen=True)
class AhpConfig:
    endpoint_url: str = ""
    account: str = ""
    protocol_versions: tuple[str, ...] = ("0.7.0",)
    auth_resource: str = "https://api.github.com"
    connect_timeout_seconds: float = 10.0
    lifecycle_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class RestartRecoveryConfig:
    """Explicit local requests, not proof of runtime recovery capability."""

    shutdown_notifications: bool = False
    automatic_restore: bool = False


@dataclass(frozen=True)
class MaintenanceConfig:
    automatic: bool = True


@dataclass(frozen=True)
class ManagerConfig:
    ahp: AhpConfig = AhpConfig()
    restart_recovery: RestartRecoveryConfig = RestartRecoveryConfig()
    maintenance: MaintenanceConfig = MaintenanceConfig()


def _load(path: Path) -> dict:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ManagerConfigError(f"could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManagerConfigError(f"{path} must contain TOML tables")
    return value


def _positive_timeout(table: dict, key: str, default: float) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ManagerConfigError(f"ahp.{key} must be a positive number")
    return float(value)


def _restart_recovery(data: dict) -> RestartRecoveryConfig:
    raw = data.get("restart_recovery", {})
    if not isinstance(raw, dict):
        raise ManagerConfigError("restart_recovery must be a TOML table")
    unknown = raw.keys() - {"shutdown_notifications", "automatic_restore"}
    if unknown:
        raise ManagerConfigError(f"unknown restart_recovery setting: {sorted(unknown)[0]}")
    for key, value in raw.items():
        if type(value) is not bool:
            raise ManagerConfigError(f"restart_recovery.{key} must be a boolean")
    return RestartRecoveryConfig(**raw)


def _maintenance(data: dict) -> MaintenanceConfig:
    raw = data.get("maintenance", {})
    if not isinstance(raw, dict):
        raise ManagerConfigError("maintenance must be a TOML table")
    unknown = raw.keys() - {"automatic"}
    if unknown:
        raise ManagerConfigError(f"unknown maintenance setting: {sorted(unknown)[0]}")
    if "automatic" in raw and type(raw["automatic"]) is not bool:
        raise ManagerConfigError("maintenance.automatic must be a boolean")
    return MaintenanceConfig(**raw)


def automatic_maintenance_enabled() -> bool:
    """Re-read policy at admission; unreadable policy never authorizes effects."""
    try:
        return load_config().maintenance.automatic
    except ManagerConfigError as exc:
        logging.getLogger(__name__).warning(
            "Automatic maintenance blocked by Manager configuration: %s", exc
        )
        return False


def load_config(root: Path | None = None) -> ManagerConfig:
    """Load the shared ``~/.worktree-manager/config.toml`` surface."""
    data = _load(config_path(root))
    raw = data.get("ahp", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ManagerConfigError("ahp must be a TOML table")

    endpoint = raw.get("endpoint_url", "")
    account = raw.get("account", "")
    resource = raw.get("auth_resource", "https://api.github.com")
    versions = raw.get("protocol_versions", ["0.7.0"])
    for key, value in (
        ("endpoint_url", endpoint),
        ("account", account),
        ("auth_resource", resource),
    ):
        if not isinstance(value, str):
            raise ManagerConfigError(f"ahp.{key} must be a string")
    if (
        not isinstance(versions, list)
        or not versions
        or any(not isinstance(version, str) or not version for version in versions)
    ):
        raise ManagerConfigError(
            "ahp.protocol_versions must be a non-empty string array"
        )
    return ManagerConfig(
        restart_recovery=_restart_recovery(data),
        maintenance=_maintenance(data),
        ahp=AhpConfig(
            endpoint_url=endpoint.strip(),
            account=account.strip(),
            protocol_versions=tuple(versions),
            auth_resource=resource,
            connect_timeout_seconds=_positive_timeout(
                raw, "connect_timeout_seconds", 10.0
            ),
            lifecycle_timeout_seconds=_positive_timeout(
                raw, "lifecycle_timeout_seconds", 30.0
            ),
        )
    )
