"""Manifest target resolution and runtime pivot materialization helpers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import cast

from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from .pivot_actions import ManifestError
from .pivot_manifest import (
    _FILE_ATTRIBUTE_REPARSE_POINT,
    MANAGED_SCHEMA_VERSION,
)


class TargetUnusableError(ValueError):
    """A manifest command exists but cannot be executed safely."""


def _activation_from_plugins_root(root: Path) -> ActivationReport:
    """Build a synthetic active report for ``ensure_pivots`` unit tests."""
    decisions: dict[str, EntryDecision[ActivePlugin]] = {}
    try:
        manifests = sorted(root.glob("*/*/pivots/*.json"))
    except OSError:
        manifests = []
    for manifest in manifests:
        try:
            plugin_root = manifest.parents[1].resolve(strict=True)
        except OSError:
            continue
        marketplace = manifest.parents[2].name
        plugin = manifest.parents[1].name
        source = f"{plugin}@{marketplace}"
        decisions[source] = EntryDecision.active(
            ActivePlugin(
                source=source,
                name=plugin,
                marketplace=marketplace,
                root=plugin_root,
                scopes=("global",),
            )
        )
    return ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions=decisions,
    )


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _payload_command(root: Path | None, command: str) -> Path | None:
    if root is None or Path(command).name != command:
        return None
    candidates = [root / "bin" / command]
    if os.name == "nt":
        candidates = [
            root / "bin" / f"{command}.cmd",
            *candidates,
        ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _resolve_command(command: Sequence[str], *, root: Path | None = None) -> list[str]:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ManifestError("command must be a non-empty array of strings")
    first = command[0]
    payload = _payload_command(root, first)
    candidate = Path(first).expanduser()
    has_path = candidate.is_absolute() or "/" in first or "\\" in first
    if payload is not None:
        resolved = str(payload)
    elif has_path:
        resolved = str(candidate if candidate.is_absolute() or root is None else root / candidate)
    else:
        resolved = shutil.which(first)
    if not resolved:
        raise FileNotFoundError(first)
    target = Path(resolved)
    canonical = target.resolve(strict=True)
    info = canonical.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        raise TargetUnusableError("command must be a regular non-reparse file")
    if os.name != "nt" and not os.access(canonical, os.X_OK):
        raise TargetUnusableError("command is not executable")
    if root is not None and (
        payload is not None or (has_path and not candidate.is_absolute())
    ):
        try:
            canonical.relative_to(root)
        except ValueError as exc:
            raise TargetUnusableError(
                "relative command escapes the identity-verified plugin root"
            ) from exc
    if os.name == "nt" and canonical.suffix.casefold() == ".ps1":
        raise TargetUnusableError(
            "PowerShell scripts must be invoked through an executable wrapper"
        )
    return [str(canonical), *command[1:]]


def _rewrite_manifest_commands(
    raw: Mapping[str, object],
    *,
    root: Path | None,
    require_targets: bool,
) -> dict[str, object]:
    """Return a copy whose external argv heads are canonical absolute paths."""
    data = deepcopy(dict(raw))

    def rewrite(container: dict[str, object], key: str) -> None:
        value = container.get(key)
        if value is None:
            return
        try:
            container[key] = _resolve_command(
                cast(Sequence[str], value),
                root=root,
            )
        except (FileNotFoundError, OSError, TargetUnusableError):
            if require_targets:
                raise

    if isinstance(data.get("list"), Sequence) and not isinstance(
        data.get("list"), (str, bytes)
    ):
        rewrite(data, "list")
    for collection in ("actions", "worktree_actions", "config_sections"):
        entries = data.get(collection)
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict) or "run" not in item:
                continue
            if collection == "actions" and item.get("kind") in {"internal", "card"}:
                continue
            rewrite(item, "run")
    return data


def _managed_manifest_data(
    template: Mapping[str, object],
    *,
    source: str,
    root: Path,
    template_name: str,
    require_targets: bool,
) -> dict[str, object]:
    data = _rewrite_manifest_commands(
        template,
        root=root,
        require_targets=require_targets,
    )
    data["schema_version"] = MANAGED_SCHEMA_VERSION
    data["plugin"] = source
    data["plugin_root"] = str(root)
    data["template"] = template_name
    return data


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _exclusive_create_text(target: Path, content: str) -> bool:
    """Atomically publish ``content`` only when ``target`` is still absent."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _materialize_active_pivots(
    destination: Path,
    activation: ActivationReport,
) -> list[str]:
    """Refresh managed entries for active roots without deleting any file."""
    if activation.authority is ScanAuthority.INDETERMINATE:
        return []
    candidates: dict[str, list[tuple[str, Path, dict[str, object]]]] = {}
    for source, active in sorted(activation.active.items()):
        seen_roots: set[Path] = set()
        for selected in active.live_roots:
            if selected.root in seen_roots:
                continue
            seen_roots.add(selected.root)
            pivot_dir = selected.root / "pivots"
            try:
                templates = sorted(pivot_dir.glob("*.json"))
            except OSError:
                continue
            for template_path in templates:
                try:
                    info = template_path.lstat()
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or stat.S_ISLNK(info.st_mode)
                        or _is_reparse(info)
                    ):
                        continue
                    raw = _read_json(template_path)
                    if not isinstance(raw, dict):
                        continue
                    candidates.setdefault(template_path.name, []).append(
                        (source, selected.root, raw)
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue

    changed: list[str] = []
    for name, owners in sorted(candidates.items()):
        sources = {source for source, _root, _template in owners}
        if len(sources) != 1:
            continue
        # live_roots is precedence-ordered, so a project-local directory wins
        # over an installed copy without weakening cross-plugin collision safety.
        source, root, template = owners[0]
        try:
            data = _managed_manifest_data(
                template,
                source=source,
                root=root,
                template_name=name,
                require_targets=False,
            )
        except ManifestError:
            continue
        content = json.dumps(
            data,
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        ) + "\n"
        template_fingerprint = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()[:12]
        base_target = destination / name
        target = base_target
        try:
            if (
                base_target.exists()
                and base_target.read_text(encoding="utf-8") == content
            ):
                continue
        except OSError:
            pass
        if base_target.exists():
            target = destination / (
                f"{base_target.stem}.{template_fingerprint}{base_target.suffix}"
            )

        if target.exists():
            try:
                if target.read_text(encoding="utf-8") == content:
                    continue
            except OSError:
                pass
            continue

        try:
            if not _exclusive_create_text(target, content):
                continue
            changed.append(target.name)
        except OSError:
            continue
    return changed

