"""Project tool contributor to the existing knowledge-plugin settings owner.

No settings writer lives here: custody and desired values are prepared here,
then reconciled under knowledge_plugins' workspace lease and atomic writer.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from . import config as cfg, git_ops, installer, pr_authority, repos

KEY = "_agentWorktreesToolPluginOverlay"
REGISTRY_KEY = "copilot_plugin_profile_root"
SETTING_KEYS = ("extraKnownMarketplaces", "enabledPlugins")


@dataclass(frozen=True)
class _Family:
    project: str
    root: Path
    common: str
    paths: tuple[Path, ...]
    identities: frozenset[str]


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse ambiguous duplicate keys at every JSON object depth."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _error(detail: str):
    from .knowledge_plugins import KnowledgePluginError

    return KnowledgePluginError(detail)


def _identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _safe_path(root: Path, path: Path) -> None:
    """Reject links/reparse points and escapes before reading or writing."""
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise _error(f"path escapes registered root: {path}") from exc
    if ".." in relative.parts or any(":" in part for part in relative.parts):
        raise _error(f"path escapes registered root: {path}")
    for item in (*reversed(root.parents), root, *(root.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts) + 1))):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _error(f"unsafe linked settings path: {item}")
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise _error(f"settings parent is not a directory: {item}")


def _common(path: Path) -> str:
    _safe_path(path, path / ".git")
    try:
        result = git_ops.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path,
                             isolated_repository=True)
    except git_ops.GitError as exc:
        raise _error(f"cannot inspect project Git custody: {exc}") from exc
    return _identity(Path(os.fsdecode(result.stdout).removesuffix("\n")))


def _family(project: str) -> _Family:
    if project not in installer.read_projects_registry(strict=True).get("projects", {}):
        raise _error("tool profile project is not registered")
    entry, root = repos.resolve_registered_repo(project)
    foreign_roots = {
        _identity(Path(local_path))
        for other in repos.list_repos(strict=True)
        if other.name != project and (local_path := other.local_path())
    }
    if _identity(root) in foreign_roots:
        raise _error("registered project root has ambiguous repository custody")
    raw_path = entry.local_path()
    if raw_path is None:
        raise _error("registered project has no local root")
    raw = Path(raw_path)
    _safe_path(raw, raw)
    common = _common(root)
    if common not in (_identity(root / ".git"), _identity(root)):
        raise _error("registered project root is not its Git anchor")
    try:
        roots = git_ops.list_worktree_paths(cwd=root, fail_on_error=True, isolated_repository=True)
    except (git_ops.GitError, RuntimeError) as exc:
        raise _error(f"cannot inspect project Git family: {exc}") from exc
    family, identities = [root], {_identity(root)}
    for path in roots:
        identity = _identity(path)
        if identity in foreign_roots:
            raise _error(f"Git family member is registered to another project: {path}")
        if identity in identities:
            continue
        _safe_path(path, path)
        if not path.is_dir():
            raise _error(f"registered Git family checkout is unavailable: {path}")
        if _common(path) != common:
            raise _error(f"foreign Git family checkout: {path}")
        family.append(path)
        identities.add(identity)
    return _Family(project, root, common, tuple(family), frozenset(identities))


def _profile(root: Path, selected: str | None) -> tuple[str | None, dict[str, dict]]:
    from . import knowledge_plugins as kp

    empty: dict[str, dict[str, Any]] = {key: {} for key in SETTING_KEYS}
    if selected is None:
        return None, empty
    windows = PureWindowsPath(selected)
    relative = Path(selected)
    if relative.is_absolute() or windows.drive or windows.root or ".." in windows.parts or ".." in relative.parts:
        raise _error("copilot_plugin_profile must stay inside the registered anchor")
    path = root / relative
    _safe_path(root, path)
    if path == root / ".github" / "copilot" / "settings.local.json":
        raise _error("the generated settings overlay cannot be a plugin profile")
    if not path.is_file():
        raise _error(f"plugin profile is unavailable: {path}")
    data = kp._load_json_object(path)
    if set(data) - set(SETTING_KEYS):
        raise _error("plugin profile supports only extraKnownMarketplaces and enabledPlugins")
    desired = {key: kp._dict_setting(data, key, path) for key in SETTING_KEYS}
    for source, on in desired["enabledPlugins"].items():
        if not isinstance(source, str) or not source.strip() or not isinstance(on, bool):
            raise _error("plugin profile enabledPlugins must contain boolean choices")
    for name, definition in desired["extraKnownMarketplaces"].items():
        if not name.strip() or not isinstance(definition, dict):
            raise _error("plugin profile marketplace must be a named object")
        kp._validate_marketplace_source(definition, marketplace=name, source_name="tool profile", path=path)
        if kp._is_local_marketplace(definition):
            source_path = definition["source"]["path"].strip()
            local = Path(source_path)
            if PureWindowsPath(source_path).drive and not local.is_absolute():
                raise _error("ambiguous local marketplace path")
            if not local.is_absolute():
                local = root / local
            _safe_path(root, local)
            if not local.is_dir():
                raise _error(f"local marketplace directory is unavailable: {local}")
            localized_source = {**definition["source"], "path": local.resolve().as_posix()}
            definition = {**definition, "source": localized_source}
        desired["extraKnownMarketplaces"][name] = definition
    return _identity(path), desired


def validate_marker(existing: dict, path: Path, *, project: str | None = None,
                    root: Path | None = None, family: _Family | None = None) -> dict | None:
    """Authenticate exact contributor scope, never just value equality."""
    marker = existing.get(KEY)
    if KEY not in existing:
        return None
    if not isinstance(marker, dict) or type(marker.get("version")) is not int or marker.get("version") != 1:
        raise _error(f"unsupported tool plugin receipt in {path}")
    if not all(isinstance(marker.get(key), str) and marker[key] for key in ("project", "root", "profile")):
        raise _error(f"malformed tool plugin receipt in {path}")
    if project is None:
        project = marker["project"]
    family = family or _family(project)
    actual_root = family.root
    if root is not None and _identity(root) != _identity(actual_root):
        raise _error(f"changed tool project root in {path}")
    if family.project != project or marker["project"] != project or marker["root"] != _identity(actual_root):
        raise _error(f"foreign-root tool plugin receipt in {path}")
    if _identity(path.parent.parent.parent) not in family.identities:
        raise _error(f"tool plugin receipt is outside its registered Git family: {path}")
    _safe_path(actual_root, Path(marker["profile"]))
    for key in SETTING_KEYS:
        if not isinstance(marker.get(key), dict):
            raise _error(f"malformed tool plugin receipt {key} in {path}")
    if any(not isinstance(value, bool) for value in marker["enabledPlugins"].values()):
        raise _error(f"malformed tool activation receipt in {path}")
    _safe_path(path.parent.parent.parent, path)
    return marker


def preserve_guard(result: dict) -> None:
    """Older personal owners must refuse mixed contributor documents."""
    from . import knowledge_plugins as kp

    if KEY in result:
        marker = result.setdefault(kp._OVERLAY_KEY, {"marketplaces": {}, "enabledPlugins": {}})
        marker["version"] = 2
    elif kp.same_json_value(result.get(kp._OVERLAY_KEY), {"version": 2, "marketplaces": {}, "enabledPlugins": {}}):
        result.pop(kp._OVERLAY_KEY)
    elif kp._OVERLAY_KEY in result:
        result[kp._OVERLAY_KEY]["version"] = 1


def protect_tool_receipts(existing: dict, result: dict, personal: dict,
                          marketplace_conflicts: list, enabled_conflicts: list) -> None:
    """A missing live tool value does not transfer its receipt to personal input."""
    tool = existing.get(KEY)
    if tool is None:
        return
    rejected_marketplaces = set()
    for setting, receipt, conflicts in (
        ("extraKnownMarketplaces", "marketplaces", marketplace_conflicts),
        ("enabledPlugins", "enabledPlugins", enabled_conflicts),
    ):
        authored = personal[receipt]
        overlaps = set(authored) & set(tool[setting])
        if setting == "extraKnownMarketplaces":
            rejected_marketplaces.update(overlaps)
        for name in overlaps:
            authored.pop(name)
            if name in existing.get(setting, {}):
                result[setting][name] = existing[setting][name]
            else:
                result.get(setting, {}).pop(name, None)
            conflicts.append(name)
        if not result.get(setting) and setting not in existing:
            result.pop(setting, None)
    from .knowledge_plugins import split_source

    for source in tuple(personal["enabledPlugins"]):
        if split_source(source)[1] in rejected_marketplaces:
            personal["enabledPlugins"].pop(source)
            result.get("enabledPlugins", {}).pop(source, None)
            enabled_conflicts.append(source)
    if not result.get("enabledPlugins") and "enabledPlugins" not in existing:
        result.pop("enabledPlugins", None)


def validate_personal_version(marker: dict, path: Path, existing: dict) -> None:
    from . import knowledge_plugins as kp

    if marker.get("version") not in (1, 2) or (marker.get("version") == 2 and KEY not in existing):
        raise _error(
            f"cannot safely manage {path}: unsupported '{kp._OVERLAY_KEY}' version {marker.get('version')!r}"
        )
    tool = existing.get(KEY)
    if isinstance(tool, dict):
        for key, personal_key in (("extraKnownMarketplaces", "marketplaces"), ("enabledPlugins", "enabledPlugins")):
            owned = marker.get(personal_key)
            if isinstance(owned, dict) and set(owned) & set(tool[key]):
                raise _error(f"ambiguous tool/personal receipt ownership in {path}")


def _verify_checkout(root: Path, checkout: Path, project: str, family: _Family) -> None:
    if project not in installer.read_projects_registry(strict=True).get("projects", {}):
        raise _error("tool profile project is not registered")
    _, registered_root = repos.resolve_registered_repo(project)
    if family.project != project or _identity(root) != _identity(registered_root):
        raise _error("project tool staging root has changed")
    if _identity(checkout) not in family.identities or _common(checkout) != family.common:
        raise _error("checkout is not in the registered project Git family")


@pr_authority.registry_transaction
def _stage(root: Path, checkout: Path, project: str, profile: str | None, desired: dict[str, dict]) -> dict[str, Any]:
    return _stage_locked(root, checkout, project, profile, desired, _family(project))


def _stage_locked(root: Path, checkout: Path, project: str, profile: str | None,
                  desired: dict[str, dict], family: _Family, *, retire_only: bool = False) -> dict[str, Any]:
    """Use only this owner's guarded transaction evidence; recheck the target."""
    from . import knowledge_plugins as kp

    path = checkout / ".github" / "copilot" / "settings.local.json"
    _safe_path(checkout, path)
    with kp._overlay_transaction(path):
        _verify_checkout(root, checkout, project, family)
        _safe_path(checkout, path)
        existing = kp._load_json_object(path)
        if retire_only and KEY not in existing:
            return {"action": "no-op", "checkout": str(checkout), "changed": False}
        previous = validate_marker(existing, path, project=project, root=root, family=family)
        personal = kp._managed_marker(existing, path, tool_family=family)
        legacy_path = checkout / ".claude" / "settings.local.json"
        _safe_path(checkout, legacy_path)
        legacy = kp._load_json_object(legacy_path)
        result = dict(existing)
        managed: dict[str, dict[str, Any]] = {key: {} for key in SETTING_KEYS}
        conflicts: dict[str, list[str]] = {key: [] for key in SETTING_KEYS}
        for key in SETTING_KEYS:
            current = kp._dict_setting(existing, key, path)
            legacy_values = kp._dict_setting(legacy, key, legacy_path)
            prior = previous[key] if previous else {}
            personal_values = personal["marketplaces" if key == "extraKnownMarketplaces" else key] if personal else {}
            if set(prior) & set(personal_values):
                raise _error(f"ambiguous tool/personal receipt ownership in {path}")
            kp._retire_previous(current, prior)
            for name, value in desired[key].items():
                if name in personal_values:
                    conflicts[key].append(name)
                    continue
                if name in current:
                    if not kp.same_json_value(current[name], value):
                        conflicts[key].append(name)
                    continue
                if name in legacy_values:
                    native = {**existing, key: current}
                    tiers = kp._read_harness_settings(
                        checkout, native, ignored_native_marketplaces=set(), ignored_native_enabled=set())
                    effective = tiers.marketplaces.get(name) if key == "extraKnownMarketplaces" else tiers.enabled.get(name)
                    if not kp.same_json_value(legacy_values[name], value) or not kp.same_json_value(effective, value):
                        conflicts[key].append(name)
                    continue
                current[name] = value
                managed[key][name] = value
            if current:
                result[key] = current
            else:
                result.pop(key, None)
        if any(conflicts.values()):
            raise _error(f"tool plugin conflicts in {checkout}: {conflicts}")
        if profile is not None:
            result[KEY] = {"version": 1, "project": project, "root": _identity(root), "profile": profile, **managed}
        else:
            result.pop(KEY, None)
        preserve_guard(result)
        changed = kp._write_overlay(path, result)
        return {"checkout": str(checkout), "changed": changed, "conflicts": conflicts}


