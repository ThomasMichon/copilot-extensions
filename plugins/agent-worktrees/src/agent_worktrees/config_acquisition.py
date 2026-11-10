"""Canonical YAML/config.d value acquisition and optional owner diagnostics."""

from __future__ import annotations

import contextlib
import contextvars
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config

from dropin_registry import EntryStatus, ScanAuthority

from .acquisition_report import AcquisitionReport, SourceObservation, failed, observe, owner_findings
from .source_probes import probe_source


# A collector belongs to one report request and its calling thread, never a cache.
_collector: contextvars.ContextVar[tuple[int, list[SourceObservation]] | None] = contextvars.ContextVar(
    "_agent_worktrees_config_observations", default=None,
)


def current_observations() -> list[SourceObservation] | None:
    active = _collector.get()
    return active[1] if active is not None and active[0] == threading.get_ident() else None


@contextlib.contextmanager
def capture_config_observations():
    observations: list[SourceObservation] = []
    token = _collector.set((threading.get_ident(), observations))
    try:
        yield observations
    finally:
        _collector.reset(token)


def opaque_source(source: str) -> None:
    failed(current_observations(), source, "config-owner", "owner-coverage-unavailable",
           "child owner does not expose complete acquisition provenance")


def migration_outcome(schema_id: str, *, unavailable=False, error=None) -> None:
    """Record only the branch the original lazy migration already took."""
    observations = current_observations()
    if unavailable:
        failed(observations, schema_id, "config-migration", "migration-unavailable", "original loader retained its input")
    elif error is not None:
        failed(observations, schema_id, "config-migration", "migration-failed", type(error).__name__)
    else:
        observe(observations, schema_id, "config-migration", ScanAuthority.COMPLETE)


def registry_call_started() -> int | None:
    """Bound diagnostics to this original consumer call, not prior observations."""
    observations = current_observations()
    return len(observations) if observations is not None else None


def registry_call_finished(start: int | None, source: str, *, stage: str = "repository-registry") -> None:
    """Keep replacements opaque unless this call supplied registry-owner evidence."""
    observations = current_observations()
    if observations is not None and start is not None and not any(
        o.stage == stage for o in observations[start:]
    ):
        opaque_source(source)


def registry_call_failed(source: str, error: Exception) -> None:
    """Retain an originally suppressed consumer failure without its body."""
    failed(current_observations(), source, "registry-consumer", "registry-helper-unavailable", type(error).__name__)


def full_load_started() -> None:
    observe(current_observations(), "load-config", "config-load", ScanAuthority.COMPLETE)
    opaque_source("execution-machine")


def source_exists(path: Path, stage: str, *, strict=False) -> bool:
    """Observe the owner's existing presence check, preserving raised errors."""
    try:
        if strict:
            try:
                path.stat()
                present = True
            except FileNotFoundError:
                ancestor = path
                while True:
                    try:
                        ancestor.lstat()
                    except FileNotFoundError:
                        if ancestor.parent == ancestor:
                            raise
                        ancestor = ancestor.parent
                    else:
                        ancestor.stat()
                        present = False
                        break
        else:
            present = path.exists()
    except Exception as exc:
        failed(current_observations(), str(path), stage, "unreadable-source", type(exc).__name__)
        raise
    if not present:
        if strict:
            observe(current_observations(), str(path), stage, ScanAuthority.ABSENT)
        else:
            failed(current_observations(), str(path), stage, "presence-provenance-unavailable",
                   "legacy presence result does not expose a raw stat outcome")
    return present


