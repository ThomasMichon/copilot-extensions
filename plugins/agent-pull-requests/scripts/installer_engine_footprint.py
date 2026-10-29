"""Maintained reset inventories and independent, read-only reuse recognition.

This data contract neither authorizes an update nor executes cleanup. Native
owners retain path, process, registration, publication and transaction custody.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


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
    history: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Recognition:
    recognizer: str | None
    reason: str

    @property
    def matched(self) -> bool:
        return self.recognizer is not None


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
    if not isinstance(profile, dict) or profile.get("version") != 1:
        raise FootprintError("A footprint revision requires a finite v1 removal profile")
    result: dict[str, Any] = {}
    for key, value in profile.items():
        if key in PROFILE_LISTS:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise FootprintError(f"Invalid footprint profile list: {key}")
            if len(set(value)) != len(value):
                raise FootprintError(f"Duplicate footprint profile entry: {key}")
            result.update((f"{key}:{item}", True) for item in value)
        else:
            result[f"profile:{key}"] = value
    for category in ("effects", "recognizers", "producers"):
        entries = snapshot[category]
        if not isinstance(entries, dict):
            raise FootprintError(f"Invalid footprint {category}")
        for name, value in entries.items():
            if not isinstance(name, str) or not name:
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


def parse(record: Any, plugin: str) -> Manifest:
    _object(record, {"version", "schema", "plugin", "history"})
    if (type(record["version"]) is not int or record["version"] != 2
            or record["schema"] != SCHEMA or record["plugin"] != plugin
            or not isinstance(record["history"], list) or not record["history"]):
        raise FootprintError("Invalid installed-footprint identity or history")
    previous = None
    for number, revision in enumerate(record["history"], 1):
        _object(revision, {"revision", "snapshot", "delta"})
        if type(revision["revision"]) is not int or revision["revision"] != number:
            raise FootprintError("Footprint revisions must be contiguous and ordered")
        snapshot = revision["snapshot"]
        if revision["delta"] != delta(previous, snapshot):
            raise FootprintError(f"Footprint revision {number} omits or misstates its delta")
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
                    or not all(isinstance(item, str) and item for item in effect["entries"])):
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
        previous = snapshot
    assert previous is not None
    return Manifest(
        plugin, len(record["history"]), **previous,
        history=tuple(revision["snapshot"] for revision in record["history"]),
    )


def require_append_only(previous: Any, current: Any, plugin: str) -> None:
    """Source-diff guards can reject rewriting the compatibility/reset history."""
    parse(previous, plugin)
    parse(current, plugin)
    old = previous["history"]
    if current["history"][:len(old)] != old:
        raise FootprintError("Published footprint history is append-only")


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


def _marker_text(path: Path) -> str:
    _plain_file(path)
    with path.open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise FootprintError("Footprint marker exceeds its read budget")
    return content.decode("utf-8-sig")


def producer_problems(manifest: Manifest, payload: Path) -> list[str]:
    problems = []
    for relative, expected in manifest.producers.items():
        try:
            if producer_digest(payload, relative) != expected:
                problems.append(f"{relative}: producer changed without its footprint revision/delta")
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
