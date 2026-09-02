#!/usr/bin/env python3
"""Reusable, exhaustive session-start context conformance scanning."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "copilot-extensions.session-context-contributors"
IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
MAX_TIMEOUT_SECONDS = 10
MAX_BYTES = 64 * 1024
DEFAULT_ENGINE_SCHEMA = "copilot-extensions.context-injection-engine"
DEFAULT_ENGINE_VERSION = 5
DEFAULT_RENDEZVOUS_TIMEOUT_SECONDS = 25
WRAPPERS = {
    "bash": "invoke-context-contributor.sh",
    "powershell": "invoke-context-contributor.ps1",
}
CATALOG_CONTRACT_PREFIX = "# payload-command-catalog-contract: "
CATALOG_DIGEST_PREFIX = "# payload-command-catalog-sha256: "


@dataclass(frozen=True)
class PluginTarget:
    """One attributable plugin payload to inspect."""

    source: str
    root: Path


@dataclass(frozen=True)
class Violation:
    """One stable, machine-readable conformance finding."""

    code: str
    message: str
    source: str = ""
    path: str = ""

    def as_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": self.message}
        if self.source:
            result["source"] = self.source
        if self.path:
            result["path"] = self.path
        return result


@dataclass
class ScanReport:
    """The complete result of one roster scan."""

    scope: str
    plugins: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "copilot-extensions.session-context-conformance",
            "version": 1,
            "scope": self.scope,
            "ok": self.ok,
            "pluginCount": len(self.plugins),
            "plugins": self.plugins,
            "violationCount": len(self.violations),
            "violations": [item.as_dict() for item in self.violations],
        }


def _load_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "file is missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "root value is not an object"
    return value, None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _add(
    report: ScanReport,
    code: str,
    message: str,
    *,
    source: str = "",
    path: Path | str | None = None,
) -> None:
    report.violations.append(
        Violation(
            code=code,
            message=message,
            source=source,
            path=str(path) if path else "",
        )
    )


def marketplace_targets(root: Path) -> tuple[list[PluginTarget], ScanReport]:
    """Discover every plugin declared by one directory marketplace."""

    root = root.expanduser().resolve()
    report = ScanReport(scope=f"marketplace:{root}")
    manifest_path = root / ".github" / "plugin" / "marketplace.json"
    if not manifest_path.is_file():
        manifest_path = root / ".claude-plugin" / "marketplace.json"
    manifest, error = _load_object(manifest_path)
    if manifest is None:
        _add(
            report,
            "marketplace-manifest-invalid",
            f"marketplace manifest is unavailable or malformed: {error}",
            path=manifest_path,
        )
        return [], report
    marketplace = manifest.get("name")
    entries = manifest.get("plugins")
    if not isinstance(marketplace, str) or not IDENTIFIER.fullmatch(marketplace):
        _add(
            report,
            "marketplace-identity-invalid",
            "marketplace name is missing or invalid",
            path=manifest_path,
        )
        marketplace = "unknown"
    if not isinstance(entries, list):
        _add(
            report,
            "marketplace-roster-invalid",
            "marketplace plugins must be a list",
            path=manifest_path,
        )
        return [], report

    manifest_root = (
        manifest_path.parents[2]
        if manifest_path.parts[-3:-1] == (".github", "plugin")
        else manifest_path.parent.parent
    )
    plugin_root = manifest_root
    metadata = manifest.get("metadata")
    if isinstance(metadata, dict):
        configured = metadata.get("pluginRoot")
        if isinstance(configured, str) and configured.strip():
            plugin_root = manifest_root / configured
    try:
        plugin_root = plugin_root.resolve()
        plugin_root.relative_to(root)
    except (OSError, ValueError):
        _add(
            report,
            "marketplace-plugin-root-escape",
            "marketplace pluginRoot escapes the marketplace root",
            path=manifest_path,
        )
        return [], report

    targets: list[PluginTarget] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _add(
                report,
                "marketplace-entry-invalid",
                f"plugin entry {index} is not an object",
                path=manifest_path,
            )
            continue
        name = entry.get("name")
        relative = entry.get("source")
        source = (
            f"{name}@{marketplace}"
            if isinstance(name, str)
            else f"entry-{index}@{marketplace}"
        )
        if (
            not isinstance(name, str)
            or not IDENTIFIER.fullmatch(name)
            or not isinstance(relative, str)
            or not relative.strip()
        ):
            _add(
                report,
                "marketplace-entry-invalid",
                f"plugin entry {index} has no valid name and relative source",
                source=source,
                path=manifest_path,
            )
            continue
        if source in seen:
            _add(
                report,
                "plugin-identity-duplicate",
                "plugin identity appears more than once in the marketplace",
                source=source,
                path=manifest_path,
            )
            continue
        seen.add(source)
        try:
            candidate = (plugin_root / relative).resolve()
            candidate.relative_to(plugin_root.resolve())
        except (OSError, ValueError):
            _add(
                report,
                "plugin-payload-escape",
                "marketplace plugin source escapes the plugin root",
                source=source,
                path=relative,
            )
            continue
        targets.append(PluginTarget(source, candidate))
    return targets, report


def _manifest(root: Path) -> tuple[dict[str, Any] | None, Path, str | None]:
    for relative in (Path("plugin.json"), Path(".claude-plugin/plugin.json")):
        path = root / relative
        if path.is_file():
            value, error = _load_object(path)
            return value, path, error
    path = root / "plugin.json"
    return None, path, "file is missing"


def _hook_paths(
    root: Path,
    manifest: dict[str, Any],
    report: ScanReport,
    source: str,
) -> list[Path]:
    configured = manifest.get("hooks")
    if configured is None:
        candidates = [root / "hooks.json", root / "hooks" / "hooks.json"]
        return [path for path in candidates if path.is_file()]
    values = [configured] if isinstance(configured, str) else configured
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        _add(
            report,
            "hook-declaration-invalid",
            "plugin hooks must be a path or list of paths",
            source=source,
        )
        return []
    paths: list[Path] = []
    for value in values:
        try:
            path = (root / value).resolve()
            path.relative_to(root)
        except (OSError, ValueError):
            _add(
                report,
                "hook-payload-escape",
                "declared hook path escapes the plugin payload",
                source=source,
                path=value,
            )
            continue
        if not path.is_file():
            _add(
                report,
                "hook-payload-missing",
                "declared hook payload is missing",
                source=source,
                path=value,
            )
            continue
        paths.append(path)
    return paths


def _session_hooks(
    root: Path,
    paths: Iterable[Path],
    report: ScanReport,
    source: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in paths:
        hooks, error = _load_object(path)
        if hooks is None:
            _add(
                report,
                "hook-payload-invalid",
                f"hook payload is malformed: {error}",
                source=source,
                path=_relative(path, root),
            )
            continue
        events = hooks.get("hooks")
        if not isinstance(events, dict):
            _add(
                report,
                "hook-payload-invalid",
                "hook payload has no hooks object",
                source=source,
                path=_relative(path, root),
            )
            continue
        raw = events.get("sessionStart", events.get("SessionStart", []))
        if not isinstance(raw, list):
            _add(
                report,
                "session-start-hooks-invalid",
                "sessionStart hooks must be a list",
                source=source,
                path=_relative(path, root),
            )
            continue
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                _add(
                    report,
                    "session-start-hook-invalid",
                    f"sessionStart hook {index} is not an object",
                    source=source,
                    path=_relative(path, root),
                )
                continue
            entries.append(entry)
    return entries


def canonical_bash_hook(source: str, contributor: dict[str, Any]) -> str:
    """Return the exact generated Bash producer hook command."""

    command = contributor["bash"]
    arguments = " ".join(shlex.quote(str(part)) for part in command)
    return (
        'r="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-'
        '${CLAUDE_PLUGIN_ROOT:-}}}"; '
        'w="$r/scripts/invoke-context-contributor.sh"; '
        f'if [ -n "$r" ] && [ -f "$w" ]; then bash "$w" {shlex.quote(source)} '
        f'{shlex.quote(contributor["id"])} {arguments}; '
        "else printf '{}'; fi"
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def canonical_powershell_hook(
    source: str,
    contributor: dict[str, Any],
) -> str:
    """Return the exact generated PowerShell producer hook command."""

    arguments = " ".join(
        _ps_quote(str(part)) for part in contributor["powershell"]
    )
    return (
        "$r = $env:COPILOT_PLUGIN_ROOT; if (-not $r) { $r = $env:PLUGIN_ROOT }; "
        "if (-not $r) { $r = $env:CLAUDE_PLUGIN_ROOT }; "
        "if ($r) { try { [IO.Directory]::SetCurrentDirectory("
        "$env:USERPROFILE) } catch {}; "
        "$w = Join-Path (Join-Path $r 'scripts') "
        "'invoke-context-contributor.ps1'; "
        f"if (Test-Path -LiteralPath $w -PathType Leaf) {{ & $w "
        f"{_ps_quote(source)} {_ps_quote(contributor['id'])} {arguments} }} "
        "else { [Console]::Out.Write('{}') } } "
        "else { [Console]::Out.Write('{}') }"
    )


def canonical_hook(
    platform: str,
    source: str,
    contributor: dict[str, Any],
) -> str:
    """Return the exact generated producer hook for one platform."""

    if platform == "bash":
        return canonical_bash_hook(source, contributor)
    if platform == "powershell":
        return canonical_powershell_hook(source, contributor)
    raise ValueError(f"unsupported hook platform: {platform}")


def canonical_authority_hook(platform: str) -> str:
    """Return the exact aggregate-authority sessionStart hook command."""

    if platform == "bash":
        return (
            'root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-'
            '${CLAUDE_PLUGIN_ROOT:-}}}"; '
            'if [ -n "$root" ] && '
            '[ -f "$root/scripts/emit-context.sh" ]; then '
            'bash "$root/scripts/emit-context.sh"; '
            "else printf '{}'; fi"
        )
    if platform == "powershell":
        return (
            "$root = $env:COPILOT_PLUGIN_ROOT; "
            "if (-not $root) { $root = $env:PLUGIN_ROOT }; "
            "if (-not $root) { $root = $env:CLAUDE_PLUGIN_ROOT }; "
            "if ($root) { $script = Join-Path (Join-Path $root 'scripts') "
            "'emit-context.ps1'; "
            "if (Test-Path -LiteralPath $script -PathType Leaf) "
            "{ & $script } else { [Console]::Out.Write('{}') } } "
            "else { [Console]::Out.Write('{}') }"
        )
    raise ValueError(f"unsupported hook platform: {platform}")


def _validate_aggregate_authority(
    root: Path,
    manifest: dict[str, Any],
    hooks: list[dict[str, Any]],
    report: ScanReport,
    source: str,
    *,
    engine_schema: str,
    engine_version: int,
    rendezvous_timeout_seconds: int,
) -> None:
    engine_relative = manifest.get("sessionContextEngine")
    if not isinstance(engine_relative, str) or not engine_relative.strip():
        _add(
            report,
            "aggregate-authority-engine-missing",
            "aggregate authority manifest has no sessionContextEngine path",
            source=source,
        )
    else:
        try:
            engine_contract_path = (root / engine_relative).resolve(strict=True)
            engine_contract_path.relative_to(root)
        except OSError:
            _add(
                report,
                "aggregate-authority-engine-missing",
                "aggregate authority engine contract is unavailable",
                source=source,
                path=engine_relative,
            )
        except ValueError:
            _add(
                report,
                "aggregate-authority-engine-escape",
                "aggregate authority engine contract escapes the plugin root",
                source=source,
                path=engine_relative,
            )
        else:
            engine_contract, error = _load_object(engine_contract_path)
            if engine_contract != {
                "schema": engine_schema,
                "version": engine_version,
            }:
                _add(
                    report,
                    "aggregate-authority-engine-incompatible",
                    (
                        "aggregate authority engine contract is incompatible"
                        + (f": {error}" if error else "")
                    ),
                    source=source,
                    path=engine_relative,
                )

    aggregate_script = root / "scripts" / "aggregate_context.py"
    if not aggregate_script.is_file():
        _add(
            report,
            "aggregate-authority-engine-script-missing",
            "aggregate authority engine script is unavailable",
            source=source,
            path="scripts/aggregate_context.py",
        )

    wrapper_requirements = {
        "scripts/emit-context.sh": (
            'root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-'
            '${CLAUDE_PLUGIN_ROOT:-}}}"',
            '"$root/scripts/aggregate_context.py"',
        ),
        "scripts/emit-context.ps1": (
            "$root = $env:COPILOT_PLUGIN_ROOT",
            "$root = $env:PLUGIN_ROOT",
            "$root = $env:CLAUDE_PLUGIN_ROOT",
            "'aggregate_context.py'",
        ),
    }
    for relative, required in wrapper_requirements.items():
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            content = ""
        if (
            not content
            or any(token not in content for token in required)
            or "$PWD" in content
            or "Get-Location" in content
        ):
            _add(
                report,
                "aggregate-authority-engine-wrapper-invalid",
                "aggregate authority wrapper is missing or not plugin-root-only",
                source=source,
                path=relative,
            )

    if len(hooks) != 1:
        _add(
            report,
            "aggregate-authority-hook-count",
            "aggregate authority must declare exactly one sessionStart hook",
            source=source,
        )
        return
    hook = hooks[0]
    if (
        hook.get("type") != "command"
        or hook.get("bash") != canonical_authority_hook("bash")
        or hook.get("powershell") != canonical_authority_hook("powershell")
    ):
        _add(
            report,
            "aggregate-authority-hook-drift",
            "aggregate authority sessionStart hook is not the canonical engine hook",
            source=source,
        )
    timeout = hook.get("timeoutSec")
    if (
        not isinstance(timeout, int)
        or timeout < rendezvous_timeout_seconds
    ):
        _add(
            report,
            "aggregate-authority-hook-timeout",
            "aggregate authority hook timeout is shorter than the rendezvous requirement",
            source=source,
        )


def _wrapper_matches(
    entry: dict[str, Any],
    platform: str,
    source: str,
    contributor_id: str,
    command: list[str],
) -> bool:
    if entry.get("type") != "command":
        return False
    if not isinstance(entry.get(platform), str):
        return False
    return entry[platform] == canonical_hook(
        platform,
        source,
        {
            "id": contributor_id,
            platform: command,
        },
    )


def _runtime_catalog_contract(
    path: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    value, error = _load_object(path)
    if value is None:
        return None, error
    version = value.get("version")
    if (
        value.get("schema") != "copilot-extensions.payload-invocation"
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version not in {1, 2}
    ):
        return None, "payload invocation schema or version is incompatible"
    if version == 1:
        if (
            not isinstance(value.get("runtimeRoot"), str)
            or "legacyRuntimeRoot" in value
            or "installationContext" in value
        ):
            return None, "payload invocation version 1 runtime contract is invalid"
    elif (
        not isinstance(value.get("legacyRuntimeRoot"), str)
        or "runtimeRoot" in value
        or value.get("installationContext") not in {"legacy", "required"}
    ):
        return None, "payload invocation version 2 runtime contract is invalid"
    raw = value.get("commands")
    if raw is None and isinstance(value.get("command"), str):
        raw = [value]
    if not isinstance(raw, list) or not raw:
        return None, "payload invocation declares no commands"
    output_dir = value.get("outputDir", "bin")
    if (
        not isinstance(output_dir, str)
        or not output_dir
        or Path(output_dir).is_absolute()
        or ".." in Path(output_dir).parts
    ):
        return None, "payload invocation outputDir is invalid"
    commands: list[dict[str, str]] = []
    for entry in raw:
        command = entry.get("command") if isinstance(entry, dict) else None
        purpose = entry.get("purpose") if isinstance(entry, dict) else None
        if (
            not isinstance(command, str)
            or not IDENTIFIER.fullmatch(command)
            or not isinstance(purpose, str)
            or not purpose.strip()
        ):
            return None, "payload invocation contains an invalid command"
        commands.append(
            {
                "id": command,
                "relativePath": f"{output_dir}/{command}",
                "purpose": purpose,
            }
        )
    plugin = value.get("plugin", commands[0]["id"])
    if not isinstance(plugin, str) or not IDENTIFIER.fullmatch(plugin):
        return None, "payload invocation plugin identity is invalid"
    windows_catalog_shim = value.get("windowsCatalogShim", "powershell")
    if windows_catalog_shim not in {"powershell", "cmd"}:
        return None, "payload invocation Windows catalog shim is invalid"
    return {
        "plugin": plugin,
        "commands": commands,
        "windowsCatalogShim": windows_catalog_shim,
    }, None


def _emitted_catalog_contract(
    path: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, str(exc)
    if "# Generated by libs/payload-invocation/generate.py. Do not edit." not in (
        content.splitlines()[:3]
    ):
        return None, "catalog emitter is not generator-owned"
    declarations = [
        line.removeprefix(CATALOG_CONTRACT_PREFIX)
        for line in content.splitlines()
        if line.startswith(CATALOG_CONTRACT_PREFIX)
    ]
    if len(declarations) != 1:
        return None, "catalog emitter has no unique generated contract"
    digest_lines = [
        line
        for line in content.splitlines()
        if line.startswith(CATALOG_DIGEST_PREFIX)
    ]
    if len(digest_lines) != 1:
        return None, "catalog emitter has no unique generated digest"
    recorded_digest = digest_lines[0].removeprefix(CATALOG_DIGEST_PREFIX)
    if not re.fullmatch(r"[0-9a-f]{64}", recorded_digest):
        return None, "catalog emitter generated digest is malformed"
    unsigned = content.replace(digest_lines[0], CATALOG_DIGEST_PREFIX, 1)
    if hashlib.sha256(unsigned.encode("utf-8")).hexdigest() != recorded_digest:
        return None, "catalog emitter differs from its generated contract"
    try:
        contract = json.loads(declarations[0])
    except json.JSONDecodeError as exc:
        return None, f"generated catalog contract is malformed: {exc}"
    if not isinstance(contract, dict):
        return None, "generated catalog contract is not an object"
    return contract, None


def scan_plugins(
    targets: Iterable[PluginTarget],
    *,
    scope: str = "plugin-roots",
    authority_source: str | None = None,
    wrapper_root: Path | None = None,
    authority_engine_schema: str = DEFAULT_ENGINE_SCHEMA,
    authority_engine_version: int = DEFAULT_ENGINE_VERSION,
    authority_timeout_seconds: int = DEFAULT_RENDEZVOUS_TIMEOUT_SECONDS,
    initial_violations: Iterable[Violation] = (),
) -> ScanReport:
    """Scan all supplied payloads and return every detected violation."""

    report = ScanReport(scope=scope, violations=list(initial_violations))
    target_list = sorted(targets, key=lambda item: item.source)
    report.plugins = [item.source for item in target_list]
    for target in target_list:
        source = target.source
        root = target.root.expanduser().resolve()
        name = source.partition("@")[0]
        if not root.is_dir():
            _add(
                report,
                "plugin-payload-missing",
                "plugin payload directory is unavailable",
                source=source,
                path=root,
            )
            continue
        manifest, manifest_path, error = _manifest(root)
        if manifest is None:
            _add(
                report,
                "plugin-manifest-invalid",
                f"plugin manifest is unavailable or malformed: {error}",
                source=source,
                path=_relative(manifest_path, root),
            )
            continue
        if manifest.get("name") != name:
            _add(
                report,
                "plugin-identity-drift",
                "plugin manifest name does not match its attributable source",
                source=source,
                path=_relative(manifest_path, root),
            )

        hook_paths = _hook_paths(root, manifest, report, source)
        hooks = _session_hooks(root, hook_paths, report, source)
        declaration_name = manifest.get("sessionContext")
        declaration: dict[str, Any] | None = None
        declaration_path: Path | None = None
        if hooks and (
            not isinstance(declaration_name, str)
            or not declaration_name.strip()
        ):
            _add(
                report,
                "context-declaration-missing",
                "sessionStart plugin has no complete context declaration",
                source=source,
            )
        elif isinstance(declaration_name, str) and declaration_name.strip():
            try:
                declaration_path = (root / declaration_name).resolve(strict=True)
                declaration_path.relative_to(root)
            except OSError:
                _add(
                    report,
                    "context-declaration-missing",
                    "declared sessionContext payload is unavailable",
                    source=source,
                    path=declaration_name,
                )
            except ValueError:
                _add(
                    report,
                    "context-declaration-escape",
                    "declared sessionContext payload escapes the plugin root",
                    source=source,
                    path=declaration_name,
                )
            else:
                declaration, error = _load_object(declaration_path)
                if declaration is None:
                    _add(
                        report,
                        "context-declaration-invalid",
                        f"sessionContext payload is malformed: {error}",
                        source=source,
                        path=declaration_name,
                    )

        contributors: list[dict[str, Any]] = []
        behavior: dict[str, Any] | None = None
        if declaration is not None:
            if (
                declaration.get("schema") != SCHEMA
                or declaration.get("version") != 1
                or declaration.get("complete") is not True
            ):
                _add(
                    report,
                    "context-declaration-incomplete",
                    "context declaration is incomplete or incompatible",
                    source=source,
                    path=_relative(declaration_path or root, root),
                )
            raw_behavior = declaration.get("sessionStart")
            if (
                not isinstance(raw_behavior, dict)
                or set(raw_behavior) != {"sideEffects", "context"}
                or raw_behavior.get("sideEffects")
                not in {"none", "restart-safe-idempotent"}
                or raw_behavior.get("context")
                not in {"none", "authority-aware", "aggregate-authority"}
            ):
                _add(
                    report,
                    "session-start-behavior-incomplete",
                    "sessionStart behavior must completely declare sideEffects and context",
                    source=source,
                    path=_relative(declaration_path or root, root),
                )
            else:
                behavior = raw_behavior
            raw_contributors = declaration.get("contributors")
            if not isinstance(raw_contributors, list):
                _add(
                    report,
                    "contributors-invalid",
                    "contributors must be a list",
                    source=source,
                    path=_relative(declaration_path or root, root),
                )
            else:
                contributors = [
                    item for item in raw_contributors if isinstance(item, dict)
                ]
                if len(contributors) != len(raw_contributors):
                    _add(
                        report,
                        "contributor-invalid",
                        "every contributor must be an object",
                        source=source,
                        path=_relative(declaration_path or root, root),
                    )

        if behavior is not None:
            side_effects = behavior["sideEffects"]
            context = behavior["context"]
            if source == authority_source:
                valid = (
                    bool(hooks)
                    and side_effects == "none"
                    and context == "aggregate-authority"
                    and not contributors
                )
            elif hooks and contributors:
                valid = context == "authority-aware"
            elif hooks:
                valid = (
                    side_effects == "restart-safe-idempotent"
                    and context == "none"
                    and not contributors
                )
            else:
                valid = not contributors
            if not valid:
                _add(
                    report,
                    "session-start-behavior-incompatible",
                    "declared sessionStart behavior is incompatible with its hooks and contributors",
                    source=source,
                    path=_relative(declaration_path or root, root),
                )

        if source == authority_source:
            _validate_aggregate_authority(
                root,
                manifest,
                hooks,
                report,
                source,
                engine_schema=authority_engine_schema,
                engine_version=authority_engine_version,
                rendezvous_timeout_seconds=authority_timeout_seconds,
            )

        seen: set[str] = set()
        valid_contributors: dict[str, dict[str, Any]] = {}
        for index, contributor in enumerate(contributors):
            contributor_id = contributor.get("id")
            location = (
                f"{_relative(declaration_path or root, root)}"
                f"#contributors[{index}]"
            )
            if (
                not isinstance(contributor_id, str)
                or not IDENTIFIER.fullmatch(contributor_id)
                or contributor_id in seen
            ):
                _add(
                    report,
                    "contributor-identity-invalid",
                    "contributor id is missing, invalid, or duplicated",
                    source=source,
                    path=location,
                )
                continue
            seen.add(contributor_id)
            valid_contributors[contributor_id] = contributor
            if contributor.get("pure") is not True:
                _add(
                    report,
                    "contributor-not-pure",
                    "context contributors must declare pure: true",
                    source=source,
                    path=location,
                )
            timeout = contributor.get("timeoutSeconds", 5)
            max_bytes = contributor.get("maxBytes", 8192)
            order = contributor.get("order", 500)
            if (
                not isinstance(order, int)
                or not isinstance(timeout, int)
                or not 1 <= timeout <= MAX_TIMEOUT_SECONDS
                or not isinstance(max_bytes, int)
                or not 1 <= max_bytes <= MAX_BYTES
            ):
                _add(
                    report,
                    "contributor-bounds-invalid",
                    "contributor order, timeoutSeconds, or maxBytes is invalid",
                    source=source,
                    path=location,
                )
            for platform, suffix in (("bash", ".sh"), ("powershell", ".ps1")):
                command = contributor.get(platform)
                if (
                    not isinstance(command, list)
                    or not command
                    or not all(isinstance(part, str) and part for part in command)
                ):
                    _add(
                        report,
                        "contributor-command-invalid",
                        f"{platform} contributor command must be a non-empty argv list",
                        source=source,
                        path=location,
                    )
                    continue
                try:
                    script = (root / command[0]).resolve(strict=True)
                    script.relative_to(root)
                except OSError:
                    _add(
                        report,
                        "contributor-command-missing",
                        f"{platform} contributor command payload is unavailable",
                        source=source,
                        path=command[0],
                    )
                except ValueError:
                    _add(
                        report,
                        "contributor-command-escape",
                        f"{platform} contributor command escapes the plugin root",
                        source=source,
                        path=command[0],
                    )
                else:
                    if script.suffix.lower() != suffix:
                        _add(
                            report,
                            "contributor-command-incompatible",
                            f"{platform} contributor command has the wrong script type",
                            source=source,
                            path=command[0],
                        )
                matching = [
                    entry
                    for entry in hooks
                    if _wrapper_matches(
                        entry,
                        platform,
                        source,
                        contributor_id,
                        command,
                    )
                ]
                if len(matching) != 1:
                    _add(
                        report,
                        "contributor-hook-drift",
                        f"{platform} hook does not uniquely preserve contributor identity and argv",
                        source=source,
                        path=location,
                    )
                elif matching[0].get("timeoutSec") != 30:
                    _add(
                        report,
                        "contributor-hook-timeout-drift",
                        "authority-aware producer hooks must use timeoutSec 30",
                        source=source,
                        path=location,
                    )

        if contributors and source != authority_source:
            for platform, filename in WRAPPERS.items():
                path = root / "scripts" / filename
                if not path.is_file():
                    _add(
                        report,
                        "producer-wrapper-missing",
                        f"{platform} authority-aware producer wrapper is missing",
                        source=source,
                        path=_relative(path, root),
                    )
                    continue
                if wrapper_root is not None:
                    expected = wrapper_root / "scripts" / filename
                    try:
                        matches = path.read_bytes() == expected.read_bytes()
                    except OSError:
                        matches = False
                    if not matches:
                        _add(
                            report,
                            "producer-wrapper-drift",
                            f"{platform} producer wrapper differs from the authority",
                            source=source,
                            path=_relative(path, root),
                        )
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    content = ""
                if (
                    "resolve_context_authority.py" not in content
                    or 'dirname "$root")/context-injection' in content
                    or "Split-Path -Parent $resolvedRoot" in content
                ):
                    _add(
                        report,
                        "producer-authority-topology-invalid",
                        f"{platform} producer wrapper cannot safely resolve a source-qualified authority",
                        source=source,
                        path=_relative(path, root),
                    )
            resolver = root / "scripts" / "resolve_context_authority.py"
            if not resolver.is_file():
                _add(
                    report,
                    "producer-authority-resolver-missing",
                    "producer has no source-qualified authority resolver",
                    source=source,
                    path=_relative(resolver, root),
                )
            elif wrapper_root is not None:
                expected_resolver = (
                    wrapper_root
                    / "scripts"
                    / "resolve_context_authority.py"
                )
                try:
                    matches = (
                        resolver.read_bytes()
                        == expected_resolver.read_bytes()
                    )
                except OSError:
                    matches = False
                if not matches:
                    _add(
                        report,
                        "producer-authority-resolver-drift",
                        "producer authority resolver differs from the adopted authority",
                        source=source,
                        path=_relative(resolver, root),
                    )

        for platform in WRAPPERS:
            for entry in hooks:
                rendered = str(entry.get(platform) or "")
                if WRAPPERS[platform] not in rendered:
                    continue
                if not any(
                    _wrapper_matches(
                        entry,
                        platform,
                        source,
                        contributor_id,
                        contributor.get(platform, []),
                    )
                    for contributor_id, contributor in valid_contributors.items()
                    if isinstance(contributor.get(platform), list)
                ):
                    _add(
                        report,
                        "contributor-hook-orphan",
                        f"{platform} wrapper hook does not match a declared contributor",
                        source=source,
                    )

        if name.startswith("agent-") and (root / "pyproject.toml").is_file():
            invocation = root / "payload-invocation.json"
            expected_contract, error = _runtime_catalog_contract(invocation)
            if error is not None:
                _add(
                    report,
                    "runtime-command-manifest-invalid",
                    f"runtime agent plugin has no usable payload command manifest: {error}",
                    source=source,
                    path=_relative(invocation, root),
                )
            elif expected_contract["plugin"] != name:
                _add(
                    report,
                    "runtime-command-catalog-identity-drift",
                    "payload command catalog plugin identity does not match the scanned plugin",
                    source=source,
                    path=_relative(invocation, root),
                )
            catalog = valid_contributors.get("command-catalog")
            if expected_contract is not None and catalog is None:
                _add(
                    report,
                    "runtime-command-catalog-missing",
                    "runtime agent plugin has operative commands but no command-catalog contributor",
                    source=source,
                )
            elif catalog is not None and (
                catalog.get("bash") != ["scripts/emit-command-catalog.sh"]
                or catalog.get("powershell")
                != ["scripts/emit-command-catalog.ps1"]
            ):
                _add(
                    report,
                    "runtime-command-catalog-command-drift",
                    "command-catalog contributor does not invoke the generated payload-local emitters",
                    source=source,
                )
            for suffix in ("sh", "ps1"):
                emitter = root / "scripts" / f"emit-command-catalog.{suffix}"
                if not emitter.is_file():
                    _add(
                        report,
                        "runtime-command-catalog-payload-missing",
                        "runtime agent plugin command catalog emitter is missing",
                        source=source,
                        path=_relative(emitter, root),
                    )
                    continue
                emitted_contract, contract_error = _emitted_catalog_contract(
                    emitter
                )
                if (
                    contract_error is not None
                    or emitted_contract != expected_contract
                ):
                    _add(
                        report,
                        "runtime-command-catalog-incomplete",
                        (
                            "generated command catalog does not cover the "
                            "payload invocation manifest"
                            + (
                                f": {contract_error}"
                                if contract_error is not None
                                else ""
                            )
                        ),
                        source=source,
                        path=_relative(emitter, root),
                    )
    return report


def render_text(report: ScanReport) -> str:
    """Render a concise human-readable result."""

    if report.ok:
        return (
            f"sessionStart conformance passed: "
            f"{len(report.plugins)} plugin(s) scanned"
        )
    lines = [
        "sessionStart conformance failed: "
        f"{len(report.violations)} violation(s) across "
        f"{len(report.plugins)} plugin(s)"
    ]
    for item in report.violations:
        owner = f"{item.source}: " if item.source else ""
        location = f" [{item.path}]" if item.path else ""
        lines.append(f"  {item.code}: {owner}{item.message}{location}")
    return "\n".join(lines)
