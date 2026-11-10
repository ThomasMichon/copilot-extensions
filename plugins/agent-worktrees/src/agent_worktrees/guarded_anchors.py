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
from .repos_registry_transaction import read_bytes, record_lock, registry_lock, remaining_timeout, revision
from .tracking import _RecordLock, _atomic_write


class GuardPublicationError(RuntimeError):
    """Source mutation may have succeeded, but its derived view is withdrawn."""


class CallerNotRegistered(GuardPublicationError):
    """A retained caller was deliberately removed from the selected registry."""


class CallerRegistrationUnavailable(GuardPublicationError):
    """Caller registration cannot be established from current observations."""


def _reader():
    package = Path(__file__).resolve().parent
    # An installed runtime reads the copy it ships, so the guard map never depends on
    # which bin/ publication scope (wrappers vs resolvers) last ran, or on a stale bin/.
    candidates = (
        package / "_runtime_assets" / "scripts" / "guarded_anchor_map.py",
        package.parents[1] / "scripts" / "guarded_anchor_map.py",
    )
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
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


def _require_configuration_observations(observations) -> None:
    from .source_probes import probe_source

    reasons = set()
    for observation in observations:
        if observation.authority is not ScanAuthority.INDETERMINATE:
            continue
        findings = {finding.reason for finding in observation.findings}
        if findings == {"owner-coverage-unavailable"}:
            continue  # This observation is not a stable owner-input proof.
        if findings == {"presence-provenance-unavailable"}:
            coverage = []
            present = probe_source(observation.source, "exists", coverage, observation.stage)
            if not present and coverage and all(
                item.authority is ScanAuthority.ABSENT for item in coverage
            ):
                continue
        reasons.update(findings or {"indeterminate-configuration"})
    if reasons:
        raise GuardPublicationError("configuration acquisition unavailable: " + ", ".join(sorted(reasons)))


def _posture_rows(registry, reader) -> tuple[list[dict], list[str]]:
    from . import installer

    try:
        projects = installer.read_projects_registry(strict=True)["projects"]
    except Exception:
        return [], sorted(registry.repos)
    rows = []
    unavailable = []
    for name, registered in sorted(registry.repos.items()):
        project = projects.get(name, {})
        if not isinstance(project, dict):
            unavailable.append(name)
            continue
        posture = (
            "reference" if registered.repo_class == "reference"
            else "worktree" if registered.repo_class in {"worktree", "knowledge"}
            and not project.get("base_repo")
            else ""
        )
        elevated = project.get("elevated") is True
        if not posture and not elevated:
            continue
        try:
            path = repos.registered_entry_root(registered)
        except repos.RepositoryResolutionError as exc:
            if not registered.local_path() or isinstance(exc.__cause__, FileNotFoundError):
                continue
            unavailable.append(name)
            continue
        identity = reader.repository_identity(path)
        if identity is None or identity["root"] != reader.canonical(path):
            unavailable.append(name)
            continue
        rows.append({
            "name": name, "delegate": "none", "posture": posture,
            "elevated": elevated, "path": reader.canonical(path),
            "registered_path": os.path.abspath(registered.local_path()),
            "identity": identity,
        })
    return rows, unavailable