def load_config_report(
    path: Path | None = None, *, include_control_plane_related_pr: bool = True,
    include_knowledge_preferences: bool = True, project: str | None = None,
) -> AcquisitionReport[Config]:
    """Report the canonical load, retaining its cache and fatal-error semantics."""
    from . import config as cfg

    with capture_config_observations() as observations:
        value = cfg.load_config(
            path, include_control_plane_related_pr=include_control_plane_related_pr,
            include_knowledge_preferences=include_knowledge_preferences, project=project,
        )
        if not any(o.stage == "config-load" for o in observations):
            failed(observations, "load-config", "config-load", "cached-config-unattributed",
                   "available Config has no owned acquisition provenance")
    return AcquisitionReport("load-config", value, tuple(observations))


def yaml_value(path: Path, observations: list[SourceObservation] | None = None, *, strict=False) -> dict[str, Any]:
    from . import config as cfg

    if observations is None:
        observations = current_observations()
    try:
        present = (
            source_exists(path, "config-yaml", strict=True) if strict else
            path.exists() if observations is None else probe_source(path, "exists", observations, "config-yaml")
        )
        if not present:
            return {}
        with open(path, encoding="utf-8") as stream:
            if strict:
                from .repos_registry_reader import _UniqueRegistryLoader
            raw = cfg.yaml.load(stream, Loader=_UniqueRegistryLoader if strict else cfg._YamlSafeLoader)
        observe(observations, str(path), "config-yaml", ScanAuthority.COMPLETE)
        if isinstance(raw, dict):
            return raw
        if raw is not None:
            failed(observations, str(path), "config-yaml", "invalid-shape", "document must be a mapping")
        return {}
    except Exception as exc:
        detail = type(exc).__name__
        failed(observations, str(path), "config-yaml", "unreadable-source", detail)
        return {}


def _load_yaml_safe_report(path: Path) -> AcquisitionReport[dict[str, Any]]:
    """Report one YAML source, without acquiring any other config layers."""
    observations: list[SourceObservation] = []
    value = yaml_value(path, observations)
    return AcquisitionReport("config-yaml", value, tuple(observations))


def dropin_value(config_d: Path, *, project_name="", warn=False, observations=None, strict=False):
    from . import config as cfg
    from .config_dropins import scan_config_dropin_registry, warn_config_dropin_findings

    if observations is None:
        observations = current_observations()
    report = scan_config_dropin_registry(
        config_d, project_name=project_name, **({"strict": True} if strict else {}),
    )
    if warn:
        warn_config_dropin_findings(report)
    observe(observations, str(config_d), "config-dropins", report.authority, findings=owner_findings(report.findings))
    if observations is not None:
        entries = dict.fromkeys([*report.snapshot.decisions, *report.active_entries])
        for entry in entries:
            decision = report.snapshot.decisions.get(entry)
            contribution = report.active_entries.get(entry) or (decision.value if decision else None)
            if decision is None:
                observe(observations, entry, "config-dropin-entry", ScanAuthority.INDETERMINATE,
                        owner=contribution.owner if contribution else None,
                        target=str(contribution.target) if contribution else None, retained=True)
                continue
            # Preserve the owner's verdict even when its available data is LKG.
            retained = entry in report.active_entries and (
                report.authority is ScanAuthority.INDETERMINATE or decision.status is EntryStatus.INDETERMINATE
            )
            uncertain = retained or decision.status is EntryStatus.INDETERMINATE or any(
                f.reason == "invalid-entry" or (
                    strict and f.reason in ("missing-target", "target-unusable", "identity-mismatch")
                ) for f in decision.findings
            )
            observe(observations, entry, "config-dropin-entry",
                    ScanAuthority.INDETERMINATE if uncertain else ScanAuthority.COMPLETE,
                    status=decision.status, findings=owner_findings(decision.findings), retained=retained,
                    owner=contribution.owner if contribution else None,
                    target=str(contribution.target) if contribution else None)
    merged = {}
    for contribution in report.active_configs:
        merged = cfg._deep_merge(merged, contribution.raw_config)
    return merged


