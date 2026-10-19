"""One local policy catalog for remote ACP discovery and attachment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit

from pydantic import ValidationError

from .remote_acp_config import (
    RemoteAcpBinding,
    RemoteAcpTarget,
    canonical_endpoint,
)
from .remote_acp_sources import (
    RemoteContribution,
    RemoteContributionReport,
    discover_remote_contributions,
)


@dataclass(frozen=True)
class RemoteTargetEntry:
    name: str
    target: RemoteAcpTarget | None
    plugin: str = ""
    description: str = ""
    projects: tuple[str, ...] = ()
    global_scope: bool = True
    reason: str = ""

    def row(self) -> dict:
        state = "bound" if self.target is not None else (
            "requires-binding" if self.reason in {
                "instance-binding-required", "binding-owner-mismatch", "binding-project-mismatch",
                "invalid-instance-binding", "credential-source-binding-required",
            } else "unavailable"
        )
        return {
            "name": "remote-acp:" + self.name,
            "display_name": self.name,
            "description": self.description,
            "aliases": [],
            "managed": False,
            "spawnable_as_target": self.target is not None,
            "spawnable": self.target is not None,
            "target_type": "remote-acp",
            "provider": "remote-acp",
            "plugin": self.plugin,
            "project": self.projects[0] if len(self.projects) == 1 and not self.global_scope else None,
            "projects": list(self.projects),
            "global_scope": self.global_scope,
            "state": state,
            "unavailable_reason": self.reason,
        }


class RemoteAcpCatalog:
    def __init__(
        self, targets: dict[str, RemoteAcpTarget], *,
        bindings: Mapping[str, RemoteAcpBinding] | None = None,
        discover: Callable[[], RemoteContributionReport] = discover_remote_contributions,
        check_auth: Callable[[RemoteAcpTarget], object] | None = None,
        project_bindings: Callable[[str], Mapping[str, RemoteAcpBinding]] | None = None,
    ) -> None:
        self.targets = targets
        self.bindings = dict(bindings or {})
        self._discover = discover
        self._check_auth = check_auth
        self._project_bindings = project_bindings or _project_bindings
        self._contributions: dict[str, RemoteContribution] = {}
        self.contribution_report = RemoteContributionReport()

    @property
    def errors(self) -> list[str]:
        return list(self.contribution_report.errors)

    def discover(self) -> RemoteContributionReport:
        return self._discover()

    def apply(self, report: RemoteContributionReport) -> None:
        self.contribution_report = self._project_report(report)
        self._contributions = dict(self.contribution_report.targets)

    def _project_report(self, report: RemoteContributionReport) -> RemoteContributionReport:
        """Retain known unavailable names without publishing a fresh scan."""
        contributions = dict(report.targets)
        for name, previous in self._contributions.items():
            if previous.plugin in report.unavailable_owners and name not in contributions:
                contributions[name] = RemoteContribution(
                    name, previous.plugin, previous.scopes, None, "contribution-unavailable",
                )
        return RemoteContributionReport(
            contributions, set(report.unavailable_owners), list(report.errors),
        )

    def _entries(
        self, report: RemoteContributionReport, project: str | None,
    ) -> dict[str, RemoteTargetEntry]:
        entries: dict[str, RemoteTargetEntry] = {}
        for name, contribution in report.targets.items():
            scopes = contribution.scopes
            projects = tuple(scope.removeprefix("project:") for scope in scopes if scope.startswith("project:"))
            requirement = contribution.requirements
            reason = contribution.reason
            target = None
            binding = None
            context = project or (
                projects[0] if "global" not in scopes and len(projects) == 1 else None
            )
            binding_error = False
            try:
                lower = self._project_bindings(context) if context else {}
                binding = self.bindings.get(name, lower.get(name))
            except ValueError:
                binding_error = True
            if contribution.plugin in report.unavailable_owners:
                reason = "contribution-unavailable"
            elif requirement is None:
                reason = reason or "invalid-requirements"
            elif binding_error:
                reason = "project-binding-unavailable"
            elif binding is None:
                reason = "instance-binding-required"
            elif binding.plugin != contribution.plugin:
                reason = "binding-owner-mismatch"
            elif binding.project and "global" not in scopes and binding.project not in projects:
                reason = "binding-project-mismatch"
            else:
                try:
                    data = requirement.model_dump(exclude={"description"})
                    if urlsplit(binding.endpoint).scheme in {"wss", "ws"} and requirement.transport != "websocket":
                        raise ValueError("WebSocket binding requires WebSocket transport")
                    data["endpoint"] = canonical_endpoint(binding.endpoint)
                    data["auth"].update(
                        identity=binding.identity, credential_source=binding.credential_source,
                        resource=binding.resource, scope=binding.scope,
                    )
                    target = RemoteAcpTarget.model_validate(data)
                except (ValueError, ValidationError):
                    reason = "invalid-instance-binding"
            entries[name] = RemoteTargetEntry(
                name, target, contribution.plugin,
                requirement.description if requirement else "",
                (binding.project,) if target and binding and binding.project else projects,
                "global" in scopes and not (target and binding and binding.project), reason,
            )
        # Explicit complete service entries retain their existing authority.
        for name, target in self.targets.items():
            entries[name] = RemoteTargetEntry(name, RemoteAcpTarget.model_validate(target))
        if self._check_auth is not None:
            from dataclasses import replace

            for name, entry in entries.items():
                if entry.target is not None:
                    try:
                        self._check_auth(entry.target)
                    except PermissionError:
                        entries[name] = replace(
                            entry, target=None, reason="credential-source-binding-required",
                        )
        return entries

    def rows(self, project: str | None = None, *, refresh: bool = True) -> list[dict]:
        if refresh:
            self.apply(self.discover())
        return [
            entry.row() for entry in self._entries(self.contribution_report, project).values()
            if project is None or entry.global_scope or project in entry.projects
        ]

    def detail(self, name: str, project: str | None = None) -> dict | None:
        report = self._project_report(self.discover())
        entry = self._entries(report, project).get(name.removeprefix("remote-acp:"))
        if entry and (project is None or entry.global_scope or project in entry.projects):
            return entry.row()
        return None

    def resolve(
        self, address: str, *, project: str | None = None,
    ) -> tuple[str, RemoteAcpTarget]:
        entries = self._entries(self.discover(), project)
        if address.startswith("remote-acp:"):
            entry = entries.get(address.removeprefix("remote-acp:"))
            candidates = [entry] if entry else []
        else:
            identity = canonical_endpoint(address)
            candidates = [
                entry for entry in entries.values()
                if entry.target and canonical_endpoint(entry.target.endpoint) == identity
                and (entry.global_scope or project in entry.projects)
                and (urlsplit(address).scheme != "wss" or entry.target.transport == "websocket")
            ]
        if len(candidates) != 1:
            raise ValueError("Remote ACP address has no unique approved target policy")
        entry = candidates[0]
        if entry.target is None:
            raise ValueError("Remote ACP target requires a valid instance binding")
        if not entry.global_scope and project not in entry.projects:
            raise ValueError("Remote ACP target requires its enabled project context")
        return entry.name, entry.target


def _project_bindings(project: str) -> Mapping[str, RemoteAcpBinding]:
    from .config import load_repo_bridge_config
    from .related_plugins import _registry_anchor

    root = _registry_anchor(project)
    if root is None:
        return {}
    config = load_repo_bridge_config(root)
    return config.remote_acp_bindings if config is not None else {}


def reconcile_remote_rows(
    rows: list[dict], catalog: RemoteAcpCatalog | None,
    report: RemoteContributionReport | None, current: RemoteAcpCatalog | None,
) -> tuple[list[dict], bool]:
    """Omit superseded remote rows after an asynchronous assembly boundary."""
    changed = current is not catalog or getattr(catalog, "contribution_report", None) is not report
    return (
        [row for row in rows if row.get("target_type") != "remote-acp"] if changed else rows,
        changed,
    )
