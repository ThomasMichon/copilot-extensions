"""Synthetic public-only Ed25519 SPKI; no private or production key material."""

from __future__ import annotations

import base64

PUBLIC_DER = {
    name: base64.b64encode(bytes.fromhex("302a300506032b6570032100") + bytes([number]) * 32).decode()
    for number, name in enumerate(("core", "periphery"), 1)
}
PUBLIC_PEM = {
    name: "-----BEGIN PUBLIC KEY-----\n" + value + "\n-----END PUBLIC KEY-----"
    for name, value in PUBLIC_DER.items()
}
