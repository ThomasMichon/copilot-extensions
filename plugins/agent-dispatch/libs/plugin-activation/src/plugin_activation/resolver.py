"""Resolve enabled plugin sources to one current, identity-verified root."""

from __future__ import annotations

import ntpath
import os
import platform
import posixpath
import re
import shutil
import stat
import subprocess
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
)
from plugin_resolve import (
    MARKETPLACE_MANIFEST_RELS,
    PLUGIN_MANIFEST_RELS,
    SETTINGS_RELS,
    MarketplaceSourceKind,
    RepoPluginSettings,
    load_marketplace,
    local_marketplace_path,
    marketplace_source_kind,
    plugin_dir,
    split_source,
)

from .state import PluginStateError, read_json_object

REGISTRY_NAME = "plugin-activation"
_SCP_REMOTE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_URI_DRIVE = re.compile(r"^/[A-Za-z]:/")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class ActivePlugin:
    source: str
    name: str
    marketplace: str
    root: Path
    scopes: tuple[str, ...]


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


@dataclass
class _SettingsLoad:
    settings: RepoPluginSettings = field(default_factory=RepoPluginSettings)
    authority: ScanAuthority = ScanAuthority.ABSENT
    findings: list[Finding] = field(default_factory=list)
    enabled_origins: dict[str, Path] = field(default_factory=dict)
    marketplace_origins: dict[str, Path] = field(default_factory=dict)


@dataclass
class _Candidate:
    root: Path | None = None
    findings: list[Finding] = field(default_factory=list)
    indeterminate: bool = False


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _strip_git_suffix(value: str, *, case_insensitive: bool = False) -> str:
    value = value.rstrip("/")
    comparison = value.casefold() if case_insensitive else value
    return value[:-4] if comparison.endswith(".git") else value


def _normalize_windows_path(value: str) -> str:
    normalized = ntpath.normpath(value.replace("/", "\\"))
    return (
        f"file:{_strip_git_suffix(normalized, case_insensitive=True).casefold()}"
    )


def _normalize_posix_path(value: str) -> str:
    return f"file:{_strip_git_suffix(posixpath.normpath(value))}"


def normalize_remote(value: str) -> str | None:
    """Normalize network and local Git remotes without folding URL path case."""
    if not isinstance(value, str):
        return None
    raw = (value or "").strip()
    if not raw:
        return None
    if _WINDOWS_DRIVE.match(raw):
        return _normalize_windows_path(raw)
    if raw.startswith(("\\\\", "//")):
        return _normalize_windows_path(raw)
    if raw.startswith("/"):
        return _normalize_posix_path(raw)

    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.casefold()
    except ValueError:
        return None
    if scheme == "file":
        path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.casefold() != "localhost":
            return _normalize_windows_path(f"\\\\{parsed.netloc}{path}")
        if _WINDOWS_DRIVE.match(path):
            return _normalize_windows_path(path)
        if _WINDOWS_URI_DRIVE.match(path):
            return _normalize_windows_path(path[1:])
        if path.startswith(("\\\\", "//")):
            return _normalize_windows_path(path)
        return _normalize_posix_path(path)
    if scheme:
        try:
            host = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError:
            return None
        if not host or parsed.query or parsed.fragment:
            return None
        if port is not None:
            host = f"{host}:{port}"
        path = _strip_git_suffix(unquote(parsed.path).lstrip("/"))
        return f"network:{host}/{path}" if path else None

    scp = _SCP_REMOTE.match(raw)
    if scp:
        host, path = scp.groups()
        return f"network:{host.casefold()}/{_strip_git_suffix(path.lstrip('/'))}"

    normalized = os.path.normpath(raw)
    if os.name == "nt":
        normalized = os.path.normcase(normalized)
    return f"file-relative:{_strip_git_suffix(normalized)}"


def _finding(
    entry: Path,
    reason: str,
    *,
    status: str = "inactive",
    target: str | None = None,
    owner: str | None = None,
    detail: str | None = None,
) -> Finding:
    return Finding(
        registry=REGISTRY_NAME,
        entry=str(entry),
        status=status,
        reason=reason,
        target=target,
        owner=owner,
        remedy="Fix the registered repo/plugin settings or reinstall the plugin.",
        detail=detail,
    )


