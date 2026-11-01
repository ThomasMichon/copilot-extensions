"""Existing pointer-registry configuration and explicit supervisor cohort intent."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Literal

from dropin_registry import ScanAuthority

from .registrar import RegistrarError
from .registrar_pointer import Pointer

log = logging.getLogger(__name__)


class RegistrarIndeterminateError(RegistrarError):
    """Trusted registrar state could not be read authoritatively."""


@dataclass(frozen=True)
class SupervisorScopeIntent:
    """Configured identity, not evidence that this execution environment exists."""

    machine: str
    execution_machine: str
    env: str

    @classmethod
    def from_dict(cls, value: object) -> SupervisorScopeIntent:
        if not isinstance(value, dict) or set(value) != {"machine", "execution_machine", "env"}:
            raise RegistrarError("supervisor scope requires machine, execution_machine and env")
        for field in ("machine", "execution_machine", "env"):
            item = value[field]
            if not isinstance(item, str) or not item.strip() or item != item.strip():
                raise RegistrarError(f"supervisor scope {field} must be explicit non-empty text")
        return cls(value["machine"], value["execution_machine"], value["env"])


@dataclass(frozen=True)
class ConfiguredSupervisorCohort:
    """One whole configuration read; inspection never certifies runtime admission."""

    state: Literal["complete", "unconfirmed", "unavailable"]
    source_revision: str | None
    revision: int | None
    members: tuple[SupervisorScopeIntent, ...]
    reason: str = ""

    @property
    def complete(self) -> bool:
        return self.state == "complete"


def _cohort(raw: object, source_revision: str) -> ConfiguredSupervisorCohort:
    if not isinstance(raw, dict) or "configured_supervisors" not in raw:
        return ConfiguredSupervisorCohort(
            "unconfirmed", source_revision, None, (), "configured-supervisors-not-declared",
        )
    value = raw["configured_supervisors"]
    try:
        if not isinstance(value, dict) or set(value) != {"revision", "members"}:
            raise RegistrarError("configured supervisors requires revision and members")
        revision = value["revision"]
        if type(revision) is not int or revision < 0:
            raise RegistrarError("configured supervisors revision must be a nonnegative integer")
        if not isinstance(value["members"], list):
            raise RegistrarError("configured supervisors members must be an explicit list")
        members = tuple(SupervisorScopeIntent.from_dict(item) for item in value["members"])
        positions = {(member.execution_machine, member.env) for member in members}
        if len(positions) != len(members):
            raise RegistrarError("configured supervisors has duplicate execution scopes")
    except RegistrarError as exc:
        log.warning("configured supervisor cohort unavailable: %s", exc)
        return ConfiguredSupervisorCohort(
            "unavailable", source_revision, None, (), "configured-supervisors-invalid",
        )
    return ConfiguredSupervisorCohort(
        "complete", source_revision, revision,
        tuple(sorted(members, key=lambda member: (member.execution_machine, member.env))),
    )


@dataclass(frozen=True)
class PointerRegistryRead:
    """Pointer selection and cohort intent from the same read, not retained cache."""

    authority: ScanAuthority
    pointers: tuple[Pointer, ...]
    cohort: ConfiguredSupervisorCohort
    envelope: dict | None = None


def read_pointer_registry(path: Path) -> PointerRegistryRead:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return PointerRegistryRead(
            ScanAuthority.ABSENT, (),
            ConfiguredSupervisorCohort(
                "unconfirmed", None, None, (), "pointer-registry-absent",
            ),
        )
    except OSError as exc:
        raise RegistrarIndeterminateError(
            f"{path}: pointer registry could not be read: {exc}"
        ) from exc
    try:
        raw = json.loads(content.decode("utf-8"))
    except UnicodeError as exc:
        raise RegistrarError(f"{path}: invalid pointer registry encoding: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RegistrarError(f"{path}: invalid pointer registry JSON: {exc}") from exc
    envelope = None
    pointers = raw
    if isinstance(raw, dict):
        if type(raw.get("version")) is not int or raw["version"] != 1:
            raise RegistrarError(f"{path}: unsupported pointer registry configuration version")
        pointers = raw.get("pointers")
        envelope = raw
    if not isinstance(pointers, list):
        raise RegistrarError(
            f"{path}: pointer registry must contain a JSON list, got {type(pointers).__name__}"
        )
    selected = []
    for index, item in enumerate(pointers):
        try:
            selected.append(Pointer.from_dict(item))
        except RegistrarError as exc:
            raise RegistrarError(f"{path}[{index}]: {exc}") from exc
    source_revision = hashlib.sha256(content).hexdigest()
    return PointerRegistryRead(
        ScanAuthority.COMPLETE, tuple(selected), _cohort(raw, source_revision), envelope,
    )


def observe_configured_supervisors(path: Path) -> ConfiguredSupervisorCohort:
    """Fresh explicit read by the registrar owner; no discovery or execution."""
    try:
        return read_pointer_registry(path).cohort
    except RegistrarError as exc:
        log.warning(
            "configured supervisor cohort observation unavailable: error_type=%s",
            type(exc).__name__,
        )
        return ConfiguredSupervisorCohort(
            "unavailable", None, None, (), "pointer-registry-unavailable",
        )


def write_pointer_registry(pointers: Iterable[Pointer], path: Path) -> Path:
    """Replace pointers under the existing owner lock, preserving cohort metadata."""
    selected = tuple(pointers)
    encoded = [pointer.to_dict() for pointer in selected]
    current = read_pointer_registry(path)
    if current.authority is ScanAuthority.COMPLETE and selected == current.pointers:
        return path
    payload = encoded if current.envelope is None else {
        **current.envelope, "pointers": encoded,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pointers-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path
