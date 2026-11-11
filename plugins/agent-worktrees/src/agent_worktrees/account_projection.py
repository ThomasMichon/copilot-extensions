"""Keep managed account projections separate from explicit local choices."""

from __future__ import annotations

from .repos_configuration_sources import identity, resource_name


def snapshot(registry):
    return (
        dict(registry.account_map),
        {name: entry.account for name, entry in registry.repos.items()},
        dict(registry.account_projection),
    )


def prepare(registry, owner):
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


def record(registry, owner, target, account):
    owner, account = resource_name(owner), identity(account)
    if target not in registry.account_projection and len(registry.account_projection) >= 1024:
        raise ValueError("Account projection record limit exceeded")
    registry.account_projection[target] = {"owner": owner, "account": account}


def explicit_repository(registry, name):
    registry.account_projection.pop("repo:" + name, None)


def explicit_default(registry):
    registry.account_projection.pop("default", None)
