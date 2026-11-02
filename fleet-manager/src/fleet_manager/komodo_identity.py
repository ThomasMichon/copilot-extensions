"""Validated SPKI identity and inspected Core global trust, without changing receipts."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_der_public_key

from .komodo_incumbent import KomodoIncumbentMaterialization, KomodoMaterial
from .planning import PlanError


def public_identity(value: object) -> bytes:
    """Accept documented PUBLIC KEY PEM or base64 SubjectPublicKeyInfo DER only."""
    if type(value) is not str or not 1 <= len(value) <= 4096:
        raise PlanError("Komodo public identity encoding is unsupported")
    text = value.strip()
    lines = text.splitlines()
    if (len(lines) >= 3 and lines[0] == "-----BEGIN PUBLIC KEY-----"
            and lines[-1] == "-----END PUBLIC KEY-----"):
        text = "".join(lines[1:-1])
    try:
        encoded = base64.b64decode(text, validate=True)
        if base64.b64encode(encoded).decode("ascii") != text:
            raise ValueError
        key = load_der_public_key(encoded)
        if key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo) != encoded:
            raise ValueError
    except (ValueError, binascii.Error, UnsupportedAlgorithm):
        raise PlanError("Komodo public identity encoding is unsupported") from None
    return encoded


def effective_global_key(core: dict[str, Any], material: KomodoMaterial,
                         retained: str) -> str:
    """Resolve only the inspected fixed key reference or an explicit inline public key."""
    environment: dict[str, str] = {}
    for entry in core["Config"].get("Env") or []:
        if type(entry) is not str:
            raise PlanError("Komodo native environment is ambiguous")
        name, separator, value = entry.partition("=")
        if not separator or name in environment:
            raise PlanError("Komodo native environment is ambiguous")
        environment[name] = value
    value = environment.get("KOMODO_PERIPHERY_PUBLIC_KEY")
    if value == "file:/config/keys/periphery.pub":
        mounts = [mount for mount in core["Mounts"]
                  if mount.get("Destination") == "/config/keys"]
        if len(mounts) != 1 or mounts[0].get("Source") != str(material.keys):
            raise PlanError("Komodo Core global public identity custody is unproved")
        if type(material) is KomodoIncumbentMaterialization:
            material.check_mounts("core", core["Mounts"])
        elif mounts[0].get("Type") != "bind":
            raise PlanError("Komodo Core global public identity custody is unproved")
        return retained
    if value is None or value.startswith("file:"):
        raise PlanError("Komodo Core global public identity custody is unproved")
    public_identity(value)
    return value
