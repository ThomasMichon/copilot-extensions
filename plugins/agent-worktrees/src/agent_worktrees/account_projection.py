"""Keep managed account projections separate from explicit local choices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .repos_configuration_sources import identity, resource_name

if TYPE_CHECKING:
    from .repos import ReposRegistry


def snapshot(registry: ReposRegistry) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, str]]]:
    return (
        dict(registry.account_map),
        {name: entry.account for name, entry in registry.repos.items()},
        dict(registry.account_projection),
    )


def prepare(registry: ReposRegistry, owner: str) -> None:
    """Withdraw only this owner's unchanged projections before recomposition."""
    owner = resource_name(owner)
    records = registry.account_projection
    if not isinstance(records, dict) or len(records) > 1024:
        raise ValueError("Invalid account projection records")
    for key, record in records.items():
        if (
            not isinstance(key, str) or not isinstance(record, dict)
            or set(record) != {"owner", "account"}
        ):
            raise ValueError("Invalid account projection record")
        resource_name(record["owner"])
        identity(record["account"])
    for key, record in tuple(records.items()):
        if record["owner"] != owner:
            continue
        if key == "default":
            if registry.default_account == record["account"]:
                registry.default_account = ""
        elif key.startswith("repo:"):
            entry = registry.repos.get(key[5:])
            if entry is not None and entry.account == record["account"]:
                entry.account = ""
        else:
            raise ValueError("Unsupported account projection target")
        del records[key]


def record(registry: ReposRegistry, owner: str, target: str, account: str) -> None:
    owner, account = resource_name(owner), identity(account)
    if target not in registry.account_projection and len(registry.account_projection) >= 1024:
        raise ValueError("Account projection record limit exceeded")
    registry.account_projection[target] = {"owner": owner, "account": account}


def explicit_repository(registry: ReposRegistry, name: str) -> None:
    registry.account_projection.pop("repo:" + name, None)


def explicit_default(registry: ReposRegistry) -> None:
    registry.account_projection.pop("default", None)
