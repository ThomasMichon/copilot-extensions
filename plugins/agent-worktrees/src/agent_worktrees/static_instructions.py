"""Copy declared instructions verbatim into one disposable worktree namespace."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

from plugin_resolve import (
    PLUGIN_MANIFEST_RELS, SETTINGS_RELS, load_marketplace, plugin_dir, split_source,
)
from agent_procutil import no_window_kwargs

NAMESPACE = ".github/instructions/agent-worktrees-generated"
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def _object(path: Path) -> dict:
    from .reconcile import _strict_json_object

    value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_strict_json_object)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _optional_object(path: Path) -> dict | None:
    try:
        return _object(path)
    except FileNotFoundError:
        if path.is_symlink() or any(parent.is_symlink() and not parent.exists() for parent in path.parents):
            raise ValueError(f"broken instruction input link: {path}")
        return None


def _component(value: object) -> str:
    if not isinstance(value, str) or not _COMPONENT.fullmatch(value) or value.endswith("."):
        raise ValueError(f"invalid instruction path component: {value!r}")
    return value


def _settings(root: Path, home: Path) -> tuple[dict, dict]:
    enabled: dict[str, bool] = {}
    markets: dict[str, tuple[dict, Path]] = {}
    layers = [
        (home / ".copilot" / name, home)
        for name in ("settings.json", "settings.local.json")
    ] + [(root.joinpath(*rel), root) for rel in SETTINGS_RELS]
    for path, base in layers:
        data = _optional_object(path)
        if data is None:
            continue
        for key, destination, expected in (
            ("enabledPlugins", enabled, bool), ("extraKnownMarketplaces", markets, dict),
        ):
            values = data.get(key, {})
            if not isinstance(values, dict):
                raise ValueError(f"invalid {key}: {path}")
            for name, value in values.items():
                if not isinstance(value, expected):
                    raise ValueError(f"invalid {key} entry {name}: {path}")
                destination[name] = (value, base) if key == "extraKnownMarketplaces" else value
    return enabled, markets


def _plugin(identity: str, markets: dict, home: Path) -> Path:
    name, market = split_source(identity)
    _component(name)
    if market:
        _component(market)
    definition, base = markets.get(market, ({}, home))
    source = definition.get("source", {})
    if not isinstance(source, dict):
        raise ValueError(f"invalid marketplace source: {market}")
    kind = source.get("source")
    if definition and kind not in ("directory", "local", "agent-worktrees-repo", "github", "git", "git-subdir", "npm", "url"):
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
        if kind == "agent-worktrees-repo" and catalog is not None and catalog.name != market:
            raise ValueError(f"registered marketplace identity mismatch: {market}")
        resolved = plugin_dir(catalog, name) if catalog is not None else None
        if resolved is None:
            raise ValueError(f"cannot resolve enabled directory plugin: {identity}")
        return resolved
    path = home / ".copilot" / "installed-plugins"
    path = path / market / name if market else path / name
    for rel in PLUGIN_MANIFEST_RELS:
        manifest = path.joinpath(*rel)
        if manifest.exists():
            if _object(manifest).get("name") != name:
                raise ValueError(f"installed plugin identity mismatch: {identity}")
            return path
    raise ValueError(f"enabled plugin payload missing: {identity}")


def _snapshot(root: Path, home: Path) -> dict[str, bytes]:
    enabled, markets = _settings(root, home)
    result: dict[str, bytes] = {}
    folded: set[str] = set()
    for identity in sorted(enabled):
        if not enabled[identity]:
            continue
        plugin = _plugin(identity, markets, home).resolve(strict=True)
        declaration = plugin / "instruction-projections.json"
        data = _optional_object(declaration)
        if data is None:
            continue
        if (
            data.get("schema") != "copilot-extensions.instruction-projections"
            or data.get("version") != 1 or not isinstance(data.get("projections"), list)
        ):
            raise ValueError(f"invalid instruction declarations: {declaration}")
        for entry in data["projections"]:
            if not isinstance(entry, dict):
                raise ValueError(f"invalid instruction declaration: {declaration}")
            if entry.get("customizationKind", "instructions") != "instructions":
                continue
            source_id = _component(entry.get("id"))
            template = entry.get("template")
            if not isinstance(template, str):
                raise ValueError(f"missing instruction template: {identity}/{source_id}")
            source = (plugin / template).resolve(strict=True)
            if Path(template).is_absolute() or not source.is_relative_to(plugin):
                raise ValueError(f"instruction template escapes plugin: {identity}/{source_id}")
            name, market = split_source(identity)
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
            relative = f"{market or '_direct'}/{local}"
            key = relative.casefold()
            if any(key == previous or key.startswith(previous + "/") or previous.startswith(key + "/") for previous in folded):
                raise ValueError(f"colliding instruction destination: {relative}")
            folded.add(key)
            result[relative] = source.read_bytes()
    return result


def _git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], input=input, capture_output=True, timeout=15,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        **no_window_kwargs(),
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"instruction Git preflight {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout


def _walk_error(error: OSError) -> None:
    raise error


def _safe_path(root: Path, path: Path) -> None:
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
            raise ValueError(f"instruction output parent is not a directory: {candidate}")


def publish(root: Path, home: Path, *, deadline: float | None = None) -> tuple[int, int, int]:
    """Resolve inside the publication lock; preflight the whole set before copying."""
    from .tracking import _RecordLock

    digest = hashlib.sha256(os.fsencode(os.path.normcase(str(root)))).hexdigest()
    lock = Path(tempfile.gettempdir()) / "agent-worktrees" / "static-instructions" / digest
    remaining = deadline - time.monotonic() if deadline is not None else 30.0
    if remaining <= 0:
        raise TimeoutError("no instruction publication budget remains")
    with _RecordLock(lock, timeout=min(30.0, remaining), require_sidecar=True):
        desired = _snapshot(root, home)
        output = root / NAMESPACE
        _safe_path(root, output)
        existing: set[str] = set()
        if output.exists():
            for parent, directories, files in os.walk(output, followlinks=False, onerror=_walk_error):
                for name in directories + files:
                    path = Path(parent) / name
                    _safe_path(root, path)
                    if name in files:
                        if not path.is_file():
                            raise ValueError(f"instruction output is not a regular file: {path}")
                        existing.add(path.relative_to(output).as_posix())
        # Include staged removals, even when Git could classify one as a rename.
        tracked = (
            _git(root, "ls-files", "-z", "--", ".github")
            + _git(root, "diff", "--cached", "--name-only", "--diff-filter=D", "--no-renames", "-z", "--", ".github")
        ).split(b"\0")
        namespace = NAMESPACE.casefold()
        for raw in tracked:
            path = os.fsdecode(raw).casefold()
            if path and (path == namespace or path.startswith(namespace + "/") or namespace.startswith(path + "/")):
                raise ValueError(f"tracked instruction output is protected: {os.fsdecode(raw)}")
        for relative in desired:
            path = output / relative
            _safe_path(root, path)
            if path.exists() and not path.is_file():
                raise ValueError(f"instruction destination is not a regular file: {path}")
        if desired != _snapshot(root, home):
            raise ValueError("instruction sources changed during preparation; retry publication")
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("instruction preparation exhausted backup budget")
        exclude = Path(os.fsdecode(_git(root, "rev-parse", "--git-path", "info/exclude")).strip())
        if not exclude.is_absolute():
            exclude = root / exclude
        rule = f"/{NAMESPACE}/"
        previous = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if rule not in previous.splitlines():
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8") as handle:
                handle.write(f"\n{rule}\n")
        if desired:
            paths = {os.fsencode(f"{NAMESPACE}/{relative}") for relative in desired}
            ignored = _git(
                root, "check-ignore", "--no-index", "-z", "--stdin",
                input=b"\0".join(sorted(paths)) + b"\0",
            )
            if paths - set(ignored.split(b"\0")):
                raise ValueError("instruction output namespace is not completely ignored by Git")
        changed = unchanged = 0
        for relative, content in desired.items():
            path = output / relative
            if path.exists() and path.read_bytes() == content:
                unchanged += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(content)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            changed += 1
        stale = existing - desired.keys()
        for relative in sorted(stale):
            (output / relative).unlink()
        return changed, unchanged, len(stale)