def _combine_authority(*authorities: ScanAuthority) -> ScanAuthority:
    if ScanAuthority.INDETERMINATE in authorities:
        return ScanAuthority.INDETERMINATE
    if all(authority is ScanAuthority.ABSENT for authority in authorities):
        return ScanAuthority.ABSENT
    return ScanAuthority.COMPLETE


def _load_json_object(path: Path) -> tuple[ScanAuthority, dict, list[Finding]]:
    if not path.exists():
        return ScanAuthority.ABSENT, {}, []
    try:
        _, data = read_json_object(path)
    except PluginStateError as exc:
        reason = (
            "entry-indeterminate"
            if isinstance(exc.__cause__, (OSError, UnicodeError))
            else "invalid-entry"
        )
        return (
            ScanAuthority.INDETERMINATE,
            {},
            [
                _finding(
                    path,
                    reason,
                    status="indeterminate",
                    detail=str(exc),
                )
            ],
        )
    return ScanAuthority.COMPLETE, data, []


def _read_settings(base: Path, rels: tuple[tuple[str, ...], ...]) -> _SettingsLoad:
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    enabled_origins: dict[str, Path] = {}
    marketplace_origins: dict[str, Path] = {}
    findings: list[Finding] = []
    authorities: list[ScanAuthority] = []

    for rel in rels:
        path = base.joinpath(*rel)
        authority, data, read_findings = _load_json_object(path)
        authorities.append(authority)
        findings.extend(read_findings)
        if authority is not ScanAuthority.COMPLETE:
            continue

        raw_enabled = data.get("enabledPlugins")
        if raw_enabled is not None and not isinstance(raw_enabled, dict):
            authorities.append(ScanAuthority.INDETERMINATE)
            findings.append(
                _finding(
                    path,
                    "invalid-entry",
                    status="indeterminate",
                    detail="enabledPlugins must be an object",
                )
            )
        elif isinstance(raw_enabled, dict):
            for source, value in raw_enabled.items():
                if not isinstance(source, str) or not isinstance(value, bool):
                    authorities.append(ScanAuthority.INDETERMINATE)
                    findings.append(
                        _finding(
                            path,
                            "invalid-entry",
                            status="indeterminate",
                            target=str(source),
                            detail="enabledPlugins entries require string keys and booleans",
                        )
                    )
                    continue
                enabled[source] = value
                enabled_origins[source] = path

        raw_marketplaces = data.get("extraKnownMarketplaces")
        if raw_marketplaces is not None and not isinstance(raw_marketplaces, dict):
            authorities.append(ScanAuthority.INDETERMINATE)
            findings.append(
                _finding(
                    path,
                    "invalid-entry",
                    status="indeterminate",
                    detail="extraKnownMarketplaces must be an object",
                )
            )
        elif isinstance(raw_marketplaces, dict):
            for marketplace, definition in raw_marketplaces.items():
                if not isinstance(marketplace, str) or not isinstance(definition, dict):
                    authorities.append(ScanAuthority.INDETERMINATE)
                    findings.append(
                        _finding(
                            path,
                            "invalid-entry",
                            status="indeterminate",
                            target=str(marketplace),
                            detail="marketplace entries require string keys and objects",
                        )
                    )
                    continue
                marketplaces[marketplace] = definition
                marketplace_origins[marketplace] = path

    authority = (
        _combine_authority(*authorities)
        if authorities
        else ScanAuthority.ABSENT
    )
    return _SettingsLoad(
        settings=RepoPluginSettings(enabled=enabled, marketplaces=marketplaces),
        authority=authority,
        findings=findings,
        enabled_origins=enabled_origins,
        marketplace_origins=marketplace_origins,
    )


def _user_settings(copilot_home: Path) -> _SettingsLoad:
    return _read_settings(
        copilot_home,
        (("settings.json",), ("settings.local.json",)),
    )


