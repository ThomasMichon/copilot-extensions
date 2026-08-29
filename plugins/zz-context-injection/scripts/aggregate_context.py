#!/usr/bin/env python3
"""Aggregate declared session-start context from the active plugin stack."""

from __future__ import annotations

import heapq
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "copilot-extensions.session-context-contributors"
MAX_INPUT_BYTES = 64 * 1024
MAX_AGGREGATE_BYTES = 64 * 1024
AGGREGATE_HEADROOM_BYTES = 4 * 1024
MAX_CONTRIBUTORS = 128
MAX_TIMEOUT_SECONDS = 10
MAX_TOTAL_TIMEOUT_SECONDS = 20
MAX_WORKERS = 16
COMMAND_CATALOG_BUDGET_BYTES = 32 * 1024
DEFAULT_AUTHORITY_SOURCE = "zz-context-injection@copilot-extensions"
IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


@dataclass(frozen=True)
class ActivePlugin:
    source: str
    name: str
    marketplace: str
    root: Path


@dataclass(frozen=True)
class Contributor:
    source: str
    plugin_root: Path
    contributor_id: str
    order: int
    timeout_seconds: int
    max_bytes: int
    command: tuple[str, ...]


@dataclass(frozen=True)
class ContributorResult:
    ok: bool
    context: str | None = None


def _diagnose(message: str) -> None:
    print(f"[zz-context-injection] {message}", file=sys.stderr)


def _authority_source() -> str | None:
    source = os.environ.get(
        "COPILOT_CONTEXT_INJECTION_AUTHORITY",
        DEFAULT_AUTHORITY_SOURCE,
    )
    name, separator, marketplace = source.partition("@")
    if (
        not separator
        or not IDENTIFIER.fullmatch(name)
        or not IDENTIFIER.fullmatch(marketplace)
    ):
        return None
    return source


def _emit_empty(message: str | None = None) -> int:
    if message:
        _diagnose(message)
    print("{}", end="")
    return 0


def _load_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _repo_root(cwd: Path) -> Path:
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _jsonc(path: Path) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    body = "\n".join(
        line for line in lines if not line.lstrip().startswith("//")
    )
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _repo_is_trusted(repo: Path) -> bool:
    config = Path.home() / ".copilot" / "config.json"
    value = _jsonc(config)
    if value is None:
        return False
    folders = value.get("trustedFolders")
    if not isinstance(folders, list):
        return False
    resolved = repo.resolve()
    for raw in folders:
        if not isinstance(raw, str):
            continue
        try:
            trusted = Path(raw).expanduser().resolve(strict=True)
            if resolved == trusted:
                return True
        except OSError:
            continue
    return False


def _has_unlisted_staged_plugins() -> bool:
    """Detect explicit plugin staging that settings reconstruction cannot see."""
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            return True
        script = """
param([int]$ProcessId)
$current = $ProcessId
for ($i = 0; $i -lt 8 -and $current -gt 0; $i++) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$current" `
        -ErrorAction Stop
    if ($process.CommandLine) {
        [Console]::Out.WriteLine($process.CommandLine)
    }
    $current = [int]$process.ParentProcessId
}
"""
        try:
            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-Command",
                    script,
                    "-ProcessId",
                    str(os.getppid()),
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return "--plugin-dir" in result.stdout
    pid = os.getppid()
    for _ in range(8):
        if pid <= 1:
            break
        try:
            command = (
                Path(f"/proc/{pid}/cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
            )
            status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        except OSError:
            return True
        if "--plugin-dir" in command:
            return True
        parent = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
        if parent is None:
            return True
        pid = int(parent.group(1))
    return False


def _settings(repo: Path) -> tuple[dict[str, bool], dict[str, tuple[dict, Path]]] | None:
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, tuple[dict, Path]] = {}
    layers = [(Path.home() / ".copilot" / "settings.json", Path.home())]
    if _repo_is_trusted(repo):
        layers.extend(
            (
                (repo / ".claude" / "settings.json", repo),
                (repo / ".claude" / "settings.local.json", repo),
                (repo / ".github" / "copilot" / "settings.json", repo),
                (repo / ".github" / "copilot" / "settings.local.json", repo),
            )
        )
    for path, base in layers:
        if not path.exists():
            continue
        value = _load_json(path)
        if value is None:
            _diagnose(f"settings are unreadable or malformed: {path}")
            return None
        raw_enabled = value.get("enabledPlugins")
        if isinstance(raw_enabled, dict):
            for key, active in raw_enabled.items():
                if isinstance(key, str) and isinstance(active, bool):
                    enabled[key] = active
        raw_marketplaces = value.get("extraKnownMarketplaces")
        if isinstance(raw_marketplaces, dict):
            for key, declaration in raw_marketplaces.items():
                if isinstance(key, str) and isinstance(declaration, dict):
                    marketplaces[key] = (declaration, base)
    return enabled, marketplaces


