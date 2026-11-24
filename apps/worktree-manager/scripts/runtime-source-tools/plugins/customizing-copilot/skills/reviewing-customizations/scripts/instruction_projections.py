"""Declarative static instruction projection management.

Plugins may ship ``instruction-projections.json`` at the payload root beside a
supported plugin manifest. This module validates those inert declarations,
renders provenance-marked repository instruction files, and maintains the
checked-in projection lock. It never executes plugin code, and never removes
a checked-in projection or the lock itself -- ``render_local_cache()`` is the
one exception: it may reconcile away an *owned*, gitignored, worktree-local
``*.local.instructions.md`` body that no longer matches a declared
source, or retire a verified untracked legacy local after new publication
(see ``docs/patterns/worktree-scoped-dynamic-guidance.md``),
never a checked-in file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterable, Iterator

import instruction_delivery as delivery
import instruction_delivery_io as delivery_io
import projection_reflect_consent
import instruction_projection_transaction as transaction

BLOCKING = "blocking"
WARNING = "warning"

DECLARATION_SCHEMA = "copilot-extensions.instruction-projections"
DECLARATION_VERSION = 1
PROJECTION_SCHEMA = "copilot-extensions.instruction-projection"
PROJECTION_VERSION = 1
LOCK_SCHEMA = "copilot-extensions.context-projections"
LOCK_VERSION = 2
RESULT_SCHEMA = "copilot-extensions.instruction-projection-result"
RESULT_VERSION = 1

DECLARATION_FILE = "instruction-projections.json"
LOCK_RELATIVE = PurePosixPath(".github/copilot/context-projections.json")
CONFIG_RELATIVE = PurePosixPath(".github/copilot/instruction-projections.config.json")
CONFIG_SCHEMA = "copilot-extensions.instruction-projections-config"
CONFIG_VERSION = 1
MARKER_PREFIX = "<!-- copilot-extension-instruction-projection "
MARKER_SUFFIX = " -->"
MAX_DECLARATION_BYTES = 16 * 1024
MAX_PROJECTIONS_PER_PLUGIN = 16
MAX_TEMPLATE_BYTES = 4 * 1024
MAX_PROJECTION_BYTES = 4 * 1024
MAX_LOCAL_TEMPLATE_BYTES = 1024 * 1024
MAX_LOCAL_CACHE_BYTES = 2 * MAX_LOCAL_TEMPLATE_BYTES
MAX_AGGREGATE_BYTES = 12 * 1024
MIN_AGGREGATE_BYTES = MAX_PROJECTION_BYTES
MAX_AGGREGATE_BYTES_CEILING = 64 * 1024
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
_LEGACY_SCAN_EXCLUDED_DIRS = {
    ".git",
    ".test-venvs",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
    "vendors",
}


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
    skip_local_cache: bool = False
    delivery_mode: str = "inline"
    delivery_declared: bool = False

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
    fallback_content: bytes | None = None

    def lock_entry(self) -> dict[str, object]:
        entry = {
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
        if self.fallback_content is not None:
            entry.update(
                deliveryMode="selector",
                fallback={
                    "destination": delivery.fallback_destination(self.spec.destination),
                    "renderedSha256": _sha256(self.fallback_content),
                    "renderedBytes": len(self.fallback_content),
                },
            )
        return entry


@dataclass
class Result:
    operation: str
    findings: list[Finding] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    lock_updated: bool = False
    declared: int = 0
    locked: int = 0
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

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
            "written": sorted(self.written),
            "removed": sorted(self.removed),
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
        if current != root.joinpath(*relative.parts) and not stat.S_ISDIR(info.st_mode):
            raise ValueError("destination parent is not a directory")
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
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
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
    value = delivery.decode_scope(fields["applyTo"])
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


def _declares_projections(payload_root: Path) -> bool:
    """Return whether a payload root has a projection declaration present.

    Uses a bare ``lstat`` (not ``_safe_existing_file``): this only decides
    whether the plugin is *participating* at all, before its manifest or
    declaration content is trusted/read. A ``payload_root`` that does not
    exist on disk simply reports no declaration, which is the common case for
    an enabled plugin this scan cannot resolve a source for.
    """
    try:
        (payload_root / DECLARATION_FILE).lstat()
    except OSError:
        return False
    return True


def _load_specs(
    repo_root: Path,
    sources: Iterable[object],
    result: Result,
    *,
    locked_plugins: frozenset[str] = frozenset(),
    template_limit: int = MAX_TEMPLATE_BYTES,
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
        manifest_path: Path | None = None
        manifest: object = None
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
                manifest_path = candidate
                manifest = candidate_manifest
                break
        if manifest_path is None:
            # An enabled plugin whose manifest can't be resolved only matters
            # to the projection stack if it actually participates: either it
            # visibly declares a projection at this payload root, or it is
            # already a locked participant (its source going unavailable is a
            # regression -- the projection can no longer be verified or
            # regenerated). Otherwise this plugin simply isn't part of the
            # instruction-projections system (a common, benign case for a
            # plugin whose source this settings-derived scan cannot resolve,
            # e.g. a directory-marketplace mapping declared only in a
            # settings layer this scan intentionally excludes for
            # reproducibility), so it is skipped rather than blocked.
            if _declares_projections(payload_root) or plugin_identity in locked_plugins:
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
            or type(declaration.get("version")) is not int
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
                _required_keys = {
                    "id",
                    "template",
                    "destination",
                    "customizationKind",
                    "applyTo",
                    "legacyMarkers",
                }
                _optional_keys = {"skipLocalCache", "deliveryMode"}
                if (
                    not isinstance(entry, dict)
                    or not _required_keys <= set(entry)
                    or not set(entry) - _required_keys <= _optional_keys
                ):
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
                if destination_rel.name.endswith(_LOCAL_CACHE_SUFFIX):
                    # Reserved for render_local_cache()'s own gitignored
                    # siblings (docs/patterns/worktree-scoped-dynamic-
                    # guidance.md) -- a checked-in destination can never
                    # legitimately claim this suffix. Rejecting it here,
                    # not just in local_sibling_destination(), keeps the
                    # checked-in orphan scan's *.local.instructions.md
                    # exclusion exhaustively safe: no genuinely-declared
                    # checked-in file can ever be misclassified as a
                    # render-only cache and skip orphan detection.
                    raise ValueError(
                        f"destination must not end in {_LOCAL_CACHE_SUFFIX!r} "
                        "(reserved for the local-cache mechanism)"
                    )
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
                skip_local_cache = entry.get("skipLocalCache", False)
                if not isinstance(skip_local_cache, bool):
                    raise ValueError("skipLocalCache must be a boolean")
                delivery_mode = entry.get("deliveryMode", "inline")
                if not isinstance(delivery_mode, str) or delivery_mode not in {"inline", "selector"}:
                    raise ValueError("deliveryMode must be inline or selector")
                # Legacy declarations remain readable, but new full fallbacks are never inline.
                delivery_mode = "selector"
                template_path = _safe_existing_file(payload_root, template_rel)
                template_raw = _canonical_template_bytes(
                    _read_bounded_regular(template_path, template_limit)
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
                    skip_local_cache=skip_local_cache,
                    delivery_mode=delivery_mode,
                    delivery_declared="deliveryMode" in entry,
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


def render_projection(
    spec: ProjectionSpec, *, include_prefer_local: bool = True
) -> RenderedProjection:
    """Render a declared inline kernel or local-first selector/body pair."""
    content, marker, fallback_content = delivery.render_projection(
        spec, include_prefer_local
    )
    return RenderedProjection(
        spec=spec,
        content=content,
        sha256=_sha256(content),
        byte_count=len(content),
        marker=marker,
        fallback_content=fallback_content,
    )


_LOCAL_CACHE_SUFFIX = ".local.instructions.md"
_INSTRUCTIONS_SUFFIX = ".instructions.md"


def local_sibling_destination(destination: str) -> str:
    """Return the derived auto-discovered local instruction sibling path."""
    return delivery.local_destination(destination)


def render_local_cache(
    repo_root: Path, discover_sources: Callable[[], Iterable[object]]
) -> Result:
    """Compatibility publisher: copy current enabled templates on every call.

    Shares the managed startup publisher's workspace lease and reserved output
    namespace. Discovery happens under that lease. No instruction rendering,
    hashing, receipt validation or unchanged-content reuse occurs. Git-tracked
    files, path indirection and ambiguous declarations remain protected.
    """
    result = Result(operation="render-local-cache")
    try:
        root = _repository_root(repo_root)
    except ValueError as exc:
        result.add(BLOCKING, "projection-root", repo_root, str(exc))
        return result
    lock_context = _local_cache_lock(root)
    try:
        lock_context.__enter__()
    except (BlockingIOError, OSError) as exc:
        result.add(
            BLOCKING,
            "projection-local-cache-lock",
            root,
            f"cannot acquire local-cache synchronization lock: {exc}",
        )
        return result
    try:
        # Resolved only now, under the held lock -- never before it (see
        # the docstring). A source list computed by the caller before
        # acquiring the lock would let an older, slower call still act on
        # stale information after a newer call already published fresher
        # content and released the lock. discover_sources may itself
        # raise (e.g. discover_enabled_sources -> validate_committed_
        # settings on malformed repo settings) -- that must report as a
        # blocking finding, not crash this best-effort call out from under
        # a worktree-create/resume or sessionStart caller.
        try:
            import instruction_copies

            instruction_copies.require_pair_complete(root, _delivery_io())
            sources = list(discover_sources())
        except (OSError, ValueError) as exc:
            result.add(
                BLOCKING,
                "projection-local-cache-discovery",
                root,
                f"could not discover enabled sources: {exc}",
            )
            return result
        return _render_local_cache_locked(root, sources, result)
    finally:
        lock_context.__exit__(None, None, None)


@contextmanager
def _local_cache_lock(repo_root: Path) -> Iterator[None]:
    lock_root = Path(tempfile.gettempdir()) / "agent-worktrees" / "static-instructions"
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for candidate in (lock_root.parent, lock_root):
        if _is_indirection(candidate) or not candidate.is_dir():
            raise OSError("local cache lock root is unsafe")
    # This digest names a workspace, never instruction contents.
    lock_name = hashlib.sha256(
        os.fsencode(unicodedata.normalize("NFC", str(repo_root)).casefold())
    ).hexdigest() + ".lock"
    lock_path = lock_root / lock_name
    if _is_indirection(lock_path):
        raise OSError("local cache lock path is unsafe")
    if lock_path.exists() and not stat.S_ISREG(lock_path.lstat().st_mode):
        raise OSError("local cache lock path is not a regular file")
    with lock_path.open("a+b") as handle:
        _lock_handle(handle)
        try:
            yield
        finally:
            _unlock_handle(handle)


def _render_local_cache_locked(
    root: Path, sources: Iterable[object], result: Result
) -> Result:
    import instruction_copies

    declarations = instruction_copies.load_declarations(
        sources, _delivery_io(), workspace=root,
        validate_path=_validate_relative, load_json=_load_json_bytes,
    )
    result.declared = declarations.declared
    for message in declarations.failures.values():
        result.add(BLOCKING, "projection-declaration", "<plugin-stack>", message)
    grouped = {
        unicodedata.normalize("NFC", copy.local_destination).casefold(): copy
        for copy in declarations.copies
    }
    desired = {copy.local_destination for copy in declarations.copies}
    existing = list(_iter_local_cache_files(root))
    existing_relatives = {path.relative_to(root).as_posix() for path in existing}

    def probe(paths: list[str]) -> tuple[set[str], bool]:
        keys = {unicodedata.normalize("NFC", path).casefold() for path in paths}
        aliases = {
            path for path in existing_relatives
            if unicodedata.normalize("NFC", path).casefold() in keys
        }
        tracked, unknown = _resolve_git_tracked_paths(root, sorted(set(paths) | aliases))
        tracked_keys = {unicodedata.normalize("NFC", path).casefold() for path in tracked}
        return tracked | {
            path for path in paths
            if unicodedata.normalize("NFC", path).casefold() in tracked_keys
        }, unknown

    tracked, inconclusive = probe(
        sorted(desired | existing_relatives),
    )
    ignored = delivery_io.ignored_local_paths(root, sorted(desired))
    aggregate = 0
    discovery_incomplete = bool(declarations.failures)
    incomplete = discovery_incomplete
    for copy in declarations.copies:
        relative = copy.local_destination
        try:
            raw = _read_bounded_regular(copy.template, MAX_LOCAL_TEMPLATE_BYTES)
            aggregate += len(raw)
            for budget in (MAX_TEMPLATE_BYTES, MAX_PROJECTION_BYTES):
                if len(raw) > budget:
                    result.add(WARNING, "projection-local-cache-budget", relative,
                               f"instruction copy is {len(raw)} bytes; budget is {budget} bytes")
            if relative in tracked or inconclusive:
                incomplete = True
                result.add(BLOCKING, "projection-local-cache-tracked", relative,
                           "destination is tracked by git or tracking status is unknown")
                continue
            if relative not in ignored:
                incomplete = True
                result.add(WARNING, "projection-local-cache-ignore", relative,
                           "local instructions are not ignored by git; adopt *.local.instructions.md ignore coverage first")
                continue
            delivery_io.require_untracked(relative, probe)
            path = _prepare_destination_parent(root, PurePosixPath(relative))
            try:
                info = path.lstat()
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("destination is not a regular file")
                if discovery_incomplete:
                    raise ValueError("partial discovery cannot reassign an existing generated slot")
            transaction.atomic_write(path, raw)
            result.changed.append(relative)
            result.written.append(relative)
        except (OSError, ValueError) as exc:
            incomplete = True
            result.add(BLOCKING, "projection-local-cache", relative,
                       f"could not write local cache: {exc}")
    budget = _load_aggregate_budget(root, result, severity=WARNING)
    if aggregate > budget:
        result.add(WARNING, "projection-local-cache-budget", "<local-cache-aggregate>",
                   f"local cache aggregate is {aggregate} bytes; budget is {budget} bytes")
    for path in existing:
        relative = path.relative_to(root).as_posix()
        if not relative.startswith(delivery.MANAGED_NAMESPACE + "/"):
            result.add(WARNING, "projection-local-cache-legacy", relative,
                       "outside the managed copy namespace; retained for explicit migration")
            continue
        if relative in desired:
            continue
        # A failed provider's old files remain available, with a visible failure.
        if incomplete:
            continue
        if relative in tracked or inconclusive:
            result.add(WARNING, "projection-local-cache-tracked", relative,
                       "stale output is tracked by git or tracking status is unknown; retained")
            continue
        try:
            path = _safe_destination(root, PurePosixPath(relative))
            key = unicodedata.normalize("NFC", relative).casefold()
            copy = grouped.get(key)
            if copy is not None:
                target = _safe_destination(root, PurePosixPath(copy.local_destination))
                try:
                    same_file = path.samefile(target)
                except FileNotFoundError:
                    same_file = False
            else:
                same_file = False
            if same_file:
                continue
            delivery_io.require_untracked(relative, probe)
            _safe_destination(root, PurePosixPath(relative)).unlink()
            result.changed.append(relative)
            result.removed.append(relative)
        except (OSError, ValueError) as exc:
            result.add(BLOCKING, "projection-local-cache-stale", relative,
                       f"could not remove stale local cache: {exc}")
    delivery_io.report_unmanaged_legacy_locals(root, set(), _delivery_io(), result)
    return result


def _git_isolated_env() -> dict[str, str]:
    """A subprocess environment with every ``GIT_*`` variable stripped.

    An inherited ``GIT_DIR``/``GIT_INDEX_FILE``/``GIT_WORK_TREE`` overrides
    ``-C <path>`` -- this probe must always query the repository it was
    actually pointed at, never one an ambient caller environment silently
    redirects it to. Mirrors ``scan_plugin_sources.py``'s own
    ``_git_isolated_env``.
    """
    return {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    } | {"LC_ALL": "C", "LANG": "C"}


def _resolve_git_tracked_paths(
    root: Path, relatives: list[str]
) -> tuple[set[str], bool]:
    """Best-effort, batched, tri-state git-tracked lookup for every path in
    ``relatives`` at once (one process pair per call, not one per file).

    Returns ``(tracked, inconclusive)``:

    * ``inconclusive`` is ``False`` and ``tracked`` is empty only when
      ``root`` is *confirmed* not a git working tree: no ``git`` binary
      (``FileNotFoundError``), or a completed ``rev-parse`` probe reporting
      so. This is the documented plain-directory case (the render-only
      path works with no ``.git`` at all) and behaves as if no tracking
      concept applied.
    * ``inconclusive`` is ``True`` whenever that can't be confirmed either
      way: the ``rev-parse`` probe itself failed to complete (timeout, any
      other subprocess error besides a missing binary), or ``root`` *is* a
      confirmed git working tree but the tracked-files query failed. The
      caller must treat every path in the batch as un-confirmable -- never
      assume "untracked"/"not applicable" merely because a check broke.
    * Otherwise ``tracked`` is the exact subset of ``relatives`` git
      currently tracks.

    Uses ``--literal-pathspecs`` (never glob-interprets pathspec magic
    characters) and a git-isolated environment (:func:`_git_isolated_env`,
    so an inherited ``GIT_DIR``/``GIT_INDEX_FILE`` can't redirect the query).
    """
    if not relatives:
        return set(), False
    env = _git_isolated_env()
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            timeout=5,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return set(), any((p / ".git").exists() for p in [root, *root.parents])
    except (OSError, subprocess.SubprocessError):
        return set(), True
    if probe.returncode != 0:
        return set(), b"not a git repository" not in probe.stderr.lower()
    if probe.stdout.strip() != b"true":
        return set(), True
    # From here on, root IS a git working tree -- any further failure is
    # inconclusive, never "confirmed untracked".
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "--literal-pathspecs",
                "ls-files",
                "-z",
                "--",
                *relatives,
            ],
            capture_output=True,
            timeout=5,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        removed = subprocess.run(
            ["git", "-C", str(root), "--literal-pathspecs", "diff", "--cached",
             "--name-only", "--diff-filter=D", "--no-renames", "-z", "--", *relatives],
            capture_output=True, timeout=5, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return set(), True
    if proc.returncode != 0 or removed.returncode != 0:
        return set(), True
    tracked = {
        entry
        for entry in (proc.stdout + removed.stdout).decode("utf-8", "surrogateescape").split("\0")
        if entry
    }
    return tracked, False


def _read_existing_local_cache(path: Path) -> bytes | None:
    """Read an existing cache safely, returning None only if absent.

    Reads use the local safety bound, not the guidance audit budget, so owned
    oversized renders remain refreshable. Nonregular/indirected files and files
    above MAX_LOCAL_CACHE_BYTES are refused without loading them wholesale.
    """
    try:
        return _read_bounded_regular(path, MAX_LOCAL_CACHE_BYTES)
    except FileNotFoundError:
        return None


def _parse_owned_local_cache_marker(
    raw: bytes, relative: str
) -> dict[str, object] | None:
    """Return the parsed provenance marker if ``raw`` is a local-cache file
    this renderer legitimately owns *at path* ``relative``, else ``None``.

    Ownership here is purely path-based: ``raw`` must carry exactly one
    well-formed ``render_projection`` marker whose own recorded
    ``destination`` (the checked-in path it was rendered from) converts,
    via :func:`local_sibling_destination`, to exactly ``relative`` -- proving
    this file is this renderer's own output sitting where it belongs, never
    a foreign file that merely matches the naming convention, and never a
    copy of a *different* projection's marker placed at the wrong
    local-cache path. This does not compare *which* plugin/sourceId the
    marker names -- a caller that must distinguish a legitimate source
    handoff from stale content does that separately (see the write loop
    above); stale-cache reconciliation deliberately stays path-based only.
    """
    if MARKER_PREFIX.encode("ascii") not in raw:
        return None
    try:
        marker = _parse_marker(raw)
        delivery.validate_local_template(raw, marker)
        if marker.get("deliveryKind") == "body" and not delivery.complete_body(raw, marker):
            raise ValueError("incomplete inline body envelope")
    except ValueError:
        return None
    marker_destination = marker.get("destination")
    if not isinstance(marker_destination, str):
        return None
    try:
        if local_sibling_destination(marker_destination) != relative:
            return None
    except ValueError:
        return None
    return marker


def _iter_local_cache_files(repo_root: Path) -> Iterable[Path]:
    return delivery_io.iter_local_files(repo_root, _delivery_io())


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
    return delivery.validate_lock_entry(entry, _validate_relative, MAX_TEMPLATE_BYTES)


def _load_aggregate_budget(
    repo_root: Path, result: Result, *, severity: str = BLOCKING
) -> int:
    """Resolve the effective aggregate projection budget for this repository.

    A small, strictly validated checked-in config can override the default
    MAX_AGGREGATE_BYTES. Invalid/unreadable config retains that default and
    reports at the requested severity: blocking for scan/sync, warning for
    local delivery. Config validity never gates local content.
    """
    config_path = repo_root.joinpath(*CONFIG_RELATIVE.parts)
    try:
        config_path.lstat()
    except FileNotFoundError:
        return MAX_AGGREGATE_BYTES
    except OSError as exc:
        result.add(
            severity,
            "projection-config",
            CONFIG_RELATIVE.as_posix(),
            f"projection config is unreadable: {exc}",
        )
        return MAX_AGGREGATE_BYTES
    try:
        _safe_destination(repo_root, CONFIG_RELATIVE)
        raw = _read_bounded_regular(config_path, 4 * 1024)
        value = _load_json_bytes(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "version", "maxAggregateBytes"}
            or value.get("schema") != CONFIG_SCHEMA
            or value.get("version") != CONFIG_VERSION
        ):
            raise ValueError("config schema/version/shape is unsupported")
        budget = value["maxAggregateBytes"]
        if (
            not isinstance(budget, int)
            or isinstance(budget, bool)
            or not (MIN_AGGREGATE_BYTES <= budget <= MAX_AGGREGATE_BYTES_CEILING)
        ):
            raise ValueError(
                "maxAggregateBytes must be an integer between "
                f"{MIN_AGGREGATE_BYTES} and {MAX_AGGREGATE_BYTES_CEILING}"
            )
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        result.add(
            severity,
            "projection-config",
            CONFIG_RELATIVE.as_posix(),
            f"projection config is invalid, using default budget: {exc}",
        )
        return MAX_AGGREGATE_BYTES
    return budget


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
            or type(value.get("version")) is not int
            or value.get("version") not in (1, LOCK_VERSION)
            or not isinstance(value.get("projections"), list)
        ):
            raise ValueError("lock schema/version/shape is unsupported")
        entries: dict[str, dict[str, object]] = {}
        portable_destinations: set[str] = set()
        sources: set[str] = set()
        for raw_entry in value["projections"]:
            if value["version"] == 1 and isinstance(raw_entry, dict) and "deliveryMode" in raw_entry:
                raise ValueError("legacy lock cannot own selector/fallback triples")
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
                "version": value["version"],
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


def load_lock_entries(repo_root: Path) -> dict[str, dict[str, object]]:
    """Safely read the current lock's entries, keyed by destination.

    Reuses ``_load_lock``'s bounded, path-safe reader (symlink/reparse
    rejection, the size bound, and full entry validation) instead of a
    caller's own ad hoc read. Returns ``{}`` -- never raises -- for every
    refusal case; a caller needing findings should call
    ``scan_repository``/``sync_repository`` directly.
    """
    result = Result(operation="lock-read")
    entries, _lock_exists, _raw = _load_lock(repo_root, result)
    return entries


_parse_marker = delivery.parse_marker


def _marker_matches_lock(
    marker: dict[str, object],
    entry: dict[str, object],
    actual_bytes: int,
    *,
    allow_content_edits: bool = False,
) -> bool:
    for key in _LOCK_ENTRY_KEYS - {"renderedSha256"}:
        if marker.get("version") == 2 and key not in delivery.SELECTOR_KEYS:
            continue
        if marker.get(key) != entry.get(key):
            return False
    return allow_content_edits or marker.get("renderedBytes") == actual_bytes


def _validate_projection_file(
    repo_root: Path,
    entry: dict[str, object],
    result: Result,
    *,
    allow_content_edits: bool = False,
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
        if entry.get("deliveryMode") == "selector" and marker.get("deliveryKind") != "selector":
            raise ValueError("locked selector is not a selector")
        if _frontmatter_apply_to(raw) != entry["applyTo"]:
            raise ValueError("projection frontmatter scope does not match lock")
        if not allow_content_edits and marker.get("deliveryKind") == "body" and not delivery.complete_body(raw, marker):
            raise ValueError("incomplete inline body envelope")
        if not _marker_matches_lock(
            marker, entry, len(raw), allow_content_edits=allow_content_edits
        ):
            raise ValueError("projection marker does not match lock ownership")
        if not allow_content_edits and _sha256(raw) != entry["renderedSha256"]:
            result.add(
                BLOCKING,
                "projection-local-modification",
                destination,
                "checked-in projection differs from its locked rendered digest",
            )
        if not allow_content_edits and len(raw) != entry["renderedBytes"]:
            raise ValueError("projection byte count does not match the lock")
        forbidden = _forbidden_content(raw)
        if forbidden and not allow_content_edits:
            result.add(
                BLOCKING,
                "projection-dynamic-content",
                destination,
                "projection contains forbidden dynamic content: "
                + ", ".join(forbidden),
            )
        if len(raw) > MAX_PROJECTION_BYTES and not allow_content_edits:
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


def _delivery_io() -> delivery_io.DeliveryIO:
    return delivery_io.DeliveryIO(_safe_destination, _read_bounded_regular, _is_indirection)


def _validate_fallback(root: Path, entry: dict, result: Result) -> bytes | None:
    try:
        return delivery_io.read_fallback(root, entry, _delivery_io())
    except (OSError, ValueError) as exc:
        result.add(BLOCKING, "projection-fallback", entry["destination"], str(exc))
        return None


def resolve_instruction_source(
    root: Path, destination: str, sources: Iterable[object] | None = None
) -> dict:
    import instruction_copies

    root = validate_repository_root(root)
    relative = _validate_relative(destination, field_name="selector destination")
    declarations = instruction_copies.load_declarations(
        sources or [], _delivery_io(), workspace=root,
        validate_path=_validate_relative, load_json=_load_json_bytes,
    )
    selected = instruction_copies.select_copy(root, destination, declarations, _delivery_io())
    if selected is not None:
        return selected
    _safe_destination(root, relative)
    result = Result(operation="resolve-source")
    entries, _, _ = _load_lock(root, result)
    if result.blocking:
        raise ValueError("missing or invalid reviewed ownership lock")
    if destination in entries:
        _validate_projection_file(root, entries[destination], result)
    if result.blocking:
        raise ValueError("reviewed selector validation failed")
    if destination not in entries:
        detail = "; ".join(declarations.failures.values())
        raise ValueError(f"no managed copy or reviewed fallback for this destination; {detail}")
    selected = delivery_io.resolve_reviewed(root, entries[destination], _delivery_io())
    if declarations.failures:
        selected["diagnostic"] = "; ".join(declarations.failures.values())
    return selected


def instruction_delivery_inventory(root: Path, automatic: set[Path]) -> dict:
    result = Result(operation="delivery-inventory")
    entries, _, _ = _load_lock(root, result)
    if result.blocking:
        raise ValueError("; ".join(finding.message for finding in result.findings))
    return delivery_io.inventory(root, automatic, entries, _delivery_io())


def _iter_projection_files(repo_root: Path) -> Iterable[Path]:
    """Yield checked-in ``*.instructions.md`` files for the orphan scan. A
    ``*.local.instructions.md`` file is excluded unless
    :func:`_resolve_git_tracked_paths` reports it tracked or inconclusive,
    including the plain-directory case where tracking is inapplicable."""
    instructions = repo_root / ".github" / "instructions"
    if not instructions.is_dir() or _is_indirection(instructions):
        return ()
    found: list[Path] = []
    local_cache: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(instructions):
        dirnames[:] = [d for d in dirnames if not _is_indirection(Path(dirpath) / d)]
        for name in filenames:
            if not name.endswith(_INSTRUCTIONS_SUFFIX):
                continue
            path = Path(dirpath) / name
            if not name.endswith(_LOCAL_CACHE_SUFFIX):
                found.append(path)
                continue
            try:
                local_cache[path.relative_to(repo_root).as_posix()] = path
            except ValueError:
                pass
    tracked, inconclusive = _resolve_git_tracked_paths(repo_root, list(local_cache))
    return found + [p for r, p in local_cache.items() if inconclusive or r in tracked]


def _scan_orphan_files(
    repo_root: Path,
    lock: dict[str, dict[str, object]],
    result: Result,
) -> None:
    try:
        for path in delivery_io.orphan_fallbacks(repo_root, lock, _delivery_io()):
            result.add(WARNING, "projection-orphan-fallback", path, "fallback is not locked")
    except (OSError, ValueError) as exc:
        result.add(BLOCKING, "projection-fallback", "<fallbacks>", str(exc))
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
    candidates: list[Path] = []
    for current, directories, filenames in os.walk(
        repo_root, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        directories[:] = [
            directory
            for directory in directories
            if directory.casefold() not in _LEGACY_SCAN_EXCLUDED_DIRS
            and not _is_indirection(current_path / directory)
        ]
        if "AGENTS.md" in filenames:
            candidates.append(current_path / "AGENTS.md")
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
            entry.get("deliveryMode", "inline") == spec.delivery_mode,
            entry["renderedSha256"] == render_projection(spec).sha256,
        )
    )


def scan_repository(
    repo_root: Path,
    sources: Iterable[object] | None = None,
    *, admission_sources: Iterable[object] | None = None,
) -> Result:
    """Validate checked-in projections offline and optionally compare sources."""
    result = Result(operation="scan")
    try:
        root = _repository_root(repo_root)
    except ValueError as exc:
        result.add(BLOCKING, "projection-root", repo_root, str(exc))
        return result
    lock, _lock_exists, _lock_raw = _load_lock(root, result)
    aggregate_budget = _load_aggregate_budget(root, result)
    total_bytes = 0
    for entry in lock.values():
        raw = _validate_projection_file(root, entry, result)
        _validate_fallback(root, entry, result)
        if raw is not None:
            total_bytes += len(raw)
    if total_bytes > aggregate_budget:
        result.add(
            WARNING,
            "projection-aggregate-budget",
            LOCK_RELATIVE.as_posix(),
            f"projection aggregate is {total_bytes} bytes; budget is "
            f"{aggregate_budget} bytes (over by {total_bytes - aggregate_budget} bytes)",
        )
    _scan_orphan_files(root, lock, result)

    if sources is not None:
        locked_plugins = frozenset(
            str(entry.get("plugin", "")) for entry in lock.values()
        )
        specs, unknown_plugins = _load_specs(
            root, sources, result, locked_plugins=locked_plugins
        )
        desired = {spec.destination: spec for spec in specs}
        admission = desired
        if admission_sources is not None:
            trusted_result = Result(operation="trusted-admission")
            trusted_specs, _ = _load_specs(root, admission_sources, trusted_result)
            result.findings.extend(trusted_result.findings)
            admission = {spec.destination: spec for spec in trusted_specs}
        _scan_legacy_regions(root, specs, result)
        for destination, spec in sorted(desired.items()):
            try:
                render_projection(spec)
            except ValueError as exc:
                result.add(BLOCKING, "projection-declaration", destination, str(exc))
                continue
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
                    trusted_spec = admission.get(destination)
                    admitted, reason = (False, "missing trusted canonical guidance delivery")
                    if trusted_spec is not None:
                        admitted, reason = delivery_io.new_source_admission(root, trusted_spec, _delivery_io())
                    result.add(
                        WARNING if admitted else BLOCKING,
                        "projection-source-update" if admitted else "projection-missing",
                        destination, reason,
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
                        ("deliveryMode", spec.delivery_mode),
                        ("renderedSha256", render_projection(spec).sha256),
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
    transaction.atomic_write(path, content)


def _current_regular_bytes(path: Path) -> bytes | None:
    return transaction.current_regular_bytes(path, _is_reparse)


def _transactional_write(
    changes: list[tuple[Path, bytes | None, bytes | None]],
    *,
    before_write: Callable[[], None] | None = None,
    validate_path: Callable[[Path], None] | None = None,
    after_write: Callable[[], None] | None = None,
) -> None:
    transaction.write(
        changes, read=_current_regular_bytes, atomic_write=_atomic_write,
        before_write=before_write, validate_path=validate_path, after_write=after_write,
    )


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


def repository_sync_lock(repo_root: Path) -> Iterator[None]:
    """Public accessor for ``sync_repository``'s own per-repository lock.

    For a caller that must hold it across its own sync + scan + lock-read
    sequence to stay atomic against a concurrent worker -- pair with
    ``sync_repository_locked`` (never plain ``sync_repository``, which
    would deadlock re-acquiring the same lock). ``repo_root`` must already
    be resolved (see ``validate_repository_root``).
    """
    return _repository_sync_lock(repo_root)


def sync_repository_locked(
    repo_root: Path, sources: Iterable[object], *,
    overwrite_existing_managed: bool = False,
) -> Result:
    """Like ``sync_repository``, but assumes the caller already holds the
    lock from ``repository_sync_lock`` -- use only inside a ``with
    repository_sync_lock(root):`` block, on the same already-resolved root.
    """
    return _sync_repository_locked(
        repo_root, list(sources), Result(operation="sync"),
        overwrite_existing_managed=overwrite_existing_managed,
    )


def sync_repository(
    repo_root: Path, sources: Iterable[object], *,
    overwrite_existing_managed: bool = False,
) -> Result:
    """Safely update projections; optional content repair requires live consent."""
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
        return _sync_repository_locked(
            root, sources, result,
            overwrite_existing_managed=overwrite_existing_managed,
        )
    finally:
        lock_context.__exit__(None, None, None)


def _sync_repository_locked(
    root: Path,
    sources: Iterable[object],
    result: Result,
    *,
    overwrite_existing_managed: bool = False,
) -> Result:
    consent = None
    if not isinstance(overwrite_existing_managed, bool):
        result.add(BLOCKING, "projection-consent", LOCK_RELATIVE, "overwrite request must be boolean")
        return result
    if overwrite_existing_managed:
        consent = projection_reflect_consent.load_consent(root)
        if consent is None or not consent.overwrite_existing_managed:
            result.add(BLOCKING, "projection-consent", LOCK_RELATIVE, "no live consent to overwrite managed artifacts")
            return result
    lock, lock_exists, lock_preimage = _load_lock(root, result)
    aggregate_budget = _load_aggregate_budget(root, result)
    locked_plugins = frozenset(
        str(entry.get("plugin", "")) for entry in lock.values()
    )
    specs, _unknown_plugins = _load_specs(
        root, sources, result, locked_plugins=locked_plugins
    )
    _scan_legacy_regions(root, specs, result)
    rendered: dict[str, RenderedProjection] = {}
    projection_preimages: dict[str, bytes | None] = {}
    fallback_changes: list[tuple[Path, bytes | None, bytes | None]] = []

    for spec in specs:
        try:
            projection = render_projection(spec)
            _safe_destination(root, PurePosixPath(spec.destination))
            fallback_changes.extend(delivery_io.plan_fallback_change(
                root, spec.destination, projection.fallback_content,
                lock.get(spec.destination), _delivery_io(),
                overwrite_existing_managed=overwrite_existing_managed,
            ))
        except (OSError, ValueError) as exc:
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
        if projection.fallback_content is not None and len(projection.fallback_content) > delivery.MAX_FALLBACK_BYTES:
            result.add(BLOCKING, "projection-budget", spec.destination,
                       "rendered reviewed fallback exceeds the safety read limit")
            continue
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
                root, existing_entry, result,
                allow_content_edits=overwrite_existing_managed,
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
    if aggregate > aggregate_budget:
        result.add(
            WARNING,
            "projection-aggregate-budget",
            LOCK_RELATIVE.as_posix(),
            f"resulting projection aggregate is {aggregate} bytes; budget is "
            f"{aggregate_budget} bytes (over by {aggregate - aggregate_budget} bytes)",
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
        for path, _, _ in fallback_changes:
            _prepare_destination_parent(root, PurePosixPath(path.relative_to(root).as_posix()))
        changes = fallback_changes + projection_changes
        if lock_changed:
            changes.append((lock_path, lock_content, lock_preimage))
        def recheck() -> None:
            if overwrite_existing_managed and projection_reflect_consent.load_consent(root) != consent:
                raise OSError("managed overwrite consent changed after validation")
            if _current_regular_bytes(lock_path) != lock_preimage:
                raise OSError("projection ownership lock changed after validation")

        def validate_target(target: Path) -> None:
            try:
                _safe_destination(root, PurePosixPath(target.relative_to(root).as_posix()))
            except ValueError as exc:
                raise OSError(str(exc)) from exc

        def final_recheck() -> None:
            if overwrite_existing_managed and projection_reflect_consent.load_consent(root) != consent:
                raise OSError("managed overwrite consent changed during publication")
            if _current_regular_bytes(lock_path) != (lock_content if lock_changed else lock_preimage):
                raise OSError("projection ownership lock changed during publication")

        _transactional_write(
            changes, before_write=recheck, validate_path=validate_target,
            after_write=final_recheck,
        )
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
    result.changed.extend(path.relative_to(root).as_posix() for path, _, _ in fallback_changes)
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
    runtime: bool = False,
    agent_worktrees_command: str | None = None,
) -> list[object]:
    """Reuse the settings/source resolver without importing the full scanner."""
    if not require_trust and not runtime:
        validate_committed_settings(repo_root)
    from scan_plugin_sources import assemble_enabled_plugins

    return assemble_enabled_plugins(
        repo_root,
        installed_root=installed_root, home=home, require_trust=require_trust,
        include_user=runtime, include_local=runtime, strict=runtime,
        include_metadata=not runtime,
        agent_worktrees_command=agent_worktrees_command,
    )


__all__ = [
    "BLOCKING",
    "WARNING",
    "DECLARATION_SCHEMA",
    "DECLARATION_VERSION",
    "LOCK_SCHEMA",
    "LOCK_VERSION",
    "CONFIG_SCHEMA",
    "CONFIG_VERSION",
    "MAX_AGGREGATE_BYTES",
    "MAX_AGGREGATE_BYTES_CEILING",
    "MIN_AGGREGATE_BYTES",
    "MAX_PROJECTION_BYTES",
    "Finding",
    "ProjectionSpec",
    "RenderedProjection",
    "Result",
    "discover_enabled_sources",
    "load_lock_entries",
    "local_sibling_destination",
    "render_local_cache",
    "render_projection",
    "repository_sync_lock",
    "scan_repository",
    "sync_repository",
    "sync_repository_locked",
    "validate_committed_settings",
    "validate_repository_root",
]
