"""Hygiene classifier for agent-ssh managed OpenSSH fragments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
    WarningBatch,
    WarningTracker,
    atomic_write_text,
)

from . import ssh_profile
from .file_lock import exclusive_file_lock

REGISTRY_NAME = "ssh-config.d"
DOCTOR_COMMAND = "agent-ssh doctor"
WARNING_STATE_ENV = "AGENT_SSH_WARNING_STATE"
MANAGED_FRAGMENT_RE = re.compile(
    r"^50-agent-ssh-(?P<transport>[a-z0-9][a-z0-9-]*)\.conf$",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"^# agent-ssh :: transport=(?P<transport>[a-z0-9][a-z0-9-]*)$"
)
_HOST_RE = re.compile(r"^Host[ \t]+(?P<alias>[^ \t]+)[ \t]*$", re.IGNORECASE)
_DIRECTIVE_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9]*)\b")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class FileIdentity:
    """Filesystem identity used to retain only an unchanged unreadable entry."""

    device: int
    inode: int
    size: int
    modified_ns: int

    @classmethod
    def from_stat(cls, info: os.stat_result) -> FileIdentity:
        return cls(
            device=int(info.st_dev),
            inode=int(info.st_ino),
            size=int(info.st_size),
            modified_ns=int(info.st_mtime_ns),
        )


@dataclass(frozen=True)
class FragmentMetadata:
    """Source identity stamped into schema-v1 managed fragments."""

    transport: str
    registry: str
    module: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "transport": self.transport,
            "registry": self.registry,
            "module": self.module,
        }


@dataclass(frozen=True)
class FragmentClaims:
    """Exact identities observed before an entry's final eligibility verdict."""

    transport: str | None = None
    aliases: tuple[str, ...] = ()
    complete: bool = False


class FragmentParseError(ValueError):
    """A malformed fragment plus exact identities parsed before the failure."""

    def __init__(
        self,
        message: str,
        *,
        transport: str | None = None,
        aliases: tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.transport = transport
        self.aliases = aliases


@dataclass(frozen=True)
class ManagedFragment:
    """One active or retained managed fragment."""

    path: str
    transport: str
    aliases: tuple[str, ...]
    document_digest: str
    file_identity: FileIdentity
    metadata: FragmentMetadata | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "entry": self.path,
            "transport": self.transport,
            "aliases": list(self.aliases),
        }
        if self.metadata is None:
            out["class"] = "legacy-managed"
        else:
            out["class"] = "managed"
            out["source"] = self.metadata.to_dict()
        return out


@dataclass(frozen=True)
class FragmentRegistryReport:
    """Current managed-fragment state and exhaustive findings."""

    snapshot: ScanSnapshot[ManagedFragment]
    entries: Mapping[str, ManagedFragment]
    findings: tuple[Finding, ...]
    blocked_aliases: frozenset[str]
    unscoped_blocking: bool = False

    @property
    def active_aliases(self) -> frozenset[str]:
        return frozenset(
            alias.casefold()
            for fragment in self.entries.values()
            for alias in fragment.aliases
        )

    def permits_probe(self, alias: str) -> bool:
        """Whether current or retained evidence permits an operational probe."""
        if self.unscoped_blocking:
            return False
        folded = alias.casefold()
        if folded in self.blocked_aliases:
            return False
        return True


