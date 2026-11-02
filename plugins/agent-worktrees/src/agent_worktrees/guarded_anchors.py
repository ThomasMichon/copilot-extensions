"""Managed-push producer for caller-specific cross-repository guard maps."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid

from dropin_registry import ScanAuthority
from plugin_activation import resolve_active_plugins

from . import registry_paths, repos
from .repos_registry_reader import read_registry
from .repos_registry_transaction import read_bytes, revision
from .tracking import _RecordLock, _atomic_write


class GuardPublicationError(RuntimeError):
    """Source mutation may have succeeded, but its derived view is withdrawn."""


def _reader():
    source = Path(__file__).resolve().parents[2] / "scripts" / "guarded_anchor_map.py"
    if not source.is_file():
        from . import config as cfg

        source = cfg.install_dir() / "bin" / "guarded_anchor_map.py"
    spec = importlib.util.spec_from_file_location("_guarded_anchor_reader", source)
    if spec is None or spec.loader is None:
        raise GuardPublicationError("guard-map reader is unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError) as exc:
        raise GuardPublicationError("guard-map reader is unusable") from exc
    return module


def build_snapshot(caller: str, root: Path, generation: str) -> dict:
    """Acquire one caller's effective overlays; never union other callers."""
    from . import config as cfg
    from .related_acquisition import (
        _related_config_source_anchors_report,
        read_related_grafted_report,
    )

    reader = _reader()
    identity = reader.repository_identity(caller)
    if identity is None or identity["root"] != reader.canonical(caller):
        raise GuardPublicationError("caller checkout identity is unavailable")
    registry_path = root / "repos.yaml"
    registry = read_registry(registry_path, strict=True)
    project = None
    for name, entry in registry.repos.items():
        try:
            registered = reader.repository_identity(repos.registered_entry_root(entry))
        except repos.RepositoryResolutionError:
            continue
        if registered and registered["common_dir"] == identity["common_dir"]:
            project = name
            break
    if project is None:
        raise GuardPublicationError("caller has no available selected-registry registration")
    configuration = cfg.load_config(
        cfg.project_dir(project) / "config.yaml", project=project,
        include_control_plane_related_pr=False, include_knowledge_preferences=False,
    )
    activation = resolve_active_plugins(project_root=Path(caller))
    sources = _related_config_source_anchors_report(
        caller, configuration=configuration, activation_report=activation,
    )
    effective = read_related_grafted_report(sources.value)
    for report in (sources, effective):
        if report.authority is ScanAuthority.INDETERMINATE:
            reasons = sorted({f.reason for f in report.findings})
            raise GuardPublicationError(f"{report.scope} unavailable: {', '.join(reasons)}")
    rows = []
    for name, entry in sorted(effective.value.related.items()):
        if not entry.delegate or entry.delegate == "none":
            continue
        registered = registry.repos.get(name)
        if registered is None:
            continue
        try:
            path = repos.registered_entry_root(registered)
        except repos.RepositoryResolutionError:
            continue  # Explicitly unavailable local registrations are not local anchors.
        target = reader.repository_identity(path)
        if target is None or target["root"] != reader.canonical(path):
            raise GuardPublicationError(f"registered guarded checkout {name!r} has no Git identity")
        rows.append({
            "name": name, "delegate": entry.delegate, "path": reader.canonical(path),
            "locus": {
                "preferred": entry.locus.preferred,
                "machines": list(entry.locus.machines),
            },
            "identity": target,
        })
    if registry._baseline != (str(registry_path.resolve()), revision(read_bytes(registry_path))):
        raise GuardPublicationError("repository registry changed during guard publication")
    if len(rows) > reader.MAX_ROOTS:
        raise GuardPublicationError("guarded anchor inventory exceeds its bound")
    return {
        "schema": reader.SCHEMA, "version": reader.VERSION,
        "source_root": identity["root"], "registry_root": reader.canonical(root),
        "source_identity": identity,
        "inventory_authority": "complete", "generation": generation,
        "observed_at": time.time(),
        # AcquisitionReport has no reusable owner-input closure. This is a
        # managed-boundary observation, not dependency-fingerprint freshness.
        "qualification": "managed-observation",
        "roots": rows,
    }


