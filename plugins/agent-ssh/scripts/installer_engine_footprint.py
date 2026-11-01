"""Maintained reset inventories and independent, read-only reuse recognition.

This data contract neither authorizes an update nor executes cleanup. Native
owners retain path, process, registration, publication and transaction custody.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
import uuid


SCHEMA = "copilot-extensions.installed-footprint"
PROFILE_LISTS = ("root_entries", "bin_entries", "script_entries", "local_bin_entries")
KINDS = {"process", "runtime-state", "registration", "shared-environment", "preserved-state"}


class FootprintError(ValueError):
    """The trusted payload's maintained footprint contract is inconsistent."""


@dataclass(frozen=True)
class Manifest:
    plugin: str
    revision: int
    profile: dict[str, Any]
    effects: dict[str, Any]
    recognizers: dict[str, Any]
    producers: dict[str, str]
    serialization_version: int
    change: dict[str, Any] | None


@dataclass(frozen=True)
class Recognition:
    recognizer: str | None
    reason: str

    @property
    def matched(self) -> bool:
        return self.recognizer is not None


@dataclass(frozen=True)
class ConstructionPlan:
    version: str
    mode: str
    recognition: Recognition


def plan_construction(
    manifest: Manifest, root: Path, version: str, *,
    admission: Callable[[Path, str], str],
) -> ConstructionPlan:
    """Choose reusable construction inputs, never cleanup or publication authority."""
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", version):
        raise FootprintError("Construction requires an immutable runtime version")
    recognized = recognize(manifest, root)
    if recognized.matched:
        selected = _marker_text(root / "current-version").strip()
        separator = "." if "+" in version else "+"
        if re.fullmatch(re.escape(version + separator) + r"repair\.[0-9a-f]{32}", selected):
            if admission(root, selected) == "reuse":
                return ConstructionPlan(selected, "incremental", recognized)
        decision = admission(root, version)
        if decision == "reuse" or (
            decision == "construct" and not os.path.lexists(root / "versions" / version)
        ):
            return ConstructionPlan(version, "incremental", recognized)
    target = root / "versions" / version
    try:
        deployment = decode(_marker_text(root / "deploy-manifest.json"))
        recorded = isinstance(deployment, dict) and deployment.get("runtime_version") == version
    except (OSError, ValueError):
        recorded = False
    if recorded or os.path.lexists(target):
        separator = "." if "+" in version else "+"
        version = f"{version}{separator}repair.{uuid.uuid4().hex}"
    return ConstructionPlan(version, "fresh", recognized)


