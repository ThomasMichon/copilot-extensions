#!/usr/bin/env python3
"""Resolve the exact adopted context-injection authority payload."""

from __future__ import annotations

try:
    import json
    import os
    import re
    import shutil
    import subprocess
    import sys
    from pathlib import Path
except (AssertionError, ImportError, RuntimeError):
    raise SystemExit(0)


AUTHORITY_SOURCE = "context-injection@copilot-extensions"
ADOPTION_SCHEMA = "copilot-extensions.context-injection"
ADOPTION_CONFIG = Path(".context-injection/config.yaml")
ENGINE_SCHEMA = "copilot-extensions.context-injection-engine"
ENGINE_VERSION = 5
MAX_INPUT_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 4096
IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def _load_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_adoption(path: Path) -> str | None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
        return None
    parsed: dict[str, object] = {}
    parent: str | None = None
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            return None
        indent = len(line) - len(line.lstrip(" "))
        if indent not in {0, 2}:
            return None
        match = re.fullmatch(r"([a-z][a-zA-Z0-9]*):(.*)", line[indent:])
        if match is None:
            return None
        key, suffix = match.groups()
        if suffix and not suffix.startswith(" "):
            return None
        raw = suffix.strip()
        if indent == 0:
            if key in parsed:
                return None
            if not raw:
                parsed[key] = {}
                parent = key
                continue
            parent = None
            target = parsed
        else:
            if parent is None or not isinstance(parsed.get(parent), dict):
                return None
            target = parsed[parent]
            if key in target:
                return None
        if not raw or raw[0] in "\"'[{&*!|>":
            return None
        target[key] = int(raw) if raw.isascii() and raw.isdigit() else raw
    if (
        set(parsed) != {"schema", "version", "authority", "engine"}
        or parsed.get("schema") != ADOPTION_SCHEMA
        or parsed.get("version") != 1
        or parsed.get("authority") != AUTHORITY_SOURCE
        or not isinstance(parsed.get("engine"), dict)
        or parsed["engine"] != {
            "schema": ENGINE_SCHEMA,
            "version": ENGINE_VERSION,
        }
    ):
        return None
    return AUTHORITY_SOURCE


def _repo_root(cwd: Path) -> Path:
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _jsonc(path: Path) -> dict | None:
    try:
        body = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("//")
        )
        value = json.loads(body)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _repo_is_trusted(repo: Path) -> bool:
    value = _jsonc(Path.home() / ".copilot" / "config.json")
    folders = value.get("trustedFolders") if value else None
    if not isinstance(folders, list):
        return False
    resolved = repo.resolve()
    for raw in folders:
        if not isinstance(raw, str):
            continue
        try:
            if Path(raw).expanduser().resolve(strict=True) == resolved:
                return True
        except OSError:
            continue
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
            return None
        raw_enabled = value.get("enabledPlugins")
        if isinstance(raw_enabled, dict):
            for source, active in raw_enabled.items():
                if isinstance(source, str) and isinstance(active, bool):
                    enabled[source] = active
        raw_marketplaces = value.get("extraKnownMarketplaces")
        if isinstance(raw_marketplaces, dict):
            for name, declaration in raw_marketplaces.items():
                if isinstance(name, str) and isinstance(declaration, dict):
                    marketplaces[name] = (declaration, base)
    return enabled, marketplaces


def _marketplace_manifest(root: Path) -> tuple[dict, Path] | None:
    for relative in (
        Path(".github/plugin/marketplace.json"),
        Path(".claude-plugin/marketplace.json"),
    ):
        path = root / relative
        value = _load_json(path)
        if value is not None:
            manifest_root = (
                path.parent.parent.parent
                if relative.parts[0] == ".github"
                else path.parent.parent
            )
            return value, manifest_root
    return None


def _directory_authority(
    marketplace: str,
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
    plugin_root = manifest_root
    metadata = manifest.get("metadata")
    if isinstance(metadata, dict):
        configured_root = metadata.get("pluginRoot")
        if isinstance(configured_root, str) and configured_root.strip():
            plugin_root = manifest_root / configured_root
    try:
        plugin_root = plugin_root.resolve(strict=True)
        plugin_root.relative_to(root)
    except (OSError, ValueError):
        return None
    entries = manifest.get("plugins")
    if not isinstance(entries, list):
        return None
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("name") == "context-injection"
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("source"), str):
        return None
    try:
        candidate = (plugin_root / matches[0]["source"]).resolve(strict=True)
        candidate.relative_to(plugin_root)
    except (OSError, ValueError):
        return None
    return candidate