def _platform_key() -> str:
    if platform.system() == "Windows":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _load_yaml_object(path: Path) -> tuple[ScanAuthority, dict, list[Finding]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ScanAuthority.ABSENT, {}, []
    except (OSError, UnicodeDecodeError) as exc:
        return (
            ScanAuthority.INDETERMINATE,
            {},
            [
                _finding(
                    path,
                    "registry-indeterminate",
                    status="indeterminate",
                    detail=str(exc),
                )
            ],
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return (
            ScanAuthority.INDETERMINATE,
            {},
            [
                _finding(
                    path,
                    "invalid-entry",
                    status="indeterminate",
                    detail=str(exc),
                )
            ],
        )
    if not isinstance(data, dict):
        return (
            ScanAuthority.INDETERMINATE,
            {},
            [
                _finding(
                    path,
                    "invalid-entry",
                    status="indeterminate",
                    detail="registry document must be a mapping",
                )
            ],
        )
    return ScanAuthority.COMPLETE, data, []


def _clean_git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _git(root: Path, *args: str) -> str:
    git = shutil.which("git")
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


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right


def _verified_project_roots(
    agent_worktrees_home: Path,
) -> tuple[list[tuple[str, Path]], list[Finding], ScanAuthority]:
    projects_path = agent_worktrees_home / "projects.yaml"
    repos_path = agent_worktrees_home / "repos.yaml"
    projects_authority, projects_data, findings = _load_yaml_object(projects_path)
    if projects_authority is ScanAuthority.ABSENT:
        return [], findings, ScanAuthority.ABSENT
    if projects_authority is ScanAuthority.INDETERMINATE:
        return [], findings, ScanAuthority.INDETERMINATE

    projects = projects_data.get("projects")
    if not isinstance(projects, dict):
        findings.append(
            _finding(
                projects_path,
                "invalid-entry",
                status="indeterminate",
                detail="projects must be a mapping",
            )
        )
        return [], findings, ScanAuthority.INDETERMINATE
    if not projects:
        return [], findings, ScanAuthority.COMPLETE

    repos_authority, repos_data, repos_findings = _load_yaml_object(repos_path)
    findings.extend(repos_findings)
    if repos_authority is not ScanAuthority.COMPLETE:
        if repos_authority is ScanAuthority.ABSENT:
            findings.append(
                _finding(
                    repos_path,
                    "registry-indeterminate",
                    status="indeterminate",
                    detail="repos registry is missing for adopted projects",
                )
            )
        return [], findings, ScanAuthority.INDETERMINATE
    repos = repos_data.get("repos")
    if not isinstance(repos, dict):
        findings.append(
            _finding(
                repos_path,
                "invalid-entry",
                status="indeterminate",
                detail="repos must be a mapping",
            )
        )
        return [], findings, ScanAuthority.INDETERMINATE

    plat = _platform_key()
    roots: list[tuple[str, Path]] = []
    registry_indeterminate = False
    for raw_name in sorted(projects, key=str):
        project = projects[raw_name]
        if not isinstance(raw_name, str) or not isinstance(project, dict):
            findings.append(
                _finding(
                    projects_path,
                    "invalid-entry",
                    status="indeterminate",
                    target=str(raw_name),
                    detail="project entries require string keys and mappings",
                )
            )
            registry_indeterminate = True
            continue
        name = raw_name
        repo = repos.get(name)
        if not isinstance(repo, dict):
            findings.append(
                _finding(projects_path, "identity-mismatch", owner=name)
            )
            continue
        raw_root = repo.get(plat)
        expected_remote = repo.get("remote")
        if (
            not isinstance(raw_root, str)
            or not raw_root.strip()
            or not isinstance(expected_remote, str)
            or not expected_remote.strip()
        ):
            findings.append(
                _finding(
                    repos_path,
                    "identity-mismatch",
                    owner=name,
                    detail=f"missing {plat} path or remote",
                )
            )
            continue
        root = Path(raw_root).expanduser()
        try:
            canonical = root.resolve(strict=True)
        except FileNotFoundError as exc:
            findings.append(
                _finding(
                    repos_path,
                    "identity-mismatch",
                    target=str(root),
                    owner=name,
                    detail=str(exc),
                )
            )
            continue
        except OSError as exc:
            findings.append(
                _finding(
                    repos_path,
                    "registry-indeterminate",
                    status="indeterminate",
                    target=str(root),
                    owner=name,
                    detail=str(exc),
                )
            )
            registry_indeterminate = True
            continue
        try:
            top = Path(_git(canonical, "rev-parse", "--show-toplevel")).resolve(
                strict=True
            )
            actual_remote = _git(canonical, "remote", "get-url", "origin")
        except subprocess.CalledProcessError as exc:
            findings.append(
                _finding(
                    repos_path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=name,
                    detail=str(exc),
                )
            )
            continue
        except (OSError, subprocess.TimeoutExpired) as exc:
            findings.append(
                _finding(
                    repos_path,
                    "registry-indeterminate",
                    status="indeterminate",
                    target=str(canonical),
                    owner=name,
                    detail=str(exc),
                )
            )
            registry_indeterminate = True
            continue
        actual_identity = normalize_remote(actual_remote)
        expected_identity = normalize_remote(expected_remote)
        if actual_identity is None or expected_identity is None:
            findings.append(
                _finding(
                    repos_path,
                    "registry-indeterminate",
                    status="indeterminate",
                    target=str(canonical),
                    owner=name,
                    detail="origin remote could not be normalized safely",
                )
            )
            registry_indeterminate = True
            continue
        if not _same_file(top, canonical) or actual_identity != expected_identity:
            findings.append(
                _finding(
                    repos_path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=name,
                    detail="Git top-level or origin remote differs from registry",
                )
            )
            continue
        roots.append((name, canonical))

    authority = (
        ScanAuthority.INDETERMINATE
        if registry_indeterminate
        else ScanAuthority.COMPLETE
    )
    return roots, findings, authority


def _regular_directory(path: Path) -> tuple[Path | None, str | None]:
    try:
        info = path.lstat()
        canonical = path.resolve(strict=True)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, str(exc)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        return None, "path must be a non-reparse directory"
    return canonical, None


def _manifest_name(root: Path) -> tuple[str | None, str | None, bool]:
    for rel in PLUGIN_MANIFEST_RELS:
        path = root.joinpath(*rel)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return None, str(exc), True
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
        ):
            return None, "plugin manifest must be a regular non-reparse file", False
        authority, data, findings = _load_json_object(path)
        if authority is ScanAuthority.INDETERMINATE:
            indeterminate = findings[0].reason == "entry-indeterminate"
            return None, findings[0].detail or findings[0].reason, indeterminate
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip(), None, False
        return None, "plugin manifest requires a non-empty name", False
    return None, "plugin manifest is missing", False


def _valid_source_part(value: str) -> bool:
    return bool(
        value
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "@" not in value
    )


def _installed_root(copilot_home: Path, source: str) -> _Candidate:
    name, marketplace = split_source(source)
    if not _valid_source_part(name) or not _valid_source_part(marketplace):
        return _Candidate(
            findings=[
                _finding(copilot_home, "invalid-entry", target=source, owner=source)
            ]
        )
    path = copilot_home / "installed-plugins" / marketplace / name
    canonical, error = _regular_directory(path)
    if canonical is None:
        if error is None:
            return _Candidate()
        indeterminate = not error.startswith("path must")
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "entry-indeterminate" if indeterminate else "identity-mismatch",
                    status="indeterminate" if indeterminate else "inactive",
                    owner=source,
                    detail=error,
                )
            ],
            indeterminate=indeterminate,
        )
    try:
        installed_root = (copilot_home / "installed-plugins").resolve(strict=True)
        relative = canonical.relative_to(installed_root)
    except OSError as exc:
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "entry-indeterminate",
                    status="indeterminate",
                    target=str(canonical),
                    owner=source,
                    detail=str(exc),
                )
            ],
            indeterminate=True,
        )
    except ValueError as exc:
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail=str(exc),
                )
            ]
        )
    if relative.parts != (marketplace, name):
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail="installed payload path does not match marketplace/plugin",
                )
            ]
        )
    manifest_name, manifest_error, manifest_indeterminate = _manifest_name(canonical)
    if manifest_error is not None:
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "entry-indeterminate"
                    if manifest_indeterminate
                    else "identity-mismatch",
                    status="indeterminate" if manifest_indeterminate else "inactive",
                    target=str(canonical),
                    owner=source,
                    detail=manifest_error,
                )
            ],
            indeterminate=manifest_indeterminate,
        )
    if manifest_name != name:
        return _Candidate(
            findings=[
                _finding(
                    path,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail="installed plugin manifest name differs from source",
                )
            ]
        )
    return _Candidate(root=canonical)


