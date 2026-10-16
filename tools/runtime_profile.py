"""Inert exact runtime records; approval and acquisition stay with their owners.

The caller supplies independently verified release/target facts and the whole-
record approval map from its existing trusted configuration/channel. Nothing in
an input record establishes trust. Dependency metadata here is asserted input;
admission must compare it with actual approved wheel bytes before construction.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import parse_tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

SCHEMA = "copilot-extensions.exact-runtime-profile"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REFERENCE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}\Z")
_ENVIRONMENT = frozenset(default_environment())


class ProfileError(ValueError):
    """A malformed, incompatible, unapproved or ambiguous selection."""


def _fields(value: Any, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or value.keys() != keys:
        raise ProfileError(f"{label}: expected exactly {sorted(keys)}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProfileError(f"{label}: expected nonempty trimmed text")
    return value


def _reference(value: Any, label: str) -> str:
    if not _REFERENCE.fullmatch(_text(value, label)):
        raise ProfileError(f"{label}: expected an opaque reference, not a URL/path")
    return value


def _digest(value: Any, label: str) -> str:
    if not _HASH.fullmatch(_text(value, label)):
        raise ProfileError(f"{label}: expected a SHA-256 content identity")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ProfileError(f"{label}: expected a list")
    result = [_text(item, label) for item in value]
    if len(set(result)) != len(result):
        raise ProfileError(f"{label}: duplicate values")
    return result


def _extras(value: Any, label: str) -> set[str]:
    items = _strings(value, label)
    normalized = {canonicalize_name(item, validate=True) for item in items}
    if len(normalized) != len(items):
        raise ProfileError(f"{label}: duplicate normalized extras")
    return normalized


def _requirement(text: str) -> Requirement:
    try:
        requirement = Requirement(text)
    except InvalidRequirement as exc:
        raise ProfileError(f"invalid requirement: {text!r}") from exc
    if requirement.url:
        raise ProfileError("URL/VCS/path requirements are not locked wheel edges")
    return requirement


def _target(value: Any) -> None:
    _fields(value, {"environment", "tags", "device"}, "target")
    env = _fields(value["environment"], set(_ENVIRONMENT), "target environment")
    for key, item in env.items():
        _text(item, key)
    for key in ("python_version", "python_full_version", "implementation_version"):
        Version(env[key])
    if ".".join(env["python_full_version"].split(".")[:2]) != env["python_version"]:
        raise ProfileError("target Python major/minor disagrees with full version")
    tags = _strings(value["tags"], "target tags")
    if not tags or any(not parse_tag(tag) for tag in tags):
        raise ProfileError("target needs actual interpreter compatibility tags")
    _reference(value["device"], "target device")


def _closure(record: dict) -> None:
    entries = record["wheels"]
    if not isinstance(entries, list) or not entries:
        raise ProfileError("closure: expected at least one wheel")
    wheels: dict[str, tuple[dict, list[Requirement]]] = {}
    target = record["target"]
    compatible = {tag for text in target["tags"] for tag in parse_tag(text)}
    for entry in entries:
        _fields(entry, {
            "name", "version", "filename", "sha256", "requires_python",
            "provides_extras", "selected_extras", "requires_dist",
        }, "wheel")
        name = canonicalize_name(_text(entry["name"], "name"), validate=True)
        if name in wheels:
            raise ProfileError(f"duplicate normalized distribution: {name}")
        version = Version(_text(entry["version"], "version"))
        filename = _text(entry["filename"], "filename")
        if "/" in filename or "\\" in filename:
            raise ProfileError("wheel filename must be a basename")
        wheel_name, wheel_version, _, tags = parse_wheel_filename(filename)
        if wheel_name != name or wheel_version != version or not tags & compatible:
            raise ProfileError(f"{name}: wheel identity or target tags disagree")
        _digest(entry["sha256"], "wheel digest")
        requires_python = entry["requires_python"]
        if not isinstance(requires_python, str) or requires_python != requires_python.strip():
            raise ProfileError(f"{name}: Requires-Python must be a trimmed string")
        if not SpecifierSet(requires_python).contains(
            target["environment"]["python_full_version"], prereleases=True,
        ):
            raise ProfileError(f"{name}: incompatible Requires-Python")
        provided = _extras(entry["provides_extras"], "provided extras")
        selected = _extras(entry["selected_extras"], "selected extras")
        if not selected <= provided:
            raise ProfileError(f"{name}: selected extra not provided")
        requirements = [_requirement(text) for text in
                        _strings(entry["requires_dist"], "Requires-Dist")]
        wheels[name] = entry, requirements

    roots = [_requirement(text) for text in _strings(record["roots"], "roots")]
    if not roots:
        raise ProfileError("closure: roots cannot be empty")
    wanted: dict[str, set[str]] = {}
    pending = [(requirement, {""}) for requirement in roots]
    environment = target["environment"]
    while pending:
        requirement, parent_extras = pending.pop()
        if requirement.marker and not any(
            requirement.marker.evaluate({**environment, "extra": extra})
            for extra in parent_extras
        ):
            continue
        name = canonicalize_name(requirement.name)
        if name not in wheels:
            raise ProfileError(f"incomplete applicable closure: {name}")
        entry, dependencies = wheels[name]
        if not requirement.specifier.contains(entry["version"], prereleases=True):
            raise ProfileError(f"{name}: locked version violates dependency")
        requested = {canonicalize_name(extra) for extra in requirement.extras}
        if not requested <= _extras(entry["provides_extras"], "provided extras"):
            raise ProfileError(f"{name}: dependency requests an unknown extra")
        previous = wanted.get(name)
        if previous is not None and requested <= previous:
            continue
        wanted[name] = requested | (previous or set())
        pending.extend((edge, {"", *wanted[name]}) for edge in dependencies)
    if wanted.keys() != wheels.keys():
        raise ProfileError("closure includes distributions unreachable from its roots")
    for name, (entry, _) in wheels.items():
        if wanted[name] != _extras(entry["selected_extras"], "selected extras"):
            raise ProfileError(f"{name}: selected extras do not match full root closure")


@dataclass(frozen=True)
class RuntimeProfile:
    canonical: bytes

    @property
    def lock_id(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical).hexdigest()

    def record(self) -> dict:
        return json.loads(self.canonical)


def validate_profile(record: dict) -> RuntimeProfile:
    """Freeze a complete asserted record, without attesting its origin/bytes."""
    try:
        _fields(record, {
            "schema", "version", "release", "mode", "artifact", "component",
            "backend", "target", "roots", "approval_source", "acquisition_policy",
            "wheels",
        }, "profile")
        if record["schema"] != SCHEMA or type(record["version"]) is not int or record["version"] != 1:
            raise ProfileError("unsupported profile schema/version")
        release = _fields(record["release"], {"identity", "sha256"}, "release")
        _reference(release["identity"], "release identity")
        _digest(release["sha256"], "source digest")
        for key in ("component", "backend", "approval_source", "acquisition_policy"):
            _reference(record[key], key)
        if record["mode"] == "artifact":
            artifact = _fields(record["artifact"], {"artifact_id", "manifest_sha256"}, "artifact")
            for key, value in artifact.items():
                _digest(value, key)
        elif record["mode"] != "source" or record["artifact"] is not None:
            raise ProfileError("source mode must be independent of artifact identity")
        _target(record["target"])
        if record["backend"] != "cpu" and record["backend"] != record["target"]["device"]:
            raise ProfileError("accelerator backend requires its explicit target device")
        _closure(record)
        return RuntimeProfile(json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii"))
    except (InvalidVersion, InvalidSpecifier, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError(f"invalid profile: {exc}") from exc


@dataclass(frozen=True)
class SelectedProfile:
    profile: RuntimeProfile
    approval_source: str


def select_profile(
    candidates: Iterable[dict], *, approvals: Mapping[str, str],
    release: dict, target: dict, component: str, roots: list[str], mode: str,
    backend: str | None = None, retained_backend: str | None = None,
    existing_installation: bool = False, preference: tuple[str, ...] = (),
) -> SelectedProfile:
    """Pure selection; approvals map whole-record digests to trusted source IDs.

    The adapter, not this function or a record's own declaration, authenticates
    that map. No adapter/runtime loader is provided here. Mode/backend changes
    are explicit; this function never performs an automatic fallback.
    """
    if backend is None:
        backend = retained_backend
        if backend is None:
            if existing_installation:
                raise ProfileError("unprofiled existing installation needs explicit migration")
            backend = "cpu"
    matches: dict[str, SelectedProfile] = {}
    for candidate in candidates:
        profile = validate_profile(candidate)
        record = profile.record()
        if approvals.get(profile.lock_id) != record["approval_source"]:
            raise ProfileError("profile lacks whole-record approval from a trusted source")
        if (
            record["release"] == release and record["target"] == target
            and record["component"] == component and record["roots"] == roots
            and record["mode"] == mode and record["backend"] == backend
        ):
            matches[profile.lock_id] = SelectedProfile(profile, record["approval_source"])
    if preference:
        if len(set(preference)) != len(preference):
            raise ProfileError("duplicate profile preference")
        for identity in preference:
            if identity in matches:
                return matches[identity]
        raise ProfileError("requested approved profile is unavailable")
    if not matches:
        raise ProfileError("requested release/target/capability profile is unavailable")
    if len(matches) != 1:
        raise ProfileError("ambiguous approved profiles need an explicit identity/order")
    return next(iter(matches.values()))