def _load_config_d_report(config_d: Path, *, project_name="", warn=False) -> AcquisitionReport[dict[str, Any]]:
    """Borrow one drop-in owner scan, retaining verdicts without copying bodies."""
    observations: list[SourceObservation] = []
    value = dropin_value(config_d, project_name=project_name, warn=warn, observations=observations)
    return AcquisitionReport("config-dropins", value, tuple(observations))


def owner_call(callback, original, *args, source="knowledge-preferences", **kwargs):
    """Qualify the selected callable, including replacements that restore themselves."""
    try:
        return callback(*args, **kwargs)
    finally:
        if callback is not original:
            failed(current_observations(), source, source,
                   "owner-coverage-unavailable", "selected callback is not the original owner")


def owner_outcome(start: int | None, source: str) -> None:
    """Complete only the original decision, never its child acquisition or reuse frontier."""
    observations = current_observations()
    if observations is not None and start is not None:
        uncertain = [
            o for o in observations[start:]
            if o.stage == source and o.authority is ScanAuthority.INDETERMINATE
        ]
        observe(observations, source, source,
                ScanAuthority.INDETERMINATE if uncertain else ScanAuthority.COMPLETE,
                findings=tuple(dict.fromkeys(f for o in uncertain for f in o.findings)))


knowledge_call = owner_call


def execution_config(config, configured_machine, machine_raw, global_raw):
    """Observe the original execution selection without certifying child inputs."""
    from dataclasses import replace

    from . import config as cfg

    source = "execution-machine"
    start = registry_call_started()
    try:
        try:
            repo = config.default_repo
        except KeyError:
            repo = None
        anchors = [repo.anchor] if repo is not None and repo.anchor else []
        if repo is not None and repo.anchor:
            from . import state_root
            from .config_sources import _CONFIG_SOURCE_ANCHORS_OWNER

            anchors = [
                item.anchor for item in owner_call(
                    state_root.config_source_anchors, _CONFIG_SOURCE_ANCHORS_OWNER,
                    config, base_anchor=repo.anchor, family="registry", source="execution-config-sources",
                )
            ]
        for anchor in anchors:
            root = Path(anchor)
            if any(_execution_file(root / rel) for rel in (
                Path(cfg.INREPO_CONFIG_DIRNAME) / "machines.yaml", Path("machines.yaml"),
            )):
                from . import machine_identity

                registry = machine_identity.load_execution_registry
                resolver = machine_identity.resolve_execution_machine
                config = replace(config, machine=owner_call(
                    resolver, machine_identity._EXECUTION_MACHINE_OWNER,
                    configured_machine if configured_machine else cfg.socket.gethostname(),
                    guest=cfg.detect_platform() == "wsl", configured=bool(configured_machine),
                    load_entries=lambda: _execution_registry(registry, anchor, config),
                    preserve_native_hostname=True, source=source,
                ))
                break
        else:
            if machine_raw.get("machine") or global_raw.get("machine"):
                failed(current_observations(), source, source, "owner-coverage-unavailable",
                       "fallback resolver does not expose selected callback provenance")
                config = replace(config, machine=cfg.detect_machine(value=config.machine))
    except Exception as exc:
        failed(current_observations(), source, source, "execution-machine-failed", type(exc).__name__)
        raise
    owner_outcome(start, source)
    return config


def _execution_file(path):
    present = path.is_file()
    if present:
        observe(current_observations(), str(path), "execution-candidate", ScanAuthority.COMPLETE)
    else:
        failed(current_observations(), str(path), "execution-candidate",
               "presence-provenance-unavailable", "legacy presence result does not expose a raw stat outcome")
    return present


def _execution_registry(callback, anchor, config):
    from . import machine_identity

    try:
        return owner_call(
            callback, machine_identity._EXECUTION_REGISTRY_OWNER, anchor,
            config=config, source="execution-machine",
        )
    except Exception as exc:
        failed(current_observations(), "execution-machine", "execution-machine",
               "execution-registry-failed", type(exc).__name__)
        raise


EXECUTION_CONFIG_OWNER = execution_config
