"""Canonical related-repository acquisition with optional diagnostic observation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plugin_activation import ActivationReport
    from .related import RelatedConfig

from dropin_registry import EntryStatus, ScanAuthority

from .acquisition_report import AcquisitionReport, SourceObservation, failed, observe, owner_findings
from .source_probes import probe_source


def parse_file(path: Path, observations: list[SourceObservation] | None = None, *, selected=False):
    from . import related as r

    try:
        data = r.yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if selected:
            failed(observations, str(path), "related-file", "source-disappeared", "selected file disappeared before reading")
        else:
            observe(observations, str(path), "related-file", ScanAuthority.ABSENT)
        return r.RelatedConfig()
    except Exception as exc:
        detail = type(exc).__name__
        failed(observations, str(path), "related-file", "unreadable-source", detail)
        return r.RelatedConfig()
    observe(observations, str(path), "related-file", ScanAuthority.COMPLETE)
    if data is None:
        return r.RelatedConfig()
    if not isinstance(data, dict):
        failed(observations, str(path), "related-file", "invalid-shape", "document must be a mapping")
        return r.RelatedConfig()
    primary = str(data.get("primary", "")).strip()

    related: dict[str, r.RelatedEntry] = {}
    raw_related = data.get("related", {})
    if isinstance(raw_related, dict):
        for name, entry in raw_related.items():
            # A bare ``name:`` (null value) is a valid minimal link.
            if entry is None:
                entry = {}
            if not isinstance(entry, dict):
                failed(observations, str(path), "related-file", "invalid-entry",
                       "related entry must be a mapping or null")
                continue
            try:
                related[str(name)] = r.RelatedEntry(
                    name=str(name),
                    role=r.normalize_role(entry.get("role", "")),
                    summary=str(entry.get("summary", "")).strip(),
                    doc=str(entry.get("doc", "")).strip(),
                    locus=r._parse_locus(entry.get("locus")),
                    delegate=r._parse_delegate(entry.get("delegate")),
                    ownership=r.normalize_ownership(entry.get("ownership")),
                    owner=str(entry.get("owner", "")).strip(),
                    audience=r.normalize_audience(entry.get("audience")),
                    ai_attribution=r._parse_ai_attribution(entry.get("ai_attribution")),
                    plugins=r._parse_plugins(entry.get("plugins")),
                    pr=r._parse_related_pr(entry.get("pr")),
                    doc_root=str(path.parent),
                )
            except AttributeError as exc:
                if observations is None:
                    raise
                failed(observations, str(path), "related-file", "invalid-entry", type(exc).__name__)

    else:
        failed(observations, str(path), "related-file", "invalid-shape",
               "related must be a mapping")
    return r.RelatedConfig(primary=primary, related=related)


def base_path(anchor: Path, observations: list[SourceObservation] | None = None):
    from . import related as r

    for candidate in (anchor / r.CANONICAL_RELATED_DIR / r.RELATED_FILENAME, r.legacy_related_path(anchor)):
        if observations is None:
            present = candidate.exists()
        else:
            present = probe_source(candidate, "exists", observations, "related-candidate")
        if present:
            return candidate
    return None


def overlay_path(anchor: Path, observations: list[SourceObservation] | None = None):
    from . import related as r

    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    if not raw:
        observe(observations, "COPILOT_EXTENSIONS_CONTEXT", "installation-context", ScanAuthority.ABSENT)
        return None
    source = "COPILOT_EXTENSIONS_CONTEXT" if raw.startswith("{") else str(Path(raw).expanduser())
    try:
        context = r.json.loads(raw if raw.startswith("{") else Path(raw).expanduser().read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        failed(observations, source, "installation-context", "installation-context-invalid", type(exc).__name__)
        return None
    if not isinstance(context, dict):
        failed(observations, source, "installation-context", "installation-context-invalid", "context must be a mapping")
        return None
    marketplace_id = str(context.get("marketplaceId") or "").strip()
    if not marketplace_id:
        failed(observations, source, "installation-context", "installation-context-invalid", "context has no marketplace selection")
        return None
    observe(observations, source, "installation-context", ScanAuthority.COMPLETE, owner=marketplace_id)
    candidate = anchor / r.MARKETPLACE_OVERLAYS_DIR / marketplace_id / r.RELATED_FILENAME
    present = candidate.exists() if observations is None else probe_source(candidate, "exists", observations, "related-candidate")
    if present:
        return candidate
    return None


def read(anchor, observations: list[SourceObservation] | None = None):
    from . import related as r

    root = Path(anchor)
    try:
        plugin_anchor = r._looks_like_plugin_anchor(root) if observations is None else has_plugin_manifest(root, observations)
    except OSError as exc:
        if observations is None:
            raise
        failed(observations, str(root), "related-anchor", "unreadable-source", type(exc).__name__)
        return r.RelatedConfig()
    if plugin_anchor:
        return parse_file(r.legacy_related_path(root), observations)
    merged = r.RelatedConfig()
    layers = []
    for select in (base_path, overlay_path):
        try:
            path = select(root, observations)
        except OSError as exc:
            if observations is None:
                raise
            failed(observations, str(root), "related-candidate", "unreadable-source", type(exc).__name__)
            continue
        if path is not None:
            layers.append(path)
    for path in layers:
        loaded = parse_file(path, observations, selected=True)
        if loaded.primary:
            merged.primary = loaded.primary
        merged.related.update(loaded.related)
    return merged


def read_related_report(anchor: str | Path) -> AcquisitionReport[RelatedConfig]:
    """Report acquisition of one anchor's related layers, not discovery closure."""
    observations = []
    value = read(anchor, observations)
    return AcquisitionReport("related-layers", value, tuple(observations))


