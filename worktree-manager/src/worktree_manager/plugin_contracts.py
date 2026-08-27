"""Manager-owned contracts for plugin-contributed Picker surfaces.

Plugins contribute static JSON manifests in ``pivots/*.json`` inside their own
installed payload. The Worktree Manager discovers and validates those files
directly; contributors never import the Manager and never need to copy a file
into Manager-owned state.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .harness_state import (
    build_projects,
    repo_plugin_enablement,
    user_enabled_plugins,
)

CONTRACT_VERSION = 1
PLUGINS_ROOT_ENV = "WORKTREE_MANAGER_PLUGINS_DIR"


class ContractError(ValueError):
    """A contribution manifest is structurally invalid."""


class UnsupportedSchemaError(ContractError):
    """A valid manifest requires a newer contract version."""


@dataclass(frozen=True)
class ColumnContract:
    key: str
    header: str
    width: int | None = None
    align: str = "l"
    style: str | None = None
    palette: str | None = None


@dataclass(frozen=True)
class ActionContract:
    key: str
    label: str
    kind: str
    run: tuple[str, ...] = ()
    confirm: bool = False
    description: str = ""
    when: Mapping[str, object] | None = None
    progress: bool = False
    internal: str | None = None
    form: Mapping[str, object] | None = None
    card: Mapping[str, object] | None = None
    available: bool = True


@dataclass(frozen=True)
class WorktreeActionContract:
    key: str
    label: str
    run: tuple[str, ...]
    confirm: bool = False
    description: str = ""
    when: Mapping[str, object] | None = None
    available: bool = True


@dataclass(frozen=True)
class ConfigSectionContract:
    key: str
    label: str
    run: tuple[str, ...]
    confirm: bool = False
    description: str = ""
    available: bool = True


@dataclass(frozen=True)
class PivotContract:
    name: str
    label: str
    after: str
    home: bool
    list_cmd: tuple[str, ...]
    id_field: str
    title_field: str
    worktree_field: str | None
    subtitle_field: str | None
    badge_fields: tuple[str, ...]
    group_field: str | None
    empty_hint: str
    columns: tuple[ColumnContract, ...]
    summary_template: str | None
    scope: str
    stream: bool
    subscribe: bool
    actions: tuple[ActionContract, ...]
    view_actions: tuple[ActionContract, ...]


@dataclass(frozen=True)
class PluginContribution:
    schema_version: int
    marketplace: str
    plugin: str
    source_path: str
    pivot: PivotContract | None
    worktree_actions: tuple[WorktreeActionContract, ...] = ()
    config_sections: tuple[ConfigSectionContract, ...] = ()
    legacy_schema: bool = False
    command_available: bool = True

    @property
    def qualified_plugin(self) -> str:
        return f"{self.plugin}@{self.marketplace}"


@dataclass(frozen=True)
class ContractFinding:
    code: str
    severity: str
    marketplace: str
    plugin: str
    source_path: str
    detail: str


@dataclass(frozen=True)
class ContractReport:
    contract_version: int
    project: str | None
    contributions: tuple[PluginContribution, ...]
    findings: tuple[ContractFinding, ...]

    def to_dict(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "project": self.project,
            "contributions": [asdict(c) for c in self.contributions],
            "findings": [asdict(f) for f in self.findings],
        }


def installed_plugins_dir(
    base: str | os.PathLike[str] | None = None,
    *,
    home_dir: Path | None = None,
) -> Path:
    if base is not None:
        return Path(base)
    if env := os.environ.get(PLUGINS_ROOT_ENV):
        return Path(env)
    if env := os.environ.get("AGENT_WORKTREES_PLUGINS_DIR"):
        return Path(env)
    home = home_dir or Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".copilot" / "installed-plugins"


def _as_argv(value: object, *, where: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ContractError(f"{where} must be a non-empty array of strings")
    if not value:
        raise ContractError(f"{where} must be a non-empty array of strings")
    if any(not isinstance(item, str) or not item for item in value):
        raise ContractError(f"{where} must contain only non-empty strings")
    return tuple(value)


def _optional_path(value: object, *, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{where} must be a non-empty string when present")
    return value.strip()


def _parse_columns(raw: object) -> tuple[ColumnContract, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ContractError("`columns` must be an array when present")
    out: list[ColumnContract] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ContractError(f"`columns[{i}]` must be an object")
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ContractError(f"`columns[{i}].key` is required")
        header = item.get("header", key)
        if not isinstance(header, str):
            raise ContractError(f"`columns[{i}].header` must be a string")
        width = item.get("width")
        if width is not None and (
            isinstance(width, bool) or not isinstance(width, int) or width <= 0
        ):
            raise ContractError(f"`columns[{i}].width` must be a positive integer")
        align = item.get("align", "l")
        if align not in ("l", "r", "c"):
            raise ContractError(f"`columns[{i}].align` must be one of l/r/c")
        style = item.get("style")
        palette = item.get("palette")
        if style is not None and not isinstance(style, str):
            raise ContractError(f"`columns[{i}].style` must be a string")
        if palette is not None and not isinstance(palette, str):
            raise ContractError(f"`columns[{i}].palette` must be a string")
        out.append(ColumnContract(
            key=key.strip(),
            header=header,
            width=width,
            align=align,
            style=style,
            palette=palette,
        ))
    return tuple(out)


def _parse_actions(
    raw: object,
    *,
    field_name: str = "actions",
) -> tuple[ActionContract, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ContractError(f"`{field_name}` must be an array when present")
    out: list[ActionContract] = []
    for i, item in enumerate(raw):
        path = f"{field_name}[{i}]"
        if not isinstance(item, Mapping):
            raise ContractError(f"`{path}` must be an object")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ContractError(f"`{path}.label` is required")
        key = item.get("key")
        action_key = key if isinstance(key, str) and key else f"action{i}"
        kind = item.get("kind") or "command"
        if kind not in ("command", "internal", "form", "card"):
            raise ContractError(f"`{path}.kind` is unsupported")
        internal = None
        form = None
        card = None
        if kind == "internal":
            verb = item.get("verb")
            if not isinstance(verb, str) or not verb.strip():
                raise ContractError(f"`{path}.verb` is required")
            args = item.get("args", [])
            if not isinstance(args, Sequence) or isinstance(args, (str, bytes)):
                raise ContractError(f"`{path}.args` must be an array")
            if any(not isinstance(arg, str) for arg in args):
                raise ContractError(f"`{path}.args` must contain only strings")
            run = tuple(args)
            internal = verb.strip()
        elif kind == "form":
            fields_from = item.get("fields_from")
            if not isinstance(fields_from, str) or not fields_from.strip():
                raise ContractError(f"`{path}.fields_from` is required")
            run = _as_argv(item.get("run"), where=f"`{path}.run`")
            form = {
                "fields_from": fields_from.strip(),
                "title_from": _optional_path(
                    item.get("title_from"), where=f"`{path}.title_from`"),
                "body_from": _optional_path(
                    item.get("body_from"), where=f"`{path}.body_from`"),
            }
        elif kind == "card":
            run = ()
            card = {
                "title_from": _optional_path(
                    item.get("title_from"), where=f"`{path}.title_from`")
                or "card.title",
                "status_from": _optional_path(
                    item.get("status_from"), where=f"`{path}.status_from`")
                or "card.status",
                "link_from": _optional_path(
                    item.get("link_from"), where=f"`{path}.link_from`")
                or "card.link",
                "body_from": _optional_path(
                    item.get("body_from"), where=f"`{path}.body_from`")
                or "card.body",
            }
        else:
            run = _as_argv(item.get("run"), where=f"`{path}.run`")
        when = item.get("when")
        if when is not None and not isinstance(when, Mapping):
            raise ContractError(f"`{path}.when` must be an object")
        progress = item.get("progress", False)
        if not isinstance(progress, bool):
            raise ContractError(f"`{path}.progress` must be a boolean")
        out.append(ActionContract(
            key=action_key,
            label=label.strip(),
            kind=kind,
            run=run,
            confirm=bool(item.get("confirm", False)),
            description=str(item.get("description", "")),
            when=dict(when) if isinstance(when, Mapping) else None,
            progress=progress,
            internal=internal,
            form=form,
            card=card,
        ))
    return tuple(out)


def _parse_worktree_actions(raw: object, *, name: str) -> tuple[WorktreeActionContract, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ContractError("`worktree_actions` must be an array when present")
    out: list[WorktreeActionContract] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ContractError(f"`worktree_actions[{i}]` must be an object")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ContractError(f"`worktree_actions[{i}].label` is required")
        key = item.get("key")
        when = item.get("when")
        if when is not None and not isinstance(when, Mapping):
            raise ContractError(f"`worktree_actions[{i}].when` must be an object")
        out.append(WorktreeActionContract(
            key=key if isinstance(key, str) and key else f"{name}{i}",
            label=label.strip(),
            run=_as_argv(item.get("run"), where=f"`worktree_actions[{i}].run`"),
            confirm=bool(item.get("confirm", False)),
            description=str(item.get("description", "")),
            when=dict(when) if isinstance(when, Mapping) else None,
        ))
    return tuple(out)


def _parse_config_sections(raw: object, *, name: str) -> tuple[ConfigSectionContract, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ContractError("`config_sections` must be an array when present")
    out: list[ConfigSectionContract] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ContractError(f"`config_sections[{i}]` must be an object")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ContractError(f"`config_sections[{i}].label` is required")
        key = item.get("key")
        out.append(ConfigSectionContract(
            key=key if isinstance(key, str) and key else f"{name}{i}",
            label=label.strip(),
            run=_as_argv(item.get("run"), where=f"`config_sections[{i}].run`"),
            confirm=bool(item.get("confirm", False)),
            description=str(item.get("description", "")),
        ))
    return tuple(out)


def parse_manifest(
    data: Mapping[str, object],
    *,
    name: str,
    marketplace: str,
    plugin: str,
    source_path: str,
) -> PluginContribution:
    if not isinstance(data, Mapping):
        raise ContractError("manifest root must be a JSON object")
    raw_version = data.get("schema_version")
    legacy = raw_version is None
    version = CONTRACT_VERSION if legacy else raw_version
    if isinstance(version, bool) or not isinstance(version, int):
        raise ContractError("`schema_version` must be an integer")
    if version != CONTRACT_VERSION:
        raise UnsupportedSchemaError(
            f"manifest requires contract v{version}; this Manager supports "
            f"v{CONTRACT_VERSION} (update the Worktree Manager)")

    pivot = None
    if "list" in data or "label" in data:
        label = data.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ContractError("`label` is required and must be a non-empty string")
        entry = data.get("entry") or {}
        if not isinstance(entry, Mapping):
            raise ContractError("`entry` must be an object when present")

        def entry_str(key: str, default: str | None) -> str | None:
            value = entry.get(key, default)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ContractError(f"`entry.{key}` must be a string")
            return value

        badges = entry.get("badges", [])
        if isinstance(badges, str):
            badge_fields = (badges,)
        elif isinstance(badges, Sequence):
            badge_fields = tuple(str(x) for x in badges)
        else:
            raise ContractError("`entry.badges` must be a string or array")
        scope = data.get("scope", "machine")
        if scope not in ("machine", "account", "global"):
            raise ContractError("`scope` must be one of machine/account/global")
        stream = data.get("stream", False)
        subscribe = data.get("subscribe", False)
        if not isinstance(stream, bool) or not isinstance(subscribe, bool):
            raise ContractError("`stream` and `subscribe` must be booleans")
        home = data.get("home", False)
        if not isinstance(home, bool):
            raise ContractError("`home` must be a boolean")
        summary = data.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ContractError("`summary` must be a string when present")
        after = data.get("after", "Worktrees")
        if not isinstance(after, str) or not after.strip():
            after = "Worktrees"
        empty_hint = data.get("empty_hint", "No entries.")
        if not isinstance(empty_hint, str):
            empty_hint = "No entries."
        pivot = PivotContract(
            name=name,
            label=label.strip(),
            after=after.strip(),
            home=home,
            list_cmd=_as_argv(data.get("list"), where="`list`"),
            id_field=entry_str("id", "id") or "id",
            title_field=entry_str("title", "title") or "title",
            worktree_field=entry_str("worktree", "target_worktree"),
            subtitle_field=entry_str("subtitle", None),
            badge_fields=badge_fields,
            group_field=entry_str("group", None),
            empty_hint=empty_hint,
            columns=_parse_columns(data.get("columns")),
            summary_template=summary,
            scope=str(scope),
            stream=stream,
            subscribe=subscribe,
            actions=_parse_actions(data.get("actions")),
            view_actions=_parse_actions(
                data.get("view_actions"), field_name="view_actions"),
        )

    worktree_actions = _parse_worktree_actions(data.get("worktree_actions"), name=name)
    config_sections = _parse_config_sections(data.get("config_sections"), name=name)
    if pivot is None and not worktree_actions and not config_sections:
        raise ContractError(
            "manifest must contribute a pivot, worktree_actions, or config_sections")
    return PluginContribution(
        schema_version=version,
        marketplace=marketplace,
        plugin=plugin,
        source_path=source_path,
        pivot=pivot,
        worktree_actions=worktree_actions,
        config_sections=config_sections,
        legacy_schema=legacy,
        command_available=True,
    )


def _enabled_keys(project: str | None, home_dir: Path | None) -> set[str]:
    enabled = {
        item.qualified: item.enabled
        for item in user_enabled_plugins(home_dir)
    }
    if project:
        match = next((p for p in build_projects(home_dir) if p.name == project), None)
        if match and match.repo:
            enabled.update(repo_plugin_enablement(match.repo.path))
    return {name for name, value in enabled.items() if value}


def _probe_commands(
    contribution: PluginContribution,
) -> tuple[PluginContribution, list[str]]:
    """Mark only the surface whose command is missing as unavailable."""
    missing: list[str] = []
    pivot = contribution.pivot
    pivot_available = True
    if pivot:
        list_command = pivot.list_cmd[0]
        if shutil.which(list_command) is None:
            pivot_available = False
            missing.append(f"pivot list: {list_command}")
        actions = []
        for action in pivot.actions:
            available = True
            if action.kind in ("command", "form") and action.run:
                command = action.run[0]
                if shutil.which(command) is None:
                    available = False
                    missing.append(f"action {action.key}: {command}")
            actions.append(replace(action, available=available))
        pivot = replace(pivot, actions=tuple(actions))
        view_actions = []
        for action in pivot.view_actions:
            available = True
            if action.kind in ("command", "form") and action.run:
                command = action.run[0]
                if shutil.which(command) is None:
                    available = False
                    missing.append(f"view action {action.key}: {command}")
            view_actions.append(replace(action, available=available))
        pivot = replace(pivot, view_actions=tuple(view_actions))

    worktree_actions = []
    for action in contribution.worktree_actions:
        command = action.run[0]
        available = shutil.which(command) is not None
        if not available:
            missing.append(f"worktree action {action.key}: {command}")
        worktree_actions.append(replace(action, available=available))

    config_sections = []
    for section in contribution.config_sections:
        command = section.run[0]
        available = shutil.which(command) is not None
        if not available:
            missing.append(f"config section {section.key}: {command}")
        config_sections.append(replace(section, available=available))

    return (
        replace(
            contribution,
            pivot=pivot,
            worktree_actions=tuple(worktree_actions),
            config_sections=tuple(config_sections),
            command_available=pivot_available,
        ),
        missing,
    )


def _legacy_pivots_dir(home_dir: Path | None) -> Path:
    if env := os.environ.get("AGENT_WORKTREES_PIVOTS_DIR"):
        return Path(env)
    home = home_dir or Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".agent-worktrees" / "pivots"


def discover_contracts(
    *,
    project: str | None = None,
    home_dir: Path | None = None,
    plugins_root: str | os.PathLike[str] | None = None,
) -> ContractReport:
    root = installed_plugins_dir(plugins_root, home_dir=home_dir)
    enabled = _enabled_keys(project, home_dir)
    contributions: list[PluginContribution] = []
    findings: list[ContractFinding] = []
    seen_labels: dict[str, PluginContribution] = {}
    home_contributions: list[PluginContribution] = []
    enabled_payloads: dict[str, list[Path]] = {}

    try:
        manifests = sorted(root.glob("*/*/pivots/*.json"))
    except OSError as exc:
        findings.append(ContractFinding(
            code="registry-unreadable",
            severity="error",
            marketplace="",
            plugin="",
            source_path=str(root),
            detail=str(exc),
        ))
        manifests = []

    for path in manifests:
        marketplace = path.parents[2].name
        plugin = path.parents[1].name
        qualified = f"{plugin}@{marketplace}"
        if qualified not in enabled:
            findings.append(ContractFinding(
                code="disabled-contribution",
                severity="info",
                marketplace=marketplace,
                plugin=plugin,
                source_path=str(path),
                detail=f"{qualified} is installed but not enabled"
                + (f" for project {project}" if project else " user-global"),
            ))
            continue
        enabled_payloads.setdefault(path.name, []).append(path)
        try:
            raw = json.loads(path.read_text("utf-8"))
            contribution = parse_manifest(
                raw,
                name=path.stem,
                marketplace=marketplace,
                plugin=plugin,
                source_path=str(path),
            )
        except OSError as exc:
            findings.append(ContractFinding(
                code="manifest-unreadable",
                severity="warning",
                marketplace=marketplace,
                plugin=plugin,
                source_path=str(path),
                detail=str(exc),
            ))
            continue
        except UnsupportedSchemaError as exc:
            findings.append(ContractFinding(
                code="unsupported-schema-version",
                severity="warning",
                marketplace=marketplace,
                plugin=plugin,
                source_path=str(path),
                detail=str(exc),
            ))
            continue
        except ContractError as exc:
            findings.append(ContractFinding(
                code="invalid-contract",
                severity="warning",
                marketplace=marketplace,
                plugin=plugin,
                source_path=str(path),
                detail=str(exc),
            ))
            continue
        except ValueError as exc:
            findings.append(ContractFinding(
                code="invalid-json",
                severity="warning",
                marketplace=marketplace,
                plugin=plugin,
                source_path=str(path),
                detail=str(exc),
            ))
            continue

        if contribution.legacy_schema:
            findings.append(ContractFinding(
                code="legacy-schema",
                severity="info",
                marketplace=marketplace,
                plugin=plugin,
                source_path=str(path),
                detail=f"schema_version omitted; interpreted as {CONTRACT_VERSION}",
            ))
        contribution, missing = _probe_commands(contribution)
        if missing:
            findings.append(ContractFinding(
                code="command-unavailable",
                severity="warning",
                marketplace=marketplace,
                plugin=plugin,
                source_path=str(path),
                detail=f"surface command(s) not on PATH: {', '.join(missing)}",
            ))

        if contribution.pivot:
            label_key = contribution.pivot.label.casefold()
            prior = seen_labels.get(label_key)
        else:
            label_key = ""
            prior = None
        if prior and prior.pivot:
            findings.extend((
                ContractFinding(
                    code="duplicate-pivot-label",
                    severity="warning",
                    marketplace=prior.marketplace,
                    plugin=prior.plugin,
                    source_path=prior.source_path,
                    detail=f"pivot label {prior.pivot.label!r} is contributed more than once",
                ),
                ContractFinding(
                    code="duplicate-pivot-label",
                    severity="warning",
                    marketplace=marketplace,
                    plugin=plugin,
                    source_path=str(path),
                    detail=f"pivot label {contribution.pivot.label!r} is contributed more than once",
                ),
            ))
        elif label_key:
            seen_labels[label_key] = contribution
        contributions.append(contribution)
        if contribution.pivot and contribution.pivot.home:
            home_contributions.append(contribution)

    if len(home_contributions) > 1:
        for contribution in home_contributions:
            findings.append(ContractFinding(
                code="duplicate-home-pivot",
                severity="warning",
                marketplace=contribution.marketplace,
                plugin=contribution.plugin,
                source_path=contribution.source_path,
                detail=(
                    "more than one enabled pivot declares `home: true`; "
                    "the Manager uses the first available contribution"
                ),
            ))

    legacy_dir = _legacy_pivots_dir(home_dir)
    try:
        legacy_files = sorted(legacy_dir.glob("*.json"))
    except OSError:
        legacy_files = []
    for legacy in legacy_files:
        owners = enabled_payloads.get(legacy.name, [])
        if not owners:
            findings.append(ContractFinding(
                code="orphan-legacy-dropin",
                severity="warning",
                marketplace="",
                plugin="",
                source_path=str(legacy),
                detail="legacy Picker registry entry has no enabled payload owner",
            ))
            continue
        if len(owners) == 1:
            try:
                if legacy.read_bytes() != owners[0].read_bytes():
                    findings.append(ContractFinding(
                        code="stale-legacy-dropin",
                        severity="info",
                        marketplace=owners[0].parents[2].name,
                        plugin=owners[0].parents[1].name,
                        source_path=str(legacy),
                        detail=f"legacy Picker copy differs from {owners[0]}",
                    ))
            except OSError as exc:
                findings.append(ContractFinding(
                    code="legacy-dropin-unreadable",
                    severity="warning",
                    marketplace="",
                    plugin="",
                    source_path=str(legacy),
                    detail=str(exc),
                ))

    return ContractReport(
        contract_version=CONTRACT_VERSION,
        project=project,
        contributions=tuple(contributions),
        findings=tuple(findings),
    )
