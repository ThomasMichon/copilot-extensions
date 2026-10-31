"""Read existing original evidence; never fabricate a historical custody baseline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .komodo_declaration import fingerprint
from .komodo_evidence import native_reference
from .planning import PlanError, decode, digest
from .private_state import read_private_file

SLOTS = ("good", "previous_good", "failed", "retirement")


def original_file(path: Path) -> bytes:
    try:
        return read_private_file(path, 1024 * 1024)
    except OSError:
        raise PlanError("Komodo repair original immutable evidence is unavailable") from None


def retained_shape(record: dict[str, Any]) -> None:
    required = {"deployment", "profile", "material", "public_identity", "native", "selection",
                "network", "fixed_profile"}
    for name in SLOTS[:3]:
        value = record[name]
        if value is None:
            continue
        if (type(value) is not dict or not required <= set(value)
                or type(value["material"]) is not dict or type(value["native"]) is not dict
                or type(value["selection"]) is not dict
                or not {"adoption", "adopted_public_identity", "adopted_network"} <= set(value["selection"])
                or ("partial_public_keys" in value and type(value["partial_public_keys"]) is not dict)
                or ("repair" in value and type(value["repair"]) is not dict)):
            raise PlanError("Komodo repair retained candidate evidence is corrupt")
        fingerprint(value["deployment"])
        for reference in value["native"].values():
            native_reference(reference)
    if record["retirement"] is not None and type(record["retirement"]) is not dict:
        raise PlanError("Komodo repair retained retirement evidence is corrupt")


def read_outcome(root: Path, reference: object) -> dict[str, Any]:
    identifier = fingerprint(reference)
    value = decode(original_file(root / "receipts" / (identifier + ".json")))
    if type(value) is not dict or digest(value) != identifier:
        raise PlanError("Komodo repair immutable outcome is missing or changed")
    return value


def validate_history(root: Path, value: object) -> dict[str, str]:
    if type(value) is not dict or not set(value) <= set(SLOTS):
        raise PlanError("Komodo repair original outcome history is corrupt")
    result: dict[str, str] = {}
    for name, reference in value.items():
        read_outcome(root, reference)
        result[name] = fingerprint(reference)
    return result


def original_history(root: Path, record: dict[str, Any], request_digest: str) -> dict[str, str]:
    for name in ("good", "failed"):
        value = record[name]
        if (type(value) is dict and value.get("repair", {}).get("request_digest") == request_digest):
            return validate_history(root, value.get("prior_outcomes"))
    result: dict[str, str] = {}
    for name in SLOTS:
        value = record[name]
        if value is not None:
            reference = digest(value)
            if read_outcome(root, reference) != value:
                raise PlanError("Komodo repair original outcome history changed")
            result[name] = reference
    return result
