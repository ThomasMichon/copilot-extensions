"""Canonical YAML/config.d value acquisition and optional owner diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dropin_registry import EntryStatus, ScanAuthority

from .acquisition_report import AcquisitionReport, SourceObservation, failed, observe, owner_findings
from .source_probes import probe_source


def yaml_value(path: Path, observations: list[SourceObservation] | None = None) -> dict[str, Any]:
    from . import config as cfg

    try:
        present = path.exists() if observations is None else probe_source(path, "exists", observations, "config-yaml")
        if not present:
            return {}
        with open(path, encoding="utf-8") as stream:
            raw = cfg.yaml.load(stream, Loader=cfg._YamlSafeLoader)
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


def dropin_value(config_d: Path, *, project_name="", warn=False, observations=None):
    from . import config as cfg
    from .config_dropins import scan_config_dropin_registry, warn_config_dropin_findings

    report = scan_config_dropin_registry(config_d, project_name=project_name)
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
                f.reason == "invalid-entry" for f in decision.findings
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
