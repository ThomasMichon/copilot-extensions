#!/usr/bin/env python3
"""Resolve and validate non-operative marketplace installation context."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlsplit, urlunsplit


class InstallationContextError(ValueError):
    """Raised when installation identity or ownership cannot be proven."""


@dataclass(frozen=True)
class NormalizedSource:
    """Portable marketplace source identity."""

    kind: str
    canonical: str
    ref: str = ""


def _fail(message: str) -> None:
    raise InstallationContextError(message)


def _property(value: Mapping[str, Any] | None, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    for candidate in value:
        if candidate != name and candidate.casefold() == name.casefold():
            _fail(f"JSON property '{candidate}' conflicts with exact case '{name}'.")
    if name not in value:
        return default
    result = value[name]
    return default if result is None else result


def _string_property(
    value: Mapping[str, Any],
    name: str,
    default: str = "",
) -> str:
    result = _property(value, name, default)
    if result is None:
        return default
    if not isinstance(result, str):
        _fail(f"Source field '{name}' must be a string.")
    if "\0" in result:
        _fail(f"Source field '{name}' may not contain NUL.")
    return result


def canonical_path(value: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
    """Return an absolute physical path while permitting a missing suffix."""

    text = os.fspath(value)
    if not text.strip():
        _fail("A required path is empty.")
    path = Path(text)
    try:
        resolved = path.resolve(strict=must_exist)
    except (OSError, RuntimeError) as error:
        _fail(f"Cannot resolve path '{value}': {error}")
    if must_exist and not resolved.exists():
        _fail(f"Path does not exist: {value}")
    return resolved


def _path_key(path: Path) -> str:
    return os.path.normcase(os.fspath(canonical_path(path)))


def paths_equal(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    return _path_key(Path(left)) == _path_key(Path(right))


def path_is_within(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    child_path = canonical_path(child)
    parent_path = canonical_path(parent)
    try:
        return os.path.commonpath((_path_key(child_path), _path_key(parent_path))) == _path_key(
            parent_path
        )
    except ValueError:
        return False


def read_json(path: str | os.PathLike[str]) -> Any:
    canonical = canonical_path(path, must_exist=True)
    try:
        return json.loads(
            canonical.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _fail(f"Invalid JSON in '{canonical}': {error}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"Duplicate JSON property '{key}'.")
        result[key] = value
    return result


def _normalize_git_url(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail("A git source URL may not contain control characters.")
    if not value.strip():
        _fail("A git source requires url.")
    candidate = value.strip()
    if re.search(r"%(?![0-9A-Fa-f]{2})", candidate):
        _fail("Git URL has a malformed percent-escape.")
    scp_match = re.fullmatch(r"[^/@:]+@([^/:]+):(.+)", candidate)
    if scp_match:
        candidate = f"ssh://{scp_match.group(1)}/{scp_match.group(2)}"
    parsed = urlsplit(candidate)
    if not parsed.scheme or not parsed.hostname:
        _fail(f"Git URL must be absolute and include a host: {value}")
    host = parsed.hostname.lower()
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            _fail(f"Git URL has an invalid host: {value}")
    elif not re.fullmatch(r"[a-z0-9._-]+", host):
        _fail(f"Git URL has an invalid host: {value}")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = ""
    try:
        if parsed.port is not None:
            defaults = {"http": 80, "https": 443}
            if defaults.get(parsed.scheme.lower()) != parsed.port:
                port = f":{parsed.port}"
    except ValueError as error:
        _fail(f"Invalid git URL '{value}': {error}")
    path = quote(
        (parsed.path or "/").replace("\\", "/"),
        safe="/-._~!$&'()*+,;=:@%[]",
    )
    path = re.sub(
        r"%([0-9a-fA-F]{2})",
        lambda match: (
            chr(int(match.group(1), 16))
            if 48 <= int(match.group(1), 16) <= 57
            or 65 <= int(match.group(1), 16) <= 90
            or 97 <= int(match.group(1), 16) <= 122
            or chr(int(match.group(1), 16)) in "-._~"
            else f"%{match.group(1).upper()}"
        ),
        path,
    )
    parts = path.split("/")
    normalized_parts: list[str] = []
    for part in parts:
        if not part and not normalized_parts:
            continue
        if part == ".":
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)
    while normalized_parts and not normalized_parts[-1]:
        normalized_parts.pop()
    path = f"/{'/'.join(normalized_parts)}"
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, "", ""))


def normalize_source(
    descriptor: Mapping[str, Any],
    base_directory: str | os.PathLike[str] | None = None,
    *,
    from_receipt: bool = False,
) -> NormalizedSource:
    """Normalize a source descriptor into the portable identity record."""

    raw_kind = _string_property(descriptor, "kind") or _string_property(
        descriptor,
        "source",
    )
    kind = raw_kind.strip().lower()
    aliases = {"local": "directory", "url": "git"}
    kind = aliases.get(kind, kind)
    ref = _string_property(descriptor, "ref")
    canonical_input = _string_property(descriptor, "canonical")
    if from_receipt and not canonical_input:
        _fail("A receipt source requires canonical identity.")
    canonical = ""

    if kind == "github":
        if canonical_input:
            if not canonical_input.startswith("github:"):
                _fail(f"Invalid canonical GitHub source '{canonical_input}'.")
            repository = canonical_input[7:]
        else:
            repository = (
                _string_property(descriptor, "repo")
                or _string_property(descriptor, "url")
            )
        repository = repository.strip()
        repository = re.sub(r"(?i)^https?://github\.com/", "", repository)
        repository = re.sub(r"(?i)^ssh://git@github\.com/", "", repository)
        repository = re.sub(r"(?i)^git@github\.com:", "", repository)
        repository = repository.strip("/")
        if repository.lower().endswith(".git"):
            repository = repository[:-4]
        if not re.fullmatch(r"[^/]+/[^/]+", repository):
            _fail(f"GitHub source requires owner/repository, got '{repository}'.")
        canonical = f"github:{repository.lower()}"
    elif kind == "git":
        if canonical_input:
            if not canonical_input.startswith("git:"):
                _fail(f"Invalid canonical git source '{canonical_input}'.")
            git_url = canonical_input[4:]
        else:
            git_url = _string_property(descriptor, "url")
        canonical = f"git:{_normalize_git_url(git_url)}"
    elif kind == "opaque":
        if canonical_input:
            canonical = canonical_input
        else:
            opaque_id = (
                _string_property(descriptor, "id")
                or _string_property(descriptor, "value")
            )
            if not opaque_id.strip():
                _fail("An opaque source requires a non-empty id.")
            canonical = f"opaque:{opaque_id}"
        if not canonical.startswith("opaque:"):
            _fail(f"Invalid canonical opaque source '{canonical}'.")
    elif kind == "directory":
        stable_id = _string_property(descriptor, "stableId").strip()
        if canonical_input:
            if canonical_input.startswith("directory-id:"):
                receipt_id = canonical_input[13:].strip()
                if not receipt_id:
                    _fail("A canonical directory-id source requires a non-empty id.")
                canonical = f"directory-id:{receipt_id}"
            elif canonical_input.startswith("directory:"):
                directory = canonical_input[10:]
                canonical = f"directory:{canonical_path(directory, must_exist=not from_receipt)}"
            else:
                _fail(f"Invalid canonical directory source '{canonical_input}'.")
        elif stable_id:
            canonical = f"directory-id:{stable_id}"
        else:
            directory_text = _string_property(descriptor, "path")
            if not directory_text.strip():
                _fail("A directory source requires a non-empty path or stableId.")
            directory = Path(directory_text)
            if not directory.is_absolute():
                if base_directory is None:
                    _fail("A relative directory source requires a declaration base directory.")
                directory = Path(base_directory) / directory
            canonical = f"directory:{canonical_path(directory, must_exist=True)}"
        if not (
            canonical.startswith("directory:") or canonical.startswith("directory-id:")
        ):
            _fail(f"Invalid canonical directory source '{canonical}'.")
    else:
        _fail(f"Unsupported source kind '{kind}'.")

    return NormalizedSource(kind=kind, canonical=canonical, ref=ref)


def source_record(source: NormalizedSource) -> str:
    fields = (
        ("version", "1"),
        ("kind", source.kind),
        ("source", source.canonical),
        ("ref", source.ref),
    )
    return "".join(
        f"{name}:{len(value.encode('utf-8'))}:{value}\n" for name, value in fields
    )


def _slug(value: str) -> str:
    result: list[str] = []
    previous_dash = False
    for character in value:
        if "A" <= character <= "Z":
            character = chr(ord(character) + 32)
        if "a" <= character <= "z" or "0" <= character <= "9":
            result.append(character)
            previous_dash = False
        elif result and not previous_dash:
            result.append("-")
            previous_dash = True
    return "".join(result).strip("-") or "marketplace"


def source_identity(source: NormalizedSource, readable_name: str) -> dict[str, Any]:
    record = source_record(source)
    digest = hashlib.sha256(record.encode("utf-8")).hexdigest()
    return {
        "kind": source.kind,
        "canonical": source.canonical,
        "ref": source.ref,
        "record": record,
        "sha256": digest,
        "fingerprint": f"sha256:{digest}",
        "marketplaceId": f"{_slug(readable_name)}--{digest[:16]}",
    }


def _get_declarations(
    key: str,
    copilot_home: Path,
    project_root: Path | None,
) -> list[dict[str, Any]]:
    settings_paths: list[tuple[Path, str, Path]] = [
        (copilot_home / "settings.json", "user", copilot_home),
        (copilot_home / "settings.local.json", "user-local", copilot_home),
    ]
    if project_root is not None:
        for relative in (
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".github/copilot/settings.json",
            ".github/copilot/settings.local.json",
        ):
            settings_paths.append(
                (project_root / relative, f"project:{project_root}", project_root)
            )
    declarations: list[dict[str, Any]] = []
    for path, label, base in settings_paths:
        if not path.is_file():
            continue
        settings = read_json(path)
        marketplaces = _property(settings, "extraKnownMarketplaces")
        if not isinstance(marketplaces, Mapping):
            continue
        for candidate in marketplaces:
            if candidate != key and candidate.casefold() == key.casefold():
                _fail(f"JSON property '{candidate}' conflicts with exact case '{key}'.")
        if key not in marketplaces:
            continue
        declaration = marketplaces[key]
        descriptor = _property(declaration, "source")
        if not isinstance(descriptor, Mapping):
            _fail(f"Marketplace '{key}' has no source in '{path}'.")
        declarations.append(
            {
                "source": normalize_source(descriptor, base),
                "declaredIn": label,
                "settingsPath": canonical_path(path),
            }
        )
    if not declarations:
        _fail(
            "No user or explicit project extraKnownMarketplaces declaration found "
            f"for installed key '{key}'."
        )
    identities = {
        (
            item["source"].kind,
            item["source"].canonical,
            item["source"].ref,
        )
        for item in declarations
    }
    if len(identities) != 1:
        locations = ", ".join(str(item["settingsPath"]) for item in declarations)
        _fail(
            f"Conflicting declarations for marketplace key '{key}' in: {locations}. "
            "Supply explicit management provenance."
        )
    return declarations


def _resolve_installed_evidence(
    payload: Path,
    copilot_home: Path,
    project_root: Path | None,
) -> dict[str, Any] | None:
    installed = canonical_path(copilot_home / "installed-plugins")
    try:
        relative = payload.relative_to(installed)
    except ValueError:
        return None
    if len(relative.parts) != 2 or not all(relative.parts):
        _fail(
            "Installed payload must be exactly "
            f"<copilot-home>/installed-plugins/<key>/<plugin>: {payload}"
        )
    key, plugin_id = relative.parts
    declarations = _get_declarations(key, copilot_home, project_root)
    return {
        "source": declarations[0]["source"],
        "pluginId": plugin_id,
        "readableName": key,
        "locator": {
            "kind": "installed",
            "copilotHome": str(copilot_home),
            "marketplaceKey": key,
            "declaredIn": [item["declaredIn"] for item in declarations],
        },
    }


def _resolve_directory_evidence(
    payload: Path,
    requested_plugin_id: str | None,
) -> dict[str, Any] | None:
    cursor = payload
    manifest_paths = (
        ".github/plugin/marketplace.json",
        "marketplace.json",
        ".plugin/marketplace.json",
        ".claude-plugin/marketplace.json",
    )
    while True:
        for relative_manifest in manifest_paths:
            manifest_path = cursor / relative_manifest
            if not manifest_path.is_file():
                continue
            manifest = read_json(manifest_path)
            metadata = _property(manifest, "metadata", {})
            if not isinstance(metadata, Mapping):
                _fail(f"Marketplace metadata must be an object in '{manifest_path}'.")
            plugin_root_text = _string_property(metadata, "pluginRoot")
            source_base = cursor
            if plugin_root_text:
                plugin_root = Path(plugin_root_text)
                if plugin_root.is_absolute():
                    _fail(
                        f"Marketplace metadata.pluginRoot must be relative in '{manifest_path}'."
                    )
                if ".." in plugin_root.parts:
                    _fail(f"Marketplace metadata.pluginRoot may not escape '{cursor}'.")
                source_base = canonical_path(cursor / plugin_root)
                if not path_is_within(source_base, cursor):
                    _fail(f"Marketplace metadata.pluginRoot escapes '{cursor}'.")
            matches: list[Mapping[str, Any]] = []
            plugins = _property(manifest, "plugins", [])
            if not isinstance(plugins, Sequence) or isinstance(plugins, (str, bytes)):
                plugins = []
            for plugin in plugins:
                if not isinstance(plugin, Mapping):
                    continue
                name = _string_property(plugin, "name")
                if requested_plugin_id and name != requested_plugin_id:
                    continue
                source_path_text = _string_property(plugin, "source")
                if not source_path_text:
                    continue
                source_path = Path(source_path_text)
                if source_path.is_absolute() or ".." in source_path.parts:
                    _fail(
                        "Marketplace plugin source must be relative and remain "
                        f"beneath '{cursor}'."
                    )
                candidate = canonical_path(source_base / source_path)
                if not path_is_within(candidate, cursor):
                    _fail(f"Marketplace plugin source escapes '{cursor}'.")
                if candidate.exists() and paths_equal(candidate, payload):
                    matches.append(plugin)
            if len(matches) != 1:
                _fail(
                    f"Marketplace manifest '{manifest_path}' does not contain exactly "
                    f"one plugin entry resolving to '{payload}'."
                )
            plugin_id = _string_property(matches[0], "name")
            return {
                "source": normalize_source(
                    {"source": "directory", "path": str(cursor)},
                    cursor,
                ),
                "pluginId": plugin_id,
                "readableName": _string_property(manifest, "name", "marketplace"),
                "locator": {
                    "kind": "directory",
                    "marketplaceRoot": str(canonical_path(cursor, must_exist=True)),
                },
            }
        if cursor.parent == cursor:
            break
        cursor = canonical_path(cursor.parent)
    return None


def _locator_matches(locator: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    if _property(locator, "kind", "") != _property(receipt, "kind", ""):
        return False
    if locator["kind"] == "installed":
        return (
            _string_property(receipt, "marketplaceKey") == locator["marketplaceKey"]
            and paths_equal(
                _string_property(receipt, "copilotHome"),
                str(locator["copilotHome"]),
            )
        )
    if locator["kind"] == "directory":
        return paths_equal(
            _string_property(receipt, "marketplaceRoot"),
            str(locator["marketplaceRoot"]),
        )
    return False


def _assert_positive_integer(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{name} must be an integer.")
    if value < 1:
        _fail(f"{name} must be at least 1.")


def _assert_receipt_state(value: Any, name: str) -> None:
    if value not in {"active", "inactive", "orphaned", "removing"}:
        _fail(f"{name} must be active, inactive, orphaned, or removing.")


def validate_namespace_receipt(
    receipt_path: str | os.PathLike[str],
    durable_home: str | os.PathLike[str],
) -> dict[str, Any]:
    actual_receipt = canonical_path(receipt_path, must_exist=True)
    cell_root = actual_receipt.parent
    marketplaces_root = canonical_path(Path(durable_home) / "marketplaces")
    if not path_is_within(cell_root, marketplaces_root):
        _fail(f"Namespace receipt '{actual_receipt}' is outside the durable marketplaces root.")
    marketplace_id = cell_root.name
    canonical_receipt = canonical_path(cell_root / "namespace.json")
    if not paths_equal(actual_receipt, canonical_receipt):
        _fail(
            f"namespace.json is not at its exact canonical receipt location "
            f"'{canonical_receipt}'."
        )
    namespace = read_json(actual_receipt)
    namespace_version = _property(namespace, "version")
    if (
        _string_property(namespace, "schema")
        != "copilot-extensions.marketplace-namespace"
        or isinstance(namespace_version, bool)
        or not isinstance(namespace_version, int)
        or namespace_version != 1
    ):
        _fail(f"Namespace receipt '{actual_receipt}' has an unsupported schema or version.")
    if _string_property(namespace, "marketplaceId") != marketplace_id:
        _fail(f"Namespace receipt '{actual_receipt}' does not match its cell directory.")
    match = re.fullmatch(r"(.+)--([0-9a-f]{16})", marketplace_id)
    if match is None:
        _fail(f"Invalid source-derived marketplace id '{marketplace_id}'.")
    _assert_positive_integer(
        _property(namespace, "generation"),
        "namespace.json generation",
    )
    _assert_receipt_state(_property(namespace, "state"), "namespace.json state")
    source_receipt = _property(namespace, "source")
    if not isinstance(source_receipt, Mapping):
        _fail(f"Namespace receipt '{actual_receipt}' has no source identity.")
    normalized = normalize_source(
        {
            "kind": _property(source_receipt, "kind"),
            "canonical": _property(source_receipt, "canonical"),
            "ref": _property(source_receipt, "ref", ""),
        },
        from_receipt=True,
    )
    identity = source_identity(normalized, match.group(1))
    if identity["marketplaceId"] != marketplace_id:
        _fail(f"Namespace receipt '{actual_receipt}' id does not match its normalized source.")
    if _string_property(source_receipt, "fingerprint") != identity["fingerprint"]:
        _fail(
            f"Namespace receipt '{actual_receipt}' fingerprint does not match "
            "its normalized source."
        )
    return {
        "receipt": namespace,
        "receiptPath": actual_receipt,
        "cellRoot": cell_root,
        "marketplaceId": marketplace_id,
        "identity": identity,
    }


def _assert_plugin_id(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value) or value in {
        ".",
        "..",
    }:
        _fail(f"Invalid filesystem-safe plugin id '{value}'.")
    basename = value.split(".", 1)[0].upper()
    if (
        basename in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"COM[1-9]", basename)
        or re.fullmatch(r"LPT[1-9]", basename)
    ):
        _fail(f"Invalid filesystem-safe plugin id '{value}'.")


def _resolve_relative_root(plugin_root: Path, relative: str, name: str) -> Path:
    if not relative.strip() or Path(relative).is_absolute():
        _fail(f"roots.{name} must be a non-empty relative path.")
    raw_parts = re.split(r"[\\/]", relative)
    if "." in raw_parts or ".." in raw_parts:
        _fail(f"roots.{name} may not escape or use dot segments.")
    resolved = canonical_path(plugin_root / relative)
    if resolved.parent != plugin_root and not path_is_within(resolved, plugin_root):
        _fail(f"roots.{name} escapes pluginRoot.")
    return resolved


def validate_context_receipt(
    receipt_path: str | os.PathLike[str],
    durable_home: str | os.PathLike[str],
    *,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_cell_root: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = environment if environment is not None else os.environ
    if not Path(durable_home).is_absolute():
        _fail("--durable-home must be absolute.")
    receipt_pointer = Path(receipt_path)
    if not receipt_pointer.is_absolute():
        _fail("The installation-context receipt pointer must be absolute.")
    for name, expectation in (
        ("expected payload root", expected_payload_root),
        ("expected cell root", expected_cell_root),
    ):
        if expectation is not None and not Path(expectation).is_absolute():
            _fail(f"{name} must be absolute.")
    actual_receipt = canonical_path(receipt_pointer, must_exist=True)
    install = read_json(actual_receipt)
    install_version = _property(install, "version")
    if (
        _string_property(install, "schema")
        != "copilot-extensions.plugin-installation"
        or isinstance(install_version, bool)
        or not isinstance(install_version, int)
        or install_version != 1
    ):
        _fail("install.json has an unsupported schema or version.")
    marketplace_id = _string_property(install, "marketplaceId")
    plugin_id = _string_property(install, "pluginId")
    if not marketplace_id or not plugin_id:
        _fail("install.json identity is incomplete.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}", marketplace_id):
        _fail(f"Invalid source-derived marketplace id '{marketplace_id}'.")
    _assert_plugin_id(plugin_id)
    durable = canonical_path(durable_home)
    cell_root = canonical_path(durable / "marketplaces" / marketplace_id)
    plugin_root = canonical_path(cell_root / "plugins" / plugin_id)
    canonical_receipt = canonical_path(plugin_root / "install.json")
    if not paths_equal(actual_receipt, canonical_receipt):
        _fail(
            f"install.json is not at its exact canonical receipt location "
            f"'{canonical_receipt}'."
        )
    if not paths_equal(_string_property(install, "pluginRoot"), plugin_root):
        _fail("install.json pluginRoot does not match its canonical cell/plugin location.")
    if expected_marketplace_id and marketplace_id != expected_marketplace_id:
        _fail(
            f"Expected marketplace '{expected_marketplace_id}', receipt names "
            f"'{marketplace_id}'."
        )
    if expected_plugin_id and plugin_id != expected_plugin_id:
        _fail(f"Expected plugin '{expected_plugin_id}', receipt names '{plugin_id}'.")
    if expected_cell_root and not paths_equal(cell_root, expected_cell_root):
        _fail(f"Expected cell '{expected_cell_root}', receipt belongs to '{cell_root}'.")
    _assert_positive_integer(_property(install, "generation"), "install.json generation")
    _assert_receipt_state(_property(install, "state"), "install.json state")

    namespace_path = canonical_path(cell_root / "namespace.json")
    if not paths_equal(_string_property(install, "namespaceReceipt"), namespace_path):
        _fail("install.json namespaceReceipt is not the exact namespace receipt in the same cell.")
    validated_namespace = validate_namespace_receipt(namespace_path, durable)
    if validated_namespace["marketplaceId"] != marketplace_id:
        _fail("namespace.json marketplaceId does not match install.json.")
    identity = validated_namespace["identity"]

    payload_receipt = _property(install, "payload")
    if not isinstance(payload_receipt, Mapping):
        _fail("install.json payload is missing.")
    payload_text = _string_property(payload_receipt, "root")
    if not Path(payload_text).is_absolute():
        _fail("payload.root must be absolute.")
    if not _string_property(payload_receipt, "version").strip():
        _fail("payload.version must be a non-empty string.")
    if _string_property(payload_receipt, "origin") not in {
        "installed",
        "directory",
        "staged",
        "explicit",
    }:
        _fail("payload.origin must be installed, directory, staged, or explicit.")
    payload_root = canonical_path(payload_text)
    if expected_payload_root and not paths_equal(payload_root, expected_payload_root):
        _fail(f"Expected payload '{expected_payload_root}', receipt names '{payload_root}'.")
    inherited_payload = environment.get("COPILOT_PLUGIN_ROOT")
    if inherited_payload:
        if not Path(inherited_payload).is_absolute():
            _fail("COPILOT_PLUGIN_ROOT must be absolute.")
        if not paths_equal(payload_root, inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT conflicts with the validated payload root.")

    roots_receipt = _property(install, "roots")
    if not isinstance(roots_receipt, Mapping):
        _fail("install.json roots are missing.")
    roots: dict[str, Path] = {}
    for name in ("versions", "snapshots", "state", "run", "logs", "cache", "launchers"):
        roots[f"{name}Root"] = _resolve_relative_root(
            plugin_root,
            _string_property(roots_receipt, name),
            name,
        )
    return {
        "action": "validate",
        "marketplaceId": marketplace_id,
        "marketplaceSlot": marketplace_id,
        "sourceFingerprint": identity["fingerprint"],
        "source": {
            "kind": identity["kind"],
            "canonical": identity["canonical"],
            "ref": identity["ref"],
        },
        "pluginId": plugin_id,
        "payloadRoot": str(payload_root),
        "cellRoot": str(cell_root),
        "pluginRoot": str(plugin_root),
        **{name: str(path) for name, path in roots.items()},
        "reposRoot": str(canonical_path(cell_root / "repos")),
        "namespaceReceipt": str(namespace_path),
        "installReceipt": str(actual_receipt),
        "generation": _property(install, "generation"),
        "state": _property(install, "state"),
    }


def _find_existing_source(
    durable_home: Path,
    fingerprint: str,
    desired_id: str,
    locator: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    marketplaces = durable_home / "marketplaces"
    if not marketplaces.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for cell_directory in sorted(path for path in marketplaces.iterdir() if path.is_dir()):
        receipt_path = cell_directory / "namespace.json"
        if not receipt_path.is_file():
            continue
        validated = validate_namespace_receipt(receipt_path, durable_home)
        receipt_fingerprint = validated["identity"]["fingerprint"]
        receipt_id = validated["marketplaceId"]
        if cell_directory.name == desired_id and receipt_fingerprint != fingerprint:
            _fail(
                f"Marketplace id '{desired_id}' is already occupied by a different "
                "full source fingerprint."
            )
        if receipt_fingerprint != fingerprint:
            continue
        locator_match = locator is None
        if locator is not None:
            receipt_locators = _property(validated["receipt"], "locators", [])
            locator_match = any(
                isinstance(known, Mapping) and _locator_matches(locator, known)
                for known in receipt_locators
            )
        results.append(
            {
                "marketplaceId": receipt_id,
                "namespaceReceipt": str(canonical_path(receipt_path)),
                "sameId": receipt_id == desired_id,
                "locatorMatch": locator_match,
            }
        )
    return results


def resolve_context(
    *,
    payload_root: str | os.PathLike[str] | None = None,
    plugin_id: str | None = None,
    copilot_home: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    context: str | os.PathLike[str] | None = None,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_cell_root: str | os.PathLike[str] | None = None,
    source_descriptor: Mapping[str, Any] | None = None,
    marketplace_key: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve existing evidence into a non-operative installation context."""

    environment = environment if environment is not None else os.environ
    home = Path(environment.get("HOME") or Path.home())
    copilot_value = Path(copilot_home or home / ".copilot")
    durable_value = Path(durable_home or home / ".copilot-extensions")
    if not copilot_value.is_absolute() or not durable_value.is_absolute():
        _fail("--copilot-home and --durable-home must be absolute.")
    copilot = canonical_path(copilot_value)
    durable = canonical_path(durable_value)
    project = (
        canonical_path(project_root, must_exist=True) if project_root is not None else None
    )
    pointer = context or environment.get("COPILOT_EXTENSIONS_CONTEXT")
    if pointer:
        payload_expectation = (
            expected_payload_root
            or payload_root
            or environment.get("COPILOT_PLUGIN_ROOT")
        )
        plugin_expectation = plugin_id or expected_plugin_id
        if not plugin_expectation:
            _fail("resolve with an explicit context requires an expected plugin id.")
        if (
            payload_expectation is None
            and expected_marketplace_id is None
            and expected_cell_root is None
        ):
            _fail(
                "resolve with an explicit context requires an expected payload, "
                "marketplace, or cell identity."
            )
        result = validate_context_receipt(
            pointer,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=plugin_expectation,
            expected_payload_root=payload_expectation,
            expected_cell_root=expected_cell_root,
            environment=environment,
        )
        result["action"] = "resolve"
        return result

    payload_value = payload_root or environment.get("COPILOT_PLUGIN_ROOT")
    if payload_value is None:
        _fail("resolve requires --payload-root or COPILOT_PLUGIN_ROOT.")
    payload_input = Path(payload_value)
    if not payload_input.is_absolute():
        _fail("The payload root must be absolute.")
    payload = canonical_path(payload_input, must_exist=True)
    if not payload.is_dir():
        _fail(f"The payload root must be an existing directory: {payload}")
    inherited_payload = environment.get("COPILOT_PLUGIN_ROOT")
    if inherited_payload:
        if not Path(inherited_payload).is_absolute():
            _fail("COPILOT_PLUGIN_ROOT must be absolute.")
        if not paths_equal(payload, inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT conflicts with --payload-root.")

    if source_descriptor is not None:
        if not plugin_id:
            _fail("Explicit source resolution requires --plugin-id.")
        evidence = {
            "source": normalize_source(source_descriptor),
            "pluginId": plugin_id,
            "readableName": marketplace_key or "marketplace",
            "locator": None,
        }
    else:
        evidence = _resolve_installed_evidence(payload, copilot, project)
        if evidence is None:
            evidence = _resolve_directory_evidence(payload, plugin_id)
        if evidence is None:
            _fail(
                f"Cannot establish marketplace provenance for payload '{payload}'. "
                "Supply an explicit source descriptor for management/development mode."
            )
        if plugin_id and plugin_id != evidence["pluginId"]:
            _fail(
                f"Expected plugin '{plugin_id}', payload evidence identifies "
                f"'{evidence['pluginId']}'."
            )

    source = evidence["source"]
    identity = source_identity(source, evidence["readableName"])
    resolved_plugin_id = str(evidence["pluginId"])
    _assert_plugin_id(resolved_plugin_id)
    existing = _find_existing_source(
        durable,
        identity["fingerprint"],
        identity["marketplaceId"],
        evidence["locator"],
    )
    rebind = [
        entry
        for entry in existing
        if not entry["sameId"] or not entry["locatorMatch"]
    ]
    if rebind:
        owners = ", ".join(str(entry["marketplaceId"]) for entry in rebind)
        _fail(
            f"Source '{identity['fingerprint']}' already owns cell/locator '{owners}'; "
            "explicit rebind or new-cell intent is required."
        )
    cell_root = canonical_path(durable / "marketplaces" / identity["marketplaceId"])
    plugin_root_path = canonical_path(cell_root / "plugins" / resolved_plugin_id)
    return {
        "action": "resolve",
        "source": {
            "kind": identity["kind"],
            "canonical": identity["canonical"],
            "ref": identity["ref"],
            "record": identity["record"],
        },
        "sourceFingerprint": identity["fingerprint"],
        "marketplaceId": identity["marketplaceId"],
        "marketplaceSlot": identity["marketplaceId"],
        "pluginId": resolved_plugin_id,
        "payloadRoot": str(payload),
        "cellRoot": str(cell_root),
        "pluginRoot": str(plugin_root_path),
        "versionsRoot": str(canonical_path(plugin_root_path / "versions")),
        "snapshotsRoot": str(canonical_path(plugin_root_path / "snapshots")),
        "stateRoot": str(canonical_path(plugin_root_path / "state")),
        "runRoot": str(canonical_path(plugin_root_path / "run")),
        "logsRoot": str(canonical_path(plugin_root_path / "logs")),
        "cacheRoot": str(canonical_path(plugin_root_path / "cache")),
        "launchersRoot": str(canonical_path(plugin_root_path / "launchers")),
        "reposRoot": str(canonical_path(cell_root / "repos")),
        "namespaceReceipt": str(canonical_path(cell_root / "namespace.json")),
        "installReceipt": str(canonical_path(plugin_root_path / "install.json")),
        "locator": evidence["locator"],
        "existingCells": existing,
        "rebindRequired": False,
        "operative": False,
    }


def _source_descriptor(arguments: argparse.Namespace) -> Mapping[str, Any] | None:
    if arguments.source_json and arguments.source_file:
        _fail("Specify only one of --source-json and --source-file.")
    if arguments.source_file:
        value = read_json(arguments.source_file)
    elif arguments.source_json:
        try:
            value = json.loads(
                arguments.source_json,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except json.JSONDecodeError as error:
            _fail(f"Invalid --source-json: {error}")
    else:
        return None
    if not isinstance(value, Mapping):
        _fail("A source descriptor must be a JSON object.")
    return value


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--copilot-home")
    parser.add_argument("--durable-home")
    parser.add_argument("--project-root")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    source_parser = subparsers.add_parser("source-id")
    source_parser.add_argument("--source-json")
    source_parser.add_argument("--source-file")
    source_parser.add_argument("--marketplace-key")

    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--source-json")
    resolve_parser.add_argument("--source-file")
    resolve_parser.add_argument("--marketplace-key")
    resolve_parser.add_argument("--plugin-id")
    resolve_parser.add_argument("--payload-root")
    resolve_parser.add_argument("--context")
    resolve_parser.add_argument("--expected-marketplace-id")
    resolve_parser.add_argument("--expected-plugin-id")
    resolve_parser.add_argument("--expected-payload-root")
    resolve_parser.add_argument("--expected-cell-root")
    _add_common_paths(resolve_parser)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--context")
    validate_parser.add_argument("--payload-root")
    validate_parser.add_argument("--expected-marketplace-id")
    validate_parser.add_argument("--expected-plugin-id")
    validate_parser.add_argument("--expected-payload-root")
    validate_parser.add_argument("--expected-cell-root")
    _add_common_paths(validate_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        descriptor = _source_descriptor(arguments) if hasattr(arguments, "source_json") else None
        if arguments.action == "source-id":
            if descriptor is None:
                _fail("source-id requires --source-json or --source-file.")
            result = source_identity(
                normalize_source(descriptor),
                arguments.marketplace_key or "marketplace",
            )
        elif arguments.action == "validate":
            pointer = arguments.context or os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
            if not pointer:
                _fail("validate requires --context or COPILOT_EXTENSIONS_CONTEXT.")
            result = validate_context_receipt(
                pointer,
                arguments.durable_home or Path.home() / ".copilot-extensions",
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=(
                    arguments.expected_payload_root or arguments.payload_root
                ),
                expected_cell_root=arguments.expected_cell_root,
            )
        else:
            result = resolve_context(
                payload_root=arguments.payload_root,
                plugin_id=arguments.plugin_id,
                copilot_home=arguments.copilot_home,
                project_root=arguments.project_root,
                durable_home=arguments.durable_home,
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_cell_root=arguments.expected_cell_root,
                source_descriptor=descriptor,
                marketplace_key=arguments.marketplace_key,
            )
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except InstallationContextError as error:
        print(f"installation-context: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
