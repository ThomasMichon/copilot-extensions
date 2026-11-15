"""Consume serving-host naming without deriving identity on the coordinator."""

from __future__ import annotations


def reported_machine(envelope: object) -> str | None:
    """Return no override for legacy absence, or an empty name for withdrawal."""
    if not isinstance(envelope, dict) or "reporting_context" not in envelope:
        return None
    value = envelope["reporting_context"]
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("unsupported reporting context")
    if value.get("status") == "unavailable" and set(value) == {"version", "status"}:
        return ""
    if value.get("status") != "resolved" or set(value) != {
        "version", "status", "hostname", "machine", "environment", "execution_machine",
    }:
        raise ValueError("invalid reporting context")
    if not isinstance(value["environment"], str) or value["environment"] not in {
        "windows", "wsl", "linux",
    }:
        raise ValueError("invalid reporting environment")
    for key in ("hostname", "machine", "execution_machine"):
        text = value[key]
        if (
            not isinstance(text, str) or not text.strip()
            or len(text.encode("utf-8")) > 256 or any(char in text for char in "\r\n\0")
        ):
            raise ValueError("invalid reporting name")
    return value["execution_machine"]
