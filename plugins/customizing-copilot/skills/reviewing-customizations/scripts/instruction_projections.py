"""Declarative static instruction projection management.

Plugins may ship ``instruction-projections.json`` beside ``plugin.json``. This
module validates those inert declarations, renders provenance-marked repository
instruction files, and maintains the checked-in projection lock. It never
executes plugin code and never removes repository files.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator

BLOCKING = "blocking"
WARNING = "warning"

DECLARATION_SCHEMA = "copilot-extensions.instruction-projections"
DECLARATION_VERSION = 1
PROJECTION_SCHEMA = "copilot-extensions.instruction-projection"
PROJECTION_VERSION = 1
LOCK_SCHEMA = "copilot-extensions.context-projections"
LOCK_VERSION = 1
RESULT_SCHEMA = "copilot-extensions.instruction-projection-result"
RESULT_VERSION = 1

DECLARATION_FILE = "instruction-projections.json"
LOCK_RELATIVE = PurePosixPath(".github/copilot/context-projections.json")
MARKER_PREFIX = "<!-- copilot-extension-instruction-projection "
MARKER_SUFFIX = " -->"
MAX_DECLARATION_BYTES = 16 * 1024
MAX_PROJECTIONS_PER_PLUGIN = 16
MAX_TEMPLATE_BYTES = 4 * 1024
MAX_PROJECTION_BYTES = 4 * 1024
MAX_AGGREGATE_BYTES = 12 * 1024
MAX_LEGACY_MARKERS = 8
CUSTOMIZATION_KINDS = {"instructions"}

IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
PLUGIN_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?$")
LEGACY_MARKER = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?:"
    r"[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$"
)
SESSION_ID = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
DYNAMIC_CONTENT = (
    (
        "session identifier",
        SESSION_ID,
    ),
    (
        "environment interpolation",
        re.compile(
            r"(?i)(\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$env:[A-Za-z_]"
            r"[A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|"
            r"\bos\.environ\b|\bprocess\.env\b)"
        ),
    ),
    (
        "session-state path construction",
        re.compile(
            r"(?i)([~\\/]\.copilot[\\/]session-state|"
            r"session-state[\\/]\s*(?:<|\{|\$)|Path\.home\s*\(\))"
        ),
    ),
    (
        "absolute installed payload path",
        re.compile(
            r"(?i)(?:[A-Za-z]:[\\/]|/(?:home|users|opt|var|tmp)/)"
            r"[^\r\n`]*?(?:installed-plugins|session-state)"
        ),
    ),
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    path: str
    message: str


@dataclass(frozen=True)
class ProjectionSpec:
    source_id: str
    plugin: str
    plugin_name: str
    plugin_version: str
    payload_root: Path
    template: str
    template_sha256: str
    template_bytes: int
    template_content: bytes
    destination: str
    customization_kind: str
    apply_to: str
    legacy_markers: tuple[str, ...]

    @property
    def source_key(self) -> str:
        return f"{self.plugin}:{self.source_id}"


@dataclass(frozen=True)
class RenderedProjection:
    spec: ProjectionSpec
    content: bytes
    sha256: str
    byte_count: int
    marker: dict[str, object]

    def lock_entry(self) -> dict[str, object]:
        return {
            "sourceId": self.spec.source_id,
            "plugin": self.spec.plugin,
            "pluginVersion": self.spec.plugin_version,
            "template": self.spec.template,
            "templateSha256": self.spec.template_sha256,
            "templateBytes": self.spec.template_bytes,
            "destination": self.spec.destination,
            "customizationKind": self.spec.customization_kind,
            "applyTo": self.spec.apply_to,
            "renderedSha256": self.sha256,
            "renderedBytes": self.byte_count,
        }


@dataclass
class Result:
    operation: str
    findings: list[Finding] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    lock_updated: bool = False
    declared: int = 0
    locked: int = 0

    @property
    def blocking(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == BLOCKING)

    @property
    def warnings(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == WARNING)

    def add(self, severity: str, check: str, path: Path | str, message: str) -> None:
        self.findings.append(Finding(severity, check, _display_path(path), message))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": RESULT_SCHEMA,
            "version": RESULT_VERSION,
            "operation": self.operation,
            "blocking": self.blocking,
            "warnings": self.warnings,
            "declared": self.declared,
            "locked": self.locked,
            "changed": sorted(self.changed),
            "unchanged": sorted(self.unchanged),
            "lockUpdated": self.lock_updated,
            "findings": [asdict(finding) for finding in self.findings],
        }


def _display_path(path: Path | str) -> str:
    if isinstance(path, Path):
        return path.as_posix()
    return str(path).replace("\\", "/")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    seen: set[str] = set()
    for key, value in pairs:
        folded = key.casefold()
        if folded in seen:
            raise ValueError("duplicate or case-conflicting JSON key")
        seen.add(folded)
        result[key] = value
    return result


def _load_json_bytes(raw: bytes) -> object:
    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
    )


def _canonical_json(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
    else:
        text = json.dumps(
            value,
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=True,
        )
    return (text + "\n").encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _is_indirection(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or _is_reparse(info)


def _validate_relative(value: object, *, field_name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    if "\\" in value or "\x00" in value or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{field_name} must use canonical POSIX path syntax")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError(f"{field_name} must be canonical and relative")
    for part in path.parts:
        if (
            part.endswith((" ", "."))
            or any(character in _WINDOWS_INVALID_CHARS for character in part)
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        ):
            raise ValueError(
                f"{field_name} must use portable filesystem components"
            )
    return path


def _portable_path_key(value: str | PurePosixPath) -> str:
    path = value if isinstance(value, PurePosixPath) else PurePosixPath(value)
    return "/".join(part.casefold() for part in path.parts)


def _safe_existing_file(root: Path, relative: PurePosixPath) -> Path:
    current = root
    if _is_indirection(current):
        raise ValueError("payload root is a symlink or reparse point")
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise ValueError("path is missing or unreadable") from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError("path contains a symlink or reparse point")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise ValueError("path is not a regular file")
    try:
        current.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError("path escapes its payload root") from exc
    return current


def _safe_destination(root: Path, relative: PurePosixPath) -> Path:
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("repository root is missing or unreadable") from exc
    if _is_indirection(root):
        raise ValueError("repository root is a symlink or reparse point")
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError("destination path is unreadable") from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError("destination contains a symlink or reparse point")
    try:
        current.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("destination escapes the repository") from exc
    return current


def _read_bounded_regular(path: Path, limit: int) -> bytes:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
        or info.st_size > limit
    ):
        raise ValueError("file is not a bounded regular file")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError("file exceeds its byte limit")
    return raw


def _plugin_identity(origin: str) -> tuple[str, str, str]:
    marketplace, separator, plugin_name = origin.partition("/")
    if (
        not separator
        or not IDENTIFIER.fullmatch(marketplace)
        or not IDENTIFIER.fullmatch(plugin_name)
    ):
        raise ValueError("plugin source must have marketplace/plugin identity")
    return f"{plugin_name}@{marketplace}", plugin_name, marketplace


def _repository_root(path: Path) -> Path:
    if _is_indirection(path):
        raise ValueError("repository root is a symlink or reparse point")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("repository root is missing or unreadable") from exc
    if not resolved.is_dir():
        raise ValueError("repository root is not a directory")
    return resolved


def validate_repository_root(path: Path) -> Path:
    """Return a resolved regular repository root or raise ``ValueError``."""
    return _repository_root(path)


def _suite_source_root(
    repo_root: Path,
    marketplace: str,
    plugin_name: str,
) -> Path | None:
    manifest = None
    for relative in (
        PurePosixPath(".github/plugin/marketplace.json"),
        PurePosixPath("marketplace.json"),
        PurePosixPath(".plugin/marketplace.json"),
        PurePosixPath(".claude-plugin/marketplace.json"),
    ):
        try:
            manifest_path = _safe_existing_file(repo_root, relative)
            manifest = _load_json_bytes(
                _read_bounded_regular(manifest_path, 256 * 1024)
            )
            break
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            continue
    if not isinstance(manifest, dict) or manifest.get("name") != marketplace:
        return None
    entries = manifest.get("plugins")
    if not isinstance(entries, list):
        return None
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("name") == plugin_name
    ]
    if len(matches) != 1:
        return None
    source = matches[0].get("source")
    if not isinstance(source, str):
        return None
    try:
        source_relative = _validate_relative(
            source, field_name="marketplace source"
        )
        plugin_root = PurePosixPath()
        metadata = manifest.get("metadata")
        if isinstance(metadata, dict) and metadata.get("pluginRoot"):
            plugin_root = _validate_relative(
                metadata["pluginRoot"], field_name="marketplace pluginRoot"
            )
        relative = plugin_root / source_relative
        candidate = _safe_existing_file(
            repo_root, relative / "plugin.json"
        ).parent
        manifest = _load_json_bytes(
            _read_bounded_regular(candidate / "plugin.json", 4096)
        )
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    return (
        candidate
        if isinstance(manifest, dict) and manifest.get("name") == plugin_name
        else None
    )


def _source_root(repo_root: Path, source: object) -> Path:
    origin = str(getattr(source, "origin", ""))
    _identity, plugin_name, marketplace = _plugin_identity(origin)
    suite_source = _suite_source_root(repo_root, marketplace, plugin_name)
    if suite_source is not None:
        return suite_source
    payload_root = getattr(source, "payload_root", None)
    if isinstance(payload_root, Path):
        return payload_root
    skills_root = getattr(source, "skills_root", None)
    if isinstance(skills_root, Path):
        return skills_root.parent
    raise ValueError("plugin source has no payload root")


def _canonical_template_bytes(raw: bytes) -> bytes:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("template must be UTF-8 without BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("template is not valid UTF-8") from exc
    text = text.replace("\r\n", "\n")
    if "\r" in text:
        raise ValueError("template contains unsupported lone CR line endings")
    return text.encode("utf-8")


def _frontmatter_apply_to(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="strict")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0] != "---\n":
        raise ValueError("template must start with YAML frontmatter")
    try:
        closing = lines.index("---\n", 1)
    except ValueError as exc:
        raise ValueError("template frontmatter is not terminated") from exc
    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\n", line)
        if not match:
            raise ValueError("template frontmatter must contain scalar keys")
        key, value = match.groups()
        if key in fields:
            raise ValueError("template frontmatter contains duplicate keys")
        fields[key] = value
    if set(fields) != {"applyTo"}:
        raise ValueError("template frontmatter must contain only applyTo")
    value = fields["applyTo"]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    if not value:
        raise ValueError("template applyTo must not be empty")
    if not "".join(lines[closing + 1 :]).strip():
        raise ValueError("template body must remain useful without provenance")
    return value


def _forbidden_content(raw: bytes) -> list[str]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return ["invalid UTF-8"]
    return [label for label, pattern in DYNAMIC_CONTENT if pattern.search(text)]


def _load_specs(
    repo_root: Path,
    sources: Iterable[object],
    result: Result,
) -> tuple[list[ProjectionSpec], set[str]]:
    specs: list[ProjectionSpec] = []
    unknown_plugins: set[str] = set()
    for source in sorted(sources, key=lambda item: str(getattr(item, "origin", ""))):
        origin = str(getattr(source, "origin", ""))
        try:
            plugin_identity, plugin_name, _marketplace = _plugin_identity(origin)
            payload_root = _source_root(repo_root, source)
        except ValueError as exc:
            result.add(
                BLOCKING,
                "projection-source",
                f"<plugin:{origin or 'unknown'}>",
                str(exc),
            )
            continue
        available_manifest = False
        for manifest_relative in (
            PurePosixPath("plugin.json"),
            PurePosixPath(".claude-plugin/plugin.json"),
        ):
            try:
                candidate = _safe_existing_file(
                    payload_root, manifest_relative
                )
                candidate_manifest = _load_json_bytes(
                    _read_bounded_regular(candidate, 4096)
                )
            except (
                OSError,
                ValueError,
                UnicodeError,
                json.JSONDecodeError,
            ):
                continue
            if (
                isinstance(candidate_manifest, dict)
                and candidate_manifest.get("name") == plugin_name
                and isinstance(candidate_manifest.get("version"), str)
                and candidate_manifest["version"]
            ):
                available_manifest = True
                break
        if not available_manifest:
            result.add(
                BLOCKING,
                "projection-source-unavailable",
                payload_root,
                "enabled plugin has no supported readable manifest",
            )
            unknown_plugins.add(plugin_identity)
            continue
        declaration_path = payload_root / DECLARATION_FILE
        try:
            declaration_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            result.add(
                BLOCKING,
                "projection-declaration",
                declaration_path,
                f"declaration is unreadable: {exc}",
            )
            unknown_plugins.add(plugin_identity)
            continue
        try:
            declaration_raw = _read_bounded_regular(
                declaration_path, MAX_DECLARATION_BYTES
            )
            declaration = _load_json_bytes(declaration_raw)
            manifest_path = _safe_existing_file(
                payload_root, PurePosixPath("plugin.json")
            )
            manifest = _load_json_bytes(
                _read_bounded_regular(manifest_path, 4096)
            )
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            result.add(
                BLOCKING,
                "projection-declaration",
                declaration_path,
                f"cannot load strict bounded declaration: {exc}",
            )
            unknown_plugins.add(plugin_identity)
            continue
        if (
            not isinstance(manifest, dict)
            or manifest.get("name") != plugin_name
            or not isinstance(manifest.get("version"), str)
            or not PLUGIN_VERSION.fullmatch(manifest["version"])
        ):
            result.add(
                BLOCKING,
                "projection-declaration",
                manifest_path,
                "projection participant manifest identity/version is "
                "missing or inconsistent",
            )
            unknown_plugins.add(plugin_identity)
            continue
        if (
            not isinstance(declaration, dict)
            or set(declaration) != {"schema", "version", "projections"}
            or declaration.get("schema") != DECLARATION_SCHEMA
            or declaration.get("version") != DECLARATION_VERSION
            or not isinstance(declaration.get("projections"), list)
            or not 1 <= len(declaration["projections"]) <= MAX_PROJECTIONS_PER_PLUGIN
        ):
            result.add(
                BLOCKING,
                "projection-declaration",
                declaration_path,
                "declaration schema/version/shape is unsupported",
            )
            unknown_plugins.add(plugin_identity)
            continue
        for index, entry in enumerate(declaration["projections"]):
            entry_path = f"{declaration_path.as_posix()}#projections[{index}]"
            try:
                if not isinstance(entry, dict) or set(entry) != {
                    "id",
                    "template",
                    "destination",
                    "customizationKind",
                    "applyTo",
                    "legacyMarkers",
                }:
                    raise ValueError("projection entry has unknown or missing keys")
                source_id = entry["id"]
                if not isinstance(source_id, str) or not IDENTIFIER.fullmatch(source_id):
                    raise ValueError("id is not a stable lowercase identifier")
                template_rel = _validate_relative(
                    entry["template"], field_name="template"
                )
                if template_rel.suffixes[-2:] != [".instructions", ".md"]:
                    raise ValueError("template must be an .instructions.md file")
                destination_rel = _validate_relative(
                    entry["destination"], field_name="destination"
                )
                required_prefix = (
                    ".github",
                    "instructions",
                    plugin_name,
                )
                if destination_rel.parts[:3] != required_prefix:
                    raise ValueError(
                        "destination must be under .github/instructions/<plugin>/"
                    )
                if destination_rel.suffixes[-2:] != [".instructions", ".md"]:
                    raise ValueError("destination must be an .instructions.md file")
                kind = entry["customizationKind"]
                if kind not in CUSTOMIZATION_KINDS:
                    raise ValueError("customizationKind is unsupported")
                apply_to = entry["applyTo"]
                if (
                    not isinstance(apply_to, str)
                    or not 1 <= len(apply_to) <= 256
                    or any(ord(ch) < 32 for ch in apply_to)
                ):
                    raise ValueError("applyTo is invalid")
                markers = entry["legacyMarkers"]
                if (
                    not isinstance(markers, list)
                    or len(markers) > MAX_LEGACY_MARKERS
                    or any(
                        not isinstance(marker, str)
                        or not LEGACY_MARKER.fullmatch(marker)
                        for marker in markers
                    )
                    or len(set(markers)) != len(markers)
                ):
                    raise ValueError("legacyMarkers is invalid")
                template_path = _safe_existing_file(payload_root, template_rel)
                template_raw = _canonical_template_bytes(
                    _read_bounded_regular(template_path, MAX_TEMPLATE_BYTES)
                )
                if _frontmatter_apply_to(template_raw) != apply_to:
                    raise ValueError(
                        "template frontmatter applyTo does not match declaration"
                    )
                forbidden = _forbidden_content(template_raw)
                if forbidden:
                    raise ValueError(
                        "template contains forbidden dynamic content: "
                        + ", ".join(forbidden)
                    )
            except (OSError, ValueError) as exc:
                result.add(
                    BLOCKING,
                    "projection-declaration",
                    entry_path,
                    str(exc),
                )
                continue
            specs.append(
                ProjectionSpec(
                    source_id=source_id,
                    plugin=plugin_identity,
                    plugin_name=plugin_name,
                    plugin_version=manifest["version"],
                    payload_root=payload_root,
                    template=template_rel.as_posix(),
                    template_sha256=_sha256(template_raw),
                    template_bytes=len(template_raw),
                    template_content=template_raw,
                    destination=destination_rel.as_posix(),
                    customization_kind=kind,
                    apply_to=apply_to,
                    legacy_markers=tuple(markers),
                )
            )
    _find_spec_conflicts(specs, result)
    result.declared = len(specs)
    return specs, unknown_plugins


def _find_spec_conflicts(specs: list[ProjectionSpec], result: Result) -> None:
    by_source: dict[str, list[ProjectionSpec]] = {}
    by_destination: dict[str, list[ProjectionSpec]] = {}
    for spec in specs:
        by_source.setdefault(spec.source_key, []).append(spec)
        by_destination.setdefault(
            _portable_path_key(spec.destination), []
        ).append(spec)
    for source_key, entries in sorted(by_source.items()):
        if len(entries) > 1:
            result.add(
                BLOCKING,
                "projection-duplicate-source",
                "<plugin-stack>",
                f"source id {source_key!r} is declared more than once",
            )
    for _destination_key, entries in sorted(by_destination.items()):
        if len(entries) > 1:
            owners = ", ".join(sorted(entry.source_key for entry in entries))
            destinations = ", ".join(
                sorted(entry.destination for entry in entries)
            )
            result.add(
                BLOCKING,
                "projection-duplicate-destination",
                destinations,
                f"portable destination is declared by multiple sources: {owners}",
            )
    ordered = sorted(specs, key=lambda item: item.destination)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if first.destination == second.destination:
                continue
            if (
                first.apply_to == second.apply_to
                or first.apply_to == "**"
                or second.apply_to == "**"
            ):
                result.add(
                    WARNING,
                    "projection-overlap",
                    "<plugin-stack>",
                    f"{first.destination} and {second.destination} have "
                    f"overlapping applyTo scopes",
                )


def render_projection(spec: ProjectionSpec) -> RenderedProjection:
    """Render one deterministic UTF-8/LF projection."""
    text = spec.template_content.decode("utf-8")
    lines = text.splitlines(keepends=True)
    closing = lines.index("---\n", 1)
    header = "".join(lines[: closing + 1])
    body = "".join(lines[closing + 1 :])
    marker: dict[str, object] = {
        "schema": PROJECTION_SCHEMA,
        "version": PROJECTION_VERSION,
        "sourceId": spec.source_id,
        "plugin": spec.plugin,
        "pluginVersion": spec.plugin_version,
        "template": spec.template,
        "templateSha256": spec.template_sha256,
        "templateBytes": spec.template_bytes,
        "destination": spec.destination,
        "customizationKind": spec.customization_kind,
        "applyTo": spec.apply_to,
        "renderedBytes": 0,
    }
    content = b""
    for _attempt in range(4):
        marker_line = (
            MARKER_PREFIX
            + json.dumps(
                marker,
                separators=(",", ":"),
                sort_keys=True,
                ensure_ascii=True,
            )
            + MARKER_SUFFIX
            + "\n"
        )
        content = (header + marker_line + body).encode("utf-8")
        if marker["renderedBytes"] == len(content):
            break
        marker["renderedBytes"] = len(content)
    if marker["renderedBytes"] != len(content):
        raise ValueError("rendered byte count did not stabilize")
    return RenderedProjection(
        spec=spec,
        content=content,
        sha256=_sha256(content),
        byte_count=len(content),
        marker=marker,
    )


_LOCK_ENTRY_KEYS = {
    "sourceId",
    "plugin",
    "pluginVersion",
    "template",
    "templateSha256",
    "templateBytes",
    "destination",
    "customizationKind",
    "applyTo",
    "renderedSha256",
    "renderedBytes",
}
_MARKER_KEYS = _LOCK_ENTRY_KEYS - {"renderedSha256"} | {"schema", "version"}


def _validate_lock_entry(entry: object) -> dict[str, object]:
    if not isinstance(entry, dict) or set(entry) != _LOCK_ENTRY_KEYS:
        raise ValueError("lock entry has unknown or missing keys")
    for key in (
        "sourceId",
        "plugin",
        "pluginVersion",
        "template",
        "templateSha256",
        "destination",
        "customizationKind",
        "applyTo",
        "renderedSha256",
    ):
        if not isinstance(entry[key], str) or not entry[key]:
            raise ValueError(f"lock entry {key} is invalid")
    if not IDENTIFIER.fullmatch(entry["sourceId"]):
        raise ValueError("lock sourceId is invalid")
    plugin_name, separator, marketplace = entry["plugin"].partition("@")
    if (
        not separator
        or not IDENTIFIER.fullmatch(plugin_name)
        or not IDENTIFIER.fullmatch(marketplace)
    ):
        raise ValueError("lock plugin identity is invalid")
    template = _validate_relative(entry["template"], field_name="lock template")
    destination = _validate_relative(
        entry["destination"], field_name="lock destination"
    )
    if destination.parts[:3] != (".github", "instructions", plugin_name):
        raise ValueError("lock destination is outside its plugin namespace")
    if template.suffixes[-2:] != [".instructions", ".md"]:
        raise ValueError("lock template is not an .instructions.md path")
    if destination.suffixes[-2:] != [".instructions", ".md"]:
        raise ValueError("lock destination is not an .instructions.md path")
    if entry["customizationKind"] not in CUSTOMIZATION_KINDS:
        raise ValueError("lock customizationKind is unsupported")
    if not PLUGIN_VERSION.fullmatch(entry["pluginVersion"]):
        raise ValueError("lock pluginVersion is invalid")
    if (
        not 1 <= len(entry["applyTo"]) <= 256
        or any(ord(ch) < 32 for ch in entry["applyTo"])
    ):
        raise ValueError("lock applyTo is invalid")
    if not DIGEST.fullmatch(entry["templateSha256"]) or not DIGEST.fullmatch(
        entry["renderedSha256"]
    ):
        raise ValueError("lock digest is invalid")
    for key in ("templateBytes", "renderedBytes"):
        if not isinstance(entry[key], int) or isinstance(entry[key], bool) or entry[key] < 1:
            raise ValueError(f"lock {key} is invalid")
    if entry["templateBytes"] > MAX_TEMPLATE_BYTES:
        raise ValueError("lock templateBytes exceeds the template budget")
    return dict(entry)


def _load_lock(
    repo_root: Path,
    result: Result,
) -> tuple[dict[str, dict[str, object]], bool, bytes | None]:
    lock_path = repo_root.joinpath(*LOCK_RELATIVE.parts)
    try:
        lock_path.lstat()
    except FileNotFoundError:
        return {}, False, None
    except OSError as exc:
        result.add(
            BLOCKING,
            "projection-lock",
            LOCK_RELATIVE.as_posix(),
            f"projection lock is unreadable: {exc}",
        )
        return {}, True, None
    try:
        _safe_destination(repo_root, LOCK_RELATIVE)
        raw = _read_bounded_regular(lock_path, 256 * 1024)
        value = _load_json_bytes(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "version", "projections"}
            or value.get("schema") != LOCK_SCHEMA
            or value.get("version") != LOCK_VERSION
            or not isinstance(value.get("projections"), list)
        ):
            raise ValueError("lock schema/version/shape is unsupported")
        entries: dict[str, dict[str, object]] = {}
        portable_destinations: set[str] = set()
        sources: set[str] = set()
        for raw_entry in value["projections"]:
            entry = _validate_lock_entry(raw_entry)
            destination = str(entry["destination"])
            portable_destination = _portable_path_key(destination)
            source_key = f"{entry['plugin']}:{entry['sourceId']}"
            if portable_destination in portable_destinations:
                raise ValueError(
                    f"duplicate portable lock destination: {destination}"
                )
            if source_key in sources:
                raise ValueError(f"duplicate lock source id: {source_key}")
            entries[destination] = entry
            portable_destinations.add(portable_destination)
            sources.add(source_key)
        canonical = _canonical_json(
            {
                "schema": LOCK_SCHEMA,
                "version": LOCK_VERSION,
                "projections": [
                    entries[destination] for destination in sorted(entries)
                ],
            },
            pretty=True,
        )
        if raw != canonical:
            raise ValueError("lock is not canonical deterministic JSON")
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        result.add(
            BLOCKING,
            "projection-lock",
            LOCK_RELATIVE.as_posix(),
            f"projection lock is malformed or unsafe: {exc}",
        )
        return {}, True, None
    result.locked = len(entries)
    return entries, True, raw


def _parse_marker(raw: bytes) -> dict[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("projection is not valid UTF-8") from exc
    matches = [
        line
        for line in text.splitlines()
        if line.startswith(MARKER_PREFIX)
    ]
    if len(matches) != 1 or not matches[0].endswith(MARKER_SUFFIX):
        raise ValueError("projection must contain exactly one provenance marker")
    payload = matches[0][len(MARKER_PREFIX) : -len(MARKER_SUFFIX)]
    try:
        marker = _load_json_bytes(payload.encode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("projection provenance marker is malformed") from exc
    if (
        not isinstance(marker, dict)
        or set(marker) != _MARKER_KEYS
        or marker.get("schema") != PROJECTION_SCHEMA
        or marker.get("version") != PROJECTION_VERSION
    ):
        raise ValueError("projection provenance marker schema is unsupported")
    return marker


def _marker_matches_lock(
    marker: dict[str, object],
    entry: dict[str, object],
    actual_bytes: int,
) -> bool:
    for key in _LOCK_ENTRY_KEYS - {"renderedSha256"}:
        if marker.get(key) != entry.get(key):
            return False
    return marker.get("renderedBytes") == actual_bytes


def _validate_projection_file(
    repo_root: Path,
    entry: dict[str, object],
    result: Result,
) -> bytes | None:
    destination = str(entry["destination"])
    relative = PurePosixPath(destination)
    try:
        path = _safe_destination(repo_root, relative)
        if not path.exists():
            result.add(
                BLOCKING,
                "projection-missing",
                destination,
                "lock owns this destination but the projection file is missing",
            )
            return None
        raw = _read_bounded_regular(path, 256 * 1024)
        if b"\r" in raw or raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("projection must use deterministic UTF-8/LF bytes")
        marker = _parse_marker(raw)
        if not _marker_matches_lock(marker, entry, len(raw)):
            raise ValueError("projection marker does not match lock ownership")
        if _sha256(raw) != entry["renderedSha256"]:
            result.add(
                BLOCKING,
                "projection-local-modification",
                destination,
                "checked-in projection differs from its locked rendered digest",
            )
        if len(raw) != entry["renderedBytes"]:
            raise ValueError("projection byte count does not match the lock")
        forbidden = _forbidden_content(raw)
        if forbidden:
            result.add(
                BLOCKING,
                "projection-dynamic-content",
                destination,
                "projection contains forbidden dynamic content: "
                + ", ".join(forbidden),
            )
        if len(raw) > MAX_PROJECTION_BYTES:
            result.add(
                BLOCKING,
                "projection-budget",
                destination,
                f"projection exceeds the {MAX_PROJECTION_BYTES}-byte file budget",
            )
        return raw
    except (OSError, ValueError) as exc:
        result.add(
            BLOCKING,
            "projection-marker",
            destination,
            str(exc),
        )
        return None


def _iter_projection_files(repo_root: Path) -> Iterable[Path]:
    instructions = repo_root / ".github" / "instructions"
    if not instructions.is_dir() or _is_indirection(instructions):
        return ()
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(instructions):
        safe_dirs: list[str] = []
        for name in dirnames:
            candidate = Path(dirpath) / name
            if not _is_indirection(candidate):
                safe_dirs.append(name)
        dirnames[:] = safe_dirs
        for name in filenames:
            if name.endswith(".instructions.md"):
                found.append(Path(dirpath) / name)
    return found


def _scan_orphan_files(
    repo_root: Path,
    lock: dict[str, dict[str, object]],
    result: Result,
) -> None:
    for path in _iter_projection_files(repo_root):
        try:
            raw = _read_bounded_regular(path, 256 * 1024)
        except (OSError, ValueError):
            continue
        if MARKER_PREFIX.encode("ascii") not in raw:
            continue
        try:
            relative = path.resolve(strict=True).relative_to(
                repo_root.resolve(strict=True)
            ).as_posix()
        except (OSError, ValueError):
            continue
        if relative not in lock:
            try:
                _parse_marker(raw)
                message = (
                    "provenance-marked projection is not owned by the lock; "
                    "review and reconcile it manually"
                )
            except ValueError as exc:
                message = f"orphaned projection also has a malformed marker: {exc}"
            result.add(
                WARNING,
                "projection-orphan-file",
                relative,
                message,
            )


def _scan_legacy_regions(
    repo_root: Path,
    specs: list[ProjectionSpec],
    result: Result,
) -> None:
    marker_owners = {
        marker: spec.destination
        for spec in specs
        for marker in spec.legacy_markers
    }
    if not marker_owners:
        return
    candidates = list(repo_root.glob("**/AGENTS.md"))
    candidates.append(repo_root / ".github" / "copilot-instructions.md")
    for path in sorted(set(candidates)):
        if not path.is_file():
            continue
        try:
            relative = path.resolve(strict=True).relative_to(
                repo_root.resolve(strict=True)
            )
        except (OSError, ValueError):
            continue
        if any(part in {".git", "node_modules", ".venv", "dist", "build"} for part in relative.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker, destination in sorted(marker_owners.items()):
            start = f"<!-- {marker}:start -->"
            end = f"<!-- {marker}:end -->"
            starts = text.count(start)
            ends = text.count(end)
            if starts == 0 and ends == 0:
                continue
            if starts != 1 or ends != 1 or text.index(start) > text.index(end):
                result.add(
                    BLOCKING,
                    "projection-legacy-region",
                    relative.as_posix(),
                    f"legacy region {marker!r} is malformed; repair it before "
                    f"transitioning to {destination}",
                )
            else:
                result.add(
                    WARNING,
                    "projection-legacy-region",
                    relative.as_posix(),
                    f"legacy managed region {marker!r} remains alongside "
                    f"{destination}; review and remove it manually after the "
                    "projection is accepted",
                )


def _desired_entry_matches(
    spec: ProjectionSpec,
    entry: dict[str, object],
) -> bool:
    return all(
        (
            entry["sourceId"] == spec.source_id,
            entry["plugin"] == spec.plugin,
            entry["pluginVersion"] == spec.plugin_version,
            entry["template"] == spec.template,
            entry["templateSha256"] == spec.template_sha256,
            entry["templateBytes"] == spec.template_bytes,
            entry["destination"] == spec.destination,
            entry["customizationKind"] == spec.customization_kind,
            entry["applyTo"] == spec.apply_to,
        )
    )


def scan_repository(
    repo_root: Path,
    sources: Iterable[object] | None = None,
) -> Result:
    """Validate checked-in projections offline and optionally compare sources."""
    result = Result(operation="scan")
    try:
        root = _repository_root(repo_root)
    except ValueError as exc:
        result.add(BLOCKING, "projection-root", repo_root, str(exc))
        return result
    lock, _lock_exists, _lock_raw = _load_lock(root, result)
    total_bytes = 0
    for entry in lock.values():
        raw = _validate_projection_file(root, entry, result)
        if raw is not None:
            total_bytes += len(raw)
    if total_bytes > MAX_AGGREGATE_BYTES:
        result.add(
            BLOCKING,
            "projection-budget",
            LOCK_RELATIVE.as_posix(),
            f"projection aggregate is {total_bytes} bytes; budget is "
            f"{MAX_AGGREGATE_BYTES} bytes",
        )
    _scan_orphan_files(root, lock, result)

    if sources is not None:
        specs, unknown_plugins = _load_specs(root, sources, result)
        desired = {spec.destination: spec for spec in specs}
        _scan_legacy_regions(root, specs, result)
        for destination, spec in sorted(desired.items()):
            entry = lock.get(destination)
            path = root.joinpath(*PurePosixPath(destination).parts)
            if entry is None:
                if path.exists():
                    try:
                        marker = _parse_marker(
                            _read_bounded_regular(path, 256 * 1024)
                        )
                        owner = marker.get("plugin", "unknown")
                        message = (
                            f"destination exists with untracked ownership {owner!r}"
                        )
                    except (OSError, ValueError):
                        message = "declared destination exists but is not safely owned"
                    result.add(
                        BLOCKING,
                        "projection-ownership",
                        destination,
                        message,
                    )
                else:
                    result.add(
                        BLOCKING,
                        "projection-missing",
                        destination,
                        "enabled plugin declares this projection but it is not "
                        "checked in or locked; run projection sync",
                    )
                continue
            locked_key = f"{entry['plugin']}:{entry['sourceId']}"
            if locked_key != spec.source_key:
                result.add(
                    BLOCKING,
                    "projection-ownership",
                    destination,
                    f"destination is locked to {locked_key!r}, not "
                    f"{spec.source_key!r}",
                )
            elif not _desired_entry_matches(spec, entry):
                changed_fields = [
                    field
                    for field, desired_value in (
                        ("pluginVersion", spec.plugin_version),
                        ("template", spec.template),
                        ("templateSha256", spec.template_sha256),
                        ("templateBytes", spec.template_bytes),
                        ("customizationKind", spec.customization_kind),
                        ("applyTo", spec.apply_to),
                    )
                    if entry.get(field) != desired_value
                ]
                result.add(
                    WARNING,
                    "projection-source-update",
                    destination,
                    "current plugin source differs from the checked-in "
                    f"projection ({', '.join(changed_fields)} changed); run "
                    "projection sync and review the diff",
                )
        desired_keys = {spec.source_key for spec in specs}
        for destination, entry in sorted(lock.items()):
            source_key = f"{entry['plugin']}:{entry['sourceId']}"
            if (
                destination not in desired
                and source_key not in desired_keys
                and entry["plugin"] not in unknown_plugins
            ):
                result.add(
                    WARNING,
                    "projection-orphan-lock",
                    destination,
                    "lock entry is no longer declared by an available enabled "
                    "plugin; review it manually (scan never deletes files)",
                )
    return result


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _current_regular_bytes(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        raise OSError(f"{path.as_posix()} is no longer a regular file")
    return path.read_bytes()


def _transactional_write(
    changes: list[tuple[Path, bytes, bytes | None]],
) -> None:
    replaced: list[tuple[Path, bytes, bytes | None]] = []
    try:
        for path, content, expected_content in changes:
            if _current_regular_bytes(path) != expected_content:
                raise OSError(
                    f"{path.as_posix()} changed after validation"
                )
            _atomic_write(path, content)
            replaced.append((path, content, expected_content))
    except OSError as exc:
        rollback_errors: list[str] = []
        for path, written_content, previous_content in reversed(replaced):
            try:
                if _current_regular_bytes(path) != written_content:
                    raise OSError(
                        "destination changed after the transaction write"
                    )
                if previous_content is None:
                    path.unlink()
                else:
                    _atomic_write(path, previous_content)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path.as_posix()}: {rollback_exc}")
        detail = f"transaction failed at {path.as_posix()}: {exc}"
        if rollback_errors:
            detail += "; rollback also failed: " + "; ".join(rollback_errors)
        raise OSError(detail) from exc


def _lock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError("another projection sync is running") from exc
        return
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise BlockingIOError("another projection sync is running") from exc


def _unlock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _repository_sync_lock(repo_root: Path) -> Iterator[None]:
    lock_root = Path(tempfile.gettempdir()) / "copilot-instruction-projections"
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _is_indirection(lock_root) or not lock_root.is_dir():
        raise OSError("projection synchronization lock root is unsafe")
    lock_name = _sha256(str(repo_root).casefold().encode("utf-8")) + ".lock"
    lock_path = lock_root / lock_name
    with lock_path.open("a+b") as handle:
        _lock_handle(handle)
        try:
            yield
        finally:
            _unlock_handle(handle)


def _prepare_destination_parent(root: Path, relative: PurePosixPath) -> Path:
    path = _safe_destination(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    return _safe_destination(root, relative)


def sync_repository(repo_root: Path, sources: Iterable[object]) -> Result:
    """Safely create or update declared projections and their lock."""
    result = Result(operation="sync")
    try:
        root = _repository_root(repo_root)
    except ValueError as exc:
        result.add(BLOCKING, "projection-root", repo_root, str(exc))
        return result
    lock_context = _repository_sync_lock(root)
    try:
        lock_context.__enter__()
    except (BlockingIOError, OSError) as exc:
        result.add(
            BLOCKING,
            "projection-sync-lock",
            root,
            f"cannot acquire repository synchronization lock: {exc}",
        )
        return result
    try:
        return _sync_repository_locked(root, sources, result)
    finally:
        lock_context.__exit__(None, None, None)


def _sync_repository_locked(
    root: Path,
    sources: Iterable[object],
    result: Result,
) -> Result:
    lock, lock_exists, lock_preimage = _load_lock(root, result)
    specs, _unknown_plugins = _load_specs(root, sources, result)
    _scan_legacy_regions(root, specs, result)
    rendered: dict[str, RenderedProjection] = {}
    projection_preimages: dict[str, bytes | None] = {}

    for spec in specs:
        try:
            projection = render_projection(spec)
            _safe_destination(root, PurePosixPath(spec.destination))
        except ValueError as exc:
            result.add(
                BLOCKING,
                "projection-destination",
                spec.destination,
                str(exc),
            )
            continue
        if projection.byte_count > MAX_PROJECTION_BYTES:
            result.add(
                BLOCKING,
                "projection-budget",
                spec.destination,
                f"rendered projection is {projection.byte_count} bytes; budget "
                f"is {MAX_PROJECTION_BYTES} bytes",
            )
        rendered[spec.destination] = projection

        path = root.joinpath(*PurePosixPath(spec.destination).parts)
        existing_entry = lock.get(spec.destination)
        if path.exists():
            if not lock_exists or existing_entry is None:
                result.add(
                    BLOCKING,
                    "projection-ownership",
                    spec.destination,
                    "refusing to overwrite an existing destination without "
                    "matching lock ownership",
                )
                continue
            projection_preimages[spec.destination] = _validate_projection_file(
                root, existing_entry, result
            )
        else:
            projection_preimages[spec.destination] = None
        if existing_entry is not None:
            locked_key = (
                f"{existing_entry['plugin']}:{existing_entry['sourceId']}"
            )
            if locked_key != spec.source_key:
                result.add(
                    BLOCKING,
                    "projection-ownership",
                    spec.destination,
                    f"refusing to replace ownership {locked_key!r} with "
                    f"{spec.source_key!r}",
                )

    merged = dict(lock)
    for destination, projection in rendered.items():
        merged[destination] = projection.lock_entry()
    aggregate = sum(int(entry["renderedBytes"]) for entry in merged.values())
    if aggregate > MAX_AGGREGATE_BYTES:
        result.add(
            BLOCKING,
            "projection-budget",
            LOCK_RELATIVE.as_posix(),
            f"resulting projection aggregate is {aggregate} bytes; budget is "
            f"{MAX_AGGREGATE_BYTES} bytes",
        )
    if result.blocking:
        return result

    lock_payload = {
        "schema": LOCK_SCHEMA,
        "version": LOCK_VERSION,
        "projections": [
            merged[destination] for destination in sorted(merged)
        ],
    }
    lock_content = _canonical_json(lock_payload, pretty=True)
    try:
        lock_path = _prepare_destination_parent(root, LOCK_RELATIVE)
        projection_changes: list[tuple[Path, bytes, bytes | None]] = []
        changed_destinations: list[str] = []
        for destination, projection in sorted(rendered.items()):
            path = _prepare_destination_parent(root, PurePosixPath(destination))
            preimage = projection_preimages[destination]
            if preimage == projection.content:
                result.unchanged.append(destination)
                continue
            projection_changes.append((path, projection.content, preimage))
            changed_destinations.append(destination)
        lock_changed = lock_preimage != lock_content
        changes = list(projection_changes)
        if lock_changed:
            changes.append((lock_path, lock_content, lock_preimage))
        _transactional_write(changes)
    except (OSError, ValueError) as exc:
        result.unchanged.clear()
        result.add(
            BLOCKING,
            "projection-write",
            LOCK_RELATIVE.as_posix(),
            f"projection transaction failed; prior files were restored when "
            f"possible: {exc}",
        )
        return result
    result.changed.extend(changed_destinations)
    result.lock_updated = lock_changed
    result.locked = len(merged)
    return result


def validate_committed_settings(repo_root: Path) -> None:
    for relative in (
        PurePosixPath(".claude/settings.json"),
        PurePosixPath(".github/copilot/settings.json"),
    ):
        path = repo_root.joinpath(*relative.parts)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(f"{relative.as_posix()} is unreadable: {exc}") from exc
        try:
            _safe_existing_file(repo_root, relative)
            value = _load_json_bytes(
                _read_bounded_regular(path, MAX_DECLARATION_BYTES)
            )
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"{relative.as_posix()} is malformed or unsafe: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(f"{relative.as_posix()} must contain a JSON object")
        enabled = value.get("enabledPlugins")
        if enabled is not None and (
            not isinstance(enabled, dict)
            or any(
                not isinstance(key, str) or not isinstance(active, bool)
                for key, active in enabled.items()
            )
        ):
            raise ValueError(
                f"{relative.as_posix()} enabledPlugins must map strings to booleans"
            )
        marketplaces = value.get("extraKnownMarketplaces")
        if marketplaces is not None and (
            not isinstance(marketplaces, dict)
            or any(
                not isinstance(key, str) or not isinstance(entry, dict)
                for key, entry in marketplaces.items()
            )
        ):
            raise ValueError(
                f"{relative.as_posix()} extraKnownMarketplaces must map "
                "strings to objects"
            )


def discover_enabled_sources(
    repo_root: Path,
    *,
    installed_root: Path | None = None,
    home: Path | None = None,
    require_trust: bool = False,
) -> list[object]:
    """Reuse the customization scanner's existing settings/source resolver."""
    if not require_trust:
        validate_committed_settings(repo_root)
    scanner_path = Path(__file__).with_name("scan-customizations.py")
    module_name = "_instruction_projection_scanner_support"
    scanner = sys.modules.get(module_name)
    if scanner is None:
        spec = importlib.util.spec_from_file_location(module_name, scanner_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load customization scanner")
        scanner = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = scanner
        spec.loader.exec_module(scanner)
    return scanner.assemble_enabled_plugins(
        repo_root,
        installed_root=installed_root,
        home=home,
        require_trust=require_trust,
        include_user=False,
        include_local=False,
    )


__all__ = [
    "BLOCKING",
    "WARNING",
    "DECLARATION_SCHEMA",
    "DECLARATION_VERSION",
    "LOCK_SCHEMA",
    "LOCK_VERSION",
    "MAX_AGGREGATE_BYTES",
    "MAX_PROJECTION_BYTES",
    "Finding",
    "ProjectionSpec",
    "RenderedProjection",
    "Result",
    "discover_enabled_sources",
    "render_projection",
    "scan_repository",
    "sync_repository",
    "validate_committed_settings",
    "validate_repository_root",
]