def _marketplace_manifest(root: Path) -> tuple[dict, Path] | None:
    for relative in (
        Path(".github/plugin/marketplace.json"),
        Path(".claude-plugin/marketplace.json"),
    ):
        path = root / relative
        value = _load_json(path)
        if value is not None:
            return value, path.parent.parent.parent if relative.parts[0] == ".github" else path.parent.parent
    return None


def _directory_plugin(
    repo: Path,
    marketplace: str,
    name: str,
    declaration: dict,
    base: Path,
) -> Path | None:
    source = declaration.get("source")
    if not isinstance(source, dict):
        return None
    if str(source.get("source", "")).strip().lower() != "directory":
        return None
    raw_path = source.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    configured = Path(raw_path).expanduser()
    root = configured if configured.is_absolute() else base / configured
    try:
        root = root.resolve(strict=True)
    except OSError:
        return None
    loaded = _marketplace_manifest(root)
    if loaded is None:
        return None
    manifest, manifest_root = loaded
    if manifest.get("name") != marketplace:
        return None
    metadata = manifest.get("metadata")
    plugin_root = manifest_root
    if isinstance(metadata, dict):
        raw_plugin_root = metadata.get("pluginRoot")
        if isinstance(raw_plugin_root, str) and raw_plugin_root.strip():
            plugin_root = manifest_root / raw_plugin_root
    try:
        plugin_root = plugin_root.resolve(strict=True)
        plugin_root.relative_to(root)
    except (OSError, ValueError):
        return None
    entries = manifest.get("plugins")
    if not isinstance(entries, list):
        return None
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("name") == name]
    if len(matches) != 1 or not isinstance(matches[0].get("source"), str):
        return None
    try:
        candidate = (plugin_root / matches[0]["source"]).resolve(strict=True)
        candidate.relative_to(plugin_root)
    except (OSError, ValueError):
        return None
    plugin_manifest = _load_json(candidate / "plugin.json")
    if plugin_manifest is None:
        plugin_manifest = _load_json(candidate / ".claude-plugin" / "plugin.json")
    return candidate if plugin_manifest and plugin_manifest.get("name") == name else None


def _active_plugins(repo: Path) -> list[ActivePlugin] | None:
    loaded = _settings(repo)
    if loaded is None:
        return None
    enabled, marketplaces = loaded
    active: list[ActivePlugin] = []
    installed = (Path.home() / ".copilot" / "installed-plugins").resolve()
    for source in sorted(key for key, value in enabled.items() if value):
        name, separator, marketplace = source.partition("@")
        name = name.strip()
        marketplace = marketplace.strip()
        if (
            not separator
            or not IDENTIFIER.fullmatch(name)
            or not IDENTIFIER.fullmatch(marketplace)
        ):
            _diagnose(f"invalid enabled plugin identity: {source!r}")
            return None
        declaration, base = marketplaces.get(marketplace, ({}, repo))
        root = _directory_plugin(repo, marketplace, name, declaration, base)
        if root is None:
            root = installed / marketplace / name
            try:
                root = root.resolve(strict=True)
                root.relative_to(installed)
            except OSError:
                _diagnose(f"active plugin payload is unavailable: {source}")
                return None
            except ValueError:
                _diagnose(f"active plugin payload escapes installed root: {source}")
                return None
        manifest = _load_json(root / "plugin.json")
        if manifest is None:
            manifest = _load_json(root / ".claude-plugin" / "plugin.json")
        if manifest is None or manifest.get("name") != name:
            _diagnose(f"active plugin identity is invalid: {source}")
            return None
        active.append(ActivePlugin(source, name, marketplace, root))
    return active


