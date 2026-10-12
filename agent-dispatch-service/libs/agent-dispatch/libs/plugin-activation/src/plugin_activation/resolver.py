"""Resolve enabled plugin sources to one current, identity-verified root.

This module is the orchestrator: it defines the public result shapes
(``ActivePlugin``, ``ActivePluginRoot``, ``ActivationReport``) and
``resolve_active_plugins``, the entry point that combines per-source
candidates from the resolution helpers in sibling ``_*`` modules
(``_config_io`` for settings I/O, ``_fs_candidates`` for filesystem checks,
``_installed_root``/``_marketplace_root`` for the two root-resolution
strategies, ``_project_roots`` for registered-project verification, and
``_remote_identity`` for Git-remote normalization).

The small ``_git`` subprocess helper (plus ``_clean_git_env`` and its
module-level ``_GIT_WHICH_CACHE``) stays resident **here**, in this module,
rather than moving out with ``_verified_project_roots`` to ``_project_roots``:
this repo's own test suite monkeypatches it directly on this module object
(``monkeypatch.setattr(resolver, "_git", ...)``, plus ``resolver.shutil`` and
``resolver.subprocess``) to simulate Git failures. Moving the function itself
elsewhere would silently break that monkeypatch -- see
``_project_roots``'s module docstring for how that module calls back into
``_git`` here without needing its own copy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
)
from plugin_resolve import SETTINGS_RELS, split_source

from ._config_io import _read_settings, _user_settings
from ._findings import REGISTRY_NAME, _combine_authority, _finding
from ._fs_candidates import _valid_source_part
from ._installed_root import _installed_root
from ._marketplace_root import _local_root
from ._memo import MarketplaceMemo
from ._project_roots import _verified_project_roots
from ._remote_identity import normalize_remote as normalize_remote  # re-export


@dataclass(frozen=True)
class ActivePluginRoot:
    """One identity-verified live root and the scopes that select it."""

    root: Path
    scopes: tuple[str, ...]
    kind: str


@dataclass(frozen=True)
class ActivePlugin:
    source: str
    name: str
    marketplace: str
    root: Path
    scopes: tuple[str, ...]
    roots: tuple[ActivePluginRoot, ...] = ()

    @property
    def live_roots(self) -> tuple[ActivePluginRoot, ...]:
        """Return every live root, preserving compatibility with older values."""
        return self.roots or (
            ActivePluginRoot(root=self.root, scopes=self.scopes, kind="selected"),
        )

    def root_for_scope(self, scope: str) -> Path | None:
        """Return the root selected by one activation scope, when known."""
        return next(
            (
                selected.root
                for selected in self.live_roots
                if scope in selected.scopes
            ),
            None,
        )


@dataclass(frozen=True)
class ActivationReport:
    """Current decisions plus the authority needed for safe reconciliation."""

    authority: ScanAuthority
    decisions: Mapping[str, EntryDecision[ActivePlugin]]
    findings: tuple[Finding, ...] = ()

    @property
    def active(self) -> Mapping[str, ActivePlugin]:
        """Return values proven active by current evidence only."""
        return {
            source: decision.value
            for source, decision in self.decisions.items()
            if decision.status
            in (EntryStatus.ACTIVE, EntryStatus.ACTIVE_WITH_ADVISORY)
            and decision.value is not None
        }

    def reconcile(
        self,
        previous: Mapping[str, ActivePlugin] | None = None,
    ) -> dict[str, ActivePlugin]:
        """Apply shared tri-state reconciliation to a prior effective set."""
        return ScanSnapshot(
            registry=REGISTRY_NAME,
            authority=self.authority,
            decisions=self.decisions,
            findings=self.findings,
        ).reconcile(previous)


def _clean_git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


_GIT_WHICH_CACHE: dict[str, str | None] = {}


def _git(root: Path, *args: str) -> str:
    # picker-performance-and-responsiveness Phase 4: cached -- PATH can't
    # change mid-process, but a cold boot calls this ~17-18 times.
    if "git" not in _GIT_WHICH_CACHE:
        _GIT_WHICH_CACHE["git"] = shutil.which("git")
    git = _GIT_WHICH_CACHE["git"]
    if not git:
        raise FileNotFoundError("git")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(  # noqa: S603 - resolved executable, fixed argv
        [git, "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        env=_clean_git_env(),
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return completed.stdout.strip()


def _decision_findings(
    source: str,
    source_findings: Mapping[str, list[Finding]],
) -> tuple[Finding, ...]:
    return tuple(source_findings.get(source, ()))


def resolve_active_plugins(
    *,
    home: str | Path | None = None,
    include_projects: bool = True,
) -> ActivationReport:
    """Resolve sources with tri-state authority; optionally restrict to global.

    Global-only callers do not inspect registered projects or their overrides.
    """
    user_home = Path(home).expanduser() if home is not None else Path.home()
    copilot_home = user_home / ".copilot"
    agent_worktrees_home = user_home / ".agent-worktrees"  # marketplace-isolation: allow registry
    registry_findings: list[Finding] = []
    scopes: dict[str, set[str]] = defaultdict(set)
    local_roots: dict[str, set[Path]] = defaultdict(set)
    scope_local_roots: dict[str, dict[str, Path]] = defaultdict(dict)
    source_findings: dict[str, list[Finding]] = defaultdict(list)
    source_indeterminate: set[str] = set()
    memo = MarketplaceMemo()

    global_settings = _user_settings(copilot_home)
    registry_findings.extend(global_settings.findings)
    for source in global_settings.settings.enabled_sources():
        scope = "global"
        scopes[source].add(scope)
        candidate = _local_root(source, global_settings, base=copilot_home, _memo=memo)
        source_findings[source].extend(candidate.findings)
        if candidate.root is not None:
            local_roots[source].add(candidate.root)
            scope_local_roots[source][scope] = candidate.root
        if candidate.indeterminate:
            source_indeterminate.add(source)

    settings_authorities = [global_settings.authority]
    if include_projects:
        project_roots, project_findings, projects_authority = _verified_project_roots(
            agent_worktrees_home
        )
        registry_findings.extend(project_findings)
        settings_authorities.append(projects_authority)
        for project, root in project_roots:
            project_settings = _read_settings(root, SETTINGS_RELS)
            settings_authorities.append(project_settings.authority)
            registry_findings.extend(project_settings.findings)
            for source in project_settings.settings.enabled_sources():
                scope = f"project:{project}"
                scopes[source].add(scope)
                candidate = _local_root(source, project_settings, base=root, _memo=memo)
                source_findings[source].extend(candidate.findings)
                if candidate.root is not None:
                    local_roots[source].add(candidate.root)
                    scope_local_roots[source][scope] = candidate.root
                if candidate.indeterminate:
                    source_indeterminate.add(source)

    authority = _combine_authority(*settings_authorities)
    decisions: dict[str, EntryDecision[ActivePlugin]] = {}
    for source in sorted(scopes):
        name, marketplace = split_source(source)
        if not _valid_source_part(name) or not _valid_source_part(marketplace):
            finding = _finding(copilot_home, "invalid-entry", target=source)
            decisions[source] = EntryDecision.inactive(finding)
            continue

        roots = local_roots.get(source, set())
        if len(roots) > 1:
            finding = _finding(
                copilot_home,
                "root-ambiguous",
                target=", ".join(sorted(str(root) for root in roots)),
                owner=source,
            )
            decisions[source] = EntryDecision.inactive(
                *_decision_findings(source, source_findings),
                finding,
            )
            continue

        needs_installed = any(
            scope not in scope_local_roots[source]
            for scope in scopes[source]
        )
        installed = _installed_root(copilot_home, source)
        if installed.indeterminate and not needs_installed:
            installed.findings = [
                Finding(
                    registry=finding.registry,
                    entry=finding.entry,
                    status="advisory",
                    reason=finding.reason,
                    target=finding.target,
                    owner=finding.owner,
                    remedy=finding.remedy,
                    detail=finding.detail,
                )
                for finding in installed.findings
            ]
        source_findings[source].extend(installed.findings)
        if installed.indeterminate and needs_installed:
            source_indeterminate.add(source)
        if source in source_indeterminate:
            decisions[source] = EntryDecision.indeterminate(
                *_decision_findings(source, source_findings)
            )
            continue

        local_root = next(iter(roots)) if roots else None
        root = local_root or installed.root
        findings = _decision_findings(source, source_findings)
        if root is None:
            finding = _finding(
                copilot_home,
                "missing-target",
                target=source,
                owner=source,
            )
            decisions[source] = EntryDecision.inactive(*findings, finding)
            continue

        roots_by_identity: dict[tuple[Path, str], set[str]] = defaultdict(set)
        for scope in sorted(scopes[source]):
            scope_root = scope_local_roots[source].get(scope)
            if scope_root is not None:
                roots_by_identity[(scope_root, "directory")].add(scope)
            elif installed.root is not None:
                roots_by_identity[(installed.root, "installed")].add(scope)
        active_roots = tuple(
            ActivePluginRoot(
                root=selected_root,
                scopes=tuple(sorted(selected_scopes)),
                kind=kind,
            )
            for (selected_root, kind), selected_scopes in sorted(
                roots_by_identity.items(),
                key=lambda item: (
                    0 if item[0][1] == "directory" else 1,
                    str(item[0][0]),
                ),
            )
        )
        active = ActivePlugin(
            source=source,
            name=name,
            marketplace=marketplace,
            root=root,
            scopes=tuple(sorted(scopes[source])),
            roots=active_roots,
        )
        decisions[source] = (
            EntryDecision.advisory(active, *findings)
            if findings
            else EntryDecision.active(active)
        )

    decision_findings = [
        finding
        for decision in decisions.values()
        for finding in decision.findings
    ]
    return ActivationReport(
        authority=authority,
        decisions=decisions,
        findings=tuple(registry_findings + decision_findings),
    )