def managed_config_dir() -> Path:
    """Return the default OpenSSH drop-in directory without creating it."""
    return Path.home() / ".ssh" / "config.d"


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _digest(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _decode_mapping(text: str, path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        value = json.loads(text)
    else:
        import yaml

        try:
            value = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: document root must be a mapping")
    return value


def _finding(
    path: Path | str,
    reason: str,
    *,
    status: str = "inactive",
    target: str | None = None,
    detail: str | None = None,
) -> Finding:
    entry = str(path)
    if status == "indeterminate":
        remedy = (
            f"Restore access and retry `{DOCTOR_COMMAND}`; only an unchanged "
            "last-known fragment may remain active."
        )
    elif reason == "legacy-unattributed":
        remedy = (
            f"Re-run `agent-ssh emit-profile` for {entry} to stamp current source "
            "identity; cleanup remains report-only."
        )
    else:
        remedy = (
            f"Run `{DOCTOR_COMMAND}`; re-run the fragment's `agent-ssh emit-profile` "
            f"command or remove {entry} after verifying it is obsolete."
        )
    return Finding(
        registry=REGISTRY_NAME,
        entry=entry,
        status=status,
        reason=reason,
        target=target,
        owner="agent-ssh",
        remedy=remedy,
        detail=detail,
    )


def _inactive(
    path: Path | str,
    reason: str,
    *,
    target: str | None = None,
    detail: str | None = None,
) -> EntryDecision[ManagedFragment]:
    return EntryDecision.inactive(
        _finding(path, reason, target=target, detail=detail)
    )


def _indeterminate(
    path: Path | str,
    *,
    target: str | None = None,
    detail: str | None = None,
) -> EntryDecision[ManagedFragment]:
    return EntryDecision.indeterminate(
        _finding(
            path,
            "entry-indeterminate",
            status="indeterminate",
            target=target,
            detail=detail,
        )
    )


def _parse_metadata(line: str) -> FragmentMetadata:
    if not line.startswith(ssh_profile.METADATA_PREFIX):
        raise ValueError("missing schema-v1 metadata")
    raw = json.loads(line[len(ssh_profile.METADATA_PREFIX) :])
    if not isinstance(raw, dict):
        raise ValueError("metadata must be a JSON object")
    if raw.get("schema_version") != 1 or isinstance(raw.get("schema_version"), bool):
        raise ValueError("metadata schema_version must be 1")
    transport = raw.get("transport")
    registry = raw.get("registry")
    module = raw.get("module")
    if not isinstance(transport, str) or not ssh_profile.is_valid_transport(transport):
        raise ValueError("metadata transport is invalid")
    if not isinstance(registry, str) or not registry:
        raise ValueError("metadata registry path is required")
    if not isinstance(module, str) or not module:
        raise ValueError("metadata module path is required")
    return FragmentMetadata(
        transport=transport,
        registry=registry,
        module=module,
    )


def _parse_structure(
    path: Path,
    text: str,
) -> tuple[str, tuple[str, ...], FragmentMetadata | None]:
    lines = _normalize_text(text).splitlines()
    if not lines:
        raise FragmentParseError("managed fragment is empty")
    header = _HEADER_RE.fullmatch(lines[0])
    if header is None:
        raise FragmentParseError("missing or invalid agent-ssh transport header")
    transport = header.group("transport")

    aliases: list[str] = []
    seen: set[str] = set()
    in_host_block = False
    for line_number, line in enumerate(lines[1:], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1].isspace():
            if not in_host_block:
                raise FragmentParseError(
                    f"line {line_number}: option appears before the first Host block",
                    transport=transport,
                    aliases=tuple(aliases),
                )
            directive = _DIRECTIVE_RE.match(stripped)
            if directive and directive.group("name").casefold() in {
                "host",
                "include",
                "match",
            }:
                raise FragmentParseError(
                    f"line {line_number}: nested {directive.group('name')} is not allowed",
                    transport=transport,
                    aliases=tuple(aliases),
                )
            continue
        host = _HOST_RE.fullmatch(line)
        if host is None:
            directive = _DIRECTIVE_RE.match(line)
            name = directive.group("name") if directive else line.split(maxsplit=1)[0]
            raise FragmentParseError(
                f"line {line_number}: top-level {name!r} is not allowed in a managed fragment",
                transport=transport,
                aliases=tuple(aliases),
            )
        alias = host.group("alias")
        if not ssh_profile.is_valid_alias(alias):
            raise FragmentParseError(
                f"line {line_number}: invalid exact Host alias {alias!r}",
                transport=transport,
                aliases=tuple(aliases),
            )
        folded = alias.casefold()
        if folded in seen:
            raise FragmentParseError(
                f"line {line_number}: duplicate Host alias {alias!r}",
                transport=transport,
                aliases=tuple(aliases),
            )
        seen.add(folded)
        aliases.append(alias)
        in_host_block = True
    metadata: FragmentMetadata | None = None
    if len(lines) > 1 and lines[1].startswith(ssh_profile.METADATA_PREFIX):
        try:
            metadata = _parse_metadata(lines[1])
        except (json.JSONDecodeError, ValueError) as exc:
            raise FragmentParseError(
                str(exc),
                transport=transport,
                aliases=tuple(aliases),
            ) from exc
    return transport, tuple(aliases), metadata


def _source_file(
    path_text: str,
) -> tuple[Path | None, str | None, str | None]:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        return None, "identity-mismatch", "source path must be absolute"
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        return None, "missing-target", str(exc)
    except OSError as exc:
        return None, "entry-indeterminate", str(exc)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        return (
            None,
            "target-unusable",
            "source must be a regular non-reparse file",
        )
    try:
        canonical = path.resolve(strict=True)
    except FileNotFoundError as exc:
        return None, "missing-target", str(exc)
    except OSError as exc:
        return None, "entry-indeterminate", str(exc)
    if os.path.normcase(str(canonical)) != os.path.normcase(str(path)):
        return (
            None,
            "identity-mismatch",
            f"source path is not canonical; current target is {canonical}",
        )
    return canonical, None, None


def _same_document(
    previous: ManagedFragment | None,
    current: ManagedFragment,
) -> bool:
    return bool(
        previous is not None
        and previous.document_digest == current.document_digest
        and previous.transport == current.transport
        and previous.aliases == current.aliases
        and previous.metadata == current.metadata
    )


def _classify_fragment(
    path: Path,
    info: os.stat_result,
    *,
    previous: ManagedFragment | None,
    syntax_check: Callable[[Path, tuple[str, ...]], str | None],
) -> tuple[EntryDecision[ManagedFragment], FragmentClaims]:
    identity = FileIdentity.from_stat(info)
    try:
        text = _read_text(path)
    except UnicodeError as exc:
        return _inactive(path, "invalid-entry", detail=str(exc)), FragmentClaims()
    except OSError as exc:
        if previous is not None and previous.file_identity == identity:
            finding = _finding(
                path,
                "entry-indeterminate",
                status="indeterminate",
                detail=str(exc),
            )
            return (
                EntryDecision.advisory(previous, finding),
                FragmentClaims(previous.transport, previous.aliases, True),
            )
        return _indeterminate(path, detail=str(exc)), FragmentClaims()

    try:
        transport, aliases, metadata = _parse_structure(path, text)
    except FragmentParseError as exc:
        return (
            _inactive(path, "invalid-entry", detail=str(exc)),
            FragmentClaims(exc.transport, exc.aliases),
        )
    claims = FragmentClaims(transport, aliases, True)

    filename = MANAGED_FRAGMENT_RE.fullmatch(path.name)
    assert filename is not None
    filename_transport = filename.group("transport")
    if transport != filename_transport:
        return (
            _inactive(
                path,
                "identity-mismatch",
                target=transport,
                detail=(
                    f"filename claims transport {filename_transport!r} but "
                    f"content claims {transport!r}"
                ),
            ),
            claims,
        )

    syntax_error = syntax_check(path, aliases)
    if syntax_error:
        return (
            _inactive(
                path,
                "invalid-entry",
                target=str(path),
                detail=f"OpenSSH rejected the fragment: {syntax_error}",
            ),
            claims,
        )

    current = ManagedFragment(
        path=str(path),
        transport=transport,
        aliases=aliases,
        document_digest=_digest(text),
        file_identity=identity,
        metadata=metadata,
    )
    if metadata is None:
        finding = _finding(
            path,
            "legacy-unattributed",
            status="advisory",
            target=transport,
            detail="fragment predates schema-v1 source identity",
        )
        return EntryDecision.advisory(current, finding), claims

    if metadata.transport != transport:
        return (
            _inactive(
                path,
                "identity-mismatch",
                target=metadata.transport,
                detail="metadata transport does not match the fragment identity",
            ),
            claims,
        )

    sources: dict[str, Path] = {}
    for role, source_text in (
        ("registry", metadata.registry),
        ("module", metadata.module),
    ):
        source, reason, detail = _source_file(source_text)
        if reason == "entry-indeterminate":
            if _same_document(previous, current):
                finding = _finding(
                    path,
                    "entry-indeterminate",
                    status="indeterminate",
                    target=source_text,
                    detail=f"{role} source could not be inspected: {detail}",
                )
                return EntryDecision.advisory(current, finding), claims
            return (
                _indeterminate(
                    path,
                    target=source_text,
                    detail=f"{role} source could not be inspected: {detail}",
                ),
                claims,
            )
        if reason is not None:
            return (
                _inactive(
                    path,
                    reason,
                    target=source_text,
                    detail=f"{role} source is invalid: {detail}",
                ),
                claims,
            )
        assert source is not None
        sources[role] = source

    try:
        registry_text = _read_text(sources["registry"])
        module_text = _read_text(sources["module"])
    except OSError as exc:
        target = str(getattr(exc, "filename", "") or metadata.registry)
        if _same_document(previous, current):
            finding = _finding(
                path,
                "entry-indeterminate",
                status="indeterminate",
                target=target,
                detail=str(exc),
            )
            return EntryDecision.advisory(current, finding), claims
        return _indeterminate(path, target=target, detail=str(exc)), claims
    except UnicodeError as exc:
        return (
            _inactive(
                path,
                "invalid-entry",
                detail=f"source encoding is invalid: {exc}",
            ),
            claims,
        )

    try:
        registry = _decode_mapping(registry_text, sources["registry"])
        module = _decode_mapping(module_text, sources["module"])
    except (json.JSONDecodeError, ValueError, UnicodeError) as exc:
        return (
            _inactive(
                path,
                "invalid-entry",
                detail=f"source document is invalid: {exc}",
            ),
            claims,
        )
    module_name = module.get("module")
    registry_transport = registry.get("transport")
    if (
        not isinstance(module_name, str)
        or not ssh_profile.is_valid_transport(module_name)
        or module_name != transport
        or not isinstance(registry_transport, str)
        or registry_transport != transport
    ):
        return (
            _inactive(
                path,
                "identity-mismatch",
                target=transport,
                detail=(
                    "current registry transport and module identity must both "
                    "match the fragment"
                ),
            ),
            claims,
        )

    try:
        expected = ssh_profile.render_fragment(
            registry,
            module,
            registry_path=sources["registry"],
            module_path=sources["module"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        return (
            _inactive(
                path,
                "invalid-entry",
                detail=f"current sources cannot render a valid fragment: {exc}",
            ),
            claims,
        )
    if _normalize_text(text) != _normalize_text(expected):
        return (
            _inactive(
                path,
                "identity-mismatch",
                target=str(sources["registry"]),
                detail=(
                    "fragment content no longer matches the current registry "
                    "and transport module"
                ),
            ),
            claims,
        )
    return EntryDecision.active(current), claims


def _registry_finding(root: Path, detail: str) -> Finding:
    return Finding(
        registry=REGISTRY_NAME,
        entry=str(root),
        status="indeterminate",
        reason="registry-indeterminate",
        owner="agent-ssh",
        remedy=(
            f"Restore access and retry `{DOCTOR_COMMAND}`; the runtime retains "
            "only its last-known managed-fragment set."
        ),
        detail=detail,
    )


def _duplicate_findings(
    entries: Mapping[str, ManagedFragment],
    claims: Mapping[str, FragmentClaims],
) -> tuple[set[str], tuple[Finding, ...]]:
    conflicts: dict[str, set[str]] = defaultdict(set)
    by_transport: dict[str, list[str]] = defaultdict(list)
    by_alias: dict[str, list[str]] = defaultdict(list)
    for entry, claim in claims.items():
        if claim.transport:
            by_transport[claim.transport.casefold()].append(entry)
        for alias in claim.aliases:
            by_alias[alias.casefold()].append(entry)
    for identity, paths in by_transport.items():
        if len(paths) > 1:
            for path in paths:
                conflicts[path].add(f"transport:{identity}")
    for identity, paths in by_alias.items():
        if len(paths) > 1:
            for path in paths:
                conflicts[path].add(f"host:{identity}")

    findings: list[Finding] = []
    for entry, identities in sorted(conflicts.items()):
        findings.append(
            _finding(
                entry,
                "duplicate",
                target=", ".join(sorted(identities)),
                detail="multiple managed fragments claim an exclusive identity",
            )
        )
    return set(conflicts), tuple(findings)


def warning_state_file() -> Path:
    """Cross-invocation warning state for the one-shot CLI."""
    override = os.environ.get(WARNING_STATE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-ssh" / "fragment-warning-state.json"


class PersistentWarningTracker:
    """Fingerprint-based warning selection persisted across CLI processes."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        limit: int = 10,
        repeat_after_seconds: float = 3600.0,
    ):
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if repeat_after_seconds < 0:
            raise ValueError("repeat_after_seconds must be non-negative")
        self.path = Path(path) if path is not None else warning_state_file()
        self.limit = limit
        self.repeat_after_seconds = repeat_after_seconds
        self._fallback = WarningTracker(
            limit=limit,
            repeat_after_seconds=repeat_after_seconds,
        )

    def _load(self) -> tuple[dict[str, float], set[str]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return {}, set()
        if not isinstance(raw, dict):
            return {}, set()
        seen_raw = raw.get("seen", {})
        current_raw = raw.get("current", [])
        if not isinstance(seen_raw, dict) or not isinstance(current_raw, list):
            return {}, set()
        seen = {
            key: float(value)
            for key, value in seen_raw.items()
            if isinstance(key, str) and isinstance(value, (int, float))
        }
        current = {value for value in current_raw if isinstance(value, str)}
        return seen, current

    def _save(self, seen: Mapping[str, float], current: set[str]) -> None:
        payload = json.dumps(
            {
                "version": 1,
                "seen": dict(sorted(seen.items())),
                "current": sorted(current),
            },
            indent=2,
            sort_keys=True,
        ) + "\n"
        try:
            atomic_write_text(self.path, payload)
        except OSError:
            pass

    def select(
        self,
        findings,
        *,
        now: float | None = None,
    ) -> WarningBatch:
        materialized = tuple(findings)
        if not materialized and not self.path.exists():
            return WarningBatch(emitted=())
        instant = time.time() if now is None else now
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        try:
            with exclusive_file_lock(lock_path):
                ordered = sorted(
                    materialized,
                    key=lambda finding: (
                        finding.registry,
                        finding.entry,
                        finding.reason,
                        finding.target or "",
                    ),
                )
                unique: dict[str, Finding] = {}
                for finding in ordered:
                    unique.setdefault(finding.fingerprint(), finding)
                seen, prior_current = self._load()
                current = set(unique)
                recovered = len(prior_current - current)
                candidates: list[Finding] = []
                next_seen: dict[str, float] = {}
                for fingerprint, finding in unique.items():
                    last = seen.get(fingerprint)
                    if last is None or instant - last >= self.repeat_after_seconds:
                        candidates.append(finding)
                        next_seen[fingerprint] = instant
                    elif last is not None:
                        next_seen[fingerprint] = last
                if current or prior_current or self.path.exists():
                    self._save(next_seen, current)
                emitted = tuple(candidates[: self.limit])
                return WarningBatch(
                    emitted=emitted,
                    suppressed=max(0, len(candidates) - len(emitted)),
                    recovered=recovered,
                )
        except OSError:
            return self._fallback.select(
                materialized,
                now=instant,
            )


def scan_fragment_registry(
    directory: str | os.PathLike[str] | None = None,
    *,
    previous: Mapping[str, ManagedFragment] | None = None,
    syntax_check: Callable[[Path, tuple[str, ...]], str | None] | None = None,
) -> FragmentRegistryReport:
    """Classify only ``50-agent-ssh-*.conf`` entries in one OpenSSH config.d."""
    root = Path(directory) if directory is not None else managed_config_dir()
    prior = dict(previous or {})
    check_syntax = syntax_check or ssh_profile.openssh_syntax_error
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        snapshot = ScanSnapshot[ManagedFragment](
            registry=REGISTRY_NAME,
            authority=ScanAuthority.ABSENT,
        )
        return FragmentRegistryReport(snapshot, {}, (), frozenset())
    except OSError as exc:
        finding = _registry_finding(root, str(exc))
        snapshot = ScanSnapshot[ManagedFragment](
            registry=REGISTRY_NAME,
            authority=ScanAuthority.INDETERMINATE,
            findings=(finding,),
        )
        return FragmentRegistryReport(
            snapshot,
            prior,
            (finding,),
            frozenset(),
            True,
        )
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or _is_reparse(root_info)
    ):
        finding = _registry_finding(
            root,
            "config.d must be a regular non-reparse directory",
        )
        snapshot = ScanSnapshot[ManagedFragment](
            registry=REGISTRY_NAME,
            authority=ScanAuthority.INDETERMINATE,
            findings=(finding,),
        )
        return FragmentRegistryReport(
            snapshot,
            prior,
            (finding,),
            frozenset(),
            True,
        )
    try:
        candidates = sorted(
            (
                path
                for path in root.iterdir()
                if MANAGED_FRAGMENT_RE.fullmatch(path.name)
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError as exc:
        finding = _registry_finding(root, str(exc))
        snapshot = ScanSnapshot[ManagedFragment](
            registry=REGISTRY_NAME,
            authority=ScanAuthority.INDETERMINATE,
            findings=(finding,),
        )
        return FragmentRegistryReport(
            snapshot,
            prior,
            (finding,),
            frozenset(),
            True,
        )

    decisions: dict[str, EntryDecision[ManagedFragment]] = {}
    claims: dict[str, FragmentClaims] = {}
    findings: list[Finding] = []
    entries: dict[str, ManagedFragment] = {}
    for path in candidates:
        key = str(path)
        try:
            info = path.lstat()
        except OSError as exc:
            decision = _indeterminate(path, detail=str(exc))
            entry_claims = FragmentClaims()
        else:
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or _is_reparse(info)
            ):
                decision = _inactive(
                    path,
                    "invalid-entry",
                    detail="managed fragment must be a regular non-reparse file",
                )
                entry_claims = FragmentClaims()
            else:
                try:
                    decision, entry_claims = _classify_fragment(
                        path,
                        info,
                        previous=prior.get(key),
                        syntax_check=check_syntax,
                    )
                except (AttributeError, KeyError, TypeError, ValueError) as exc:
                    decision = _inactive(
                        path,
                        "invalid-entry",
                        detail=f"managed fragment classification failed: {exc}",
                    )
                    entry_claims = FragmentClaims()
        decisions[key] = decision
        claims[key] = entry_claims
        findings.extend(decision.findings)
        if decision.status in (
            EntryStatus.ACTIVE,
            EntryStatus.ACTIVE_WITH_ADVISORY,
        ) and decision.value is not None:
            entries[key] = decision.value

    duplicate_entries, duplicate_findings = _duplicate_findings(entries, claims)
    for entry in duplicate_entries:
        entries.pop(entry, None)
    findings.extend(duplicate_findings)

    active_aliases = {
        alias.casefold()
        for fragment in entries.values()
        for alias in fragment.aliases
    }
    blocked_aliases = {
        alias.casefold()
        for entry, entry_claims in claims.items()
        if entry not in entries
        for alias in entry_claims.aliases
        if alias.casefold() not in active_aliases
    }
    unscoped_blocking = any(
        not entry_claims.complete and entry not in entries
        for entry, entry_claims in claims.items()
    )
    snapshot = ScanSnapshot(
        registry=REGISTRY_NAME,
        authority=ScanAuthority.COMPLETE,
        decisions=decisions,
        findings=tuple(
            finding
            for decision in decisions.values()
            for finding in decision.findings
        ),
    )
    return FragmentRegistryReport(
        snapshot=snapshot,
        entries=entries,
        findings=tuple(findings),
        blocked_aliases=frozenset(blocked_aliases),
        unscoped_blocking=unscoped_blocking,
    )


class FragmentRegistry:
    """Stateful operational sweep with bounded, deduplicated warnings."""

    def __init__(
        self,
        directory: str | os.PathLike[str] | None = None,
        *,
        warning_tracker: WarningTracker | PersistentWarningTracker | None = None,
        warning_state_path: str | os.PathLike[str] | None = None,
        syntax_check: Callable[[Path, tuple[str, ...]], str | None] | None = None,
    ):
        self.directory = Path(directory) if directory is not None else None
        if warning_tracker is not None and warning_state_path is not None:
            raise ValueError(
                "warning_tracker and warning_state_path are mutually exclusive"
            )
        self.warning_tracker = warning_tracker or PersistentWarningTracker(
            warning_state_path
        )
        self.syntax_check = syntax_check
        self._entries: dict[str, ManagedFragment] = {}
        self.last_report: FragmentRegistryReport | None = None

    @staticmethod
    def _emit_warnings(
        report: FragmentRegistryReport,
        tracker: WarningTracker | PersistentWarningTracker,
    ) -> None:
        batch = tracker.select(report.findings)
        for finding in batch.emitted:
            target = f" -> {finding.target}" if finding.target else ""
            detail = f": {finding.detail}" if finding.detail else ""
            print(
                f"[WARN] {finding.registry}: {finding.reason}: "
                f"{finding.entry}{target}{detail}; run `{DOCTOR_COMMAND}`",
                file=sys.stderr,
            )
        if batch.suppressed:
            print(
                f"[WARN] {batch.suppressed} additional managed-fragment "
                f"finding(s) suppressed; run `{DOCTOR_COMMAND}`",
                file=sys.stderr,
            )
        if batch.recovered:
            print(
                f"[OK] {batch.recovered} managed-fragment finding(s) recovered; "
                "current state is active again",
                file=sys.stderr,
            )

    def refresh(self, *, emit_warnings: bool = True) -> FragmentRegistryReport:
        report = scan_fragment_registry(
            self.directory,
            previous=self._entries,
            syntax_check=self.syntax_check,
        )
        self._entries = dict(report.entries)
        self.last_report = report
        if emit_warnings:
            self._emit_warnings(report, self.warning_tracker)
        return report


def doctor_payload(
    report: FragmentRegistryReport,
    directory: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    root = Path(directory) if directory is not None else managed_config_dir()
    return {
        "registry": REGISTRY_NAME,
        "path": str(root),
        "authority": report.snapshot.authority.value,
        "active": [
            fragment.to_dict()
            for _, fragment in sorted(report.entries.items())
        ],
        "findings": [finding.to_dict() for finding in report.findings],
        "fix_available": False,
        "active_basis": "current-evidence-only",
        "retention_possible": (
            report.snapshot.authority is ScanAuthority.INDETERMINATE
            or any(finding.status == "indeterminate" for finding in report.findings)
        ),
        "unscoped_blocking": report.unscoped_blocking,
    }


def format_doctor(
    report: FragmentRegistryReport,
    directory: str | os.PathLike[str] | None = None,
) -> str:
    root = Path(directory) if directory is not None else managed_config_dir()
    label = "[WARN]" if report.findings else "[OK]"
    lines = [
        f"{label} {REGISTRY_NAME} is {report.snapshot.authority.value}; "
        f"{len(report.entries)} managed fragment(s) confirmed active by current evidence.",
        f"  path: {root}",
    ]
    if (
        report.snapshot.authority is ScanAuthority.INDETERMINATE
        or any(finding.status == "indeterminate" for finding in report.findings)
    ):
        lines.append(
            "  A running process may retain only matching last-known fragments "
            "for indeterminate entries."
        )
    if report.unscoped_blocking:
        lines.append(
            "  Incomplete managed-fragment identity blocks fresh alias probes "
            "until every managed entry can be classified."
        )
    for _, fragment in sorted(report.entries.items()):
        lines.append(
            f"  + {fragment.transport}: {fragment.path} "
            f"[{', '.join(fragment.aliases) or 'no aliases'}]"
        )
        if fragment.metadata is None:
            lines.append("    class: legacy-managed")
        else:
            lines.append("    class: managed")
            lines.append(f"    registry: {fragment.metadata.registry}")
            lines.append(f"    module: {fragment.metadata.module}")
    for finding in report.findings:
        target = f" -> {finding.target}" if finding.target else ""
        lines.append(f"  - {finding.reason}: {finding.entry}{target}")
        if finding.detail:
            lines.append(f"    {finding.detail}")
        if finding.remedy:
            lines.append(f"    {finding.remedy}")
    lines.append("  Cleanup is report-only; no --fix operation is available.")
    return "\n".join(lines)