def filesystem_anchors(base: Path, observations: list[SourceObservation] | None = None):
    from . import related as r

    try:
        directory = base.is_dir() if observations is None else probe_source(base, "directory", observations, "plugin-directory")
        if not directory:
            return []
    except OSError as exc:
        failed(observations, str(base), "plugin-directory", "unreadable-source", type(exc).__name__)
        return []
    # Path.glob can suppress directory enumeration errors; this compatibility
    # lane has no authoritative membership report to borrow.
    failed(observations, str(base), "plugin-directory", "membership-unobserved",
           "compatibility glob does not expose complete directory scan authority")
    rel = f"{r.INREPO_DIRNAME}/{r.RELATED_FILENAME}"
    found = set()
    for pattern in (f"*/*/{rel}", f"*/{rel}"):
        try:
            for related_yaml in base.glob(pattern):
                plugin_dir = related_yaml.parent.parent
                try:
                    if (r._has_plugin_manifest(plugin_dir) if observations is None else has_plugin_manifest(plugin_dir, observations)):
                        selected = str(plugin_dir.resolve())
                        found.add(selected)
                        observe(observations, selected, "plugin-contribution", ScanAuthority.COMPLETE)
                except OSError as exc:
                    failed(observations, str(plugin_dir), "plugin-contribution", "unreadable-source", type(exc).__name__)
        except OSError as exc:
            failed(observations, str(base), "plugin-directory", "unreadable-source", type(exc).__name__)
    return [r._PluginContributionAnchor(path, r._plugin_manifest_name(Path(path)) if observations is None else plugin_manifest_name(Path(path), observations))
            for path in sorted(found, key=os.path.normcase)]