def build_snapshot(caller: str, root: Path, generation: str, prior_project: str | None = None) -> dict:
    """Acquire one caller's effective overlays; never union other callers."""
    from . import config as cfg
    from .config_acquisition import capture_config_observations
    from .config_cache import ConfigCacheSession
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
    registration_unavailable = False
    for name, entry in registry.repos.items():
        try:
            registered = reader.repository_identity(repos.registered_entry_root(entry))
        except repos.RepositoryResolutionError:
            registration_unavailable |= bool(entry.local_path())
            continue
        if registered is None:
            registration_unavailable = True
            continue
        if registered and registered["common_dir"] == identity["common_dir"]:
            project = name
            break
    if project is None:
        if prior_project is not None and prior_project not in registry.repos:
            raise CallerNotRegistered("caller has no available selected-registry registration")
        if registration_unavailable:
            raise CallerRegistrationUnavailable("caller registration identity is unavailable")
        raise CallerNotRegistered("caller has no available selected-registry registration")
    with ConfigCacheSession(ttl=None).scope(), capture_config_observations() as observations:
        configuration = cfg.load_config(
            cfg.project_dir(project) / "config.yaml", project=project,
            include_control_plane_related_pr=False, include_knowledge_preferences=False,
        )
        activation = resolve_active_plugins(home=cfg._home(), project_root=Path(caller))
        sources = _related_config_source_anchors_report(
            caller, configuration=configuration, activation_report=activation,
        )
        effective = read_related_grafted_report(sources.value)
    _require_configuration_observations(observations)
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
        except repos.RepositoryResolutionError as exc:
            if not registered.local_path() or isinstance(exc.__cause__, FileNotFoundError):
                continue
            raise GuardPublicationError(f"registered guarded checkout {name!r} is unavailable") from exc
        target = reader.repository_identity(path)
        if target is None or target["root"] != reader.canonical(path):
            raise GuardPublicationError(f"registered guarded checkout {name!r} has no Git identity")
        rows.append({
            "name": name, "delegate": entry.delegate,
            "path": reader.canonical(path),
            "registered_path": os.path.abspath(registered.local_path()),
            "locus": {
                "preferred": entry.locus.preferred,
                "machines": list(entry.locus.machines),
            },
            "identity": target,
        })
    if registry._baseline != (str(registry_path.resolve()), revision(read_bytes(registry_path))):
        raise GuardPublicationError("repository registry changed during guard publication")
    postures, posture_errors = _posture_rows(registry, reader)
    if len(rows) + len(postures) > reader.MAX_ROOTS:
        raise GuardPublicationError("guarded anchor inventory exceeds its bound")
    return {
        "schema": reader.SCHEMA, "version": reader.VERSION,
        "source_root": identity["root"], "registry_root": reader.canonical(root),
        "source_identity": identity, "source_project": project,
        "inventory_authority": "complete", "generation": generation,
        "observed_at": time.time(),
        # AcquisitionReport has no reusable owner-input closure. This is a
        # managed-boundary observation, not dependency-fingerprint freshness.
        "qualification": "managed-observation",
        "roots": rows, "postures": postures,
        "posture_authority": "partial" if posture_errors else "complete",
        "posture_errors": posture_errors,
    }


def _view_data(path: Path, reader) -> object:
    with reader._open_map(path) as stream:
        raw = stream.read(reader.MAX_BYTES + 1)
    if len(raw) > reader.MAX_BYTES:
        raise ValueError("oversized guard map")
    return json.loads(raw)


def _view_paths(directory: Path) -> list[Path]:
    with os.scandir(directory) as entries:
        return [
            directory / entry.name for entry in entries
            if os.path.normcase(entry.name).endswith(".json")
        ]


def _withdraw_views(directory: Path, *, paths=None, reader=None) -> None:
    reader = reader if reader is not None else _reader()
    reader.validate_directory(directory)
    for path in _view_paths(directory) if paths is None else paths:
        try:
            data = _view_data(path, reader)
        except (FileNotFoundError, ValueError):
            data = {}
        scope = {
            key: data[key] for key in ("source_root", "registry_root", "source_project")
            if isinstance(data, dict) and isinstance(data.get(key), str)
        }
        _atomic_write(path, json.dumps({
            **scope, "inventory_authority": "unavailable",
            "generation": uuid.uuid4().hex,
        }))


