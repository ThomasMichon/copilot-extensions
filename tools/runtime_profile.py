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
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Mapping

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import parse_tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from installer_wheel_inputs import bounded_tag, _check_deadline

SCHEMA = "copilot-extensions.exact-runtime-profile"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REFERENCE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}\Z")
_ENVIRONMENT = frozenset({
    "implementation_name", "implementation_version", "os_name", "platform_machine",
    "platform_release", "platform_system", "platform_version", "python_full_version",
    "platform_python_implementation", "python_version", "sys_platform",
})


class ProfileError(ValueError):
    """A malformed, incompatible, unapproved or ambiguous selection."""


class ProfileUnavailable(ProfileError):
    """No approved record matches the authority's requested selection."""


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


def _strings(value: Any, label: str, deadline: float) -> list[str]:
    if not isinstance(value, list):
        raise ProfileError(f"{label}: expected a list")
    result = []
    for item in value:
        _check_deadline(deadline)
        result.append(_text(item, label))
    _check_deadline(deadline)
    if len(set(result)) != len(result):
        raise ProfileError(f"{label}: duplicate values")
    return result


def _extras(value: Any, label: str, deadline: float) -> set[str]:
    items = _strings(value, label, deadline)
    normalized = set()
    for item in items:
        _check_deadline(deadline)
        normalized.add(canonicalize_name(item, validate=True))
    _check_deadline(deadline)
    if len(normalized) != len(items):
        raise ProfileError(f"{label}: duplicate normalized extras")
    return normalized


def _requirement(text: str, deadline: float) -> Requirement:
    _check_deadline(deadline)
    try:
        requirement = Requirement(text)
    except InvalidRequirement as exc:
        raise ProfileError(f"invalid requirement: {text!r}") from exc
    _check_deadline(deadline)
    if requirement.url:
        raise ProfileError("URL/VCS/path requirements are not locked wheel edges")
    return requirement


def _tag(text: str, max_tags: int, deadline: float):
    canonical, _ = bounded_tag(text, limit=max_tags, deadline=deadline)
    result = parse_tag(canonical)
    _check_deadline(deadline)
    return result


def _target(value: Any, max_tags: int, deadline: float) -> None:
    _fields(value, {"environment", "tags", "device"}, "target")
    env = _fields(value["environment"], set(_ENVIRONMENT), "target environment")
    for key, item in env.items():
        _check_deadline(deadline)
        _text(item, key)
    for key in ("python_version", "python_full_version", "implementation_version"):
        _check_deadline(deadline)
        Version(env[key])
    _check_deadline(deadline)
    if ".".join(env["python_full_version"].split(".")[:2]) != env["python_version"]:
        raise ProfileError("target Python major/minor disagrees with full version")
    tags = _strings(value["tags"], "target tags", deadline)
    if not tags or any(not _tag(tag, max_tags, deadline) for tag in tags):
        raise ProfileError("target needs actual interpreter compatibility tags")
    _reference(value["device"], "target device")


def _wheel_filename(filename: str, max_tags: int, deadline: float):
    if not filename.endswith(".whl") or "/" in filename or "\\" in filename:
        raise ProfileError("wheel filename must be a .whl basename")
    prefix, python, abi, platform = filename[:-4].rsplit("-", 3)
    canonical, _ = bounded_tag(
        "-".join((python, abi, platform)), limit=max_tags, deadline=deadline,
    )
    return parse_wheel_filename(prefix + "-" + canonical + ".whl")


