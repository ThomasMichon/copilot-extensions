"""Shared ``machines.yaml`` registry model, parsing, and matching.

Before this module existed, at least three plugins/consumers
(``agent-worktrees``, ``worktree-manager``, ``agent-bridge``) each defined
their own ``MachineEntry``/``SSHEnvironment`` dataclasses and their own
``machines.yaml`` parser -- subtly different in what they tolerated (see
:func:`parse_machines_yaml_file`'s ``require_alias`` parameter for the one
real behavioral difference this module preserves rather than silently
unifying away) and all independently prone to the matching bugs
:func:`find_machine_entry` and ``machine_transport.identity.is_local_machine``
exist to fix once, here.

This module owns only **parsing a file you already resolved the path to**
and **merging two already-resolved files**. Resolving *which* path(s) a
given repo's machines.yaml lives at (canonical in-repo location, legacy
repo-root fallback, a bound knowledge-repo overlay, etc.) is deliberately
left to each consumer: that resolution differs enough between consumers
(agent-worktrees' knowledge-overlay redirect has no equivalent in
worktree-manager) that forcing one shared resolution policy would be a
behavior change, not a pure de-duplication.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

import yaml

__all__ = [
    "AmbiguousMachineError",
    "MachineIdentity",
    "MachineEntry",
    "SSHEnvironment",
    "find_machine_entry",
    "machine_name",
    "merge_machines_yaml",
    "parse_machines_yaml",
    "parse_machines_yaml_file",
]


class AmbiguousMachineError(ValueError):
    """A configured identity matches more than one machine or SSH target."""


class MachineIdentity(Protocol):
    """Read-only identity fields shared by consumer-specific machine records."""

    @property
    def key(self) -> str: ...

    @property
    def alias(self) -> str: ...

    @property
    def hostname(self) -> str: ...

    @property
    def display_name(self) -> str: ...


_Machine = TypeVar("_Machine", bound=MachineIdentity)


@dataclass(frozen=True)
class SSHEnvironment:
    """An SSH environment for a machine (windows, wsl, linux)."""

    name: str
    alias: str
    shell: str = ""
    port: int = 22
    user: str | None = None


@dataclass(frozen=True)
class MachineEntry:
    """A registered machine from ``machines.yaml``."""

    key: str
    display_name: str
    environment: str = ""
    alias: str = ""
    # Raw OS hostname (COMPUTERNAME) when it differs from ``key``. Lets a
    # machine be keyed by a stable friendly name (e.g. ``host-box1``) while
    # the box reports a different, unrenameable COMPUTERNAME (e.g.
    # ``cpc-tmich-oixui``). Empty means ``key`` is the hostname (the common
    # case).
    hostname: str = ""
    role: str = ""
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    ssh_environments: list[SSHEnvironment] = field(default_factory=list)
    ssh_ready: bool = False
    copilot: bool = True


def parse_machines_yaml_file(
    path: Path, *, require_alias: bool = False,
) -> dict[str, MachineEntry]:
    """Parse one ``machines.yaml`` file's ``machines:`` block into entries.

    ``require_alias`` controls what happens to an ``ssh.environments[]``
    entry that names an environment (``name:``) but carries no ``alias:``
    -- a host documented/declared but not (yet) SSH-reachable for that
    environment:

    - ``False`` (the default, matching ``worktree-manager``'s historical
      behavior): the environment entry is KEPT with ``alias=""``, so a UI
      consumer can render it as a disabled/unreachable tab rather than
      silently omitting it.
    - ``True`` (matching ``agent-worktrees``' historical behavior): the
      environment entry is DROPPED entirely -- a CLI dispatch consumer that
      always treats "in ``ssh_environments``" as "has a usable alias" must
      request this to avoid ever resolving an empty SSH target.

      Picking the wrong one for a given consumer is a real behavior change,
      not a style choice -- preserve whichever this call site already
      required before switching to this shared parser.
    """
    with open(path, encoding="utf-8") as f:
        raw: Any = yaml.safe_load(f)

    # Validate the document root itself is a mapping BEFORE calling
    # ``.get()`` on it -- a non-empty YAML sequence or scalar root (e.g. a
    # bare ``- a`` list, or a plain string) has no ``.get`` method and would
    # otherwise raise ``AttributeError`` instead of this function's own
    # documented ``ValueError`` for an unusable file.
    if not isinstance(raw, dict) or not isinstance(raw.get("machines"), dict):
        raise ValueError(f"machines.yaml at {path} is missing 'machines' key")

    return parse_machines_yaml(raw, require_alias=require_alias)


def parse_machines_yaml(
    raw: dict[str, Any], *, require_alias: bool = False,
    default_ssh_alias_to_key: bool = False, default_ssh_shell: str = "",
    keep_unnamed_environments: bool = False, preserve_environment_values: bool = False,
) -> dict[str, MachineEntry]:
    """Parse resolved registry data, with explicit legacy consumer defaults.

    File consumers retain their existing defaults. Bridge opts into key aliases,
    bash shells and unnamed environments; explicit empty values stay explicit.
    Missing-value policies do not change normalization. Consumers preserving
    historical raw environment values must explicitly opt in.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("machines", {}), dict):
        raise ValueError("machines.yaml 'machines' must be a mapping")
    entries: dict[str, MachineEntry] = {}
    for key, data in raw.get("machines", {}).items():
        if not isinstance(data, dict):
            continue
        description_raw = data.get("description", "")
        if description_raw is None:
            description_raw = ""
        if not isinstance(description_raw, str):
            raise ValueError(f"machine '{key}' description must be a string")
        capabilities_raw = data.get("capabilities", [])
        if capabilities_raw is None:
            capabilities_raw = []
        if not isinstance(capabilities_raw, list):
            raise ValueError(f"machine '{key}' capabilities must be a list")
        capabilities: list[str] = []
        for capability_raw in capabilities_raw:
            if not isinstance(capability_raw, str):
                raise ValueError(
                    f"machine '{key}' capabilities must contain only strings"
                )
            capability = capability_raw.strip()
            if capability and capability not in capabilities:
                capabilities.append(capability)
        ssh_envs: list[SSHEnvironment] = []
        ssh_block = data.get("ssh", {})
        if ssh_block is None:
            ssh_block = {}
        if not isinstance(ssh_block, dict):
            raise ValueError(f"machine '{key}' ssh must be a mapping")
        for env in ssh_block.get("environments", []) or []:
            if not isinstance(env, dict):
                continue
            if preserve_environment_values or require_alias:
                name = env.get("name", "")
                if not name and not keep_unnamed_environments and not require_alias:
                    continue
                if require_alias and ("name" not in env or "alias" not in env):
                    continue
                ssh_envs.append(SSHEnvironment(
                    name=name,
                    alias=env.get("alias", str(key) if default_ssh_alias_to_key else ""),
                    shell=env.get("shell", default_ssh_shell),
                    port=env.get("port", 22),
                    user=env.get("user"),
                ))
            else:
                name = str(env.get("name") or "").strip()
                if not name and not keep_unnamed_environments:
                    continue
                ssh_envs.append(SSHEnvironment(
                    name=name,
                    alias=str(env.get("alias", str(key) if default_ssh_alias_to_key else "") or "").strip(),
                    shell=str(env.get("shell", default_ssh_shell) or "").strip(),
                    port=env.get("port", 22), user=env.get("user"),
                ))
        # Coerce the entry's own identity fields to ``str`` unconditionally
        # (not just in the permissive branch above): a YAML key/value that
        # parses as a non-string (e.g. a bare ``123:`` machine key, or
        # ``alias: 123``) previously crashed downstream string-only matching
        # (``key.lower()``, alias comparisons) in both historical
        # implementations -- worktree-manager's own parser already coerced
        # defensively; this generalizes that same safety to every consumer.
        key = str(key)
        entries[key] = MachineEntry(
            key=key,
            display_name=str(data.get("display_name") or key),
            environment=str(data.get("environment") or ""),
            alias=str(data.get("alias") or ""),
            hostname=str(data.get("hostname") or ""),
            role=data.get("role", ""),
            description=description_raw.strip(),
            capabilities=capabilities,
            ssh_environments=ssh_envs,
            ssh_ready=bool(ssh_block.get("ready", False)),
            copilot=bool(data.get("copilot", True)),
        )
    return entries


