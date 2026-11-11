"""Local attempt evidence, never authority to adopt another repository."""

from __future__ import annotations

import hashlib
import json
import re

from . import config as cfg
from .repos_configuration_sources import ConfigurationError
from .tracking import _atomic_write


def reference(target: dict, url: str) -> str:
    return hashlib.sha256(json.dumps([target, url], sort_keys=True).encode()).hexdigest()


def path(project: str, receipt: str):
    if not re.fullmatch(r"[a-f0-9]{64}", receipt):
        raise ConfigurationError("invalid-creation-receipt", "Select an exact native creation receipt.")
    return cfg.project_dir(project) / "knowledge-creation" / (receipt + ".json")


def read(project: str, receipt: str) -> dict:
    source = path(project, receipt)
    try:
        if source.stat().st_size > 32768:
            raise ValueError
        value = json.loads(source.read_text(encoding="utf-8"))
        fields = {
            "version", "status", "operation", "target", "receipt", "url", "stage", "creation",
            "repository", "safeguards", "protection_attempts", "protection_attempt",
            "registration_changed", "binding_changed", "cleanup", "message", "failure",
            "receipt_persistence",
        }
        if (
            not isinstance(value, dict) or set(value) - fields
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("receipt") != receipt
            or value.get("creation") not in {"not-attempted", "unknown", "confirmed"}
            or value.get("status") not in {"incomplete", "created", "protected"}
            or value.get("operation") not in {"create", "protect-default"}
            or type(value.get("protection_attempts")) is not int or value["protection_attempts"] < 0
        ):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, RecursionError):
        raise ConfigurationError("creation-receipt-unavailable", "Inspect the native creation receipt owner.") from None


def save(project: str, value: dict) -> None:
    """Persist sanitized stage evidence through the existing atomic writer."""
    _atomic_write(path(project, value["receipt"]), json.dumps({"version": 1, **value}, sort_keys=True))


def reserve(project: str, value: dict) -> None:
    """Reserve before POST while the caller holds the native registry lock."""
    destination = path(project, value["receipt"])
    if destination.exists():
        raise ConfigurationError(
            "creation-already-attempted", "Creation was already attempted; inspect its receipt read-only, not by replay.",
        )
    save(project, value)
