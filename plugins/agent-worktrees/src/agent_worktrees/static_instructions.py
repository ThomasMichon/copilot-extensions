"""Copy declared instructions verbatim into one disposable worktree namespace."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unicodedata

from plugin_resolve import (
    PLUGIN_MANIFEST_RELS, RepoPluginSettings, SETTINGS_RELS, load_marketplace, marketplace_manifest_name,
    plugin_dir, split_source,
)
from plugin_activation import validate_identity
from plugin_activation._installed_root import _installed_root
from plugin_activation.state import _reject_duplicates, _split_jsonc_header

from . import static_preparation as preparation

NAMESPACE = ".github/instructions/agent-worktrees-generated"
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_PATHSPEC_ENV = {
    "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS",
    "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS",
}


def _remaining(deadline: float | None, ceiling: float | None = None) -> float | None:
    if deadline is None:
        return ceiling
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("instruction publication budget exhausted")
    return min(remaining, ceiling) if ceiling is not None else remaining


def _read_regular_bytes(path: Path) -> bytes:
    preparation.observe(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"instruction input is not a regular file: {path}")
    content = path.read_bytes()
    preparation.observe(path)
    preparation.remember_bytes(path, content)
    return content


def _object(path: Path, *, jsonc_header: bool = False) -> dict:
    raw = _read_regular_bytes(path).decode("utf-8-sig")
    if jsonc_header:
        _, raw = _split_jsonc_header(raw)
    value = json.loads(raw, object_pairs_hook=_reject_duplicates)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _optional_object(path: Path, *, jsonc_header: bool = False) -> dict | None:
    try:
        return _object(path, jsonc_header=jsonc_header)
    except FileNotFoundError:
        if path.is_symlink() or any(parent.is_symlink() and not parent.exists() for parent in path.parents):
            raise ValueError(f"broken instruction input link: {path}")
        return None


def _component(value: object) -> str:
    if (
        not isinstance(value, str) or not value or value in (".", "..")
        or value.endswith((" ", "."))
        or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in value)
        or value.split(".", 1)[0].casefold() in _RESERVED_NAMES
    ):
        raise ValueError(f"invalid instruction path component: {value!r}")
    return value


def _settings(root: Path, home: Path, *, deadline: float | None = None) -> tuple[dict, dict]:
    enabled: dict[str, bool] = {}
    markets: dict[str, tuple[dict, Path]] = {}
    layers = [
        (home / ".copilot" / name, home / ".copilot")
        for name in ("settings.json", "settings.local.json")
    ] + [(root.joinpath(*rel), root) for rel in SETTINGS_RELS]
    for path, base in layers:
        _remaining(deadline)
        data = _optional_object(path, jsonc_header=True)
        if data is None:
            continue
        for key, destination, expected in (
            ("enabledPlugins", enabled, bool), ("extraKnownMarketplaces", markets, dict),
        ):
            values = data.get(key, {})
            if not isinstance(values, dict):
                raise ValueError(f"invalid {key}: {path}")
            for name, value in values.items():
                if key == "enabledPlugins":
                    if "@" not in name:
                        continue
                    validate_identity(name)
                if not isinstance(value, expected):
                    raise ValueError(f"invalid {key} entry {name}: {path}")
                destination[name] = (value, base) if key == "extraKnownMarketplaces" else value
    return enabled, markets


def _plugin(identity: str, markets: dict, home: Path, *, deadline: float | None = None) -> Path:
    _remaining(deadline)
    name, market = split_source(identity)
    _component(name)
    _component(market)
    definition, base = markets.get(market, ({}, home))
    source = definition.get("source", {})
    if not isinstance(source, dict):
        raise ValueError(f"invalid marketplace source: {market}")
    kind = source.get("source")
    if market in markets and kind not in ("directory", "local", "agent-worktrees-repo", "github", "git", "git-subdir", "npm", "url"):
        raise ValueError(f"invalid marketplace source kind: {market}")
    if kind in ("directory", "local", "agent-worktrees-repo"):
        if kind == "agent-worktrees-repo":
            from .repos import resolve_registered_repo

            repo = source.get("repo")
            if not isinstance(repo, str) or not repo.strip():
                raise ValueError(f"missing registered marketplace repo: {market}")
            _, path = resolve_registered_repo(repo)
            configured_path = source.get("path", ".ai")
            if not isinstance(configured_path, str) or not configured_path.strip():
                raise ValueError(f"missing registered marketplace path: {market}")
            configured = Path(configured_path).expanduser()
            path = configured if configured.is_absolute() else path / configured
        else:
            configured_path = source.get("path")
            if not isinstance(configured_path, str) or not configured_path.strip():
                raise ValueError(f"missing directory marketplace path: {market}")
            path = Path(configured_path).expanduser()
            if not path.is_absolute():
                path = base / path
        catalog = load_marketplace(path)
        _remaining(deadline)
        expected_name = (
            market if kind == "agent-worktrees-repo"
            else marketplace_manifest_name(market, RepoPluginSettings(marketplaces={market: definition}))
        )
        if catalog is not None and catalog.name != expected_name:
            raise ValueError(f"marketplace identity mismatch: {market}")
        resolved = plugin_dir(catalog, name) if catalog is not None else None
        if resolved is None:
            raise ValueError(f"cannot resolve enabled directory plugin: {identity}")
        return resolved
    candidate = _installed_root(home / ".copilot", identity)
    if candidate.root is None:
        detail = "; ".join(finding.detail or finding.reason for finding in candidate.findings)
        raise ValueError(f"enabled plugin payload unavailable: {identity}: {detail or 'missing payload'}")
    return candidate.root


def _selection(root: Path, home: Path, *, deadline: float | None = None) -> tuple[list, dict, dict]:
    enabled, markets = _settings(root, home, deadline=deadline)
    selection, plugins, failures = [], {}, {}
    for identity in sorted(enabled):
        _remaining(deadline)
        if not enabled[identity]:
            continue
        try:
            plugin = _plugin(identity, markets, home, deadline=deadline).resolve(strict=True)
            selection.append([identity, str(plugin), [
                preparation.stamp(
                    plugin.joinpath(*relative), content_digest=True, allow_links=True, deadline=deadline,
                )
                for relative in PLUGIN_MANIFEST_RELS
            ]])
            plugins[identity] = plugin
        except (OSError, ValueError, RuntimeError) as exc:
            failures[identity] = f"{identity}: {exc}"
    return selection, plugins, failures


class _Snapshot(dict):
    def __init__(self, selection: list, failures: dict):
        super().__init__()
        self.selection = selection
        self.failures = list(failures.values())
        self.failed_sources = set(failures)
        self.owners: dict[str, str] = {}


def _snapshot(root: Path, home: Path, *, deadline: float | None = None) -> _Snapshot:
    selection, plugins, failures = _selection(root, home, deadline=deadline)
    result = _Snapshot(selection, failures)
    folded: set[str] = set()
    for identity, plugin in plugins.items():
        _remaining(deadline)
        try:
            entries = _plugin_snapshot(root, identity, plugin, deadline=deadline)
        except TimeoutError:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            result.failures.append(f"{identity}: {exc}")
            result.failed_sources.add(identity)
            continue
        for relative, content in entries.items():
            key = unicodedata.normalize("NFC", relative).casefold()
            if any(key == previous or key.startswith(previous + "/") or previous.startswith(key + "/") for previous in folded):
                raise ValueError(f"colliding instruction destination: {relative}")
            folded.add(key)
            result[relative] = content
            result.owners[relative] = identity
    _remaining(deadline)
    return result


def _plugin_snapshot(root: Path, identity: str, plugin: Path, *, deadline: float | None) -> dict:
    source_key = unicodedata.normalize("NFC", plugin.as_posix()).casefold()
    output_key = unicodedata.normalize("NFC", (root / NAMESPACE).as_posix()).casefold()
    if source_key == output_key or source_key.startswith(output_key + "/"):
        raise ValueError(f"instruction source overlaps the disposable output namespace: {identity}")
    declaration = plugin / "instruction-projections.json"
    data = _optional_object(declaration)
    if data is None:
        return {}
    if (
        data.get("schema") != "copilot-extensions.instruction-projections"
        or type(data.get("version")) is not int or data["version"] != 1
        or not isinstance(data.get("projections"), list)
    ):
        raise ValueError(f"invalid instruction declarations: {declaration}")
    result, folded = {}, set()
    for entry in data["projections"]:
        _remaining(deadline)
        if not isinstance(entry, dict):
            raise ValueError(f"invalid instruction declaration: {declaration}")
        if entry.get("customizationKind", "instructions") != "instructions":
            raise ValueError(f"unsupported instruction customization kind: {declaration}")
        source_id = entry.get("id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"missing instruction identity: {identity}")
        template = entry.get("template")
        if not isinstance(template, str):
            raise ValueError(f"missing instruction template: {identity}/{source_id}")
        preparation.observe(plugin / template)
        source = (plugin / template).resolve(strict=True)
        if Path(template).is_absolute() or not source.is_relative_to(plugin):
            raise ValueError(f"instruction template escapes plugin: {identity}/{source_id}")
        source_key = unicodedata.normalize("NFC", source.as_posix()).casefold()
        if source_key == output_key or source_key.startswith(output_key + "/"):
            raise ValueError(f"instruction template overlaps disposable output: {identity}/{source_id}")
        _, market = split_source(identity)
        destination = entry.get("destination")
        prefix = ".github/instructions/"
        if (
            not isinstance(destination, str) or not destination.startswith(prefix)
            or not destination.endswith(".instructions.md") or "\\" in destination
        ):
            raise ValueError(f"invalid instruction destination: {identity}/{source_id}")
        parts = destination[len(prefix):].split("/")
        for part in parts:
            _component(part)
        local = "/".join(parts)[:-len(".instructions.md")] + ".local.instructions.md"
        relative = f"{market}/{local}"
        key = unicodedata.normalize("NFC", relative).casefold()
        if any(key == previous or key.startswith(previous + "/") or previous.startswith(key + "/") for previous in folded):
            raise ValueError(f"colliding instruction destination: {relative}")
        folded.add(key)
        result[relative] = _read_regular_bytes(source)
    return result


def _git(
    root: Path, *args: str, input: bytes | None = None, deadline: float | None = None,
) -> bytes:
    from .push_timeout import run_bounded
    from .git_ops import repository_identity_env

    env = {
        key: value for key, value in repository_identity_env().items()
        if key.upper() not in _PATHSPEC_ENV
    }
    owned_group = (
        deadline is None and os.name != "nt"
        and os.environ.get("WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP") == "1"
    )
    try:
        result = run_bounded(
            ["git", "-C", str(root), *args], cwd=None, input=input, binary=True,
            timeout=_remaining(deadline, 15.0), env=env,
            inherit_process_group=owned_group,
        )
    except subprocess.TimeoutExpired as exc:
        if owned_group:
            # Only the outer owner can sweep this shared group safely.
            raise SystemExit(
                "instruction Git preflight timed out; exiting Manager preparation "
                "so its owner can reap remaining descendants"
            ) from exc
        raise
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"instruction Git preflight {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout


def _walk_error(error: OSError) -> None:
    raise error


def _protect_partial_outputs(
    root: Path, home: Path, desired: _Snapshot, existing: set[str], *, deadline: float | None,
) -> dict:
    previous = preparation.ownership_history(root, home, deadline=deadline)
    folded = {path: unicodedata.normalize("NFC", path).casefold() for path in existing}
    for relative in list(desired):
        _remaining(deadline)
        key = unicodedata.normalize("NFC", relative).casefold()
        overlaps = [
            path for path, value in folded.items()
            if key == value or key.startswith(value + "/") or value.startswith(key + "/")
        ]
        if not overlaps:
            continue
        record = previous.get(relative)
        proven = (
            overlaps == [relative] and isinstance(record, dict)
            and isinstance(record.get("source"), str) and isinstance(record.get("sha256"), str)
        )
        if proven:
            try:
                validate_identity(record["source"])
                proven = (
                    record["source"] not in desired.failed_sources
                    and hashlib.sha256(_read_regular_bytes(root / NAMESPACE / relative)).hexdigest()
                    == record["sha256"]
                )
            except (OSError, ValueError, KeyError, TypeError):
                proven = False
        if not proven:
            desired.failures.append(f"{desired.owners[relative]}: partial output ownership is unconfirmed: {relative}")
            del desired[relative]
            del desired.owners[relative]
    return previous


def pair_pending_path(root: Path) -> Path:
    from .local_cache_worker import metadata_directory

    directory = metadata_directory(root)
    if directory is None:
        raise ValueError("cannot resolve Git metadata for pending paired preparation")
    return directory / "pair-pending"


def publication_lock(root: Path, *, deadline: float | None = None):
    from .tracking import _RecordLock

    digest = hashlib.sha256(os.fsencode(unicodedata.normalize("NFC", str(root)).casefold())).hexdigest()
    lock = Path(tempfile.gettempdir()) / "agent-worktrees" / "static-instructions" / digest
    return _RecordLock(lock, timeout=_remaining(deadline, 30.0), require_sidecar=True)


def _safe_path(root: Path, path: Path, *, removable_files: set[Path] | None = None) -> None:
    path.relative_to(root)
    for candidate in reversed((path, *path.parents)):
        if candidate == root or not candidate.is_relative_to(root):
            continue
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"instruction output redirects through a link: {candidate}")
        if candidate != path and not stat.S_ISDIR(info.st_mode):
            if removable_files and candidate in removable_files and stat.S_ISREG(info.st_mode):
                return
            raise ValueError(f"instruction output parent is not a directory: {candidate}")


def current(root: Path, home: Path, *, deadline: float | None = None) -> preparation.Publication | None:
    selection, _, failures = _selection(root, home, deadline=deadline)
    return None if failures else preparation.read(root, home, selection, deadline=deadline)


def publish(root: Path, home: Path, *, deadline: float | None = None) -> preparation.Publication:
    """Resolve inside the publication lock; preflight the whole set before copying."""
    with publication_lock(root, deadline=deadline):
        try:
            pair_pending_path(root).lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError("paired worktree preparation is incomplete; repair the pair before publishing instructions")
        cached = current(root, home, deadline=deadline)
        if cached is not None:
            return cached
        with preparation.capture() as inputs:
            desired = _snapshot(root, home, deadline=deadline)
        if desired.failures and not desired:
            raise ValueError("; ".join(desired.failures))
        output = root / NAMESPACE
        _safe_path(root, output)
        existing: set[str] = set()
        existing_directories: set[Path] = set()
        if output.exists():
            for parent, directories, files in os.walk(output, followlinks=False, onerror=_walk_error):
                _remaining(deadline)
                for name in directories + files:
                    path = Path(parent) / name
                    _safe_path(root, path)
                    if name in files:
                        if not path.is_file():
                            raise ValueError(f"instruction output is not a regular file: {path}")
                        existing.add(path.relative_to(output).as_posix())
                    else:
                        existing_directories.add(path)
        previous_owners = (
            _protect_partial_outputs(root, home, desired, existing, deadline=deadline)
            if desired.failures else {}
        )
        stale = set() if desired.failures else existing - desired.keys()
        removable_files = {output / relative for relative in stale}
        exclude = Path(os.fsdecode(_git(root, "rev-parse", "--git-path", "info/exclude", deadline=deadline)).strip())
        if not exclude.is_absolute():
            exclude = root / exclude
        # Git's index queries may read ignore rules before our later append.
        if exclude.exists():
            _read_regular_bytes(exclude)
        validated_git = preparation.git_state(root, deadline=deadline)
        # Include staged removals, even when Git could classify one as a rename.
        tracked = (
            _git(root, "ls-files", "-z", "--", ":(icase).github", deadline=deadline)
            + _git(root, "diff", "--cached", "--name-only", "--diff-filter=D", "--no-renames", "-z", "--", ":(icase).github", deadline=deadline)
        ).split(b"\0")
        # The exclude file is reread and checked by check-ignore below; an
        # independent append must not be lost while index evidence stays fixed.
        for path in validated_git:
            if Path(path).resolve() == exclude.resolve():
                validated_git[path] = preparation.stamp(
                    Path(path), content_digest=True, allow_links=True, deadline=deadline,
                )
        preparation.require_git_state(root, validated_git, deadline=deadline)
        namespace = NAMESPACE.casefold()
        for raw in tracked:
            path = os.fsdecode(raw).casefold()
            if path and (path == namespace or path.startswith(namespace + "/") or namespace.startswith(path + "/")):
                raise ValueError(f"tracked instruction output is protected: {os.fsdecode(raw)}")
        for relative in desired:
            path = output / relative
            _safe_path(root, path, removable_files=removable_files)
            if path.exists() and not path.is_file() and not path.is_dir():
                raise ValueError(f"instruction destination is not a regular file: {path}")
        selected, _, _ = _selection(root, home, deadline=deadline)
        if not preparation.matches(inputs, deadline=deadline) or desired.selection != selected:
            raise ValueError("instruction sources changed during preparation; retry publication")
        rule = f"/{NAMESPACE}/"
        previous = _read_regular_bytes(exclude).decode("utf-8") if exclude.exists() else ""
        if rule not in previous.splitlines():
            _remaining(deadline)
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8") as handle:
                handle.write(f"\n{rule}\n")
            for path in validated_git:
                if Path(path).resolve() == exclude.resolve():
                    validated_git[path] = preparation.stamp(
                        Path(path), content_digest=True, allow_links=True, deadline=deadline,
                    )
        if desired:
            paths = {os.fsencode(f"{NAMESPACE}/{relative}") for relative in desired}
            ignored = _git(
                root, "check-ignore", "--no-index", "-z", "--stdin",
                input=b"\0".join(sorted(paths)) + b"\0", deadline=deadline,
            )
            if paths - set(ignored.split(b"\0")):
                raise ValueError("instruction output namespace is not completely ignored by Git")
        preparation.require_git_state(root, validated_git, deadline=deadline)
        revision = preparation.publication_revision(root)
        mutation_started = False

        def note_mutation() -> None:
            nonlocal revision, mutation_started
            if not mutation_started:
                revision = preparation.record_publication(root, changed=True)
                mutation_started = True

        for relative in sorted(stale):
            _remaining(deadline)
            note_mutation()
            _remaining(deadline)
            (output / relative).unlink()
        retained_parents = {
            parent
            for relative in existing & desired.keys()
            for parent in (output / relative).parents
        }
        for directory in sorted(
            () if desired.failures else existing_directories,
            key=lambda path: len(path.parts), reverse=True,
        ):
            if directory not in retained_parents:
                _remaining(deadline)
                directory.rmdir()
        changed = unchanged = 0
        for relative, content in desired.items():
            _remaining(deadline)
            path = output / relative
            if path.exists() and _read_regular_bytes(path) == content:
                unchanged += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(content)
                _remaining(deadline)
                note_mutation()
                _remaining(deadline)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            changed += 1
        _remaining(deadline)
        if not preparation.matches(inputs, deadline=deadline):
            raise ValueError("instruction sources changed during publication; retry preparation")
        preparation.require_git_state(root, validated_git, deadline=deadline)
        identity = preparation.save(
            root, home, selected, inputs, desired, NAMESPACE,
            validated_git=validated_git, revision=revision, deadline=deadline,
            ready=not desired.failures, sources=desired.owners, previous_owners=previous_owners,
        )
        return preparation.Publication(
            changed, unchanged, len(stale), identity, failures=tuple(desired.failures),
            publication_revision=revision,
        )
