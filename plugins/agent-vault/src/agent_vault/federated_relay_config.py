"""Installation-local opt-in, separate from portable vault/unlock settings."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit


class MachineAuth(TypedDict):
    kind: Literal["machine"]
    principal: str
    audience: str


class AzureAuth(TypedDict):
    kind: Literal["az-login"]
    resource: str
    tenant: str


class Grant(TypedDict):
    principal: str
    source: str
    account: str
    recipient: str
    host: str
    action: str


class Selection(TypedDict):
    schema: str
    version: int
    mode: str
    fleet: str
    gateway: str
    slot: str
    installation: str
    service: str
    prefix: str
    gateway_origin: str
    conduit_file: str
    gateway_ca: str | None
    proof_key_file: str
    account_policy_file: str
    grants: list[Grant]
    auth: MachineAuth | AzureAuth


def read_reference(path: Path, limit: int = 65536) -> bytes:
    if not path.is_absolute():
        raise ValueError("relay-reference-must-be-absolute")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("relay-reference-must-be-ordinary")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("relay-reference-replaced")
        body = stream.read(limit + 1)
    if not body or len(body) > limit:
        raise ValueError("relay-reference-size")
    return body


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("relay-duplicate-field")
        value[key] = item
    return value


def document(path: Path) -> dict[str, Any]:
    value = json.loads(read_reference(path), object_pairs_hook=_pairs,
                       parse_constant=lambda _: None)
    if type(value) is not dict:
        raise ValueError("relay-document-invalid")
    return value


def text(value: object) -> str:
    if (not isinstance(value, str) or not 0 < len(value) <= 2048
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise ValueError("relay-field-invalid")
    return value


def reference(value: object) -> Path:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError("relay-reference-invalid")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("relay-reference-must-be-absolute")
    return path


def load(root: Path) -> Selection | None:
    path = root / "federated-relay.json"
    if not os.path.lexists(path):
        return None
    value = document(path)
    common = {"schema", "version", "mode"}
    if (value.get("schema") != "agent-vault.federated-relay"
            or type(value.get("version")) is not int or value["version"] != 1):
        raise ValueError("relay-configuration-unsupported")
    if value.get("mode") == "disabled" and set(value) == common:
        return None
    fields = {
        "fleet", "gateway", "slot", "installation", "service", "prefix", "gateway_origin",
        "conduit_file", "gateway_ca", "proof_key_file", "account_policy_file", "grants", "auth",
    }
    if value.get("mode") != "federated-relay" or set(value) != common | fields:
        raise ValueError("relay-configuration-invalid")
    for name in ("fleet", "gateway", "slot", "installation", "service", "prefix", "gateway_origin"):
        text(value[name])
    uri = urlsplit(value["gateway_origin"])
    if (uri.scheme != "https" or not uri.hostname or uri.username is not None
            or uri.password is not None or uri.path or uri.query or uri.fragment):
        raise ValueError("relay-gateway-origin-invalid")
    for name in ("conduit_file", "proof_key_file", "account_policy_file"):
        reference(value[name])
    if value["gateway_ca"] is not None:
        reference(value["gateway_ca"])
    auth = value["auth"]
    if type(auth) is not dict:
        raise ValueError("relay-auth-invalid")
    expected = ({"kind", "principal", "audience"} if auth.get("kind") == "machine"
                else {"kind", "resource", "tenant"} if auth.get("kind") == "az-login" else set())
    if not expected or set(auth) != expected:
        raise ValueError("relay-auth-invalid")
    for item in auth.values():
        text(item)
    if auth.get("resource") == "*":
        raise ValueError("relay-auth-invalid")
    grants = value["grants"]
    grant_fields = {"principal", "source", "account", "recipient", "host", "action"}
    if (type(grants) is not list or not 0 < len(grants) <= 128
            or any(type(grant) is not dict or set(grant) != grant_fields for grant in grants)):
        raise ValueError("relay-grants-invalid")
    for grant in grants:
        for item in grant.values():
            text(item)
    selected_auth: MachineAuth | AzureAuth
    if auth["kind"] == "machine":
        selected_auth = {
            "kind": "machine", "principal": text(auth["principal"]),
            "audience": text(auth["audience"]),
        }
    else:
        selected_auth = {
            "kind": "az-login", "resource": text(auth["resource"]), "tenant": text(auth["tenant"]),
        }
    return {
        "schema": value["schema"], "version": value["version"], "mode": value["mode"],
        "fleet": value["fleet"], "gateway": value["gateway"], "slot": value["slot"],
        "installation": value["installation"], "service": value["service"],
        "prefix": value["prefix"], "gateway_origin": value["gateway_origin"],
        "conduit_file": value["conduit_file"], "gateway_ca": value["gateway_ca"],
        "proof_key_file": value["proof_key_file"], "account_policy_file": value["account_policy_file"],
        "grants": [{
            "principal": grant["principal"], "source": grant["source"], "account": grant["account"],
            "recipient": grant["recipient"], "host": grant["host"], "action": grant["action"],
        } for grant in grants],
        "auth": selected_auth,
    }
