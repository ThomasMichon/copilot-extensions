"""Provider-owned Worktree Picker source registry.

Venue plugins publish project-scoped JSON descriptors under the shared
agent-worktrees runtime root. The Picker reads those descriptors without
importing provider packages; all live work still crosses the provider's
declared command/SSH boundary.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from . import source_identity

SOURCES_DIR_ENV = "AGENT_WORKTREES_SOURCES_DIR"
SCHEMA_VERSION = 1
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_READ_ONLY_CAPABILITIES = frozenset({"list", "messages", "sessions", "refresh"})
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderSource:
    """One validated provider-backed Picker source."""

    kind: str
    source_id: str
    label: str
    project: str
    provider: str
    target_id: str
    instance_id: str
    alias: str
    shell: str
    venue: dict
    capabilities: dict[str, bool]
    resolve_argv: tuple[str, ...]
    connect_argv: tuple[str, ...] = ()


def sources_dir(base: str | os.PathLike[str] | None = None) -> Path:
    """Return the provider-source registry directory."""
    if base is not None:
        return Path(base).expanduser()
    override = os.environ.get(SOURCES_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path("~/.agent-worktrees/sources").expanduser()


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _require_private_path(path: Path, label: str) -> None:
    if os.name != "posix" or not hasattr(os, "getuid"):
        return
    info = path.stat(follow_symlinks=False)
    if info.st_uid != os.getuid():
        raise ValueError(f"{label} must be owned by the current user")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"{label} must not be group/world-writable")


def _capabilities(value: object) -> dict[str, bool]:
    if not isinstance(value, dict):
        raise ValueError("capabilities must be a mapping")
    out: dict[str, bool] = {}
    for key, enabled in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("capability names must be non-empty strings")
        if not isinstance(enabled, bool):
            raise ValueError(f"capability {key!r} must be boolean")
        out[key] = enabled
    return out


def _parse_source(raw: object, provider: str) -> ProviderSource:
    if not isinstance(raw, dict):
        raise ValueError("source entry must be a mapping")
    kind = _required_string(raw.get("kind"), "kind")
    if kind != source_identity.PROVIDER_EXEC_KIND:
        raise ValueError(f"unsupported provider source kind {kind!r}")
    project = _required_string(raw.get("project"), "project")
    target_id = _required_string(raw.get("target_id"), "target_id")
    label = _required_string(raw.get("label"), "label")
    if len(label) > 80 or any(
        ord(character) < 32 or ord(character) == 127
        for character in label
    ):
        raise ValueError(
            "label must be at most 80 characters without control characters"
        )
    alias = _required_string(raw.get("alias"), "alias")
    if not _ALIAS_RE.fullmatch(alias):
        raise ValueError("alias must be an exact OpenSSH Host name")
    shell = raw.get("shell", "bash")
    if not isinstance(shell, str) or shell not in {"bash", "pwsh"}:
        raise ValueError("shell must be bash or pwsh")
    instance_id = _required_string(raw.get("instance_id"), "instance_id")
    venue = raw.get("venue")
    if not isinstance(venue, dict):
        raise ValueError("venue must be a mapping")
    venue_provider = _required_string(venue.get("provider"), "venue.provider")
    venue_target = _required_string(venue.get("target_id"), "venue.target_id")
    venue_instance = _required_string(venue.get("instance_id"), "venue.instance_id")
    if (
        venue_provider != provider
        or venue_target != target_id
        or venue_instance != instance_id
    ):
        raise ValueError("venue identity must match provider, target_id, and instance_id")
    if venue.get("transport") != source_identity.PROVIDER_EXEC_KIND:
        raise ValueError("venue transport must be provider-exec")
    if not isinstance(venue.get("ready"), bool):
        raise ValueError("venue.ready must be boolean")
    if not isinstance(venue.get("posture_verified"), bool):
        raise ValueError("venue.posture_verified must be boolean")
    assignment = venue.get("assignment")
    if (
        not isinstance(assignment, dict)
        or assignment.get("kind") != "lease"
        or not isinstance(assignment.get("effort"), str)
        or not assignment["effort"].strip()
        or not isinstance(assignment.get("acquired_at"), (int, float))
    ):
        raise ValueError("venue.assignment must identify the active provider lease")
    resolve_argv = raw.get("resolve")
    if (
        not isinstance(resolve_argv, list)
        or not resolve_argv
        or any(not isinstance(part, str) or not part for part in resolve_argv)
    ):
        raise ValueError("resolve must be a non-empty string argv")
    command = resolve_argv[0]
    if not (
        os.path.isabs(command)
        or re.match(r"^[A-Za-z]:[\\/]", command)
    ):
        raise ValueError("resolve command must use an absolute executable path")
    connect_argv = raw.get("connect")
    if (
        not isinstance(connect_argv, list)
        or not connect_argv
        or any(not isinstance(part, str) or not part for part in connect_argv)
    ):
        raise ValueError("connect must be a non-empty string argv")
    connect_command = connect_argv[0]
    if not (
        os.path.isabs(connect_command)
        or re.match(r"^[A-Za-z]:[\\/]", connect_command)
    ):
        raise ValueError("connect command must use an absolute executable path")
    capabilities = _capabilities(raw.get("capabilities"))
    if capabilities.get("list") is not True:
        raise ValueError("provider source must advertise list capability")
    unsupported = sorted(
        key
        for key, enabled in capabilities.items()
        if enabled and key not in _READ_ONLY_CAPABILITIES
    )
    if unsupported:
        raise ValueError(
            "provider source enables unsupported capabilities: "
            + ", ".join(unsupported)
        )
    return ProviderSource(
        kind=kind,
        source_id=source_identity.provider_exec_id(provider, target_id),
        label=label,
        project=project,
        provider=provider,
        target_id=target_id,
        instance_id=instance_id,
        alias=alias,
        shell=shell,
        venue=dict(venue),
        capabilities=capabilities,
        resolve_argv=tuple(resolve_argv),
        connect_argv=tuple(connect_argv),
    )


def _load_file(path: Path) -> list[ProviderSource]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("registry entry must be a regular file")
    _require_private_path(path, "registry entry")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("registry document must be a mapping")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {payload.get('schema_version')!r}"
        )
    provider = _required_string(payload.get("provider"), "provider")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("sources must be a list")
    parsed: list[ProviderSource] = []
    for index, raw in enumerate(raw_sources):
        try:
            parsed.append(_parse_source(raw, provider))
        except ValueError as exc:
            _LOG.warning(
                "ignoring invalid Picker source %s[%d]: %s",
                path,
                index,
                exc,
            )
    return parsed


def load(
    project: str,
    base: str | os.PathLike[str] | None = None,
) -> list[ProviderSource]:
    """Load unique provider sources registered for *project*.

    Malformed entries are isolated and logged. Every descriptor sharing an
    ambiguous canonical source ID is rejected.
    """
    directory = sources_dir(base)
    if directory.exists():
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("source registry must be a regular directory")
            _require_private_path(directory, "source registry")
        except (OSError, ValueError) as exc:
            _LOG.warning("ignoring unsafe Picker source registry %s: %s", directory, exc)
            return []
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError as exc:
        _LOG.warning("could not scan Picker source registry %s: %s", directory, exc)
        return []
    wanted = project.strip().casefold()
    out: list[ProviderSource | None] = []
    indexes: dict[str, int] = {}
    ambiguous: set[str] = set()
    for entry in entries:
        try:
            parsed = _load_file(entry)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            _LOG.warning("ignoring invalid Picker source registry %s: %s", entry, exc)
            continue
        for source in parsed:
            if source.project.casefold() != wanted:
                continue
            if source.source_id in ambiguous:
                continue
            if source.source_id in indexes:
                _LOG.warning(
                    "rejecting ambiguous duplicate Picker source id %s from %s",
                    source.source_id,
                    entry,
                )
                out[indexes.pop(source.source_id)] = None
                ambiguous.add(source.source_id)
                continue
            indexes[source.source_id] = len(out)
            out.append(source)
    return [source for source in out if source is not None]