def installed(root=None, *, home=None, report=None, observations=None):
    from . import related as r

    override = os.environ.get(r.INSTALLED_PLUGINS_ENV)
    if root is not None or override:
        return filesystem_anchors(root or r.installed_plugins_root(), observations)
    try:
        report = report or r.resolve_active_plugins(home=home)
    except (OSError, ValueError) as exc:
        failed(observations, "plugin-activation", "plugin-activation", "activation-unavailable", type(exc).__name__)
        return []
    observe(observations, "plugin-activation", "plugin-activation", report.authority, findings=owner_findings(report.findings))
    if observations is not None:
        if report.plugin_names is not None:
            failed(observations, "plugin-activation", "plugin-activation", "activation-scope-incomplete",
                   "supplied activation report covers only selected plugin names")
        for source, decision in report.decisions.items():
            plugin = decision.value
            observe(observations, source, "activation-entry",
                    ScanAuthority.INDETERMINATE if decision.status is EntryStatus.INDETERMINATE else ScanAuthority.COMPLETE,
                    status=decision.status, findings=owner_findings(decision.findings), owner=source,
                    target=str(plugin.root) if plugin is not None else None)
    if report.authority is ScanAuthority.INDETERMINATE:
        return []
    found = {}
    for plugin in report.active.values():
        for selected in plugin.live_roots:
            try:
                candidate = (r.related_path(selected.root) if observations is None else
                             Path(selected.root) / (r.INREPO_DIRNAME if has_plugin_manifest(Path(selected.root), observations) else r.CANONICAL_RELATED_DIR) / r.RELATED_FILENAME)
                present = candidate.is_file() if observations is None else probe_source(candidate, "file", observations, "plugin-contribution")
                if present:
                    found[str(selected.root)] = plugin.name
                    observe(observations, str(candidate), "plugin-contribution", ScanAuthority.COMPLETE,
                            owner=getattr(plugin, "source", None), target=str(selected.root))

            except OSError as exc:
                failed(observations, str(selected.root), "plugin-contribution", "unreadable-source", type(exc).__name__)
    return [r._PluginContributionAnchor(path, found[path]) for path in sorted(found, key=os.path.normcase)]


def installed_plugin_related_anchors_report(
    root: Path | None = None, *, home: str | Path | None = None, report: ActivationReport | None = None,
) -> AcquisitionReport[list[str]]:
    """Retain activation decisions alongside the unchanged available anchor list."""
    observations = []
    value = installed(root, home=home, report=report, observations=observations)
    return AcquisitionReport("installed-plugin-related-anchors", value, tuple(observations))


def grafted(anchors, observations=None):
    from . import related as r

    merged = r.RelatedConfig()
    for anchor in anchors:
        rc = r.read_related(anchor) if observations is None else read(anchor, observations)
        plugin_anchor = r._is_installed_plugin_anchor(anchor) if observations is None else is_installed_plugin_anchor(anchor, observations)
        if rc.primary and not plugin_anchor:
            merged.primary = rc.primary
        for name, entry in rc.related.items():
            origin = (anchor if isinstance(anchor, r._PluginContributionAnchor) else
                      r._PluginContributionAnchor(str(anchor), r._plugin_manifest_name(Path(str(anchor))) if observations is None else plugin_manifest_name(Path(str(anchor)), observations))
                      if plugin_anchor else str(anchor))
            entry.origin_anchor = origin
            if plugin_anchor:
                entry.origin_layer = "plugin"
            elif isinstance(anchor, r._ConfigContributionAnchor):
                entry.origin_layer = anchor.source_layer
            else:
                entry.origin_layer = "repository"
            entry.origin_plugin = origin.plugin_name if isinstance(origin, r._PluginContributionAnchor) else ""
            merged.related[name] = entry
    return merged


def read_related_grafted_report(anchors: list[str | Path]) -> AcquisitionReport[RelatedConfig]:
    """Acquire ordered graft layers once, retaining uncertainty and positive entries."""
    observations = []
    value = grafted(anchors, observations)
    return AcquisitionReport("related-graft", value, tuple(observations))


