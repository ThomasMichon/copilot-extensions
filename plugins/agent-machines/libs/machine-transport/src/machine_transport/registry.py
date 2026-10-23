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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

import yaml

from .machine_environment import MachineEnvironment, legacy_alias_forms, normalize_environment, split_machine_environment

_SafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

__all__ = [
    "AmbiguousMachineError",
    "PhysicalMachineError",
    "MachineSelection",
    "resolve_machine_selection",
    "entry_physical_host",
    "find_machine_metadata",
    "MachineIdentity",
    "MachineEntry",
    "SSHEnvironment",
    "find_machine_entry",
    "machine_name",
    "normalize_machine_entries",
    "machine_identity_names",
    "merge_machines_yaml",
    "parse_machines_yaml",
    "parse_machines_yaml_file",
]


class AmbiguousMachineError(ValueError):
    """A configured identity matches more than one machine or SSH target."""


class PhysicalMachineError(ValueError):
    """Physical metadata needs an execution context to select a logical machine."""


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
    execution_platform: str = ""
    physical_host: str = ""
    compatibility_aliases: tuple[str, ...] = ()
    legacy_key: str = ""


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
        raw: Any = yaml.load(f, Loader=_SafeLoader)

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
        execution_platform = data.get("execution_platform", "")
        physical_host = data.get("physical_host", "")
        if execution_platform not in ("", "windows", "wsl", "linux"):
            raise ValueError(f"execution space '{key}' has an invalid execution_platform")
        if execution_platform and (
            not key or any(char.isspace() for char in key)
            or any(separator in key for separator in ("/", "\\", "#"))
        ):
            raise ValueError("execution-space keys must be nonempty canonical names, not paths or references")
        if not isinstance(physical_host, str):
            raise ValueError(f"execution space '{key}' physical_host must be a string")
        if execution_platform and any(env.name != execution_platform for env in ssh_envs):
            raise ValueError(f"execution space '{key}' cannot contain another space's SSH environment")
        compatibility_aliases = data.get("compatibility_aliases", []) or []
        if not isinstance(compatibility_aliases, (list, tuple)) or any(
            not isinstance(value, str) or not value.strip() for value in compatibility_aliases
        ):
            raise ValueError(f"machine '{key}' compatibility_aliases must contain nonempty strings")
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
            execution_platform=execution_platform,
            physical_host=physical_host,
            compatibility_aliases=tuple(value.strip() for value in compatibility_aliases),
        )
    _validate_execution_space_keys(entries)
    return entries