def decode(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FootprintError(f"Duplicate footprint field: {key}")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def _object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise FootprintError(f"Expected footprint fields: {sorted(keys)}")
    return value


def _relative(value: Any) -> str:
    if (not isinstance(value, str) or not value
            or any(not re.fullmatch(r"[A-Za-z0-9_.+-]+", part) or part in {".", ".."}
                   for part in value.split("/"))):
        raise FootprintError("Footprint producer must be a finite relative file")
    return value


def _range(value: Any) -> tuple[int, int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(type(item) is not int or item < 1 for item in value)
            or value[0] > value[1]):
        raise FootprintError("Invalid footprint contract range")
    return value[0], value[1]


def _items(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Stable artifact identities make additions, edits and retirements explicit."""
    _object(snapshot, {"profile", "effects", "recognizers", "producers"})
    profile = snapshot["profile"]
    required = {"version", "module", *PROFILE_LISTS}
    if (not isinstance(profile, dict) or not required <= profile.keys()
            or profile.keys() - required - {"guard_module", "guard_function", "nested_engine"}
            or type(profile.get("version")) is not int or profile["version"] != 1
            or not isinstance(profile["module"], str)
            or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", profile["module"])):
        raise FootprintError("A footprint revision requires a finite v1 removal profile")
    result: dict[str, Any] = {}
    for key, value in profile.items():
        if key in PROFILE_LISTS:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise FootprintError(f"Invalid footprint profile list: {key}")
            if len(set(value)) != len(value):
                raise FootprintError(f"Duplicate footprint profile entry: {key}")
            for item in value:
                _relative(item)
                if key in {"root_entries", "local_bin_entries"} and "/" in item:
                    raise FootprintError("Root and command footprint entries must be finite leaves")
            result.update((f"{key}:{item}", True) for item in value)
        else:
            result[f"profile:{key}"] = value
    for category in ("effects", "recognizers", "producers"):
        entries = snapshot[category]
        if not isinstance(entries, dict):
            raise FootprintError(f"Invalid footprint {category}")
        for name, value in entries.items():
            if (not isinstance(name, str) or not name
                    or (category != "producers" and not re.fullmatch(r"[A-Za-z0-9_.+-]+", name))):
                raise FootprintError("Empty footprint identity")
            result[f"{category}:{name}"] = value
    return result


def delta(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, list[str]]:
    before = _items(previous) if previous is not None else {}
    after = _items(current)
    return {
        "introduced": sorted(after.keys() - before.keys()),
        "changed": sorted(key for key in before.keys() & after.keys() if before[key] != after[key]),
        "retired": sorted(before.keys() - after.keys()),
    }


def _validate_snapshot(snapshot: Any) -> None:
    _items(snapshot)
    for path, digest in snapshot["producers"].items():
        _relative(path)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise FootprintError("Invalid footprint producer digest")
    if not snapshot["producers"]:
        raise FootprintError("A footprint must name its actual producers")
    for effect in snapshot["effects"].values():
        _object(effect, {"kind", "scope", "entries", "disposition"})
        if (not isinstance(effect["kind"], str) or effect["kind"] not in KINDS
                or not isinstance(effect["scope"], str)
                or effect["scope"] not in {"runtime-root", "installation", "user-environment"}
                or not isinstance(effect["disposition"], str)
                or effect["disposition"] not in {"preserve", "retire", "reset"}
                or not isinstance(effect["entries"], list)
                or not all(isinstance(item, str) and item for item in effect["entries"])
                or len(set(effect["entries"])) != len(effect["entries"])):
            raise FootprintError("Invalid footprint effect descriptor")
        if (effect["kind"] in {"preserved-state", "shared-environment"}
                and effect["disposition"] != "preserve"):
            raise FootprintError("Protected/shared state cannot be declared disposable")
    for recognizer in snapshot["recognizers"].values():
        _object(recognizer, {"kind", "footprint", "deployment_schema", "completion_contract"})
        if recognizer["kind"] != "versioned-runtime-v1":
            raise FootprintError("Unsupported footprint recognizer provider")
        for key in ("footprint", "deployment_schema", "completion_contract"):
            _range(recognizer[key])


def _validate_change(change: Any, revision: int) -> None:
    if change is None:
        return
    _object(change, {"from_revision", "delta", "dispositions"})
    if type(change["from_revision"]) is not int or change["from_revision"] != revision - 1:
        raise FootprintError("A footprint change must name the preceding contract revision")
    assessment = _object(change["delta"], {"introduced", "changed", "retired"})
    for entries in assessment.values():
        if (not isinstance(entries, list) or not all(isinstance(item, str) and item for item in entries)
                or entries != sorted(set(entries))):
            raise FootprintError("Footprint change identities must be unique and sorted")
    dispositions = change["dispositions"]
    if not isinstance(dispositions, dict) or set(dispositions) != set(assessment["retired"]):
        raise FootprintError("Every retired footprint identity requires an explicit disposition")
    for disposition in dispositions.values():
        if not isinstance(disposition, dict):
            raise FootprintError("Invalid footprint retirement disposition")
        action = disposition.get("action")
        _object(disposition, {"action", "reason", "target"} if action == "retained" else {"action", "reason"})
        if (not isinstance(action, str) or action not in {"retained", "preserve-unknown"}
                or not isinstance(disposition["reason"], str) or not disposition["reason"].strip()
                or (action == "retained" and (
                    not isinstance(disposition["target"], str) or not disposition["target"]
                ))):
            raise FootprintError("Unsupported footprint retirement disposition")


def parse(record: Any, plugin: str) -> Manifest:
    """Normalize supported documents; source revisions are not installed layouts."""
    if (not isinstance(record, dict) or type(record.get("version")) is not int
            or record.get("schema") != SCHEMA or record.get("plugin") != plugin):
        raise FootprintError("Invalid installed-footprint identity")
    version = record["version"]
    change = None
    if version == 2:
        _object(record, {"version", "schema", "plugin", "history"})
        if not isinstance(record["history"], list) or not record["history"]:
            raise FootprintError("Invalid installed-footprint history")
        previous = None
        for number, revision in enumerate(record["history"], 1):
            _object(revision, {"revision", "snapshot", "delta"})
            if type(revision["revision"]) is not int or revision["revision"] != number:
                raise FootprintError("Footprint revisions must be contiguous and ordered")
            snapshot = revision["snapshot"]
            _validate_snapshot(snapshot)
            if revision["delta"] != delta(previous, snapshot):
                raise FootprintError(f"Footprint revision {number} omits or misstates its delta")
            previous = snapshot
        assert previous is not None
        snapshot = previous
        number = len(record["history"])
    elif version == 3:
        _object(record, {"version", "schema", "plugin", "revision", "contract", "producers", "change"})
        number = record["revision"]
        if type(number) is not int or number < 1:
            raise FootprintError("Invalid footprint contract revision")
        contract = _object(record["contract"], {"profile", "effects", "recognizers"})
        snapshot = {**contract, "producers": record["producers"]}
        _validate_snapshot(snapshot)
        change = record["change"]
        _validate_change(change, number)
    else:
        raise FootprintError(f"Unsupported installed-footprint serialization version: {version}")
    return Manifest(plugin, number, **snapshot, serialization_version=version, change=change)


def _contract_items(manifest: Manifest) -> dict[str, Any]:
    items = _items({key: getattr(manifest, key) for key in ("profile", "effects", "recognizers", "producers")})
    for category in PROFILE_LISTS:
        for entry in manifest.profile[category]:
            items[f"{category}:{entry}"] = {"scope": category, "entry": entry}
    for name, effect in manifest.effects.items():
        del items[f"effects:{name}"]
        for key in ("kind", "scope", "disposition"):
            items[f"effects:{name}:{key}"] = effect[key]
        for entry in effect["entries"]:
            items[f"effects:{name}:entry:{entry}"] = {
                key: effect[key] for key in ("kind", "scope", "disposition")
            } | {"entry": entry}
    # Scope coverage changes matter; source hash refreshes do not.
    for name in manifest.producers:
        items[f"producers:{name}"] = {"scope": name}
    return items


def contract_delta(previous: Manifest, current: Manifest) -> dict[str, list[str]]:
    before, after = _contract_items(previous), _contract_items(current)
    return {
        "introduced": sorted(after.keys() - before.keys()),
        "changed": sorted(key for key in before.keys() & after.keys() if before[key] != after[key]),
        "retired": sorted(before.keys() - after.keys()),
    }


def require_transition(previous: Any, current: Any, plugin: str) -> None:
    """Require intentional semantic changes against the real published baseline."""
    before, after = parse(previous, plugin), parse(current, plugin)
    if after.serialization_version != 3:
        raise FootprintError("Source declarations must use the current footprint serialization")
    assessment = contract_delta(before, after)
    if not any(assessment.values()):
        if after.revision != before.revision or after.change != before.change:
            raise FootprintError("Source-only changes must preserve the footprint revision and assessment")
        return
    if (after.revision != before.revision + 1 or after.change is None
            or after.change["from_revision"] != before.revision
            or after.change["delta"] != assessment):
        raise FootprintError("Semantic footprint changes require the next revision and exact assessment")
    old_items, new_items = _contract_items(before), _contract_items(after)
    for identity, disposition in after.change["dispositions"].items():
        if disposition["action"] == "retained":
            target = disposition["target"]
            if target not in new_items or new_items[target] != old_items[identity]:
                raise FootprintError(f"Retired footprint is not retained by its declared target: {identity}")


def _plain_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        value = candidate.lstat()
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise FootprintError("Redirected footprint evidence is not reusable")


def _plain_file(path: Path) -> None:
    _plain_path(path)
    value = path.stat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise FootprintError("Footprint evidence must be an ordinary unshared file")


def _bounded_text(path: Path, limit: int, kind: str) -> str:
    _plain_file(path)
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise FootprintError(f"Footprint {kind} exceeds its read budget")
    return content.decode("utf-8-sig")


def _marker_text(path: Path) -> str:
    return _bounded_text(path, 65536, "marker")


def _manifest_text(path: Path) -> str:
    return _bounded_text(path, 1 << 20, "manifest")


def producer_problems(manifest: Manifest, payload: Path) -> list[str]:
    problems = []
    for relative, expected in manifest.producers.items():
        try:
            if producer_digest(payload, relative) != expected:
                problems.append(f"{relative}: producer changed without refreshing its source digest")
        except (OSError, ValueError) as error:
            problems.append(f"{relative}: producer cannot be verified ({error})")
    return problems


def producer_digest(payload: Path, relative: str) -> str:
    """Cover new/removed producers too, without hashing this manifest into itself."""
    path = payload.joinpath(*_relative(relative).split("/"))
    _plain_path(path)
    paths = sorted(path.rglob("*"), key=lambda item: item.as_posix()) if path.is_dir() else [path]
    digest = hashlib.sha256()
    for candidate in paths:
        local = candidate.relative_to(payload)
        if (any(part == "__pycache__" or part.endswith(".egg-info") for part in local.parts)
                or candidate.name.endswith((".pyc", ".pyo"))
                or local.as_posix() == "scripts/uninstall-profile.json"):
            continue
        # Inspect directories too: rglob does not traverse a redirected child,
        # but silently ignoring that child would make completeness unprovable.
        if candidate.is_dir():
            _plain_path(candidate)
            continue
        _plain_file(candidate)
        name = local.as_posix().encode("utf-8")
        content = candidate.read_bytes().replace(b"\r\n", b"\n")
        if (len(local.parts) == 3 and local.parts[0] == "src"
                and candidate.name in {"__init__.py", "_build_info.py"}):
            # Promotion owns these version literals, not the surrounding code.
            content = re.sub(
                rb'^(\s*(?:__version__|_FALLBACK_VERSION)\s*(?::\s*str\s*)?=\s*["\'])'
                rb'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?(["\'])',
                rb'\g<1><release-version>\g<2>', content, flags=re.MULTILINE,
            )
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    if not paths or not path.exists():
        raise FootprintError("Missing footprint producer")
    return digest.hexdigest()


def recognize(manifest: Manifest, root: Path, *, footprint_revision: int = 1) -> Recognition:
    """Recognize layout ability only; absence/corruption selects fresh, not refusal.

    The caller still owns source/content, lifecycle, health and custody checks.
    No installed code is imported or executed, and the reset inventory survives
    every no-match result. The known legacy marker layout is revision 1; callers
    with a newer observed layout supply its revision, not the incoming revision.
    No new installed receipt is required.
    """
    try:
        if type(footprint_revision) is not int or footprint_revision < 1:
            return Recognition(None, "footprint-unknown")
        root = Path(os.path.abspath(root))
        deployment_path = root / "deploy-manifest.json"
        selection_path = root / "current-version"
        deployment = decode(_marker_text(deployment_path))
        selected = _marker_text(selection_path).strip()
        if not re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", selected):
            return Recognition(None, "selection-unrecognized")
        complete_path = root / "versions" / selected / ".install-complete.json"
        complete = decode(_marker_text(complete_path))
        if (not isinstance(deployment, dict) or deployment.get("service") != manifest.plugin
                or type(deployment.get("schema_version")) is not int
                or deployment.get("runtime_version") != selected
                or not isinstance(complete, dict)
                or set(complete) - {"version", "completed_at", "pid", "payload_hash"}
                or complete.get("version") != selected
                or not isinstance(complete.get("completed_at"), str)
                or type(complete.get("pid")) is not int or complete["pid"] < 0
                or ("payload_hash" in complete and not isinstance(complete["payload_hash"], str))):
            return Recognition(None, "markers-unrecognized")
        for name, recognizer in manifest.recognizers.items():
            values = {"footprint": footprint_revision, "deployment_schema": deployment["schema_version"],
                      "completion_contract": 1}
            if all(low <= values[key] <= high
                   for key in values for low, high in (_range(recognizer[key]),)):
                return Recognition(name, "layout-compatible")
        return Recognition(None, "contracts-unsupported")
    except (OSError, ValueError):
        return Recognition(None, "evidence-unavailable")