def _refresh_locked(
    root: Path, callers=(), *, include_prepared: bool = True, require_callers: bool = True,
    budget=None,
) -> dict:
    directory = root / "guarded-anchors"
    reader = _reader()
    reader.validate_directory(directory)
    requested = {reader.canonical(caller) for caller in callers}
    explicit = requested if require_callers else set()
    selected = set(requested)
    prepared = set()
    prior_projects = {}
    old_paths = []
    for path in _view_paths(directory) if include_prepared else ():
        old_paths.append(path)
        try:
            data = _view_data(path, reader)
            source = data.get("source_root") if isinstance(data, dict) else None
            if (
                isinstance(source, str) and os.path.isabs(source)
                and data.get("registry_root") == reader.canonical(root)
                and reader.map_path(source, root) == path
            ):
                selected.add(source)
                prepared.add(source)
                project = data.get("source_project")
                if isinstance(project, str) and project:
                    prior_projects[source] = project
        except (FileNotFoundError, ValueError, TypeError):
            pass  # Corrupt prior views are withdrawn, never retained as facts.
    if not include_prepared:
        old_paths = [reader.map_path(caller, root) for caller in selected]
        for caller in selected:
            try:
                data = _view_data(reader.map_path(caller, root), reader)
                if (
                    isinstance(data, dict) and data.get("source_root") == caller
                    and data.get("registry_root") == reader.canonical(root)
                ):
                    project = data.get("source_project")
                    if isinstance(project, str) and project:
                        prior_projects[caller] = project
            except (FileNotFoundError, ValueError, TypeError):
                pass
    # Withdraw before acquisition: even an interrupted or failed producer must
    # not leave the old policy looking current after a managed mutation.
    _withdraw_views(directory, paths=old_paths, reader=reader)
    generation = uuid.uuid4().hex
    published = 0
    errors = []
    for caller in sorted(selected):
        if budget is not None:
            remaining_timeout(budget, float("inf"))
        identity = reader.repository_identity(caller)
        if identity is None or identity["root"] != caller:
            if caller in requested and caller not in prepared and not require_callers:
                print(f"[guarded-anchors] new caller identity unavailable; not prepared: {caller}", file=sys.stderr)
                continue
            if caller not in explicit:
                try:
                    Path(caller).stat()
                except FileNotFoundError:
                    reader.map_path(caller, root).unlink(missing_ok=True)
                    continue
                except OSError:
                    pass
            errors.append(f"{caller}: caller checkout identity is unavailable")
            continue
        try:
            _atomic_write(reader.map_path(caller, root), json.dumps({
                "schema": reader.SCHEMA, "version": reader.VERSION,
                "source_root": caller, "registry_root": reader.canonical(root),
                "source_project": prior_projects.get(caller),
                "inventory_authority": "unavailable", "generation": generation,
            }))
            if budget is None:
                snapshot = build_snapshot(caller, root, generation, prior_projects.get(caller))
            else:
                from .inrepo_config_source import resolution_budget

                with resolution_budget(deadline=budget.execution_deadline):
                    snapshot = build_snapshot(caller, root, generation, prior_projects.get(caller))
            if budget is not None:
                remaining_timeout(budget, float("inf"))
            encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > reader.MAX_BYTES:
                raise GuardPublicationError("guard map exceeds its byte bound")
            _atomic_write(reader.map_path(caller, root), encoded)
            published += 1
        except CallerNotRegistered as exc:
            if caller in explicit:
                errors.append(f"{caller}: {exc}")
            else:
                reader.map_path(caller, root).unlink(missing_ok=True)
        except CallerRegistrationUnavailable as exc:
            if caller in requested and caller not in prepared and not require_callers:
                reader.map_path(caller, root).unlink(missing_ok=True)
                print(f"[guarded-anchors] new caller registration unavailable; not prepared: {caller}", file=sys.stderr)
            else:
                errors.append(f"{caller}: {exc}")
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            errors.append(f"{caller}: {exc}")
    if errors:
        raise GuardPublicationError(
            "guard map publication failed; affected views withdrawn: " + "; ".join(errors)
        )
    return {"published": published, "generation": generation}


def _prepare_directory(root: Path) -> Path:
    directory = root / "guarded-anchors"
    reader = _reader()
    try:
        reader.validate_directory(directory)
    except FileNotFoundError:
        directory.mkdir(parents=True, exist_ok=True)
        reader.validate_directory(directory)
    return directory


def refresh(
    callers=(), *, registry_root: Path | None = None, include_prepared: bool = True,
) -> dict:
    root = registry_root if registry_root is not None else registry_paths.registry_root()
    directory = _prepare_directory(root)
    with registry_lock(root / "repos.yaml"), _RecordLock(
        directory / "publication", timeout=10.0, require_sidecar=True,
    ):
        return _refresh_locked(root, callers, include_prepared=include_prepared)


def managed_write(write, *, caller: str | Path | None = None, budget=None, validate=None):
    """Validate under publication locks before withdrawing views for mutation."""
    root = registry_paths.registry_root()
    directory = _prepare_directory(root)
    arguments = {"budget": budget} if budget is not None else {}
    publication = (
        _RecordLock(directory / "publication", timeout=10.0, require_sidecar=True)
        if budget is None else record_lock(directory / "publication", budget=budget)
    )
    with registry_lock(root / "repos.yaml", **arguments), publication:
        if budget is not None:
            remaining_timeout(budget, float("inf"))
        if validate is not None:
            validate()
        if budget is not None:
            remaining_timeout(budget, float("inf"))
        # Readers must stop using old policy before the source mutation commits.
        _withdraw_views(directory)
        if budget is not None:
            remaining_timeout(budget, float("inf"))
        result = write()
        try:
            reader = _reader()
            identity = reader.repository_identity(caller) if caller is not None else None
            callers = [identity["root"]] if identity else []
            _refresh_locked(root, callers, require_callers=False, **arguments)
            if budget is not None:
                remaining_timeout(budget, float("inf"))
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


def prepare_resident() -> None:
    try:
        refresh()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[guarded-anchors] initial preparation failed (fail-open): {exc}", file=sys.stderr)


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