def _withdraw_views(directory: Path) -> None:
    for path in directory.glob("*.json"):
        try:
            with path.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            data = json.loads(raw) if len(raw) <= 1024 * 1024 else {}
        except (OSError, ValueError):
            data = {}
        scope = {
            key: data[key] for key in ("source_root", "registry_root")
            if isinstance(data, dict) and isinstance(data.get(key), str)
        }
        _atomic_write(path, json.dumps({
            **scope, "inventory_authority": "unavailable",
            "generation": uuid.uuid4().hex,
        }))


def _refresh_locked(root: Path, callers=(), *, include_prepared: bool = True) -> dict:
    directory = root / "guarded-anchors"
    try:
        reader = _reader()
    except (OSError, ValueError, RuntimeError):
        _withdraw_views(directory)
        raise
    selected = {reader.canonical(caller) for caller in callers}
    old_paths = []
    for path in directory.glob("*.json") if include_prepared else ():
        old_paths.append(path)
        try:
            data = json.loads(path.read_bytes())
            source = data.get("source_root") if isinstance(data, dict) else None
            if (
                isinstance(source, str) and os.path.isabs(source)
                and data.get("registry_root") == reader.canonical(root)
                and reader.map_path(source, root) == path
            ):
                selected.add(source)
        except (OSError, ValueError, TypeError):
            pass  # Corrupt prior views are withdrawn, never retained as facts.
    if not include_prepared:
        old_paths = [reader.map_path(caller, root) for caller in selected]
    # Withdraw before acquisition: even an interrupted or failed producer must
    # not leave the old policy looking current after a managed mutation.
    for path in old_paths:
        path.unlink(missing_ok=True)
    generation = uuid.uuid4().hex
    published = 0
    errors = []
    for caller in sorted(selected):
        identity = reader.repository_identity(caller)
        if identity is None or identity["root"] != caller:
            continue  # Removed caller checkouts withdraw their obsolete view.
        try:
            _atomic_write(reader.map_path(caller, root), json.dumps({
                "schema": reader.SCHEMA, "version": reader.VERSION,
                "source_root": caller, "registry_root": reader.canonical(root),
                "inventory_authority": "unavailable", "generation": generation,
            }))
            snapshot = build_snapshot(caller, root, generation)
            encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > reader.MAX_BYTES:
                raise GuardPublicationError("guard map exceeds its byte bound")
            _atomic_write(reader.map_path(caller, root), encoded)
            published += 1
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            errors.append(f"{caller}: {exc}")
    if errors:
        raise GuardPublicationError(
            "guard map publication failed; affected views withdrawn: " + "; ".join(errors)
        )
    return {"published": published, "generation": generation}


def refresh(
    callers=(), *, registry_root: Path | None = None, include_prepared: bool = True,
) -> dict:
    root = registry_root if registry_root is not None else registry_paths.registry_root()
    directory = root / "guarded-anchors"
    directory.mkdir(parents=True, exist_ok=True)
    with _RecordLock(directory / "publication", timeout=10.0, require_sidecar=True):
        return _refresh_locked(root, callers, include_prepared=include_prepared)


def managed_write(write, *, caller: str | Path | None = None):
    """Serialize source mutations with publication, including all prepared views."""
    root = registry_paths.registry_root()
    directory = root / "guarded-anchors"
    directory.mkdir(parents=True, exist_ok=True)
    with _RecordLock(directory / "publication", timeout=10.0, require_sidecar=True):
        # Readers must stop using old policy before the source mutation commits.
        _withdraw_views(directory)
        result = write()
        try:
            reader = _reader()
            identity = reader.repository_identity(caller) if caller is not None else None
            callers = [identity["root"]] if identity else []
            _refresh_locked(root, callers)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise GuardPublicationError(
                f"managed source write completed; guard-map publication incomplete: {exc}"
            ) from exc
        return result


def prepare(caller: str | Path) -> str:
    """Prepare this caller only; failure is advisory to launch, not cached success."""
    try:
        reader = _reader()
        identity = reader.repository_identity(caller)
        if identity is None:
            return ""
        refresh([identity["root"]], include_prepared=False)
        return ""
    except (OSError, ValueError, RuntimeError) as exc:
        return f"[guarded-anchors] preparation failed; publication/withdrawal unconfirmed: {exc}\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-worktrees refresh-guards")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        reader = _reader()
        identity = reader.repository_identity(args.repo)
        if identity is None:
            raise GuardPublicationError("caller checkout identity is unavailable")
        result = {"version": 1, **refresh([identity["root"]])}
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"refresh-guards: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result) if args.json else f"Published {result['published']} caller guard maps.")
    return 0