def prepare_checkout(checkout: str | Path, *, config: cfg.Config | None = None) -> dict:
    """Stage only one registered checkout; no profile preserves old behavior."""
    if config is not None and not hasattr(config, "default_repo"):
        config = None
    if config is None:
        anchor = git_ops.resolve_to_anchor(Path(checkout))
        entry = next((entry for entry in repos.list_repos(strict=True)
                      if (local_path := entry.local_path()) and _identity(Path(local_path)) == _identity(anchor)), None)
        if entry is None:
            path = Path(checkout) / ".github" / "copilot" / "settings.local.json"
            _safe_path(Path(checkout), path)
            if path.is_file() and KEY in path.read_text(encoding="utf-8"):
                raise _error("unregistered checkout carries tool plugin receipt")
            return {"action": "no-op"}
        config = cfg.load_project_config(entry.name, include_control_plane_related_pr=False)
    repo = config.default_repo
    selected = getattr(repo, "copilot_plugin_profile", None)
    if selected is None:
        path = Path(checkout) / ".github" / "copilot" / "settings.local.json"
        if not path.is_file():
            return {"action": "no-op"}
        _safe_path(Path(checkout), path)
        if KEY not in path.read_text(encoding="utf-8"):
            return {"action": "no-op"}
    project = config.repo_name
    candidate = Path(checkout)
    with pr_authority.guard():
        family = _family(project)
        root = family.root
        if _identity(Path(repo.anchor)) != _identity(root):
            raise _error("effective project anchor differs from its registered root")
        if _identity(candidate) not in family.identities:
            raise _error("checkout is not in the registered project Git family")
        profile, desired = _profile(root, selected)
        registry = installer.read_projects_registry(strict=True)
        entry = registry["projects"][project]
        if REGISTRY_KEY in entry and entry[REGISTRY_KEY] != _identity(root):
            raise _error("project tool receipt retry root has changed")
        entry[REGISTRY_KEY] = _identity(root)
        installer.write_projects_registry(registry)
        summary = _stage_locked(root, candidate, project, profile, desired, family)
        if any(summary["conflicts"].values()):
            raise _error(f"tool plugin conflicts in {candidate}: {summary['conflicts']}")
        return summary