def merge_machines_yaml(
    legacy: dict[str, MachineEntry] | None,
    canonical: dict[str, MachineEntry] | None,
) -> dict[str, MachineEntry]:
    """Additively merge a legacy repo-root registry with the canonical
    in-repo one. The canonical entry wins a key collision (the two files
    carry disjoint keys in practice); this is strictly additive (dotfiles
    #7914 -- returning only whichever file happened to resolve first
    silently dropped every machine in the other file facility-wide)."""
    entries: dict[str, MachineEntry] = {}
    if legacy:
        entries.update(legacy)
    if canonical:
        entries.update(canonical)
    return entries


def machine_name(entry: MachineEntry) -> str:
    """The canonical name for a machine entry: its alias if one is defined
    (the colloquial multi-machine system name), otherwise its key (the real
    hostname)."""
    return entry.alias or entry.key


def find_machine_entry(
    entries: Mapping[str, _Machine], name: str, *, reject_ambiguous: bool = False,
) -> _Machine | None:
    """Look up a machine by key, alias, ``hostname`` field, or
    ``display_name`` (case-insensitive).

    Hostnames are case-insensitive, so match without regard to case (Windows
    reports COMPUTERNAME in mixed case but tooling often lowercases it).
    Matching the explicit ``hostname`` field lets a machine keyed by a
    friendly name still be found by its raw COMPUTERNAME. Returns ``None``
    if no entry matches. ``reject_ambiguous`` rejects multiple non-exact matches
    instead of returning the first; exact keys always retain precedence, and
    strict matching prefers a unique case-insensitive key over other identities.
    """
    if not name:
        return None
    if name in entries:
        return entries[name]
    name_lower = name.lower()
    if reject_ambiguous:
        key_matches = [entry for key, entry in entries.items() if key.lower() == name_lower]
        if len(key_matches) > 1:
            raise AmbiguousMachineError(f"Machine '{name}' is ambiguous in topology")
        if key_matches:
            return key_matches[0]
    matches: list[_Machine] = []
    for key, entry in entries.items():
        if any(value and value.lower() == name_lower for value in (
            key, entry.alias, entry.hostname, entry.display_name,
        )):
            if not reject_ambiguous:
                return entry
            matches.append(entry)
    if len(matches) > 1:
        raise AmbiguousMachineError(f"Machine '{name}' is ambiguous in topology")
    return matches[0] if matches else None