def _windows_argv(command_line: str) -> tuple[str, ...] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
    except ImportError:
        return None
    argument_count = ctypes.c_int()
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    arguments = parser(command_line, ctypes.byref(argument_count))
    if not arguments:
        return None
    try:
        return tuple(arguments[index] for index in range(argument_count.value))
    finally:
        ctypes.windll.kernel32.LocalFree(
            ctypes.cast(arguments, ctypes.c_void_p)
        )


def _process_ancestry() -> list[tuple[str, ...]] | None:
    injected = os.environ.get("COPILOT_CONTEXT_INJECTION_TEST_ANCESTRY")
    if injected is not None and "PYTEST_CURRENT_TEST" in os.environ:
        try:
            value = json.loads(injected)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, list) or not all(
            isinstance(command, list)
            and command
            and all(isinstance(argument, str) for argument in command)
            for command in value
        ):
            return None
        return [tuple(command) for command in value]
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            return None
        script = r"""
$result = @()
$processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
$byId = @{}
foreach ($process in $processes) { $byId[[int]$process.ProcessId] = $process }
$current = [int]$env:COPILOT_CONTEXT_INJECTION_ANCESTRY_PID
while ($current -gt 0) {
    if (-not $byId.ContainsKey($current)) { throw "incomplete ancestry" }
    $process = $byId[$current]
    if ($process.CommandLine) {
        $result += [pscustomobject]@{ commandLine = [string]$process.CommandLine }
        if (
            [string]$process.Name -ieq 'copilot.exe' -or
            [string]$process.Name -ieq 'copilot' -or
            [string]$process.CommandLine -match '(^|\s)--acp(\s|$)'
        ) { break }
    }
    if ([int]$process.ParentProcessId -eq $current) { throw "ancestry cycle" }
    $current = [int]$process.ParentProcessId
}
[Console]::Out.Write((ConvertTo-Json -InputObject @($result) -Compress))
"""
        try:
            environment = os.environ.copy()
            environment["COPILOT_CONTEXT_INJECTION_ANCESTRY_PID"] = str(
                os.getppid()
            )
            result = subprocess.run(
                [powershell, "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
                env=environment,
            )
            records = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return None
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            return None
        ancestry = []
        for record in records:
            command_line = (
                record.get("commandLine") if isinstance(record, dict) else None
            )
            arguments = (
                _windows_argv(command_line)
                if isinstance(command_line, str)
                else None
            )
            if not arguments:
                return None
            ancestry.append(arguments)
        return ancestry
    pid = os.getppid()
    seen: set[int] = set()
    ancestry = []
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        except OSError:
            return None
        arguments = tuple(
            item.decode("utf-8", errors="surrogateescape")
            for item in raw.split(b"\0")
            if item
        )
        if not arguments:
            return None
        ancestry.append(arguments)
        parent = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
        if parent is None:
            return None
        pid = int(parent.group(1))
    return ancestry


def _plugin_dirs(arguments: tuple[str, ...]) -> tuple[str, ...] | None:
    values: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--plugin-dir":
            index += 1
            if (
                index >= len(arguments)
                or not arguments[index]
                or arguments[index].startswith("--")
            ):
                return None
            values.append(arguments[index])
        elif argument.startswith("--plugin-dir="):
            value = argument.partition("=")[2]
            if not value:
                return None
            values.append(value)
        elif argument.startswith("--plugin-dir"):
            return None
        index += 1
    return tuple(values)


def _staged_roots() -> tuple[Path, ...] | None:
    ancestry = _process_ancestry()
    if ancestry is None:
        return None
    acp = [arguments for arguments in ancestry if "--acp" in arguments]
    if not acp:
        return ()
    extracted = [_plugin_dirs(arguments) for arguments in acp]
    if any(value is None for value in extracted):
        return None
    assert all(value is not None for value in extracted)
    if any(value != extracted[0] for value in extracted[1:]):
        return None
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in extracted[0] or ():
        configured = Path(raw).expanduser()
        if not configured.is_absolute():
            return None
        try:
            root = configured.resolve(strict=True)
        except OSError:
            return None
        identity = os.path.normcase(str(root))
        if not root.is_dir() or identity in seen:
            continue
        seen.add(identity)
        roots.append(root)
    return tuple(roots)


def _manifest(root: Path) -> dict | None:
    return _load_json(root / "plugin.json") or _load_json(
        root / ".claude-plugin" / "plugin.json"
    )


def _valid_authority(root: Path) -> bool:
    manifest = _manifest(root)
    if manifest is None or manifest.get("name") != "context-injection":
        return False
    declaration_relative = manifest.get("sessionContext")
    contract_relative = manifest.get("sessionContextEngine")
    if not isinstance(declaration_relative, str) or not isinstance(
        contract_relative, str
    ):
        return False
    try:
        declaration_path = (root / declaration_relative).resolve(strict=True)
        contract_path = (root / contract_relative).resolve(strict=True)
        aggregate = (root / "scripts" / "aggregate_context.py").resolve(
            strict=True
        )
        for path in (declaration_path, contract_path, aggregate):
            path.relative_to(root)
    except (OSError, ValueError):
        return False
    declaration = _load_json(declaration_path)
    contract = _load_json(contract_path)
    behavior = declaration.get("sessionStart") if declaration else None
    return (
        declaration is not None
        and declaration.get("schema")
        == "copilot-extensions.session-context-contributors"
        and declaration.get("version") == 1
        and declaration.get("complete") is True
        and isinstance(behavior, dict)
        and behavior.get("sideEffects") == "none"
        and behavior.get("context") == "aggregate-authority"
        and declaration.get("contributors") == []
        and contract
        == {"schema": ENGINE_SCHEMA, "version": ENGINE_VERSION}
        and aggregate.is_file()
    )


def _installed_authority(marketplace: str) -> Path | None:
    try:
        installed_root = (
            Path.home() / ".copilot" / "installed-plugins"
        ).resolve(strict=True)
        candidate = (
            installed_root / marketplace / "context-injection"
        ).resolve(strict=True)
        candidate.relative_to(installed_root)
    except (OSError, ValueError):
        return None
    return candidate


def resolve_authority(repo: Path) -> Path | None:
    if not _repo_is_trusted(repo):
        return None
    try:
        adoption_path = (repo / ADOPTION_CONFIG).resolve(strict=True)
        adoption_path.relative_to(repo.resolve())
    except (OSError, ValueError):
        return None
    if _load_adoption(adoption_path) != AUTHORITY_SOURCE:
        return None
    loaded = _settings(repo)
    if loaded is None:
        return None
    enabled, marketplaces = loaded
    if enabled.get(AUTHORITY_SOURCE) is not True:
        return None
    same_name = [
        source
        for source, active in enabled.items()
        if active and source.partition("@")[0] == "context-injection"
    ]
    if same_name != [AUTHORITY_SOURCE]:
        return None

    marketplace = AUTHORITY_SOURCE.partition("@")[2]
    declaration, base = marketplaces.get(marketplace, ({}, repo))
    candidate = _directory_authority(marketplace, declaration, base)
    if candidate is None:
        candidate = _installed_authority(marketplace)
        if candidate is None:
            return None
    try:
        candidate = candidate.resolve(strict=True)
    except OSError:
        return None
    staged = _staged_roots()
    if staged is None:
        return None
    if staged:
        matches = [
            root.resolve()
            for root in staged
            if (_manifest(root) or {}).get("name") == "context-injection"
        ]
        if len(matches) != 1 or matches[0] != candidate:
            return None
    override = os.environ.get("COPILOT_CONTEXT_INJECTION_ENGINE_ROOT")
    if override:
        configured = Path(override).expanduser()
        if not configured.is_absolute():
            return None
        try:
            configured = configured.resolve(strict=True)
        except OSError:
            return None
        if configured != candidate:
            return None
    return candidate if _valid_authority(candidate) else None


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(payload) > MAX_INPUT_BYTES:
        return 0
    try:
        value = json.loads(payload.decode("utf-8"))
        raw_cwd = value.get("cwd") if isinstance(value, dict) else None
        if not isinstance(raw_cwd, str) or not raw_cwd:
            return 0
        cwd = Path(raw_cwd).resolve(strict=True)
        if not cwd.is_dir():
            return 0
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return 0
    authority = resolve_authority(_repo_root(cwd))
    if authority is not None:
        print(str(authority), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