def source_anchors(base_anchor, *, installed_anchors=None, observations=None):
    from . import config_sources, related as r, related_cli as cli

    try:
        config = cli.cfg.load_config(include_control_plane_related_pr=False)
        if observations is None:
            srcs = cli.state_root_mod.config_source_anchors(config, base_anchor=base_anchor)
        else:
            source_report = config_sources.config_source_anchors_report(config, base_anchor=base_anchor)
            srcs = source_report.value
            observations.extend(source_report.observations)
        anchors = [r.config_contribution_anchor(s.anchor, s.origin) for s in srcs if s.anchor]
    except Exception as exc:
        failed(observations, base_anchor, "related-sources", "source-selection-failed", type(exc).__name__)
        anchors = []
    if not anchors:
        anchors = [r.config_contribution_anchor(base_anchor, "harness")]
    elif os.path.abspath(anchors[0]) != os.path.abspath(base_anchor):
        anchors.insert(0, r.config_contribution_anchor(base_anchor, "harness"))
    try:
        key = r._anchor_key if observations is None else lambda anchor: anchor_key(anchor, observations)
        existing = {key(a) for a in anchors}
        if installed_anchors is not None:
            discovered = installed_anchors
            # Supplied values have no activation-acquisition provenance.
            failed(observations, "installed-anchors", "related-sources", "activation-unobserved",
                   "explicit anchor list has no activation coverage report")
        elif observations is None:
            discovered = r.installed_plugin_related_anchors()
        else:
            plugin_report = r.installed_plugin_related_anchors_report()
            discovered = plugin_report.value
            observations.extend(plugin_report.observations)
        plugins = [p for p in discovered if key(p) not in existing]
    except Exception as exc:
        failed(observations, "plugin-activation", "related-sources", "source-selection-failed", type(exc).__name__)
        plugins = []
    selected = [*plugins, *anchors]
    if observations is not None:
        for anchor in selected:
            observe(observations, str(anchor), "related-source-order", ScanAuthority.COMPLETE,
                    owner="plugin" if isinstance(anchor, r._PluginContributionAnchor) else getattr(anchor, "source_layer", "repository"))
    return selected


def _related_config_source_anchors_report(
    base_anchor: str, *, installed_anchors: list[str] | None = None,
) -> AcquisitionReport[list[str]]:
    """Report source selection only; no registry checkout paths are resolved here."""
    observations = []
    value = source_anchors(base_anchor, installed_anchors=installed_anchors, observations=observations)
    return AcquisitionReport("related-source-anchors", value, tuple(observations))


def has_plugin_manifest(plugin_dir: Path, observations=None) -> bool:
    for manifest in (plugin_dir / "plugin.json", plugin_dir / ".claude-plugin" / "plugin.json"):
        present = manifest.is_file() if observations is None else probe_source(manifest, "file", observations, "plugin-manifest")
        if present:
            return True
    return False


def plugin_manifest_name(plugin_root: Path, observations=None) -> str:
    from . import related as r

    for manifest in (plugin_root / "plugin.json", plugin_root / ".claude-plugin" / "plugin.json"):
        try:
            raw = r.json.loads(manifest.read_text(encoding="utf-8"))
        except FileNotFoundError:
            observe(observations, str(manifest), "plugin-identity", ScanAuthority.ABSENT)
            continue
        except (OSError, ValueError) as exc:
            failed(observations, str(manifest), "plugin-identity", "unreadable-source", type(exc).__name__)
            continue
        if isinstance(raw, dict):
            name = str(raw.get("name") or "").strip()
            if name:
                observe(observations, str(manifest), "plugin-identity", ScanAuthority.COMPLETE)
                return name
        failed(observations, str(manifest), "plugin-identity", "invalid-entry", "manifest has no usable identity")
    failed(observations, str(plugin_root), "plugin-identity", "identity-unavailable", "plugin identity is unavailable")
    return ""


def is_installed_plugin_anchor(anchor, observations=None) -> bool:
    from . import related as r

    if isinstance(anchor, r._PluginContributionAnchor):
        return True
    try:
        root = r.installed_plugins_root().resolve()
        return root in Path(anchor).resolve().parents
    except (OSError, RuntimeError) as exc:
        if observations is None and not isinstance(exc, OSError):
            raise
        failed(observations, str(anchor), "plugin-anchor", "resolution-failed", type(exc).__name__)
        return False


def anchor_key(anchor, observations=None) -> str:
    try:
        resolved = Path(anchor).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        if observations is None and not isinstance(exc, OSError):
            raise
        failed(observations, str(anchor), "related-anchor-key", "resolution-failed", type(exc).__name__)
        resolved = Path(os.path.abspath(os.path.expanduser(str(anchor))))
    return os.path.normcase(str(resolved))