def _session_start_hooks(plugin: ActivePlugin, manifest: dict) -> bool | None:
    configured = manifest.get("hooks")
    candidates: list[Path] = []
    if isinstance(configured, str):
        candidates.append(plugin.root / configured)
    elif isinstance(configured, list):
        candidates.extend(plugin.root / item for item in configured if isinstance(item, str))
    else:
        candidates.extend((plugin.root / "hooks.json", plugin.root / "hooks" / "hooks.json"))
    found = False
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(plugin.root)
        except (OSError, ValueError):
            return None
        if not resolved.is_file():
            continue
        found = True
        hooks = _load_json(resolved)
        if hooks is None:
            return None
        events = hooks.get("hooks")
        if not isinstance(events, dict):
            return None
        entries = events.get("sessionStart", events.get("SessionStart", []))
        if not isinstance(entries, list):
            return None
        if entries:
            return True
    return False if found or not configured else None


def _manifest(plugin: ActivePlugin) -> dict | None:
    value = _load_json(plugin.root / "plugin.json")
    return value or _load_json(plugin.root / ".claude-plugin" / "plugin.json")


def _contributors(active: list[ActivePlugin]) -> list[Contributor] | None:
    contributors: list[Contributor] = []
    platform_key = "powershell" if os.name == "nt" else "bash"
    for plugin in active:
        plugin_manifest = _manifest(plugin)
        if plugin_manifest is None:
            return None
        has_hooks = _session_start_hooks(plugin, plugin_manifest)
        if has_hooks is None:
            _diagnose(f"cannot inspect session-start hooks for {plugin.source}")
            return None
        declaration_path = plugin_manifest.get("sessionContext")
        if not isinstance(declaration_path, str) or not declaration_path.strip():
            if has_hooks:
                _diagnose(f"session-start plugin has no complete context declaration: {plugin.source}")
                return None
            continue
        try:
            path = (plugin.root / declaration_path).resolve(strict=True)
            path.relative_to(plugin.root)
        except (OSError, ValueError):
            _diagnose(f"context declaration escapes or is missing: {plugin.source}")
            return None
        declaration = _load_json(path)
        if (
            declaration is None
            or declaration.get("schema") != SCHEMA
            or declaration.get("version") != 1
            or declaration.get("complete") is not True
        ):
            _diagnose(f"context declaration is incomplete or incompatible: {plugin.source}")
            return None
        raw_contributors = declaration.get("contributors")
        if not isinstance(raw_contributors, list):
            return None
        seen: set[str] = set()
        for raw in raw_contributors:
            if not isinstance(raw, dict):
                return None
            contributor_id = raw.get("id")
            command = raw.get(platform_key)
            if (
                not isinstance(contributor_id, str)
                or not contributor_id
                or contributor_id in seen
                or raw.get("pure") is not True
                or not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and part for part in command)
            ):
                return None
            seen.add(contributor_id)
            order = raw.get("order", 500)
            timeout = raw.get("timeoutSeconds", 5)
            max_bytes = raw.get("maxBytes", 8192)
            if (
                not isinstance(order, int)
                or not isinstance(timeout, int)
                or not 1 <= timeout <= MAX_TIMEOUT_SECONDS
                or not isinstance(max_bytes, int)
                or not 1 <= max_bytes <= MAX_AGGREGATE_BYTES
            ):
                return None
            contributors.append(
                Contributor(
                    plugin.source,
                    plugin.root,
                    contributor_id,
                    order,
                    timeout,
                    max_bytes,
                    tuple(command),
                )
            )
            if len(contributors) > MAX_CONTRIBUTORS:
                return None
    ordered = sorted(
        contributors, key=lambda item: (item.order, item.source, item.contributor_id)
    )
    catalog_count = sum(
        item.contributor_id == "command-catalog" for item in ordered
    )
    declared_bytes = sum(
        item.max_bytes
        + len(
            (
                f"[context-contributor: "
                f"{item.source}/{item.contributor_id}]\n\n"
            ).encode("utf-8")
        )
        for item in ordered
        if item.contributor_id != "command-catalog"
    )
    if catalog_count:
        declared_bytes += COMMAND_CATALOG_BUDGET_BYTES
    schedule: list[int] = [0] * min(MAX_WORKERS, max(1, len(ordered)))
    heapq.heapify(schedule)
    for item in sorted(ordered, key=lambda value: value.timeout_seconds, reverse=True):
        elapsed = heapq.heappop(schedule)
        heapq.heappush(schedule, elapsed + item.timeout_seconds)
    declared_time = max(schedule)
    if declared_bytes > MAX_AGGREGATE_BYTES - AGGREGATE_HEADROOM_BYTES:
        _diagnose("declared contributor bytes exceed aggregate admission budget")
        return None
    if declared_time > MAX_TOTAL_TIMEOUT_SECONDS - 2:
        _diagnose("declared contributor time exceeds aggregate admission budget")
        return None
    return ordered


