"""General user-owned Worktree Manager configuration."""

from __future__ import annotations

from dataclasses import dataclass
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
class ManagerConfig:
    ahp: AhpConfig = AhpConfig()


def _load(path: Path) -> dict:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManagerConfigError(f"could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManagerConfigError(f"{path} must contain TOML tables")
    return value


def _positive_timeout(table: dict, key: str, default: float) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ManagerConfigError(f"ahp.{key} must be a positive number")
    return float(value)


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
