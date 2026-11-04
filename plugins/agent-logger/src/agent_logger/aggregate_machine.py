"""Shared current-machine qualification for aggregate selection and effects."""

from __future__ import annotations

import platform

from agent_logger.aggregate import MachineIdentity
from agent_logger.config import Config
from agent_logger.segmenter.platform import detect_machine


def default_machine_identity() -> MachineIdentity:
    name = platform.system().lower()
    platform_name = "macos" if name == "darwin" else "windows" if name == "windows" else "linux"
    return MachineIdentity(name=detect_machine(), platform=platform_name)


def _machine_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"machine.{field_name} must be null or a non-empty string")
    return value.strip()


def resolve_machine_identity(
    cfg: Config, *, fallback: MachineIdentity | None = None,
) -> MachineIdentity:
    raw = cfg.get("machine", {})
    if not isinstance(raw, dict):
        raise ValueError("machine configuration must be a mapping")
    fallback = fallback or default_machine_identity()
    return MachineIdentity(
        name=_machine_text(raw.get("name"), "name") or fallback.name,
        platform=fallback.platform,
        role=_machine_text(raw.get("role"), "role"),
    )