def _marketplace_manifest(
    root: Path,
) -> tuple[Path | None, dict | None, str | None, bool]:
    for rel in MARKETPLACE_MANIFEST_RELS:
        path = root.joinpath(*rel)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return path, None, str(exc), True
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
        ):
            return (
                path,
                None,
                "marketplace manifest must be a regular non-reparse file",
                False,
            )
        authority, data, findings = _load_json_object(path)
        if authority is ScanAuthority.INDETERMINATE:
            indeterminate = findings[0].reason == "entry-indeterminate"
            return (
                path,
                None,
                findings[0].detail or findings[0].reason,
                indeterminate,
            )
        return path, data, None, False
    return None, None, "marketplace manifest is missing", False


def _local_root(
    source: str,
    loaded_settings: _SettingsLoad,
    *,
    base: Path,
) -> _Candidate:
    name, marketplace = split_source(source)
    definition = loaded_settings.settings.marketplaces.get(marketplace)
    if definition is None:
        return _Candidate()
    origin = loaded_settings.marketplace_origins.get(marketplace, base)
    raw_source = definition.get("source") if isinstance(definition, dict) else None
    if not isinstance(raw_source, dict):
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail="marketplace source must be an object",
                )
            ],
            indeterminate=True,
        )
    kind = raw_source.get("source")
    if not isinstance(kind, str):
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail="marketplace source kind must be a string",
                )
            ],
            indeterminate=True,
        )
    source_kind = marketplace_source_kind(
        marketplace,
        loaded_settings.settings,
    )
    if source_kind is MarketplaceSourceKind.INVALID:
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail=f"unsupported marketplace source kind: {kind!r}",
                )
            ],
            indeterminate=True,
        )
    if source_kind is MarketplaceSourceKind.REMOTE:
        return _Candidate()
    raw_path = raw_source.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail="local marketplace requires a non-empty string path",
                )
            ],
            indeterminate=True,
        )

    marketplace_path = local_marketplace_path(
        marketplace,
        loaded_settings.settings,
        repo_dir=base,
    )
    if marketplace_path is None:
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail="local marketplace requires a non-empty path",
                )
            ],
            indeterminate=True,
        )
    try:
        canonical_marketplace = marketplace_path.resolve(strict=True)
    except FileNotFoundError as exc:
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "missing-target",
                    target=str(marketplace_path),
                    owner=source,
                    detail=str(exc),
                )
            ]
        )
    except OSError as exc:
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "entry-indeterminate",
                    status="indeterminate",
                    target=str(marketplace_path),
                    owner=source,
                    detail=str(exc),
                )
            ],
            indeterminate=True,
        )

    manifest_path, manifest, manifest_error, manifest_indeterminate = _marketplace_manifest(
        canonical_marketplace
    )
    if manifest_error is not None or manifest is None:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "entry-indeterminate"
                    if manifest_indeterminate
                    else "identity-mismatch",
                    status="indeterminate" if manifest_indeterminate else "inactive",
                    target=str(canonical_marketplace),
                    owner=source,
                    detail=manifest_error,
                )
            ],
            indeterminate=manifest_indeterminate,
        )
    if manifest.get("name") != marketplace:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "identity-mismatch",
                    target=str(canonical_marketplace),
                    owner=source,
                    detail="marketplace manifest name differs from source",
                )
            ]
        )
    loaded_marketplace = load_marketplace(canonical_marketplace)
    if loaded_marketplace is None or loaded_marketplace.name != marketplace:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "identity-mismatch",
                    target=source,
                    owner=source,
                    detail="marketplace could not be loaded with the expected identity",
                )
            ]
        )
    if name in loaded_marketplace.duplicates:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "root-ambiguous",
                    target=source,
                    owner=source,
                    detail="marketplace declares the plugin more than once",
                )
            ]
        )
    entry = loaded_marketplace.plugins.get(name)
    if entry is None or not isinstance(entry.source, str):
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "missing-target",
                    target=source,
                    owner=source,
                    detail="marketplace has no relative source for this plugin",
                )
            ]
        )
    plugin_source = Path(entry.source.strip())
    plugin_root = Path(loaded_marketplace.plugin_root)
    if (
        not entry.source.strip()
        or plugin_source.is_absolute()
        or plugin_root.is_absolute()
    ):
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "identity-mismatch",
                    target=entry.source,
                    owner=source,
                    detail="local plugin source and pluginRoot must be relative",
                )
            ]
        )
    candidate = canonical_marketplace / plugin_root / plugin_source
    try:
        canonical = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "missing-target",
                    target=str(candidate),
                    owner=source,
                    detail=str(exc),
                )
            ]
        )
    except OSError as exc:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "entry-indeterminate",
                    status="indeterminate",
                    target=str(candidate),
                    owner=source,
                    detail=str(exc),
                )
            ],
            indeterminate=True,
        )
    try:
        canonical.relative_to(canonical_marketplace)
    except ValueError:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail="plugin source escapes its marketplace root",
                )
            ]
        )
    manifest_name, plugin_error, plugin_indeterminate = _manifest_name(canonical)
    if plugin_error is not None or manifest_name != name:
        return _Candidate(
            findings=[
                _finding(
                    canonical,
                    "entry-indeterminate"
                    if plugin_indeterminate
                    else "identity-mismatch",
                    status="indeterminate" if plugin_indeterminate else "inactive",
                    target=str(canonical),
                    owner=source,
                    detail=plugin_error
                    or "plugin manifest name differs from marketplace entry",
                )
            ],
            indeterminate=plugin_indeterminate,
        )
    if plugin_dir(loaded_marketplace, name) != canonical:
        return _Candidate(
            findings=[
                _finding(
                    canonical,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail="shared marketplace resolver rejected the plugin root",
                )
            ]
        )
    return _Candidate(root=canonical)


