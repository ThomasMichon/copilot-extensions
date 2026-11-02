"""Preserve explicitly declared control files from a pinned main commit."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

MANIFEST_PATH = ".github/promotion-control.json"
SCHEMA = "copilot-extensions.promotion-control"
_SHA = re.compile(r"[0-9a-f]{40}")
_WORKFLOW = re.compile(r"\.github/workflows/[a-z0-9][a-z0-9._-]*\.ya?ml")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
             *(f"lpt{i}" for i in range(10))}


class ControlPreservationError(RuntimeError):
    pass


def _git(repo: Path, args: list[str]) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", *args], cwd=repo, env=env, capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlPreservationError(f"cannot read pinned control: {exc}") from exc
    if result.returncode:
        raise ControlPreservationError(
            f"cannot read pinned control: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout


def _tree(repo: Path, main_head: str) -> dict[str, tuple[str, str, str]]:
    entries = {}
    for record in _git(repo, ["ls-tree", "-rz", main_head]).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, blob = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", errors="surrogateescape")
        entries[path] = (mode, kind, blob)
    return entries


def _blob(repo: Path, entry: tuple[str, str, str], *, limit: int) -> bytes:
    mode, kind, blob = entry
    if mode != "100644" or kind != "blob":
        raise ControlPreservationError("control files must be non-executable regular Git blobs")
    if int(_git(repo, ["cat-file", "-s", blob])) > limit:
        raise ControlPreservationError("control file exceeds its size limit")
    return _git(repo, ["cat-file", "blob", blob])


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ControlPreservationError(f"duplicate control manifest key: {key}")
        result[key] = value
    return result


def _paths(raw: bytes) -> list[str]:
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlPreservationError(f"invalid control manifest: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "version", "files"}
        or manifest["schema"] != SCHEMA
        or type(manifest["version"]) is not int or manifest["version"] != 1
        or not isinstance(manifest["files"], list)
        or not 1 <= len(manifest["files"]) <= 64
    ):
        raise ControlPreservationError("unsupported control manifest")
    paths = manifest["files"]
    for path in paths:
        if (
            not isinstance(path, str) or not _WORKFLOW.fullmatch(path)
            or path.rsplit("/", 1)[1].split(".", 1)[0] in _RESERVED
        ):
            raise ControlPreservationError("control declarations must name exact portable top-level workflows")
    if len(set(paths)) != len(paths):
        raise ControlPreservationError("duplicate control path")
    return [MANIFEST_PATH, *sorted(paths)]


def _destination(root: Path, relative: str) -> Path:
    current = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        if current.exists():
            aliases = [child.name for child in current.iterdir() if child.name.casefold() == part.casefold()]
            if aliases and aliases != [part]:
                raise ControlPreservationError(f"case-equivalent control path: {relative}")
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (not stat.S_ISREG(info.st_mode) if index == len(parts) - 1
                else not stat.S_ISDIR(info.st_mode))
        ):
            raise ControlPreservationError(f"unsafe candidate control path: {relative}")
    return current


def _historical_control(repo: Path, prior: str, main_head: str) -> tuple[dict, list[str]]:
    if (
        _git(repo, ["cat-file", "-t", prior]).strip() != b"commit"
        or _git(repo, ["merge-base", prior, main_head]).strip().decode("ascii") != prior
    ):
        raise ControlPreservationError("previous control is not an ancestor of pinned main")
    entries = _tree(repo, prior)
    manifest = entries.get(MANIFEST_PATH)
    if manifest is None:
        raise ControlPreservationError("previous control manifest is missing")
    paths = _paths(_blob(repo, manifest, limit=64 << 10))
    for path in paths:
        if path not in entries:
            raise ControlPreservationError("previous declared control is missing")
        _blob(repo, entries[path], limit=1 << 20)
    return entries, paths


def _read_state(repo: Path, entries: dict) -> dict | None:
    state_entry = entries.get(".github/release-pipeline-state.json")
    if state_entry is None:
        return None
    try:
        state = json.loads(
            _blob(repo, state_entry, limit=1 << 20).decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlPreservationError("invalid main pipeline state") from exc
    if (
        not isinstance(state, dict)
        or state.get("schema") != "copilot-extensions.release-pipeline-state"
        or type(state.get("version")) is not int or state["version"] != 1
        or not isinstance(state.get("last_promotion"), (dict, type(None)))
    ):
        raise ControlPreservationError("unsupported main pipeline state")
    return state


def _previous_paths(repo: Path, main_head: str, entries: dict) -> dict[str, str]:
    """Authenticate deletion scope from Main's previous preservation receipt."""
    state = _read_state(repo, entries)
    if state is None:
        return {}
    previous = (state.get("last_promotion") or {}).get("control_preserved")
    if previous is None:
        return {}
    if (
        not isinstance(previous, dict)
        or set(previous) - {"main_head", "manifest_blob", "files", "retired"}
        or not {"main_head", "manifest_blob", "files"} <= set(previous)
        or not isinstance(previous["main_head"], str)
        or not _SHA.fullmatch(previous["main_head"])
        or not isinstance(previous["files"], dict)
        or not isinstance(previous["manifest_blob"], str)
        or not _SHA.fullmatch(previous["manifest_blob"])
    ):
        raise ControlPreservationError("invalid previous control provenance")
    prior = previous["main_head"]
    old_entries, paths = _historical_control(repo, prior, main_head)
    if old_entries[MANIFEST_PATH][2] != previous["manifest_blob"]:
        raise ControlPreservationError("previous control manifest differs from provenance")
    if set(paths) != set(previous["files"]):
        raise ControlPreservationError("previous control path inventory differs from provenance")
    for path in paths:
        entry = old_entries.get(path)
        if entry is None or entry[2] != previous["files"][path]:
            raise ControlPreservationError("previous control blob differs from provenance")
    retired = previous.get("retired", {})
    if not isinstance(retired, dict) or len(retired) > 64:
        raise ControlPreservationError("invalid retired control provenance")
    snapshots = {prior: (old_entries, paths)}
    for path, origin in retired.items():
        if (
            not isinstance(path, str) or not _WORKFLOW.fullmatch(path)
            or not isinstance(origin, str) or not _SHA.fullmatch(origin)
            or path in paths
        ):
            raise ControlPreservationError("invalid retired control path or origin")
        if origin not in snapshots:
            snapshots[origin] = _historical_control(repo, origin, main_head)
        if path not in snapshots[origin][1]:
            raise ControlPreservationError("retired control was not declared by its origin")
    return {**retired, **dict.fromkeys(paths, prior)}


