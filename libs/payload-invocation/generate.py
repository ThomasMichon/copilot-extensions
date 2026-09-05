#!/usr/bin/env python3
"""Generate checked-in payload-local command shims from plugin manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = Path(__file__).resolve().parent / "templates"
SCHEMA = "copilot-extensions.payload-invocation"
LEGACY_VERSION = 1
VERSION = 2

_PLUGIN = re.compile(r"^[a-z][a-z0-9-]*$")
_COMMAND = re.compile(r"^[a-z][a-z0-9-]*$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_RUNTIME_ROOT = re.compile(r"^\.[a-z0-9-]+$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]+$")
_PURPOSE = re.compile(r"^[A-Za-z0-9 ._/-]+$")
_OUTPUT_DIR = re.compile(r"^[a-z0-9][a-z0-9_./-]*$")
_INSTALLER = re.compile(r"^[a-z][a-z0-9-]*$")
_DISPATCHER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_./-]*$")
_WINDOWS_CATALOG_SHIMS = {"powershell", "cmd"}
_POSIX_CATALOG_MODES = {"python", "shell"}
_PROVISION_MODES = {"snapshot", "direct"}
_INSTALLATION_CONTEXT_MODES = {"legacy", "required"}


def _eligible_core_runtime_plugins() -> set[str]:
    marketplace_path = REPO / ".github" / "plugin" / "marketplace.json"
    try:
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"{marketplace_path}: cannot load canonical marketplace roster: {exc}"
        ) from exc
    if not isinstance(marketplace, dict) or not isinstance(
        marketplace.get("plugins"), list
    ):
        raise ValueError(
            f"{marketplace_path}: canonical marketplace roster is invalid"
        )
    eligible: set[str] = set()
    for entry in marketplace["plugins"]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        source = entry.get("source")
        if (
            not isinstance(name, str)
            or not _PLUGIN.fullmatch(name)
            or not isinstance(source, str)
        ):
            continue
        plugin_root = REPO / source
        if (plugin_root / "pyproject.toml").is_file():
            eligible.add(name)
    return eligible


def _load_command(path: Path, value: object, *, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: invalid {label}: {value!r}")
    checks = {
        "command": _COMMAND,
        "module": _MODULE,
        "purpose": _PURPOSE,
    }
    command: dict[str, str] = {}
    for field, pattern in checks.items():
        field_value = value.get(field)
        if not isinstance(field_value, str) or not pattern.fullmatch(field_value):
            raise ValueError(f"{path}: invalid {label}.{field}: {field_value!r}")
        command[field] = field_value
    return command


def load_manifest(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest_version = data.get("version")
    if (
        data.get("schema") != SCHEMA
        or not isinstance(manifest_version, int)
        or isinstance(manifest_version, bool)
        or manifest_version not in {LEGACY_VERSION, VERSION}
    ):
        raise ValueError(
            f"{path}: expected {SCHEMA} version {LEGACY_VERSION} or {VERSION}"
        )
    runtime_root_field = (
        "runtimeRoot" if manifest_version == LEGACY_VERSION else "legacyRuntimeRoot"
    )
    shared_checks = {runtime_root_field: _RUNTIME_ROOT, "noSelfProvisionEnv": _ENV}
    for field, pattern in shared_checks.items():
        value = data.get(field)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError(f"{path}: invalid {field}: {value!r}")
    if manifest_version == LEGACY_VERSION:
        if "legacyRuntimeRoot" in data or "installationContext" in data:
            raise ValueError(
                f"{path}: version 1 cannot declare installation-context fields"
            )
        installation_context = "legacy"
    else:
        if "runtimeRoot" in data:
            raise ValueError(f"{path}: version 2 uses legacyRuntimeRoot")
        installation_context = data.get("installationContext")
        if installation_context not in _INSTALLATION_CONTEXT_MODES:
            raise ValueError(
                f"{path}: invalid installationContext: {installation_context!r}"
            )

    raw_commands = data.get("commands")
    if raw_commands is None:
        command = _load_command(
            path,
            {field: data.get(field) for field in ("command", "module", "purpose")},
            label="command",
        )
        commands = [command]
        plugin = data.get("plugin", command["command"])
        if plugin != command["command"]:
            raise ValueError(
                f"{path}: legacy plugin must equal command; use commands[] for "
                "distinct plugin identity"
            )
    else:
        if any(field in data for field in ("command", "module", "purpose")):
            raise ValueError(
                f"{path}: commands cannot be combined with top-level command/module/purpose"
            )
        if not isinstance(raw_commands, list) or not raw_commands:
            raise ValueError(f"{path}: commands must be a non-empty list")
        commands = [
            _load_command(path, value, label=f"commands[{index}]")
            for index, value in enumerate(raw_commands)
        ]
        plugin = data.get("plugin")
    if not isinstance(plugin, str) or not _PLUGIN.fullmatch(plugin):
        raise ValueError(f"{path}: invalid plugin: {plugin!r}")
    command_ids = [command["command"] for command in commands]
    if len(command_ids) != len(set(command_ids)):
        raise ValueError(f"{path}: duplicate command id")
    output_dir = data.get("outputDir", "bin")
    if (
        not isinstance(output_dir, str)
        or not _OUTPUT_DIR.fullmatch(output_dir)
        or ".." in Path(output_dir).parts
    ):
        raise ValueError(f"{path}: invalid outputDir: {output_dir!r}")
    installer = data.get("installer", "install")
    if not isinstance(installer, str) or not _INSTALLER.fullmatch(installer):
        raise ValueError(f"{path}: invalid installer: {installer!r}")
    windows_catalog_shim = data.get("windowsCatalogShim", "powershell")
    if windows_catalog_shim not in _WINDOWS_CATALOG_SHIMS:
        raise ValueError(
            f"{path}: invalid windowsCatalogShim: {windows_catalog_shim!r}"
        )
    posix_catalog_mode = data.get("posixCatalogMode", "python")
    if posix_catalog_mode not in _POSIX_CATALOG_MODES:
        raise ValueError(
            f"{path}: invalid posixCatalogMode: {posix_catalog_mode!r}"
        )
    if posix_catalog_mode == "shell" and raw_commands is not None:
        raise ValueError(f"{path}: shell posixCatalogMode supports one command")
    provision_mode = data.get("provisionMode", "snapshot")
    if provision_mode not in _PROVISION_MODES:
        raise ValueError(f"{path}: invalid provisionMode: {provision_mode!r}")
    session_start_bootstrap = data.get("sessionStartBootstrap", True)
    if not isinstance(session_start_bootstrap, bool):
        raise ValueError(
            f"{path}: invalid sessionStartBootstrap: {session_start_bootstrap!r}"
        )
    payload_root_env = data.get("payloadRootEnv", "")
    if not isinstance(payload_root_env, str) or (
        payload_root_env and not _ENV.fullmatch(payload_root_env)
    ):
        raise ValueError(f"{path}: invalid payloadRootEnv: {payload_root_env!r}")
    payload_dispatcher = data.get("payloadDispatcher", {})
    if not isinstance(payload_dispatcher, dict):
        raise ValueError(
            f"{path}: invalid payloadDispatcher: {payload_dispatcher!r}"
        )
    normalized_dispatcher: dict[str, str] = {}
    for platform in ("posix", "windows"):
        value = payload_dispatcher.get(platform, "")
        if not isinstance(value, str) or (
            value
            and (
                not _DISPATCHER.fullmatch(value)
                or ".." in Path(value).parts
                or not (path.parent / value).is_file()
            )
        ):
            raise ValueError(
                f"{path}: invalid payloadDispatcher.{platform}: {value!r}"
            )
        normalized_dispatcher[platform] = value
    if bool(normalized_dispatcher["posix"]) != bool(
        normalized_dispatcher["windows"]
    ):
        raise ValueError(
            f"{path}: payloadDispatcher must declare both posix and windows"
        )
    catalog_gate = data.get("catalogGate", "")
    if not isinstance(catalog_gate, str) or (
        catalog_gate
        and (
            not _DISPATCHER.fullmatch(catalog_gate)
            or ".." in Path(catalog_gate).parts
            or not (path.parent / catalog_gate).is_file()
        )
    ):
        raise ValueError(f"{path}: invalid catalogGate: {catalog_gate!r}")
    if installation_context == "required":
        if plugin not in _eligible_core_runtime_plugins():
            raise ValueError(
                f"{path}: installationContext required is limited to "
                "runtime-bearing core suite plugin identities"
            )
        if not payload_root_env:
            raise ValueError(
                f"{path}: installationContext required needs payloadRootEnv"
            )
        if not all(normalized_dispatcher.values()):
            raise ValueError(
                f"{path}: installationContext required needs payloadDispatcher "
                "for both platforms"
            )
    data["runtimeRoot"] = data[runtime_root_field]
    data["installationContext"] = installation_context
    data["outputDir"] = output_dir
    data["installer"] = installer
    data["windowsCatalogShim"] = windows_catalog_shim
    data["posixCatalogMode"] = posix_catalog_mode
    data["provisionMode"] = provision_mode
    data["sessionStartBootstrap"] = session_start_bootstrap
    data["payloadRootEnv"] = payload_root_env
    data["payloadDispatcher"] = normalized_dispatcher
    data["catalogGate"] = catalog_gate
    data["plugin"] = plugin
    data["commands"] = commands
    data["multiCommandManifest"] = raw_commands is not None
    return data


def render(
    template: str,
    data: dict[str, object],
    *,
    command: dict[str, str] | None = None,
) -> str:
    commands = data["commands"]
    assert isinstance(commands, list) and commands
    selected = command or commands[0]
    assert isinstance(selected, dict)
    output_parts = Path(str(data["outputDir"])).parts
    payload_up = "/".join(".." for _part in output_parts)
    output_dir = str(data["outputDir"])
    catalog_specs = [
        {
            "id": item["command"],
            "relativePath": f"{output_dir}/{item['command']}",
            "purpose": item["purpose"],
        }
        for item in commands
    ]
    windows_catalog_suffix = (
        ".cmd" if data["windowsCatalogShim"] == "cmd" else ".ps1"
    )
    windows_catalog_shell = (
        "cmd" if data["windowsCatalogShim"] == "cmd" else "direct"
    )
    windows_cmd_host_block = (
        'set "_PSHOST="\n'
        'for /f "delims=" %%I in (\'"%SystemRoot%\\System32\\where.exe" '
        "pwsh 2^>nul') do if not defined _PSHOST set \"_PSHOST=%%I\"\n"
        'if not defined _PSHOST set "_PSHOST=%SystemRoot%\\System32\\'
        'WindowsPowerShell\\v1.0\\powershell.exe"\n'
        '"%_PSHOST%" -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*'
    )
    installer_name = str(data["installer"])
    if data["provisionMode"] == "direct":
        provision_posix = 'bash "$_installer" provision >&2'
        provision_powershell = (
            "& $_hostExe -NoProfile -ExecutionPolicy Bypass -File "
            "$_installer provision 2>&1 |\n"
            "    ForEach-Object { [Console]::Error.WriteLine($_) }\n"
            "$_provisionRc = $LASTEXITCODE"
        )
    else:
        provision_posix = (
            'bash "$_installer" stamp >&2\n'
            '_snapshot="$(cat "$_runtime_root/payload-dir" 2>/dev/null || true)"\n'
            f'_snapshot_installer="$_snapshot/scripts/{installer_name}.sh"\n'
            'if [ ! -f "$_snapshot_installer" ]; then\n'
            "    printf '[%s] stamped snapshot installer not found: %s\\n' \\\n"
            '        "$_command" "$_snapshot_installer" >&2\n'
            "    exit 127\n"
            "fi\n"
            'bash "$_snapshot_installer" provision >&2'
        )
        provision_powershell = (
            "& $_hostExe -NoProfile -ExecutionPolicy Bypass -File "
            "$_installer stamp 2>&1 |\n"
            "    ForEach-Object { [Console]::Error.WriteLine($_) }\n"
            "$_provisionRc = $LASTEXITCODE\n\n"
            "        if ($_provisionRc -eq 0) {\n"
            "            $_snapshot = ''\n"
            "            try { $_snapshot = "
            "([IO.File]::ReadAllText((Join-Path $_runtimeRoot "
            "'payload-dir'))).Trim() } catch {}\n"
            "            $_snapshotInstaller = if ($_snapshot) { "
            f"Join-Path $_snapshot 'scripts\\{installer_name}.ps1' "
            "} else { '' }\n"
            "            if (-not ($_snapshotInstaller -and "
            "(Test-Path -LiteralPath $_snapshotInstaller))) {\n"
            "                [Console]::Error.WriteLine("
            '"[$_command] stamped snapshot installer not found: '
            '$_snapshotInstaller")\n'
            "                $_provisionRc = 127\n"
            "            } else {\n"
            "                & $_hostExe -NoProfile -ExecutionPolicy Bypass "
            "-File $_snapshotInstaller provision 2>&1 |\n"
            "                    ForEach-Object { "
            "[Console]::Error.WriteLine($_) }\n"
            "                $_provisionRc = $LASTEXITCODE\n"
            "            }\n"
            "        }"
        )
    catalog_specs_ps = [
        {
            "id": item["command"],
            "relativePath": (
                f"{output_dir}/{item['command']}{windows_catalog_suffix}".replace(
                    "/", "\\"
                )
            ),
            "purpose": item["purpose"],
        }
        for item in commands
    ]
    values = {
        "PLUGIN": str(data["plugin"]),
        "COMMAND": str(selected["command"]),
        "MODULE": str(selected["module"]),
        "RUNTIME_ROOT": str(data["runtimeRoot"]),
        "NO_SELFPROVISION_ENV": str(data["noSelfProvisionEnv"]),
        "PURPOSE": str(selected["purpose"]),
        "OUTPUT_DIR": str(data["outputDir"]),
        "OUTPUT_DIR_PS": str(data["outputDir"]).replace("/", "\\"),
        "PAYLOAD_UP": payload_up,
        "PAYLOAD_UP_PS": payload_up.replace("/", "\\"),
        "PAYLOAD_UP_WIN": payload_up.replace("/", "\\"),
        "INSTALLER": str(data["installer"]),
        "WINDOWS_CATALOG_SUFFIX": windows_catalog_suffix,
        "WINDOWS_CATALOG_SHELL": windows_catalog_shell,
        "WINDOWS_CMD_HOST_BLOCK": windows_cmd_host_block,
        "PROVISION_POSIX": provision_posix,
        "PROVISION_POWERSHELL": provision_powershell,
        "CATALOG_GATE_POSIX": (
            f'catalog_gate="$self_root/{data["catalogGate"]}"\n'
            'if ! "$py" -E -X utf8 "$catalog_gate" --check --cwd "$PWD"; then\n'
            "    printf '%s\\n' '{}'\n"
            "    exit 0\n"
            "fi\n"
            if data["catalogGate"]
            else ""
        ),
        "CATALOG_GATE_POWERSHELL": (
            "$catalogGate = Join-Path $selfRoot "
            f"'{str(data['catalogGate']).replace('/', chr(92))}'\n"
            "$python = Get-Command python -ErrorAction SilentlyContinue\n"
            "if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }\n"
            "if (-not $python) { Write-Output '{}'; exit 0 }\n"
            "& $python.Source -E -X utf8 $catalogGate --check "
            "--cwd (Get-Location).Path *> $null\n"
            "if ($LASTEXITCODE -ne 0) { Write-Output '{}'; exit 0 }\n"
            if data["catalogGate"]
            else ""
        ),
        "PAYLOAD_DISPATCH_POSIX": (
            (
                f'export {data["payloadRootEnv"]}="$_payload_root"\n'
                if data["payloadRootEnv"]
                else ""
            )
            + (
                "if ! command -v bash >/dev/null 2>&1; then\n"
                f"    printf '[{selected['command']}] required payload "
                "dispatcher needs bash.\\n' >&2\n"
                "    exit 126\n"
                "fi\n"
                f'exec bash "$_payload_root/{data["payloadDispatcher"]["posix"]}" "$@"'
                if data["installationContext"] == "required"
                else f'exec "$_payload_root/{data["payloadDispatcher"]["posix"]}" "$@"'
            )
            if data["payloadDispatcher"]["posix"]
            else ""
        ),
        "PAYLOAD_DISPATCH_POWERSHELL": (
            (
                f"$env:{data['payloadRootEnv']} = $_payloadRoot\n"
                if data["payloadRootEnv"]
                else ""
            )
            + "$_payloadDispatcher = Join-Path $_payloadRoot "
            f"'{str(data['payloadDispatcher']['windows']).replace('/', chr(92))}'\n"
            "& $_payloadDispatcher @args\n"
            "exit $LASTEXITCODE"
            if data["payloadDispatcher"]["windows"]
            else ""
        ),
        "PAYLOAD_ROOT_ENV_POSIX": (
            f'export {data["payloadRootEnv"]}="$_payload_root"\n'
            if data["payloadRootEnv"]
            else ""
        ),
        "PAYLOAD_ROOT_ENV_POWERSHELL_BEFORE": (
            "    $_payloadRootEnvPrevious = "
            f"[Environment]::GetEnvironmentVariable('{data['payloadRootEnv']}', "
            "'Process')\n"
            f"    $env:{data['payloadRootEnv']} = $_payloadRoot\n"
            "    try {\n"
            if data["payloadRootEnv"]
            else ""
        ),
        "PAYLOAD_ROOT_ENV_POWERSHELL_AFTER": (
            "    $_payloadRc = $LASTEXITCODE\n"
            "    } finally {\n"
            "        if ($null -eq $_payloadRootEnvPrevious) { "
            f"Remove-Item Env:{data['payloadRootEnv']} "
            "-ErrorAction SilentlyContinue } else { "
            f"$env:{data['payloadRootEnv']} = $_payloadRootEnvPrevious }}\n"
            "    }\n"
            if data["payloadRootEnv"]
            else ""
        ),
        "PAYLOAD_ROOT_ENV_POWERSHELL_EXIT_CODE": (
            "$_payloadRc" if data["payloadRootEnv"] else "$LASTEXITCODE"
        ),
        "CATALOG_SPECS_JSON": json.dumps(
            catalog_specs, ensure_ascii=True, separators=(",", ":")
        ),
        "CATALOG_SPECS_JSON_PS": json.dumps(
            catalog_specs_ps, ensure_ascii=True, separators=(",", ":")
        ),
        "CATALOG_CONTRACT_JSON": json.dumps(
            {
                "plugin": data["plugin"],
                "commands": catalog_specs,
                "windowsCatalogShim": data["windowsCatalogShim"],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ),
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"@@{key}@@", value)
    digest_placeholder = "@@CATALOG_FILE_SHA256@@"
    if digest_placeholder in rendered:
        unsigned = rendered.replace(digest_placeholder, "")
        rendered = rendered.replace(
            digest_placeholder,
            hashlib.sha256(unsigned.encode("utf-8")).hexdigest(),
        )
    remaining = sorted(set(re.findall(r"@@[A-Z_]+@@", rendered)))
    if remaining:
        raise ValueError(f"unresolved template fields: {', '.join(remaining)}")
    return rendered


def expected_files(manifest: Path) -> dict[Path, str]:
    data = load_manifest(manifest)
    installer = str(data["installer"])
    protected_paths: set[Path] = set()
    for suffix in (".sh", ".ps1"):
        installer_path = manifest.parent / "scripts" / f"{installer}{suffix}"
        if not installer_path.is_file():
            raise ValueError(f"{manifest}: installer not found: {installer_path}")
        resolver_path = manifest.parent / "scripts" / f"resolve-runtime{suffix}"
        if not resolver_path.is_file():
            raise ValueError(f"{manifest}: runtime resolver not found: {resolver_path}")
        protected_paths.update((installer_path, resolver_path))
    output = manifest.parent / str(data["outputDir"])
    generated: dict[Path, str] = {}
    commands = data["commands"]
    assert isinstance(commands, list)
    for command in commands:
        assert isinstance(command, dict)
        command_id = str(command["command"])
        dispatcher = data["payloadDispatcher"]
        assert isinstance(dispatcher, dict)
        for path, template in (
            (
                output / command_id,
                "dispatcher-posix.tmpl" if dispatcher["posix"] else "posix-shim.tmpl",
            ),
            (
                output / f"{command_id}.ps1",
                "dispatcher-powershell.tmpl"
                if dispatcher["windows"]
                else "powershell-shim.tmpl",
            ),
            (output / f"{command_id}.cmd", "cmd-shim.tmpl"),
        ):
            if path in protected_paths:
                raise ValueError(f"{manifest}: generated path collision: {path}")
            if path in generated:
                raise ValueError(f"{manifest}: duplicate generated path: {path}")
            generated[path] = render(
                (TEMPLATES / template).read_text(encoding="utf-8"),
                data,
                command=command,
            )
    catalog_prefix = "catalog-multi" if data["multiCommandManifest"] else "catalog"
    posix_catalog_template = (
        "catalog-posix-shell.tmpl"
        if data["posixCatalogMode"] == "shell"
        else f"{catalog_prefix}-posix.tmpl"
    )
    catalog_outputs = (
        (
            manifest.parent / "scripts" / "emit-command-catalog.sh",
            posix_catalog_template,
        ),
        (
            manifest.parent / "scripts" / "emit-command-catalog.ps1",
            f"{catalog_prefix}-powershell.tmpl",
        ),
    )
    protected_paths.update(path for path, _template in catalog_outputs)
    command_paths = set(generated)
    collisions = sorted(command_paths & protected_paths)
    if collisions:
        raise ValueError(
            f"{manifest}: generated path collision: {', '.join(map(str, collisions))}"
        )
    for path, template in catalog_outputs:
        if path in generated:
            raise ValueError(f"{manifest}: duplicate generated path: {path}")
        generated[path] = render(
            (TEMPLATES / template).read_text(encoding="utf-8"),
            data,
        )
    return generated


def display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def process_manifest(manifest: Path, *, check: bool) -> list[str]:
    errors: list[str] = []
    for path, expected in expected_files(manifest).items():
        executable = path.suffix == "" or path.name == "emit-command-catalog.sh"
        if check:
            try:
                actual = path.read_text(encoding="utf-8")
            except OSError:
                actual = ""
            if actual != expected:
                errors.append(f"{display_path(path)}: generated content is stale")
            if (
                executable
                and os.name != "nt"
                and path.exists()
                and not path.stat().st_mode & stat.S_IXUSR
            ):
                errors.append(f"{display_path(path)}: POSIX shim is not executable")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8", newline="\n")
        if executable:
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"generated {display_path(path)}")
    return errors


def discover_manifests() -> list[Path]:
    return sorted((REPO / "plugins").glob("*/payload-invocation.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="process every plugin manifest")
    parser.add_argument("--check", action="store_true", help="fail when generated files drift")
    args = parser.parse_args(argv)

    manifests = discover_manifests() if args.all else [path.resolve() for path in args.manifests]
    if not manifests:
        parser.error("provide a manifest or use --all")

    errors: list[str] = []
    for manifest in manifests:
        errors.extend(process_manifest(manifest, check=args.check))
    if errors:
        print("\n".join(errors))
        return 1
    if args.check:
        print(f"payload-invocation: {len(manifests)} manifest(s) in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
