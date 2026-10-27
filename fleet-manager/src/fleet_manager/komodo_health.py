"""Authenticated current Core/Periphery readiness over the owned loopback publication."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from .artifact_staging import _bytes
from .komodo_declaration import KomodoDeclaration
from .komodo_profile import KomodoMaterialization, ordinary_path
from .planning import PlanError, decode, digest, identifier

MAX_RESPONSE = 64 * 1024


class _Starting(Exception):
    pass


def public_keys(material: KomodoMaterialization, *, missing_ok: bool = False) -> dict[str, str]:
    result = {}
    try:
        ordinary_path(material.keys)
    except OSError:
        raise PlanError("Komodo retained public identity is unavailable") from None
    for name in ("core", "periphery"):
        path = material.keys / (name + ".pub")
        try:
            ordinary_path(path)
            value = _bytes(path, 4096).decode("utf-8").strip()
        except FileNotFoundError:
            if missing_ok:
                continue
            raise PlanError("Komodo retained public identity is unavailable") from None
        except (OSError, UnicodeError):
            raise PlanError("Komodo retained public identity is unavailable") from None
        if not value:
            raise PlanError("Komodo retained public identity is unavailable")
        result[name] = value
    return result


def public_key_fingerprints(material: KomodoMaterialization) -> dict[str, str]:
    return {name: digest(value) for name, value in public_keys(material, missing_ok=True).items()}


def identity_digest(material: KomodoMaterialization) -> str:
    return digest(public_keys(material))


async def _request(client: aiohttp.ClientSession, base: str, endpoint: str,
                   payload: dict[str, Any], *, token: str | None,
                   deadline: float) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _Starting
    headers = {} if token is None else {"authorization": token}
    try:
        async with client.post(
            base + endpoint, json=payload, headers=headers, allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=min(5, remaining)),
        ) as response:
            if response.status >= 500:
                raise _Starting
            if response.status != 200:
                raise PlanError("Komodo authenticated health request was refused")
            data = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                data.extend(chunk)
                if len(data) > MAX_RESPONSE:
                    raise PlanError("Komodo health response exceeds its byte bound")
            value = decode(bytes(data))
            if not isinstance(value, dict):
                raise PlanError("Komodo health response has an unsupported shape")
            return value
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise _Starting from None


async def current_health(declaration: KomodoDeclaration, material: KomodoMaterialization,
                         *, deadline: float) -> dict[str, str]:
    """Call only after exact native ownership, image and loopback-port inspection.

    Fixed read APIs verify the live connection, not a saved readiness document.
    No enrollment, onboarding-key issuance or workload write is performed.
    """
    selected = declaration.to_dict()
    credentials = material.credential_bytes()
    base = "http://127.0.0.1:" + str(selected["core_port"])
    token = None
    async with aiohttp.ClientSession(
        trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
        connector=aiohttp.TCPConnector(limit=1),
    ) as client:
        while time.monotonic() < deadline:
            try:
                if token is None:
                    login = await _request(client, base, "/auth/login/LoginLocalUser", {
                        "username": credentials["admin_username"].decode("utf-8"),
                        "password": credentials["admin_password"].decode("utf-8"),
                    }, token=None, deadline=deadline)
                    envelope = login.get("data")
                    token = envelope.get("jwt") if isinstance(envelope, dict) else login.get("jwt")
                    if (type(token) is not str or not 1 <= len(token) <= 8192
                            or any(ord(char) <= 32 or ord(char) >= 127 for char in token)):
                        raise PlanError("Komodo local authentication returned an unsupported shape")
                version = await _request(client, base, "/read/GetVersion", {}, token=token,
                                         deadline=deadline)
                if version.get("version") != selected["images"]["core"]["version"]:
                    raise PlanError("Komodo current Core version differs from selected artifact")
                server = await _request(client, base, "/read/GetServer", {
                    "server": selected["server_name"],
                }, token=token, deadline=deadline)
                config, info = server.get("config"), server.get("info")
                if (server.get("name") != selected["server_name"]
                        or not isinstance(config, dict) or config.get("enabled") is not True
                        or not isinstance(info, dict)):
                    raise PlanError("Komodo registered Periphery differs from selected instance")
                keys = public_keys(material)
                if info.get("public_key") != keys["periphery"]:
                    raise PlanError("Komodo registered Periphery identity differs from retained keys")
                state = await _request(client, base, "/read/GetServerState", {
                    "server": selected["server_name"],
                }, token=token, deadline=deadline)
                periphery = await _request(client, base, "/read/GetPeripheryInformation", {
                    "server": selected["server_name"],
                }, token=token, deadline=deadline)
                if (state.get("status") != "Ok" or periphery.get("docker_connected") is not True):
                    raise _Starting
                if (periphery.get("version") != selected["images"]["periphery"]["version"]
                        or periphery.get("public_key") != keys["periphery"]):
                    raise PlanError("Komodo live Periphery differs from selected version or identity")
                server_id = server.get("id", server.get("_id"))
                if isinstance(server_id, dict) and set(server_id) == {"$oid"}:
                    server_id = server_id["$oid"]
                identifier(server_id, "Komodo current server identity")
                return {
                    "core_version": version["version"], "server": server_id,
                    "periphery_version": periphery["version"],
                    "public_identity": digest(keys),
                }
            except _Starting:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(min(2, remaining))
    raise PlanError("Komodo authenticated current readiness deadline exceeded")