def _command(contributor: Contributor) -> list[str] | None:
    first, *rest = contributor.command
    try:
        script = (contributor.plugin_root / first).resolve(strict=True)
        script.relative_to(contributor.plugin_root)
    except (OSError, ValueError):
        return None
    suffix = script.suffix.lower()
    if os.name == "nt":
        if suffix != ".ps1":
            return None
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            return None
        return [powershell, "-NoProfile", "-File", str(script), *rest]
    if suffix != ".sh":
        return None
    return ["bash", str(script), *rest]


def _run(
    contributor: Contributor,
    hook_input: bytes,
    session_cwd: Path,
) -> ContributorResult:
    command = _command(contributor)
    if command is None:
        _diagnose(f"invalid contributor command: {contributor.source}/{contributor.contributor_id}")
        return ContributorResult(False)
    environment = os.environ.copy()
    for variable in (
        "COPILOT_PLUGIN_DATA",
        "CLAUDE_PLUGIN_DATA",
        "PLUGIN_DATA",
    ):
        environment.pop(variable, None)
    root = str(contributor.plugin_root)
    environment.update(
        {
            "COPILOT_PLUGIN_ROOT": root,
            "PLUGIN_ROOT": root,
            "CLAUDE_PLUGIN_ROOT": root,
        }
    )
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                env=environment,
                cwd=session_cwd,
                start_new_session=os.name != "nt",
            )
            process.communicate(
                input=hook_input,
                timeout=contributor.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            if "process" in locals() and process.poll() is None:
                try:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait()
            _diagnose(
                f"contributor failed: "
                f"{contributor.source}/{contributor.contributor_id}"
            )
            return ContributorResult(False)
        stdout_size = stdout_file.tell()
        stderr_size = stderr_file.tell()
        if (
            process.returncode != 0
            or stdout_size > contributor.max_bytes
            or stderr_size > contributor.max_bytes
        ):
            _diagnose(
                f"contributor rejected: "
                f"{contributor.source}/{contributor.contributor_id}"
            )
            return ContributorResult(False)
        stdout_file.seek(0)
        stdout = stdout_file.read()
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _diagnose(f"contributor emitted invalid JSON: {contributor.source}/{contributor.contributor_id}")
        return ContributorResult(False)
    if payload == {}:
        return ContributorResult(True)
    if not isinstance(payload, dict) or set(payload) != {"additionalContext"}:
        return ContributorResult(False)
    context = payload["additionalContext"]
    if not isinstance(context, str) or not context.strip():
        return ContributorResult(False)
    if "[context-contributor:" in context:
        _diagnose(
            f"contributor forged a reserved marker: "
            f"{contributor.source}/{contributor.contributor_id}"
        )
        return ContributorResult(False)
    return ContributorResult(True, context.strip())


def _command_catalog(
    contributor: Contributor,
    context: str,
) -> dict | None:
    marker = "```json\n"
    if marker not in context:
        return None
    raw = context.split(marker, 1)[1].split("\n```", 1)[0]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "copilot-extensions.session-command-catalog"
        or payload.get("version") != 1
        or not isinstance(payload.get("plugin"), str)
        or payload["plugin"] != contributor.source.partition("@")[0]
        or not isinstance(payload.get("commands"), list)
        or not isinstance(payload.get("payload"), dict)
    ):
        return None
    return {
        "source": contributor.source,
        "plugin": payload["plugin"],
        "payload": payload["payload"],
        "commands": payload["commands"],
    }


