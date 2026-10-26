"""Finite selected local continuity projection -> original enrollment -> outbound access."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import stat
import time

import aiohttp
from joserfc.jwk import ECKey

from . import planning
from .gateway_service_caller import _read
from .gateway_service_channel import TransportError, check, origin, shape, unpack
from .gateway_service_connector import verified_tls
from .gateway_service_context import fields
from .gateway_enrollment import enrollment_scope
from .machine_identity import PROFILE, machine_headers, make_machine_proof
from .private_state import create_private_file, private_directory, read_private_file
from .operations import OperationIntent
from .runtime_identity import _text, _time

PROVIDER = "local-projection-v1"
CAPABILITY = "delegated-machine-enrollment-v1"


def _projection(path: Path, harness: str) -> dict:
    value = fields(planning.decode(read_private_file(path, 16384)), {
        "schema", "version", "provider", "authority", "audience", "delegation", "grant", "gateway_ca"})
    check(value["schema"] == "fleet-manager.continuity-projection"
          and type(value["version"]) is int and value["version"] == 1
          and value["provider"] == PROVIDER, "unsupported-continuity-source")
    declaration = fields(
        value["delegation"], {"id", "scope", "grant_sha256", "grant_expires_at"})
    scope = enrollment_scope(declaration["scope"])
    planning.identifier(declaration["id"], "delegation")
    check(scope["harness"] == harness,
          "continuity-harness-mismatch")
    check(isinstance(value["grant"], str)
          and re.fullmatch(r"[A-Za-z0-9_-]{43}", value["grant"]) is not None
          and isinstance(declaration["grant_sha256"], str)
          and re.fullmatch(r"[0-9a-f]{64}", declaration["grant_sha256"]) is not None
          and hmac.compare_digest(hashlib.sha256(value["grant"].encode()).hexdigest(),
                                  declaration["grant_sha256"]), "continuity-grant-mismatch")
    check(type(scope.get("expires_at")) is int and int(time.time()) < scope["expires_at"],
          "continuity-grant-expired")
    check(_time(declaration["grant_expires_at"]), "invalid-continuity-grant")
    check(isinstance(scope.get("slots"), list) and 1 <= len(scope["slots"]) <= 64,
          "invalid-continuity-scope")
    check(value["gateway_ca"] is None or (
        isinstance(value["gateway_ca"], str) and Path(value["gateway_ca"]).is_absolute()),
        "invalid-continuity-trust")
    origin(scope["destination"])
    planning.identifier(value["authority"], "authority")
    check(_text(value["audience"], 512), "invalid-continuity-scope")
    return value


def _state(root: Path, projection: dict, *, apply: bool) -> dict | None:
    """Immutable single-winner state; an interrupted partial file is never rotated."""
    path = root / "machine.json"
    scope, declaration = projection["delegation"]["scope"], projection["delegation"]
    source_digest = planning.digest({
        "provider": projection["provider"], "authority": projection["authority"],
        "audience": projection["audience"], "delegation": declaration})
    for component in root.parents:
        info = component.lstat()
        check(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
              and not getattr(info, "st_file_attributes", 0) & 0x400,
              "continuity-state-indirection")
    if root.exists():
        private_directory(root)
    if path.exists():
        value = fields(planning.decode(read_private_file(path, 16384)), {
            "version", "source_digest", "private_key", "request"})
        check(type(value["version"]) is int and value["version"] == 1
              and value["source_digest"] == source_digest, "continuity-state-conflict")
        request = fields(value["request"], {"intent", "delegation", "scope", "public_key"})
        check(isinstance(value["private_key"], dict) and "d" in value["private_key"],
              "invalid-continuity-key")
        key = ECKey.import_key(value["private_key"])
        check(key.as_dict(private=False) == request["public_key"]
              and request["scope"] == scope and request["delegation"] == declaration["id"],
              "continuity-state-conflict")
        intent = OperationIntent.from_dict(request["intent"]).to_dict()
        binding = {
            "delegation": declaration["id"], "delegation_digest": planning.digest(declaration),
            "scope": scope, "public_key": request["public_key"]}
        check(intent["command"] == "machine.enroll"
              and intent["parameters"] == {"enrollment": planning.digest(binding)}
              and intent["authority"] == projection["authority"]
              and intent["principal"] == scope["principal"] and intent["account"] == scope["account"]
              and intent["target"] == scope["machine"] and intent["generation"] == scope["generation"],
              "continuity-state-conflict")
        return value
    if not apply:
        return None
    check(int(time.time()) < declaration["grant_expires_at"], "continuity-delegation-expired")
    key = ECKey.generate_key("P-256")
    binding = {
        "delegation": declaration["id"], "delegation_digest": planning.digest(declaration),
        "scope": scope, "public_key": key.as_dict(private=False)}
    intent = {
        "schema": "fleet-manager.operation-intent", "version": 1,
        "operation": "enroll-" + secrets.token_hex(16), "authority": projection["authority"],
        "principal": scope["principal"], "account": scope["account"], "target": scope["machine"],
        "generation": scope["generation"], "command": "machine.enroll",
        "parameters": {"enrollment": planning.digest(binding)}}
    OperationIntent.from_dict(intent)
    value = {
        "version": 1, "source_digest": source_digest, "private_key": key.as_dict(private=True),
        "request": {
            "intent": intent, "delegation": declaration["id"],
            "scope": scope, "public_key": binding["public_key"]}}
    try:
        private_directory(root)
        descriptor = create_private_file(path)
    except FileExistsError:
        return _state(root, projection, apply=False)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(planning.canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return value


async def bootstrap(source: Path, *, harness: str, state_root: Path,
                    apply: bool) -> dict[str, str]:
    """No provider login, sync, SSH, installation, retries or lifecycle changes."""
    check(source.is_absolute() and state_root.is_absolute()
          and ".." not in state_root.parts and "session-state" not in state_root.parts,
          "invalid-continuity-state-root")
    check(source.parent != state_root and source.parent not in state_root.parents
          and state_root not in source.parents, "continuity-state-source-overlap")
    for parent in (state_root, *state_root.parents):
        check(not (parent / ".git").exists(), "continuity-state-in-repository")
    projection = await asyncio.to_thread(_projection, source, harness)
    scope = projection["delegation"]["scope"]
    gateway = origin(scope["destination"])
    tls = verified_tls(projection["gateway_ca"])
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls), trust_env=False,
        cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as session:
        session._retry_connection = False

        async def exchange(path: str, *, payload: bytes | None = None,
                           authenticated: dict[str, str] | None = None) -> tuple[int, dict]:
            async with session.request(
                "GET" if payload is None else "POST", gateway + path, data=payload,
                headers=authenticated, allow_redirects=False,
            ) as response:
                raw = await _read(response, 16384)
                try:
                    return response.status, unpack(raw.decode("utf-8"))
                except UnicodeError:
                    raise TransportError("invalid-gateway-response") from None

        status, challenge = await exchange("/_fleet/enrollment-challenge")
        check(status == 200, "unsupported-enrollment-receiver", 409)
        shape(challenge, {"profile", "nonce"})
        check(challenge["profile"] == CAPABILITY and isinstance(challenge["nonce"], str)
              and len(challenge["nonce"]) == 43, "unsupported-enrollment-receiver", 409)
        retained = await asyncio.to_thread(_state, state_root, projection, apply=apply)
        if retained is None:
            return {"status": "needed", "stage": "enrollment"}
        key = ECKey.import_key(retained["private_key"])

        async def connect() -> bool:
            status, challenge = await exchange("/_fleet/machine-challenge")
            check(status == 200, "machine-authentication-unavailable")
            shape(challenge, {"profile", "nonce"})
            check(challenge["profile"] == PROFILE, "unsupported-profile")
            payload = planning.canonical({"slot": scope["slots"][0]})
            proof = make_machine_proof(
                key, principal=scope["principal"], audience=projection["audience"],
                kind="caller", resource=scope["slots"][0], method="POST",
                target_uri=gateway + "/_fleet/machine-connect", nonce=challenge["nonce"], body=payload)
            status, connected = await exchange(
                "/_fleet/machine-connect", payload=payload,
                authenticated=machine_headers(scope["principal"], proof))
            if status == 401:
                return False
            check(status == 200, "outbound-connection-refused")
            shape(connected, {"profile", "state", "principal"})
            check(connected == {
                "profile": PROFILE, "state": "connected", "principal": scope["principal"],
            }, "connection-result-mismatch")
            return True

        if await connect():
            return {"status": "ready", "stage": "connected"}
        if apply:
            check(int(time.time()) < projection["delegation"]["grant_expires_at"],
                  "continuity-delegation-expired")
            payload = planning.canonical(retained["request"])
            proof = make_machine_proof(
                key, principal=scope["principal"], audience=projection["audience"],
                kind="caller", resource="enrollment", method="POST",
                target_uri=gateway + "/_fleet/enroll", nonce=challenge["nonce"], body=payload)
            status, enrolled = await exchange(
                "/_fleet/enroll", payload=payload,
                authenticated={"Fleet-Enrollment-Grant": projection["grant"],
                               "Fleet-Machine-Proof": proof})
            check(status == 200, "enrollment-refused-or-unknown", 409)
            shape(enrolled, {"profile", "operation", "enrollment", "state"})
            check(enrolled["profile"] == PROFILE and enrolled["state"] == "published"
                  and enrolled["operation"] == retained["request"]["intent"]["operation"]
                  and enrolled["enrollment"] == retained["request"]["intent"]["parameters"]["enrollment"],
                  "enrollment-result-mismatch")
        else:
            return {"status": "needed", "stage": "connection"}
        check(await connect(), "outbound-connection-refused")
        return {"status": "ready", "stage": "connected"}