def _decision_findings(
    source: str,
    source_findings: Mapping[str, list[Finding]],
) -> tuple[Finding, ...]:
    return tuple(source_findings.get(source, ()))


def resolve_active_plugins(
    *,
    home: str | Path | None = None,
) -> ActivationReport:
    """Resolve global or registered-project sources with tri-state authority."""
    user_home = Path(home).expanduser() if home is not None else Path.home()
    copilot_home = user_home / ".copilot"
    agent_worktrees_home = user_home / ".agent-worktrees"
    registry_findings: list[Finding] = []
    scopes: dict[str, set[str]] = defaultdict(set)
    local_roots: dict[str, set[Path]] = defaultdict(set)
    source_findings: dict[str, list[Finding]] = defaultdict(list)
    source_indeterminate: set[str] = set()

    global_settings = _user_settings(copilot_home)
    registry_findings.extend(global_settings.findings)
    for source in global_settings.settings.enabled_sources():
        scopes[source].add("global")
        candidate = _local_root(source, global_settings, base=copilot_home)
        source_findings[source].extend(candidate.findings)
        if candidate.root is not None:
            local_roots[source].add(candidate.root)
        if candidate.indeterminate:
            source_indeterminate.add(source)

    project_roots, project_findings, projects_authority = _verified_project_roots(
        agent_worktrees_home
    )
    registry_findings.extend(project_findings)
    settings_authorities = [global_settings.authority, projects_authority]
    for project, root in project_roots:
        project_settings = _read_settings(root, SETTINGS_RELS)
        settings_authorities.append(project_settings.authority)
        registry_findings.extend(project_settings.findings)
        for source in project_settings.settings.enabled_sources():
            scopes[source].add(f"project:{project}")
            candidate = _local_root(source, project_settings, base=root)
            source_findings[source].extend(candidate.findings)
            if candidate.root is not None:
                local_roots[source].add(candidate.root)
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

        installed = _installed_root(copilot_home, source)
        source_findings[source].extend(installed.findings)
        if installed.indeterminate:
            source_indeterminate.add(source)
        if source in source_indeterminate:
            decisions[source] = EntryDecision.indeterminate(
                *_decision_findings(source, source_findings)
            )
            continue

        root = installed.root or (next(iter(roots)) if roots else None)
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

        active = ActivePlugin(
            source=source,
            name=name,
            marketplace=marketplace,
            root=root,
            scopes=tuple(sorted(scopes[source])),
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
