"""Bound local Azure identities for routed acquisition; no account switching."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid

from agent_procutil import windowless_daemon_kwargs

from ..config import AuthSpec
from ..routing_errors import RoutingError


async def azure_json(args: list[str], *, timeout: float) -> object:
    from credential_relay.sources.az_login import _az_argv
    from .injectors import _terminate_tree

    argv = _az_argv([*args, "--output", "json"])
    if not argv:
        raise RoutingError("routing auth identity unavailable: local Azure CLI not found")
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **windowless_daemon_kwargs(),
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            raise RoutingError("routing auth identity unavailable: Azure CLI command failed")
        return json.loads(stdout)
    except (OSError, TimeoutError, asyncio.TimeoutError,
            json.JSONDecodeError, UnicodeDecodeError) as exc:
        await _terminate_tree(proc)
        raise RoutingError("routing auth identity unavailable: Azure CLI probe failed") from exc
    except BaseException:
        await _terminate_tree(proc)
        raise


def _account_identity(account: object) -> dict[str, str]:
    if not isinstance(account, dict):
        raise RoutingError("routing auth identity unavailable: malformed Azure account")
    user = account.get("user")
    if not isinstance(user, dict) or any(
        not isinstance(value, str) or not value.strip()
        for value in (user.get("name"), user.get("type"),
                      account.get("tenantId"), account.get("id"))
    ):
        raise RoutingError("routing auth identity unavailable: incomplete Azure account")
    principal_type = user["type"].casefold()
    if principal_type not in {"user", "serviceprincipal"}:
        raise RoutingError("routing auth identity unavailable: unsupported Azure principal")
    if principal_type == "serviceprincipal":
        try:
            uuid.UUID(user["name"])
        except ValueError as exc:
            raise RoutingError("routing auth identity unavailable: service principal "
                               "has no stable application identifier") from exc
    return {
        "principal": user["name"].casefold(),
        "principal_type": principal_type,
        "tenant": account["tenantId"].casefold(),
        "subscription": account["id"],
        "cloud": str(account.get("environmentName", "")),
        "azure_config": os.environ.get("AZURE_CONFIG_DIR", ""),
    }


async def resolve_entra_identity(spec: AuthSpec, *, timeout: float) -> dict[str, str]:
    # A relay token cannot be attributed to the local az identity. Its current
    # protocol has no identity probe/pin: never silently choose another provider.
    if not spec.tenant and shutil.which("ado-auth-helper"):
        raise RoutingError(
            "routing auth identity unavailable: credential relay cannot bind identity; "
            "use an identity-pinned command/env source")
    default = _account_identity(await azure_json(["account", "show"], timeout=timeout))
    if not spec.tenant or spec.tenant.casefold() == default["tenant"]:
        return default
    accounts = await azure_json(["account", "list", "--all"], timeout=timeout)
    if not isinstance(accounts, list):
        raise RoutingError("routing auth identity unavailable: malformed Azure account list")
    candidates = [_account_identity(account) for account in accounts
                  if isinstance(account, dict)
                  and str(account.get("tenantId", "")).casefold() == spec.tenant.casefold()]
    principals = {(c["principal"], c["principal_type"]) for c in candidates}
    if len(principals) != 1:
        raise RoutingError("routing auth identity unavailable: requested tenant is "
                           "unavailable or has ambiguous principals")
    return sorted(candidates, key=lambda c: c["subscription"])[0]