@pr_authority.registry_transaction
def reconcile_project(project: str, repo_dir: str | Path | None, registry: dict) -> None:
    """Register/re-register the anchor and existing family, retaining retry scope."""
    entry = registry["projects"][project]
    try:
        config = cfg.load_project_config(project, include_control_plane_related_pr=False)
    except cfg.ProjectNotRegisteredError:
        # Legacy registry-only adoption can precede an available checkout.
        # It cannot enable this optional feature or retire prior receipts.
        declared = cfg._load_inrepo_config(str(repo_dir)) if repo_dir is not None else {}
        if REGISTRY_KEY in entry or declared.get("copilot_plugin_profile") is not None:
            raise
        return
    repo = config.default_repo
    if repo.copilot_plugin_profile is None and REGISTRY_KEY not in entry:
        return
    family = _family(project)
    root = family.root
    if repo_dir is not None and _identity(Path(repo_dir)) != _identity(root):
        raise _error("register target differs from the registered project root")
    if _identity(Path(repo.anchor)) != _identity(root):
        raise _error("effective project anchor differs from its registered root")
    if REGISTRY_KEY in entry and entry[REGISTRY_KEY] != _identity(root):
        raise _error("project tool receipt retry root has changed")
    profile, desired = _profile(root, repo.copilot_plugin_profile)
    entry[REGISTRY_KEY] = _identity(root)
    installer.write_projects_registry(registry)
    for checkout in family.paths:
        summary = _stage_locked(root, checkout, project, profile, desired, family)
        if any(summary["conflicts"].values()):
            raise _error(f"tool plugin conflicts in {checkout}: {summary['conflicts']}")


@pr_authority.registry_transaction
def retire_project(project: str) -> list[dict]:
    """Retire only tool receipts before deregistration; failures retain registry."""
    registry = installer.read_projects_registry(strict=True)
    entry = registry.get("projects", {}).get(project, {})
    if REGISTRY_KEY not in entry:
        return []
    family = _family(project)
    root = family.root
    if entry[REGISTRY_KEY] != _identity(root):
        raise _error("project tool receipt retry root has changed")
    completed = []
    try:
        for checkout in family.paths:
            summary = _stage_locked(root, checkout, project, None,
                                    {key: {} for key in SETTING_KEYS}, family, retire_only=True)
            if summary.get("action") != "no-op":
                completed.append(summary)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error(f"tool retirement failed; project retained for retry; completed checkouts={completed}: {exc}") from exc
    return completed