def _catalog_fragment(catalogs: list[dict]) -> str:
    payload = {
        "schema": "copilot-extensions.session-command-catalog.aggregate",
        "version": 1,
        "catalogs": catalogs,
    }
    return (
        "## session command catalog\n\n"
        "Invoke each exact `argv`; do not search `PATH` or substitute a "
        "same-named command.\n\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"
        "```"
    )


def main() -> int:
    hook_input = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(hook_input) > MAX_INPUT_BYTES:
        return _emit_empty("hook input exceeds the configured limit")
    try:
        payload = json.loads(hook_input.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _emit_empty("hook input is not valid JSON")
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return _emit_empty("hook input has no cwd")
    try:
        launch_cwd = Path(cwd).resolve(strict=True)
        if not launch_cwd.is_dir():
            raise OSError
        repo = _repo_root(launch_cwd)
        own_root = Path(os.environ["COPILOT_PLUGIN_ROOT"]).resolve(strict=True)
    except (KeyError, OSError):
        return _emit_empty("aggregator payload root is unavailable")
    if _has_unlisted_staged_plugins():
        return _emit_empty(
            "staged plugin inventory is not authoritative; aggregation disabled"
        )
    active = _active_plugins(repo)
    if not active:
        return _emit_empty("active plugin stack is unavailable")
    own = [plugin for plugin in active if plugin.root == own_root]
    if len(own) != 1:
        return _emit_empty("aggregator authority is absent or ambiguous")
    authority_source = _authority_source()
    if authority_source is None:
        return _emit_empty(
            "configured source-qualified authority identity is invalid"
        )
    if own[0].source != authority_source:
        return _emit_empty("aggregator is not the selected source-qualified authority")
    max_name = max(plugin.name for plugin in active)
    if own[0].name != max_name:
        return _emit_empty("aggregator is not the final active plugin")
    if sum(plugin.name == max_name for plugin in active) != 1:
        return _emit_empty("final plugin ordering is ambiguous")
    contributors = _contributors(active)
    if contributors is None:
        return _emit_empty()
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    started = time.monotonic()
    futures = [
        executor.submit(_run, contributor, hook_input, launch_cwd)
        for contributor in contributors
    ]
    outputs: list[ContributorResult] = []
    try:
        for future in futures:
            remaining = MAX_TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError
            outputs.append(future.result(timeout=remaining))
    except TimeoutError:
        for future in futures:
            future.cancel()
        return _emit_empty("aggregate contributor deadline exceeded")
    except Exception as exc:
        _diagnose(f"aggregate contributor collection failed: {exc}")
        return _emit_empty("aggregate contributor collection failed")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if not all(result.ok for result in outputs):
        return _emit_empty("one or more contributors failed; direct hooks retained")
    fragments: list[tuple[int, str, str]] = []
    catalogs: list[dict] = []
    catalog_order = 500
    for contributor, result in zip(contributors, outputs, strict=True):
        context = result.context
        if context is None:
            continue
        if contributor.contributor_id == "command-catalog":
            catalog = _command_catalog(contributor, context)
            if catalog is None:
                return _emit_empty(
                    f"command catalog is malformed: {contributor.source}"
                )
            catalogs.append(catalog)
            catalog_order = min(catalog_order, contributor.order)
            continue
        escaped_context = context.replace("[owner:", "[producer-owner:")
        fragment = (
            f"[context-contributor: "
            f"{contributor.source}/{contributor.contributor_id}]\n"
            f"{escaped_context}"
        )
        fragments.append(
            (contributor.order, contributor.source, fragment)
        )
    if catalogs:
        catalog_fragment = _catalog_fragment(catalogs)
        if len(catalog_fragment.encode("utf-8")) > COMMAND_CATALOG_BUDGET_BYTES:
            return _emit_empty("aggregate command catalog exceeds its budget")
        fragments.append((catalog_order, "command-catalogs", catalog_fragment))
    if not fragments:
        return _emit_empty()
    context = "\n\n".join(
        fragment for _, _, fragment in sorted(fragments)
    )
    if len(context.encode("utf-8")) > MAX_AGGREGATE_BYTES:
        return _emit_empty("aggregate context exceeds the configured limit")
    print(json.dumps({"additionalContext": context}, ensure_ascii=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
