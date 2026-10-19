"""Declared harness/knowledge roles and the legacy machine-agent target."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml


class ControlPlaneError(ValueError):
    """A declared control-plane relationship is invalid or unavailable."""


@dataclass(frozen=True)
class ControlPlaneRepositories:
    harness: str
    knowledge: str | None = None
    legacy_project: str | None = None

    @property
    def paired(self) -> bool:
        return self.knowledge is not None

    @property
    def legacy_compatible(self) -> bool:
        return self.legacy_project == self.harness

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "harness": self.harness,
            "knowledge": self.knowledge,
            "legacy_project": self.legacy_project,
            "paired": self.paired,
            "legacy_compatible": self.legacy_compatible,
        }


def _repository(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", value
    ):
        raise ControlPlaneError(
            f"control_plane.{field} must be a non-empty registered repository name"
        )
    return value


def parse_control_plane(
    data: Mapping[str, object],
) -> ControlPlaneRepositories | None:
    """Read declared roles without discovering, binding or launching repositories."""
    value = data.get("control_plane")
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        project = _repository(value.strip(), "project")
        return ControlPlaneRepositories(project, legacy_project=project)
    if not isinstance(value, Mapping):
        raise ControlPlaneError("control_plane must be a repository name or mapping")
    unknown = set(value) - {"project", "harness", "knowledge"}
    if unknown:
        raise ControlPlaneError("control_plane contains unsupported fields")
    if "harness" not in value and "knowledge" not in value:
        project = value.get("project")
        if project is None:
            return None
        name = _repository(project, "project")
        return ControlPlaneRepositories(name, legacy_project=name)

    harness = _repository(value.get("harness"), "harness")
    knowledge = _repository(value.get("knowledge"), "knowledge")
    if harness.casefold() == knowledge.casefold():
        raise ControlPlaneError(
            "a paired control plane requires distinct harness and knowledge repositories"
        )
    project = (
        _repository(value["project"], "project") if "project" in value else None
    )
    if project is not None and project != harness:
        raise ControlPlaneError("control_plane.project must equal control_plane.harness")
    return ControlPlaneRepositories(harness, knowledge, project)


def load_control_plane(path: str | Path) -> ControlPlaneRepositories | None:
    """Read one explicitly selected topology; absent declarations remain absent."""
    source = Path(path).expanduser()
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ControlPlaneError(f"cannot read control-plane topology {source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ControlPlaneError(f"invalid control-plane topology YAML: {source}") from exc
    if not isinstance(data, Mapping):
        raise ControlPlaneError("control-plane topology must contain a YAML mapping")
    return parse_control_plane(data)