def _closure(record: dict, max_tags: int, deadline: float) -> None:
    entries = record["wheels"]
    if not isinstance(entries, list) or not entries:
        raise ProfileError("closure: expected at least one wheel")
    wheels: dict[str, tuple[dict, list[Requirement]]] = {}
    target = record["target"]
    compatible = {tag for text in target["tags"] for tag in _tag(text, max_tags, deadline)}
    for entry in entries:
        _check_deadline(deadline)
        _fields(entry, {
            "name", "version", "filename", "sha256", "requires_python",
            "provides_extras", "selected_extras", "requires_dist",
        }, "wheel")
        name = canonicalize_name(_text(entry["name"], "name"), validate=True)
        if name in wheels:
            raise ProfileError(f"duplicate normalized distribution: {name}")
        version = Version(_text(entry["version"], "version"))
        filename = _text(entry["filename"], "filename")
        wheel_name, wheel_version, _, tags = _wheel_filename(filename, max_tags, deadline)
        _check_deadline(deadline)
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
        provided = _extras(entry["provides_extras"], "provided extras", deadline)
        selected = _extras(entry["selected_extras"], "selected extras", deadline)
        if not selected <= provided:
            raise ProfileError(f"{name}: selected extra not provided")
        requirements = [_requirement(text, deadline) for text in
                        _strings(entry["requires_dist"], "Requires-Dist", deadline)]
        wheels[name] = entry, requirements

    roots = [_requirement(text, deadline) for text in _strings(record["roots"], "roots", deadline)]
    if not roots:
        raise ProfileError("closure: roots cannot be empty")
    wanted: dict[str, set[str]] = {}
    pending = [(requirement, {""}) for requirement in roots]
    environment = target["environment"]
    while pending:
        _check_deadline(deadline)
        requirement, parent_extras = pending.pop()
        if requirement.marker:
            applicable = False
            for extra in parent_extras:
                _check_deadline(deadline)
                if requirement.marker.evaluate({**environment, "extra": extra}):
                    applicable = True
                    break
            if not applicable:
                continue
        _check_deadline(deadline)
        name = canonicalize_name(requirement.name)
        if name not in wheels:
            raise ProfileError(f"incomplete applicable closure: {name}")
        entry, dependencies = wheels[name]
        if not requirement.specifier.contains(entry["version"], prereleases=True):
            raise ProfileError(f"{name}: locked version violates dependency")
        requested = set()
        for extra in requirement.extras:
            _check_deadline(deadline)
            requested.add(canonicalize_name(extra))
        if not requested <= _extras(entry["provides_extras"], "provided extras", deadline):
            raise ProfileError(f"{name}: dependency requests an unknown extra")
        previous = wanted.get(name)
        if previous is not None and requested <= previous:
            continue
        wanted[name] = requested | (previous or set())
        for edge in dependencies:
            _check_deadline(deadline)
            pending.append((edge, {"", *wanted[name]}))
    if wanted.keys() != wheels.keys():
        raise ProfileError("closure includes distributions unreachable from its roots")
    for name, (entry, _) in wheels.items():
        _check_deadline(deadline)
        if wanted[name] != _extras(entry["selected_extras"], "selected extras", deadline):
            raise ProfileError(f"{name}: selected extras do not match full root closure")


@dataclass(frozen=True)
class RuntimeProfile:
    canonical: bytes

    @property
    def lock_id(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical).hexdigest()

    def record(self) -> dict:
        return json.loads(self.canonical)


def validate_profile(
    record: dict, *, max_tags: int = 10_000, deadline: float | None = None,
) -> RuntimeProfile:
    """Freeze a complete asserted record, without attesting its origin/bytes."""
    try:
        deadline = time.monotonic() + 120 if deadline is None else deadline
        _check_deadline(deadline)
        if type(max_tags) is not int or max_tags <= 0:
            raise ProfileError("profile tag limit must be a positive integer")
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
            _check_deadline(deadline)
            _reference(record[key], key)
        if record["mode"] == "artifact":
            artifact = _fields(record["artifact"], {"artifact_id", "manifest_sha256"}, "artifact")
            for key, value in artifact.items():
                _check_deadline(deadline)
                _digest(value, key)
        elif record["mode"] != "source" or record["artifact"] is not None:
            raise ProfileError("source mode must be independent of artifact identity")
        _target(record["target"], max_tags, deadline)
        if record["backend"] != "cpu" and record["backend"] != record["target"]["device"]:
            raise ProfileError("accelerator backend requires its explicit target device")
        _closure(record, max_tags, deadline)
        _check_deadline(deadline)
        canonical = json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        _check_deadline(deadline)
        return RuntimeProfile(canonical)
    except (InvalidVersion, InvalidSpecifier, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError(f"invalid profile: {exc}") from exc


@dataclass(frozen=True)
class SelectedProfile:
    profile: RuntimeProfile
    approval_source: str


def validate_profile_preference(preference: tuple[str, ...]) -> None:
    """Validate desired lock identities before any availability-dependent path."""
    for identity in preference:
        _digest(identity, "profile preference")
    if len(set(preference)) != len(preference):
        raise ProfileError("duplicate profile preference")


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
    validate_profile_preference(preference)
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
        for identity in preference:
            if identity in matches:
                return matches[identity]
        raise ProfileUnavailable("requested approved profile is unavailable")
    if not matches:
        raise ProfileUnavailable("requested release/target/capability profile is unavailable")
    if len(matches) != 1:
        raise ProfileError("ambiguous approved profiles need an explicit identity/order")
    return next(iter(matches.values()))