def machine_identity_names(
    entry: MachineIdentity, *, entries: Mapping[str, MachineIdentity] | None = None,
    include_metadata: bool = True,
) -> tuple[str, ...]:
    """Selection names; metadata exclusion leaves only keys, aliases and profiles."""
    values = (entry.key,)
    if include_metadata and not getattr(entry, "execution_platform", ""):
        hostname = entry.hostname
        if hostname and split_machine_environment(entry.key).environment == "wsl":
            hostname = split_machine_environment(hostname, "wsl").logical_machine
        values += (hostname,)
    values += (entry.alias,)
    values += (
        *getattr(entry, "compatibility_aliases", ()),
        *(env.alias for env in getattr(entry, "ssh_environments", ())),
    )
    display = entry.display_name
    if include_metadata and entries is not None and display and not getattr(entry, "execution_platform", ""):
        others = [other for other in entries.values() if other is not entry]
        if not any(
            other.display_name.casefold() == display.casefold()
            or display.casefold() in {name.casefold() for name in machine_identity_names(other)}
            for other in others
        ):
            values += (display,)
    if include_metadata and entries is not None:
        platform = getattr(entry, "execution_platform", "") or split_machine_environment(entry.key).environment
        if platform in ("windows", "wsl"):
            bases = (entry_physical_host(entry), entry.hostname, entry.display_name,
                     *(other.alias for other in entries.values()
                       if entry_physical_host(other).casefold() == entry_physical_host(entry).casefold()))
            for base in bases:
                if not base:
                    continue
                for candidate in legacy_alias_forms(base, platform):
                    try:
                        selected = resolve_machine_selection(entries, candidate)
                    except PhysicalMachineError:
                        try:
                            selected = resolve_machine_selection(entries, candidate, platform)
                        except ValueError:
                            continue
                    except ValueError:
                        continue
                    if selected.entry is not None and selected.entry.key == entry.key:
                        values += (candidate,)
    return tuple(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def normalize_machine_entries(
    entries: Mapping[str, MachineEntry],
) -> dict[str, MachineEntry]:
    """Project legacy Windows/WSL hosts into independently addressable machines.

    This is a read-only projection, not a persisted identity migration. Explicit
    execution-space keys and SSH aliases are never rewritten. A legacy WSL
    declaration supplies the Windows association; other mixed platforms, duplicate
    profiles, and generated-key/alias collisions require explicit topology repair.
    """
    result: dict[str, MachineEntry] = {}

    def add(entry: MachineEntry) -> None:
        if entry.execution_platform and (
            not entry.key or any(char.isspace() for char in entry.key)
            or any(separator in entry.key for separator in ("/", "\\", "#"))
        ):
            raise ValueError("execution-space keys must be nonempty canonical names, not paths or references")
        if any(key.casefold() == entry.key.casefold() for key in result):
            raise AmbiguousMachineError(f"logical machine key {entry.key!r} is ambiguous")
        result[entry.key] = entry

    for entry in entries.values():
        platforms = [env.name for env in entry.ssh_environments]
        for platform_name in ("windows", "wsl", "linux"):
            if platforms.count(platform_name) > 1:
                raise AmbiguousMachineError(
                    f"machine {entry.key!r} has ambiguous {platform_name} SSH profiles"
                )
        if entry.execution_platform or "wsl" not in platforms:
            if entry.execution_platform and any(
                name != entry.execution_platform for name in platforms
            ):
                raise ValueError(f"machine {entry.key!r} has a mismatched SSH platform")
            add(entry)
            continue
        if any(name not in ("windows", "wsl") for name in platforms):
            raise AmbiguousMachineError(
                f"machine {entry.key!r} mixes WSL with an unknown native platform; "
                "declare independent execution_platform entries"
            )
        physical_host = entry.physical_host or entry.key
        add(replace(
            entry, execution_platform="windows", physical_host=physical_host,
            legacy_key=entry.key,
            ssh_environments=[env for env in entry.ssh_environments if env.name == "windows"],
        ))
        add(replace(
            entry, key=f"{entry.key}-wsl", alias="", compatibility_aliases=(),
            execution_platform="wsl", physical_host=physical_host,
            legacy_key=entry.key,
            environment="wsl",
            ssh_environments=[env for env in entry.ssh_environments if env.name == "wsl"],
        ))

    owners: dict[str, str] = {}
    for entry in result.values():
        for name in machine_identity_names(entry):
            prior = owners.setdefault(name.casefold(), entry.key)
            if prior != entry.key:
                raise AmbiguousMachineError(
                    f"topology identity {name!r} is ambiguous across machine entries: "
                    f"{', '.join(sorted((prior, entry.key)))}"
                )
    _validate_execution_space_keys(result)
    return result


def _validate_execution_space_keys(entries: Mapping[str, MachineEntry]) -> None:
    if any(entry.execution_platform for entry in entries.values()):
        normalized = [key.casefold() for key in entries]
        if len(normalized) != len(set(normalized)):
            raise ValueError("execution-space keys must be unique without regard to case")


def merge_machines_yaml(
    legacy: dict[str, MachineEntry] | None,
    canonical: dict[str, MachineEntry] | None,
) -> dict[str, MachineEntry]:
    """Additively merge a legacy repo-root registry with the canonical
    in-repo one. The canonical entry wins a key collision (the two files
    carry disjoint keys in practice); this is strictly additive (dotfiles
    #7914 -- returning only whichever file happened to resolve first
    silently dropped every machine in the other file facility-wide).
    Scoped keys must remain case-insensitively unique across both files."""
    entries: dict[str, MachineEntry] = {}
    if legacy:
        entries.update(legacy)
    if canonical:
        entries.update(canonical)
    _validate_execution_space_keys(entries)
    return entries


def machine_name(entry: MachineEntry) -> str:
    """The canonical name for a machine entry: its alias if one is defined
    (the colloquial multi-machine system name), otherwise its key (the real
    hostname)."""
    return entry.key if entry.execution_platform else entry.alias or entry.key


def _find_machine_entry(
    entries: Mapping[str, _Machine], name: str, *, reject_ambiguous: bool = False,
    execution_platform: str | None = None,
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
    if execution_platform is not None:
        if execution_platform not in ("windows", "wsl", "linux"):
            raise ValueError("invalid execution_platform")
        # Only an owner with known execution context may use physical association.
        # First resolve explicit identities; platform mismatch selects a sibling,
        # never falls through to an unrelated physical hostname.
        selected = (
            _find_machine_entry(entries, name, reject_ambiguous=reject_ambiguous)
            if not any(getattr(entry, "execution_platform", "") for entry in entries.values()) or any(
                name.casefold() in {value.casefold() for value in machine_identity_names(entry)}
                for entry in entries.values()
            ) else None
        )
        if selected and getattr(selected, "execution_platform", "") == execution_platform:
            return selected
        if (
            selected is not None and execution_platform == "wsl"
            and not any(getattr(entry, "execution_platform", "") for entry in entries.values())
            and split_machine_environment(selected.key).environment != "wsl"
        ):
            guest = _find_machine_entry(
                entries, split_machine_environment(selected.key, "wsl").logical_machine,
                reject_ambiguous=reject_ambiguous,
            )
            if guest is not None and split_machine_environment(guest.key).environment == "wsl":
                return guest
        def physical_host(entry: MachineIdentity) -> str:
            return entry_physical_host(entry)

        physical = physical_host(selected) if selected else ""
        physical_matches = {
            physical_host(entry).casefold()
            for entry in entries.values()
            if name.casefold() in {
                entry.hostname.casefold(), getattr(entry, "physical_host", "").casefold(),
                entry.display_name.casefold(), physical_host(entry).casefold(),
            }
        } if selected is None else set()
        candidates = [
            entry for entry in entries.values()
            if getattr(entry, "execution_platform", "") == execution_platform
            and (
                entry is selected
                or (physical and physical_host(entry).casefold() == physical.casefold())
                or (
                    selected is None
                    and physical_host(entry).casefold() in physical_matches
                )
            )
        ]
        if len(candidates) > 1:
            raise AmbiguousMachineError(
                f"Machine {name!r} has ambiguous {execution_platform} entries"
            )
        if candidates:
            return candidates[0]
        legacy_candidates = [
            entry for entry in entries.values()
            if not getattr(entry, "execution_platform", "")
            and split_machine_environment(entry.key).environment == execution_platform
            and (
                physical_host(entry).casefold() == name.casefold()
                or (physical and physical_host(entry).casefold() == physical.casefold())
            )
        ]
        if selected is None and len(legacy_candidates) > 1:
            raise AmbiguousMachineError(f"Machine {name!r} has ambiguous legacy {execution_platform} entries")
        if selected is None and legacy_candidates:
            return legacy_candidates[0]
        if (selected and getattr(selected, "execution_platform", "")) or any(
            getattr(entry, "execution_platform", "") and name.casefold() in {
                entry.hostname.casefold(), getattr(entry, "physical_host", "").casefold(),
                physical_host(entry).casefold(),
            }
            for entry in entries.values()
        ):
            raise ValueError(f"machine {name!r} has no {execution_platform} logical entry")
        return selected if selected and not getattr(selected, "execution_platform", "") else None
    if name in entries:
        return entries[name]
    name_lower = name.lower()
    # Strict matching's own precedence rule: a unique case-insensitive KEY
    # match wins outright before any other identity is even considered,
    # regardless of multi-platform mode -- only a genuine key collision
    # (two distinctly-cased keys colliding case-insensitively) is ambiguous
    # at this step.
    if reject_ambiguous:
        key_matches = [entry for key, entry in entries.items() if key.lower() == name_lower]
        if len(key_matches) > 1:
            raise AmbiguousMachineError(f"Machine '{name}' is ambiguous in topology")
        if key_matches:
            return key_matches[0]
    if not any(getattr(entry, "execution_platform", "") for entry in entries.values()):
        matches: list[_Machine] = []
        for key, entry in entries.items():
            if any(value.lower() == name_lower for value in machine_identity_names(entry)):
                if not reject_ambiguous:
                    return entry
                matches.append(entry)
        if len(matches) > 1:
            raise AmbiguousMachineError(f"Machine '{name}' is ambiguous in topology")
        if matches:
            return matches[0]
        displays = [
            entry for entry in entries.values()
            if entry.display_name and entry.display_name.casefold() == name.casefold()
        ]
        if len(displays) > 1:
            raise AmbiguousMachineError(
                f"display label {name!r} names multiple machines; use a machine key"
            )
        return displays[0] if displays else None
    keyed = [entry for key, entry in entries.items() if key.lower() == name_lower]
    if keyed:
        return keyed[0]
    aliases = [
        entry for entry in entries.values()
        if any(isinstance(value, str) and value and value.lower() == name_lower for value in (
            entry.alias, *getattr(entry, "compatibility_aliases", ()),
            *(env.alias for env in getattr(entry, "ssh_environments", ())),
        ))
    ]
    if len(aliases) > 1:
        raise AmbiguousMachineError(f"ambiguous execution-space alias {name!r}; select a registered key")
    if aliases:
        return aliases[0]
    matches = [
        entry for entry in entries.values()
        if not getattr(entry, "execution_platform", "")
        and (
            (entry.hostname and entry.hostname.lower() == name_lower)
            or (entry.display_name and entry.display_name.lower() == name_lower)
        )
    ]
    if len(matches) > 1:
        raise AmbiguousMachineError(f"ambiguous machine identity {name!r}; select a registered key")
    if matches:
        return matches[0]
    if any(
        getattr(entry, "execution_platform", "")
        and name_lower in {
            entry.hostname.lower(),
            getattr(entry, "physical_host", "").lower(),
            entry_physical_host(entry).lower(),
            entry.display_name.lower(),
        }
        for entry in entries.values()
    ):
        raise PhysicalMachineError(
            f"physical-host metadata {name!r} does not select an execution space; "
            "use its registered key"
        )
    return None


@dataclass(frozen=True)
class MachineSelection(Generic[_Machine]):
    entry: _Machine | None
    target: MachineEnvironment
    exact: bool = False


def entry_physical_host(entry: MachineIdentity) -> str:
    declared = getattr(entry, "physical_host", "")
    if declared:
        return declared
    parsed = split_machine_environment(entry.key)
    platform = getattr(entry, "execution_platform", "")
    return entry.key if platform and parsed.environment and platform != parsed.environment else parsed.machine


def resolve_machine_selection(
    entries: Mapping[str, _Machine], name: str, environment: str | None = None, *,
    allow_physical_context: bool = False, reject_ambiguous: bool = True,
) -> MachineSelection[_Machine]:
    """One exact-first map for labels, legacy formats and execution context."""
    raw = name.strip()
    wanted = normalize_environment(environment)
    try:
        direct = _find_machine_entry(entries, raw, reject_ambiguous=reject_ambiguous)
    except PhysicalMachineError:
        direct = None
    exact = direct is not None
    if direct is not None:
        declared = getattr(direct, "execution_platform", "")
        implied = declared or split_machine_environment(direct.key).environment
        if wanted and implied and implied != wanted:
            profile_only = raw.casefold() != direct.key.casefold() and any(
                raw.casefold() == env.alias.casefold()
                for env in getattr(direct, "ssh_environments", ())
            )
            physical = raw.casefold() == entry_physical_host(direct).casefold()
            historical_host = (
                getattr(direct, "legacy_key", "") == direct.key
                and declared == "windows" and wanted == "wsl"
            )
            if profile_only or not (
                historical_host or (allow_physical_context and physical)
            ):
                raise ValueError(f"registered machine {raw!r} conflicts with the requested environment")
        target = MachineEnvironment(entry_physical_host(direct), wanted or implied)
        lookup = raw
    else:
        hostname = any(raw.casefold() == entry.hostname.casefold() for entry in entries.values())
        target = (
            MachineEnvironment(raw, wanted) if hostname and wanted
            else split_machine_environment(raw, environment)
        )
        lookup = target.machine
    if target.environment:
        entry = _find_machine_entry(
            entries, lookup, reject_ambiguous=reject_ambiguous,
            execution_platform=target.environment,
        )
        if entry is not None:
            if (
                target.environment == "wsl" and not getattr(entry, "execution_platform", "")
                and split_machine_environment(entry.key).environment != "wsl"
                and not any(env.name == "wsl" for env in getattr(entry, "ssh_environments", ()))
            ):
                physical = entry_physical_host(entry)
                guest_key = MachineEnvironment(physical, "wsl").logical_machine
                collision = _find_machine_entry(entries, guest_key, reject_ambiguous=True)
                if collision is not None and split_machine_environment(collision.key).environment != "wsl":
                    raise ValueError(
                        f"guest identity {guest_key!r} collides with native machine {collision.key!r}"
                    )
                return MachineSelection(None, MachineEnvironment(physical, "wsl"), exact)
            return MachineSelection(
                entry, MachineEnvironment(entry_physical_host(entry), target.environment), exact,
            )
    elif direct is not None:
        return MachineSelection(direct, target, True)
    elif entries:
        # Preserve the registry's refusal of unqualified physical metadata.
        entry = _find_machine_entry(entries, lookup, reject_ambiguous=reject_ambiguous)
        if entry is not None:
            return MachineSelection(entry, MachineEnvironment(entry_physical_host(entry)), False)
    if target.environment == "wsl":
        collision = _find_machine_entry(entries, target.logical_machine, reject_ambiguous=True)
        if (
            collision is not None and getattr(collision, "execution_platform", "") != "wsl"
            and split_machine_environment(collision.key).environment != "wsl"
        ):
            raise ValueError(
                f"guest identity {target.logical_machine!r} collides with native machine {collision.key!r}"
            )
    return MachineSelection(None, target, exact)


def find_machine_entry(
    entries: Mapping[str, _Machine], name: str, *, reject_ambiguous: bool = False,
    execution_platform: str | None = None,
) -> _Machine | None:
    if not name:
        return None
    if execution_platform is not None:
        entry = _find_machine_entry(
            entries, name, reject_ambiguous=reject_ambiguous, execution_platform=execution_platform,
        )
        if entry is not None:
            return entry
    return resolve_machine_selection(
        entries, name, execution_platform, allow_physical_context=True,
        reject_ambiguous=reject_ambiguous,
    ).entry


def find_machine_metadata(entries: Mapping[str, _Machine], name: str) -> _Machine | None:
    """Borrow family metadata for a missing guest, never execution authority."""
    selected = resolve_machine_selection(entries, name)
    return selected.entry or (
        _find_machine_entry(entries, selected.target.machine, reject_ambiguous=True)
        if selected.target.environment else None
    )