def _preserve_control(scratch: Path, *, main_head: str, repo: Path) -> dict | None:
    if not _SHA.fullmatch(main_head) or _git(repo, ["cat-file", "-t", main_head]).strip() != b"commit":
        raise ControlPreservationError("control input must be a pinned main commit")
    entries = _tree(repo, main_head)
    candidate_entries = {}
    for record in _git(scratch, ["ls-files", "--stage", "-z"]).split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            candidate_entries[path.decode("utf-8", errors="surrogateescape")] = metadata.split()[0]
    manifest_entry = entries.get(MANIFEST_PATH)
    manifest_aliases = [path for path in entries if path.casefold() == MANIFEST_PATH.casefold()]
    if manifest_aliases and manifest_aliases != [MANIFEST_PATH]:
        raise ControlPreservationError("case-equivalent main control manifest")
    if manifest_entry is None:
        if _previous_paths(repo, main_head, entries):
            raise ControlPreservationError("control ownership removal requires a new plan")
        candidate_manifest = scratch / MANIFEST_PATH
        if (
            any(path.casefold() == MANIFEST_PATH.casefold() for path in candidate_entries)
            or candidate_manifest.exists() or candidate_manifest.is_symlink()
        ):
            raise ControlPreservationError("dev cannot introduce a main control manifest")
        return None
    raw = _blob(repo, manifest_entry, limit=64 << 10)
    paths = _paths(raw)
    retired = {
        path: origin for path, origin in _previous_paths(repo, main_head, entries).items()
        if path not in paths and path not in entries
    }
    if len(retired) > 64:
        raise ControlPreservationError("retired control inventory exceeds its bound")
    candidate_manifest = _destination(scratch, MANIFEST_PATH)
    if candidate_manifest.exists() and candidate_manifest.read_bytes() != raw:
        raise ControlPreservationError("dev carries a conflicting control declaration")
    contents = {}
    for path in [*paths, *sorted(retired)]:
        parts = path.split("/")
        prefixes = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
        for existing in entries:
            for prefix in prefixes:
                if existing[:len(prefix)].casefold() == prefix.casefold() and (
                    len(existing) == len(prefix) or existing[len(prefix)] == "/"
                ) and existing[:len(prefix)] != prefix:
                    raise ControlPreservationError(f"case-equivalent main control path: {path}")
        for prefix in prefixes:
            if candidate_entries.get(prefix) in (b"120000", b"160000"):
                raise ControlPreservationError(f"symlink or submodule candidate control path: {path}")
        _destination(scratch, path)
        if path in retired:
            continue
        if any(existing.startswith(path + "/") for existing in entries):
            raise ControlPreservationError(f"non-file main control path: {path}")
        if path not in entries:
            raise ControlPreservationError(f"missing declared main control file: {path}")
        contents[path] = _blob(repo, entries[path], limit=1 << 20)
    for path in sorted(retired):
        (scratch / path).unlink(missing_ok=True)
    for path, content in contents.items():
        destination = scratch / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(0o644)
    result = {
        "main_head": main_head,
        "manifest_blob": manifest_entry[2],
        "files": {path: entries[path][2] for path in paths},
    }
    if retired:
        result["retired"] = retired
    return result


def preserve_control(scratch: Path, *, main_head: str, repo: Path) -> dict | None:
    """Read only pinned-main declarations; dev cannot activate or redefine them."""
    try:
        return _preserve_control(scratch, main_head=main_head, repo=repo)
    except OSError as exc:
        raise ControlPreservationError(f"cannot preserve main control: {exc}") from exc
