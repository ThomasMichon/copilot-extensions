"""Identity-gated plugin contributions from ``registrar.d``.

``pointers.json`` remains the explicit operator/service registry.  This module
owns the separate, untrusted plugin candidate index: each manifest is classified
independently, current plugin activation proves authority, and only authoritative
evidence withdraws previously active declarations.
"""

from __future__ import annotations

import json
import os
import stat
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
    scan_directory,
)
from plugin_activation import ActivationReport, ActivePlugin, resolve_active_plugins

from .registrar import ProfileDeclaration, RegistrarError
from .registrar_discovery import RegistrarIndeterminateError, read_declaration_file

REGISTRY_NAME = "registrar.d"
REGISTRAR_DROPINS_DIR_ENV = "AGENT_DISPATCH_REGISTRAR_DROPINS_DIR"
_DECL_SUFFIXES = (".yaml", ".yml", ".json")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def registrar_dropins_dir() -> Path:
    """Resolve the plugin candidate registry without creating it."""
    override = os.environ.get(REGISTRAR_DROPINS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-dispatch" / "registrar.d"


@dataclass(frozen=True)
class RegistrarManifest:
    """One validated attributed candidate manifest."""

    plugin: str
    plugin_root: str
    registrar: str
    source_path: str = ""
    schema_version: int = 1


@dataclass(frozen=True)
class PluginDeclaration:
    """A declaration plus the candidate and document that contributed it."""

    declaration: ProfileDeclaration
    source_path: str
    manifest_path: str
    plugin: str


@dataclass(frozen=True)
class RegistrarCandidate:
    """One active manifest and its reconciled declaration documents."""

    manifest: RegistrarManifest
    declaration_entries: Mapping[str, PluginDeclaration]


@dataclass(frozen=True)
class RegistrarRegistryReport:
    """Plugin candidate scan, reconciled entries, winners, and findings."""

    snapshot: ScanSnapshot[RegistrarCandidate]
    entries: Mapping[str, RegistrarCandidate]
    declarations: tuple[PluginDeclaration, ...]
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class CombinedRegistrarReport:
    """Trusted declarations merged with lower-precedence plugin candidates."""

    trusted: tuple[ProfileDeclaration, ...]
    plugins: RegistrarRegistryReport
    declarations: tuple[ProfileDeclaration, ...]
    findings: tuple[Finding, ...]


class ManifestError(ValueError):
    """A registrar candidate manifest is structurally invalid."""


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def parse_manifest(data: object, *, source_path: str = "") -> RegistrarManifest:
    """Parse the only supported plugin-owned registrar manifest schema."""
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a JSON object")

    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ManifestError("`schema_version` must be 1")

    plugin = data.get("plugin")
    if not isinstance(plugin, str) or "@" not in plugin or not plugin.strip():
        raise ManifestError("schema v1 requires canonical `plugin` name@marketplace")

    plugin_root = data.get("plugin_root")
    if not isinstance(plugin_root, str) or not plugin_root.strip():
        raise ManifestError("schema v1 requires non-empty absolute `plugin_root`")
    if not Path(plugin_root).expanduser().is_absolute():
        raise ManifestError("`plugin_root` must be absolute on the current platform")

    registrar = data.get("registrar")
    if not isinstance(registrar, str) or not registrar.strip():
        raise ManifestError("schema v1 requires non-empty relative `registrar`")
    registrar = registrar.strip()
    win_path = PureWindowsPath(registrar)
    posix_path = PurePosixPath(registrar)
    if (
        win_path.is_absolute()
        or bool(win_path.drive)
        or posix_path.is_absolute()
        or ".." in win_path.parts
        or ".." in posix_path.parts
    ):
        raise ManifestError("`registrar` must be a root-contained relative path")

    return RegistrarManifest(
        plugin=plugin.strip(),
        plugin_root=plugin_root.strip(),
        registrar=registrar,
        source_path=source_path,
    )


def _finding(
    path: Path | str,
    reason: str,
    *,
    status: str = "inactive",
    target: str | None = None,
    owner: str | None = None,
    detail: str | None = None,
) -> Finding:
    entry = str(path)
    if status == "indeterminate" or reason in {
        "entry-indeterminate",
        "registry-indeterminate",
    }:
        remedy = (
            "Restore access and retry `agent-dispatch registrar doctor`; "
            "the runtime retains only matching last-known state."
        )
    else:
        remedy = (
            f"Run `agent-dispatch registrar doctor`; remove {entry} or "
            "reinstall/re-enable its contributor."
        )
    return Finding(
        registry=REGISTRY_NAME,
        entry=entry,
        status=status,
        reason=reason,
        target=target,
        owner=owner,
        remedy=remedy,
        detail=detail,
    )


def _inactive(
    path: Path | str,
    reason: str,
    *,
    target: str | None = None,
    owner: str | None = None,
    detail: str | None = None,
) -> EntryDecision[RegistrarCandidate]:
    return EntryDecision.inactive(
        _finding(path, reason, target=target, owner=owner, detail=detail)
    )


def _indeterminate(
    path: Path | str,
    *,
    target: str | None = None,
    owner: str | None = None,
    detail: str | None = None,
) -> EntryDecision[RegistrarCandidate]:
    return EntryDecision.indeterminate(
        _finding(
            path,
            "entry-indeterminate",
            status="indeterminate",
            target=target,
            owner=owner,
            detail=detail,
        )
    )


def _activation_advisories(
    path: Path,
    plugin: str,
    decision: EntryDecision,
) -> list[Finding]:
    return [
        _finding(
            path,
            finding.reason,
            status="advisory",
            target=finding.target,
            owner=plugin,
            detail=finding.detail,
        )
        for finding in decision.findings
    ]


def _canonical_plugin_root(
    path: Path,
    manifest: RegistrarManifest,
) -> tuple[Path | None, EntryDecision[RegistrarCandidate] | None]:
    root = Path(manifest.plugin_root).expanduser()
    try:
        info = root.lstat()
        canonical = root.resolve(strict=True)
    except FileNotFoundError as exc:
        return None, _inactive(
            path,
            "missing-target",
            target=str(root),
            owner=manifest.plugin,
            detail=str(exc),
        )
    except OSError as exc:
        return None, _indeterminate(
            path,
            target=str(root),
            owner=manifest.plugin,
            detail=str(exc),
        )
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        return None, _inactive(
            path,
            "target-unusable",
            target=str(root),
            owner=manifest.plugin,
            detail="plugin_root must be a regular non-reparse directory",
        )
    return canonical, None


def _canonical_registrar_target(
    path: Path,
    manifest: RegistrarManifest,
    plugin_root: Path,
) -> tuple[Path | None, EntryDecision[RegistrarCandidate] | None]:
    target = plugin_root / Path(manifest.registrar)
    try:
        info = target.lstat()
        canonical = target.resolve(strict=True)
    except FileNotFoundError as exc:
        return None, _inactive(
            path,
            "missing-target",
            target=str(target),
            owner=manifest.plugin,
            detail=str(exc),
        )
    except OSError as exc:
        return None, _indeterminate(
            path,
            target=str(target),
            owner=manifest.plugin,
            detail=str(exc),
        )
    try:
        canonical.relative_to(plugin_root)
    except ValueError:
        return None, _inactive(
            path,
            "identity-mismatch",
            target=str(target),
            owner=manifest.plugin,
            detail="registrar target escapes the active plugin root",
        )
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        return None, _inactive(
            path,
            "target-unusable",
            target=str(target),
            owner=manifest.plugin,
            detail="registrar target must be a regular non-reparse directory",
        )
    return canonical, None


def _classify_declaration(
    path: Path,
    *,
    manifest: RegistrarManifest,
    plugin_root: Path,
    active_plugin: ActivePlugin | None,
) -> EntryDecision[PluginDeclaration]:
    try:
        declaration = read_declaration_file(
            path, allow_plugin_companion=True
        )
    except RegistrarIndeterminateError as exc:
        return EntryDecision.indeterminate(
            _finding(
                path,
                "entry-indeterminate",
                status="indeterminate",
                owner=manifest.plugin,
                detail=str(exc),
            )
        )
    except (RegistrarError, UnicodeDecodeError) as exc:
        return EntryDecision.inactive(
            _finding(
                path,
                "invalid-entry",
                owner=manifest.plugin,
                detail=str(exc),
            )
        )
    if declaration.owner and declaration.owner != manifest.plugin:
        return EntryDecision.inactive(
            _finding(
                path,
                "identity-mismatch",
                owner=manifest.plugin,
                target=declaration.owner,
                detail="plugin declaration owner must match its attributed source",
            )
        )
    if not declaration.owner:
        declaration = declaration.with_owner(manifest.plugin)
    if declaration.kind == "plugin-companion":
        if active_plugin is None:
            return EntryDecision.indeterminate(
                _finding(
                    path,
                    "entry-indeterminate",
                    status="indeterminate",
                    owner=manifest.plugin,
                    detail="plugin activation could not be confirmed",
                )
            )
        try:
            plugin_data = json.loads(
                (plugin_root / "plugin.json").read_text(encoding="utf-8-sig")
            )
            if not isinstance(plugin_data, dict):
                raise ValueError("plugin.json must contain an object")
            plugin_version = (
                declaration.runtime_generation or plugin_data.get("version")
            )
        except OSError as exc:
            return EntryDecision.indeterminate(
                _finding(
                    path,
                    "entry-indeterminate",
                    status="indeterminate",
                    owner=manifest.plugin,
                    detail=f"plugin companion version could not be read: {exc}",
                )
            )
        except (UnicodeDecodeError, ValueError) as exc:
            return EntryDecision.inactive(
                _finding(
                    path,
                    "invalid-entry",
                    owner=manifest.plugin,
                    detail=f"plugin companion version could not be read: {exc}",
                )
            )
        if not isinstance(plugin_version, str) or not plugin_version:
            return EntryDecision.inactive(
                _finding(
                    path,
                    "invalid-entry",
                    owner=manifest.plugin,
                    detail=(
                        "plugin companion requires plugin.json version or an explicit "
                        "runtime_generation"
                    ),
                )
            )
        scopes = tuple(
            sorted(
                {
                    scope
                    for live_root in active_plugin.live_roots
                    if live_root.root.resolve() == plugin_root
                    for scope in live_root.scopes
                }
            )
        )
        if not scopes:
            return EntryDecision.inactive(
                _finding(
                    path,
                    "identity-mismatch",
                    owner=manifest.plugin,
                    detail="plugin companion root has no authoritative activation scopes",
                )
            )
        declaration = declaration.with_plugin_provenance(
            plugin_root=str(plugin_root),
            source_path=str(path.resolve()),
            plugin_version=plugin_version,
            activation_scopes=scopes,
        )
    return EntryDecision.active(
        PluginDeclaration(
            declaration=declaration,
            source_path=str(path),
            manifest_path=manifest.source_path,
            plugin=manifest.plugin,
        )
    )


def _manifest_identity_matches(
    manifest: RegistrarManifest,
    previous: RegistrarCandidate | None,
    *,
    canonical_root: Path | None = None,
) -> bool:
    if previous is None:
        return False
    current_root = (
        str(canonical_root)
        if canonical_root is not None
        else str(Path(manifest.plugin_root).expanduser())
    )
    return (
        previous.manifest.plugin == manifest.plugin
        and previous.manifest.plugin_root == current_root
        and previous.manifest.registrar == manifest.registrar
    )


def _candidate(
    manifest: RegistrarManifest,
    plugin_root: Path,
    declaration_entries: Mapping[str, PluginDeclaration],
) -> RegistrarCandidate:
    return RegistrarCandidate(
        manifest=RegistrarManifest(
            plugin=manifest.plugin,
            plugin_root=str(plugin_root),
            registrar=manifest.registrar,
            source_path=manifest.source_path,
        ),
        declaration_entries=dict(declaration_entries),
    )


def _retained_declarations(
    snapshot: ScanSnapshot[PluginDeclaration],
    previous: RegistrarCandidate | None,
    *,
    identity_matches: bool,
) -> dict[str, PluginDeclaration]:
    """Retain only unchanged/read-indeterminate documents from the same manifest."""
    if previous is None or not identity_matches:
        return {}
    prior = previous.declaration_entries
    if snapshot.authority is ScanAuthority.INDETERMINATE:
        return dict(prior)
    if snapshot.authority is ScanAuthority.ABSENT:
        return {}

    retained: dict[str, PluginDeclaration] = {}
    for entry, decision in snapshot.decisions.items():
        old = prior.get(entry)
        if old is None:
            continue
        if decision.status is EntryStatus.INDETERMINATE:
            retained[entry] = old
        elif (
            decision.status in (EntryStatus.ACTIVE, EntryStatus.ACTIVE_WITH_ADVISORY)
            and decision.value == old
        ):
            retained[entry] = old
    return retained


def _uncertain_candidate(
    path: Path,
    manifest: RegistrarManifest,
    plugin_root: Path,
    declaration_entries: Mapping[str, PluginDeclaration],
    *,
    target: str,
    detail: str,
    findings: Sequence[Finding] = (),
) -> EntryDecision[RegistrarCandidate]:
    candidate = _candidate(manifest, plugin_root, declaration_entries)
    uncertainty = _finding(
        path,
        "entry-indeterminate",
        status="indeterminate",
        target=target,
        owner=manifest.plugin,
        detail=detail,
    )
    return EntryDecision.advisory(candidate, uncertainty, *findings)


def _classify_manifest(
    path: Path,
    activation_source: Callable[[], ActivationReport],
    previous: RegistrarCandidate | None,
    observed_sources: set[str],
) -> EntryDecision[RegistrarCandidate]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        manifest = parse_manifest(data, source_path=str(path))
    except (json.JSONDecodeError, UnicodeDecodeError, ManifestError) as exc:
        return _inactive(path, "invalid-entry", detail=str(exc))

    observed_sources.add(manifest.plugin)
    activation = activation_source()
    activation_decision = activation.decisions.get(manifest.plugin)
    if activation.authority is not ScanAuthority.INDETERMINATE:
        if activation_decision is None:
            return _inactive(
                path,
                "not-enabled",
                target=manifest.plugin_root,
                owner=manifest.plugin,
                detail="plugin is not enabled globally or in any registered project",
            )
        if (
            activation_decision.status is EntryStatus.INACTIVE
            or activation_decision.value is None
            and activation_decision.status is not EntryStatus.INDETERMINATE
        ):
            finding = activation_decision.findings[0]
            return _inactive(
                path,
                finding.reason,
                target=finding.target or manifest.plugin_root,
                owner=manifest.plugin,
                detail=finding.detail,
            )

    plugin_root, root_error = _canonical_plugin_root(path, manifest)
    if root_error is not None:
        if root_error.status is EntryStatus.INDETERMINATE:
            retained = (
                previous.declaration_entries
                if _manifest_identity_matches(manifest, previous)
                and previous is not None
                else {}
            )
            return _uncertain_candidate(
                path,
                manifest,
                Path(manifest.plugin_root).expanduser(),
                retained,
                target=manifest.plugin_root,
                detail=root_error.findings[0].detail or "plugin root is unreadable",
            )
        return root_error
    assert plugin_root is not None
    identity_matches = _manifest_identity_matches(
        manifest,
        previous,
        canonical_root=plugin_root,
    )

    registrar_target, target_error = _canonical_registrar_target(
        path, manifest, plugin_root
    )
    if target_error is not None:
        if target_error.status is EntryStatus.INDETERMINATE:
            retained = (
                previous.declaration_entries
                if identity_matches and previous is not None
                else {}
            )
            return _uncertain_candidate(
                path,
                manifest,
                plugin_root,
                retained,
                target=str(plugin_root / Path(manifest.registrar)),
                detail=target_error.findings[0].detail or "registrar target is unreadable",
            )
        return target_error
    assert registrar_target is not None
    active_plugin = activation_decision.value if activation_decision is not None else None

    declaration_snapshot = scan_directory(
        registrar_target,
        lambda declaration_path: _classify_declaration(
            declaration_path,
            manifest=manifest,
            plugin_root=plugin_root,
            active_plugin=active_plugin,
        ),
        registry=REGISTRY_NAME,
        suffixes=_DECL_SUFFIXES,
    )
    if declaration_snapshot.authority is ScanAuthority.INDETERMINATE:
        detail = "; ".join(
            finding.detail or finding.reason
            for finding in declaration_snapshot.findings
        )
        retained = _retained_declarations(
            declaration_snapshot,
            previous,
            identity_matches=identity_matches,
        )
        return _uncertain_candidate(
            path,
            manifest,
            plugin_root,
            retained,
            target=str(registrar_target),
            detail=detail or "registrar target could not be enumerated",
        )
    if declaration_snapshot.authority is ScanAuthority.ABSENT:
        return _inactive(
            path,
            "missing-target",
            target=str(registrar_target),
            owner=manifest.plugin,
            detail="registrar target disappeared during classification",
        )

    prior_declarations = (
        previous.declaration_entries
        if identity_matches and previous is not None
        else None
    )
    declaration_entries = declaration_snapshot.reconcile(prior_declarations)
    retained_entries = _retained_declarations(
        declaration_snapshot,
        previous,
        identity_matches=identity_matches,
    )

    if activation.authority is ScanAuthority.INDETERMINATE:
        return _uncertain_candidate(
            path,
            manifest,
            plugin_root,
            retained_entries,
            target=manifest.plugin_root,
            detail="effective plugin activation evidence is indeterminate",
            findings=declaration_snapshot.findings,
        )

    if activation_decision is None:
        return EntryDecision.inactive(
            _finding(
                path,
                "not-enabled",
                target=manifest.plugin_root,
                owner=manifest.plugin,
                detail="plugin is not enabled globally or in any registered project",
            ),
            *declaration_snapshot.findings,
        )
    if activation_decision.status is EntryStatus.INDETERMINATE:
        detail = "; ".join(
            finding.detail or finding.reason
            for finding in activation_decision.findings
        )
        return _uncertain_candidate(
            path,
            manifest,
            plugin_root,
            retained_entries,
            target=manifest.plugin_root,
            detail=detail or "plugin root eligibility is indeterminate",
            findings=declaration_snapshot.findings,
        )
    if activation_decision.status is EntryStatus.INACTIVE or activation_decision.value is None:
        finding = activation_decision.findings[0]
        return EntryDecision.inactive(
            _finding(
                path,
                finding.reason,
                target=finding.target or manifest.plugin_root,
                owner=manifest.plugin,
                detail=finding.detail,
            ),
            *declaration_snapshot.findings,
        )
    live_roots = {
        selected.root for selected in activation_decision.value.live_roots
    }
    if plugin_root not in live_roots:
        return EntryDecision.inactive(
            _finding(
                path,
                "identity-mismatch",
                target=manifest.plugin_root,
                owner=manifest.plugin,
                detail=(
                    "manifest plugin_root differs from authoritative live plugin roots "
                    + ", ".join(str(root) for root in sorted(live_roots))
                ),
            ),
            *declaration_snapshot.findings,
        )

    candidate = _candidate(manifest, plugin_root, declaration_entries)
    advisories = list(declaration_snapshot.findings)
    if activation_decision.status is EntryStatus.ACTIVE_WITH_ADVISORY:
        advisories.extend(
            _activation_advisories(path, manifest.plugin, activation_decision)
        )
    if advisories:
        return EntryDecision.advisory(candidate, *advisories)
    return EntryDecision.active(candidate)


def _quarantine_plugin_duplicates(
    entries: Mapping[str, RegistrarCandidate],
) -> tuple[tuple[PluginDeclaration, ...], tuple[Finding, ...]]:
    claims: dict[str, list[PluginDeclaration]] = defaultdict(list)
    for _, candidate in sorted(entries.items()):
        for _, contributed in sorted(candidate.declaration_entries.items()):
            claims[contributed.declaration.name].append(contributed)

    active: list[PluginDeclaration] = []
    findings: list[Finding] = []
    for name, peers in sorted(claims.items()):
        if len(peers) == 1:
            active.append(peers[0])
            continue
        peer_paths = sorted(peer.source_path for peer in peers)
        for peer in peers:
            conflicts = [path for path in peer_paths if path != peer.source_path]
            findings.append(
                _finding(
                    peer.source_path,
                    "duplicate",
                    target=name,
                    owner=peer.plugin,
                    detail=(
                        "plugin declaration name is also claimed by "
                        + ", ".join(conflicts)
                    ),
                )
            )
    return tuple(active), tuple(findings)


def _quarantine_source_duplicates(
    entries: Mapping[str, RegistrarCandidate],
) -> tuple[dict[str, RegistrarCandidate], tuple[Finding, ...]]:
    by_source: dict[str, list[tuple[str, RegistrarCandidate]]] = defaultdict(list)
    for entry, candidate in sorted(entries.items()):
        by_source[candidate.manifest.plugin].append((entry, candidate))

    active: dict[str, RegistrarCandidate] = {}
    findings: list[Finding] = []
    for source, peers in sorted(by_source.items()):
        if len(peers) == 1:
            entry, candidate = peers[0]
            active[entry] = candidate
            continue
        peer_entries = [entry for entry, _ in peers]
        for entry, _ in peers:
            conflicts = [peer for peer in peer_entries if peer != entry]
            findings.append(
                _finding(
                    entry,
                    "duplicate",
                    target=source,
                    owner=source,
                    detail=(
                        "plugin source is also claimed by "
                        + ", ".join(conflicts)
                    ),
                )
            )
    return active, tuple(findings)


def _ensure_indeterminate_remedy(finding: Finding) -> Finding:
    if finding.remedy is not None or finding.status != "indeterminate":
        return finding
    return Finding(
        registry=finding.registry,
        entry=finding.entry,
        status=finding.status,
        reason=finding.reason,
        target=finding.target,
        owner=finding.owner,
        remedy=(
            "Restore access and retry `agent-dispatch registrar doctor`; "
            "the runtime retains only matching last-known state."
        ),
        detail=finding.detail,
    )


def _retention_rejection(
    entry: str,
    candidate: RegistrarCandidate,
    activation: ActivationReport,
) -> Finding | None:
    """Return a definitive reason a last-known candidate may not be retained."""
    if activation.authority is ScanAuthority.INDETERMINATE:
        return None
    decision = activation.decisions.get(candidate.manifest.plugin)
    if decision is None:
        return _finding(
            entry,
            "not-enabled",
            target=candidate.manifest.plugin_root,
            owner=candidate.manifest.plugin,
            detail="plugin is not enabled globally or in any registered project",
        )
    if decision.status is EntryStatus.INDETERMINATE:
        return None
    if decision.status is EntryStatus.INACTIVE or decision.value is None:
        cause = decision.findings[0]
        return _finding(
            entry,
            cause.reason,
            target=cause.target or candidate.manifest.plugin_root,
            owner=candidate.manifest.plugin,
            detail=cause.detail,
        )
    live_roots = {selected.root for selected in decision.value.live_roots}
    if Path(candidate.manifest.plugin_root) not in live_roots:
        return _finding(
            entry,
            "identity-mismatch",
            target=candidate.manifest.plugin_root,
            owner=candidate.manifest.plugin,
            detail=(
                "active plugin roots are now "
                + ", ".join(str(root) for root in sorted(live_roots))
            ),
        )
    return None


def scan_registrar_registry(
    directory: str | os.PathLike[str] | None = None,
    *,
    previous: Mapping[str, RegistrarCandidate] | None = None,
    activation_report: ActivationReport | None = None,
) -> RegistrarRegistryReport:
    """Scan and reconcile attributed plugin registrar candidates."""
    root = Path(directory) if directory is not None else registrar_dropins_dir()
    prior = dict(previous or {})
    resolved_activation = activation_report
    observed_sources: set[str] = set()

    def current_activation() -> ActivationReport:
        nonlocal resolved_activation
        if resolved_activation is None:
            resolved_activation = resolve_active_plugins()
        return resolved_activation

    snapshot = scan_directory(
        root,
        lambda path: _classify_manifest(
            path,
            current_activation,
            prior.get(str(path)),
            observed_sources,
        ),
        registry=REGISTRY_NAME,
        suffixes=(".json",),
    )
    entries = snapshot.reconcile(prior)
    retention_findings: list[Finding] = []
    if entries:
        activation = current_activation()
        retained: dict[str, RegistrarCandidate] = {}
        for entry, candidate in entries.items():
            rejection = _retention_rejection(entry, candidate, activation)
            if rejection is None:
                retained[entry] = candidate
            else:
                retention_findings.append(rejection)
        entries = retained
    active_entries, source_duplicate_findings = _quarantine_source_duplicates(
        entries
    )
    declarations, duplicate_findings = _quarantine_plugin_duplicates(
        active_entries
    )
    scan_findings = tuple(
        _ensure_indeterminate_remedy(finding)
        for finding in snapshot.findings
    )
    activation_findings: list[Finding] = []
    if resolved_activation is not None:
        if resolved_activation.authority is ScanAuthority.INDETERMINATE:
            activation_findings.extend(resolved_activation.findings)
        for source in sorted(observed_sources):
            decision = resolved_activation.decisions.get(source)
            if decision is not None and decision.status is EntryStatus.INDETERMINATE:
                activation_findings.extend(decision.findings)
    unique_activation_findings: list[Finding] = []
    seen_activation_findings: set[str] = set()
    for finding in activation_findings:
        fingerprint = finding.fingerprint()
        if fingerprint in seen_activation_findings:
            continue
        seen_activation_findings.add(fingerprint)
        unique_activation_findings.append(finding)
    return RegistrarRegistryReport(
        snapshot=snapshot,
        entries=entries,
        declarations=declarations,
        findings=(
            *scan_findings,
            *unique_activation_findings,
            *retention_findings,
            *source_duplicate_findings,
            *duplicate_findings,
        ),
    )


def combine_registrar_sources(
    trusted: Sequence[ProfileDeclaration],
    plugins: RegistrarRegistryReport,
) -> CombinedRegistrarReport:
    """Merge trusted declarations over plugin candidates by profile name."""
    trusted_by_name = {declaration.name: declaration for declaration in trusted}
    selected = dict(trusted_by_name)
    findings = list(plugins.findings)
    for contributed in plugins.declarations:
        name = contributed.declaration.name
        if name in trusted_by_name:
            findings.append(
                _finding(
                    contributed.source_path,
                    "duplicate",
                    target=name,
                    owner=contributed.plugin,
                    detail="trusted pointers.json declaration wins this profile name",
                )
            )
            continue
        selected[name] = contributed.declaration
    return CombinedRegistrarReport(
        trusted=tuple(trusted),
        plugins=plugins,
        declarations=tuple(selected[name] for name in sorted(selected)),
        findings=tuple(findings),
    )
