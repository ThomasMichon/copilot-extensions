"""Actual Bridge commands must admit cold publication before touching a runtime."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from test_adopter_publication import SHELLS, _adopter
from test_command_binding import (
    POWERSHELL, _snapshot, _write,
    binding_fixture as binding_fixture,
    powershell_host as powershell_host,
)
from test_installer_publication import _policy
from test_native_publication import _completed_slot
from test_simple_first_use_publication import _release_payload


@pytest.fixture(scope="module")
def bridge_release(tmp_path_factory) -> Path:
    return _release_payload(tmp_path_factory.mktemp("bridge-release"), "agent-bridge")


def _selected_cell(fixture: dict, environment: dict) -> tuple[Path, dict]:
    primitive = fixture["primitive"]
    cell = primitive.stamp_context(
        payload_version="1.0.0", payload_origin="installed",
        expected_namespace_generation=0, expected_install_generation=0,
        **{key: fixture["arguments"][key]
           for key in ("payload_root", "plugin_id", "environment")},
    )
    primitive.compare_and_swap_activation(
        context=cell["installReceipt"],
        expected_marketplace_id=cell["marketplaceId"],
        expected_plugin_id=fixture["arguments"]["plugin_id"],
        expected_namespace_generation=cell["namespaceGeneration"],
        expected_install_generation=cell["generation"],
        expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active",
        legacy_disposition="absent",
        legacy_probe={
            "declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z",
        },
        legacy_root=fixture["legacy"], environment=fixture["environment"],
    )
    _write(fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    })
    environment["COPILOT_EXTENSIONS_CONTEXT"] = cell["installReceipt"]
    return Path(cell["pluginRoot"]), {
        **fixture["arguments"], "context": cell["installReceipt"],
    }


def _record_installer(fixture: dict, shell: str) -> None:
    scripts = fixture["payload"] / "scripts"
    if shell == "powershell":
        (scripts / "install.ps1").write_text(
            "param([string]$Action, [string]$InstallDir)\n"
            "[System.Runtime.ProfileOptimization]::StartProfile($null)\n"
            "$record=@{action=$Action;root=$InstallDir;"
            "environmentRoot=$env:AGENT_BRIDGE_INSTALL_DIR}|ConvertTo-Json -Compress\n"
            "[IO.File]::WriteAllText((Join-Path $InstallDir 'installer-ran.json'), $record)\n"
            "exit 37\n",
            encoding="utf-8-sig",
        )
    else:
        (scripts / "install.sh").write_text(
            "#!/usr/bin/env bash\n"
            '[[ "$1" == provision && "$2" == --install-dir && '
            '"$3" == "$AGENT_BRIDGE_INSTALL_DIR" ]] || exit 98\n'
            'printf reached > "$3/installer-ran"\nexit 37\n',
            encoding="utf-8",
        )


def _command(fixture: dict, shell: str, script: Path, *arguments: str) -> list[str]:
    if shell == "powershell":
        assert POWERSHELL is not None
        return [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(script), *arguments]
    interpreter = shutil.which("bash")
    assert interpreter is not None
    return [interpreter, str(script), *arguments]


def _run(fixture: dict, environment: dict, command: list[str], host):
    if host:
        return host.run(command)
    return subprocess.run(
        command, cwd=fixture["home"], env=environment, capture_output=True, text=True,
        timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _initialize_shell_profiles(
    fixture: dict, environment: dict, *, engines: tuple[str | None, ...] | None = None,
) -> set[str]:
    if os.name != "nt":
        return set()
    profiles = {
        f"AppData/Local/Microsoft/{prefix}PowerShell/StartupProfileData-NonInteractive"
        for prefix in ("", "Windows/")
    }
    allowed = {""}
    for name in profiles:
        allowed.update(parent.as_posix() for parent in Path(name).parents)
        allowed.add(name)
    before = _snapshot(fixture["home"])
    selected = (POWERSHELL, shutil.which("pwsh")) if engines is None else engines
    for engine in dict.fromkeys(filter(None, selected)):
        control = subprocess.run(
            [engine, "-NoLogo", "-NoProfile", "-Command",
             "[System.Runtime.ProfileOptimization]::StartProfile($null)"],
            cwd=fixture["home"], env=environment, capture_output=True, text=True,
            timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert control.returncode == 0, control.stdout + control.stderr
    after = _snapshot(fixture["home"])
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    assert changed <= allowed, sorted(changed)
    return profiles & after.keys()


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(60)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("existing_global", [False, True])
@pytest.mark.parametrize("loss", [
    "available", "pointer", "publisher", "publisher-and-pointer",
    "deactivated-publisher-and-pointer",
])
def test_bridge_cell_stamp_preserves_global_ownership(
    binding_fixture: dict, bridge_release: Path, shell: str, existing_global: bool, loss: str,
) -> None:
    fixture = binding_fixture
    environment = _adopter(fixture, "agent-bridge", bridge_release)
    for key in (
        "COPILOT_PLUGIN_INSTALL_SMOKE", "AGENT_BRIDGE_NO_SELFPROVISION",
        "COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH", "COPILOT_EXTENSIONS_BOOT_TRACE",
        "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS",
    ):
        environment.pop(key, None)
    if os.name == "nt":
        environment.pop("PSModulePath", None)
        environment["LOCALAPPDATA"] = str(fixture["home"] / "AppData/Local")
        environment["APPDATA"] = str(fixture["home"] / "AppData/Roaming")
    shell_profiles = _initialize_shell_profiles(fixture, environment)

    def stamp(root: Path):
        suffix = "ps1" if shell == "powershell" else "sh"
        command = _command(fixture, shell, fixture["payload"] / f"scripts/install.{suffix}")
        command += (
            ["-Action", "stamp", "-InstallDir", str(root)] if shell == "powershell" else
            ["stamp", "--install-dir", str(root)]
        )
        result = _run(fixture, environment, command, None)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    global_paths = [
        fixture["home"] / ".local/bin" / name
        for name in (
            ("agent-bridge.ps1", "agent-bridge.cmd") if shell == "powershell" else
            ("agent-bridge",)
        )
    ]
    if existing_global:
        stamp(fixture["legacy"])
    globals_before = {path: path.read_bytes() if path.exists() else None for path in global_paths}
    assert all(value is not None for value in globals_before.values()) is existing_global

    cell, arguments = _selected_cell(fixture, environment)
    _policy(fixture, cell, arguments, "standalone")
    stamped = stamp(cell)
    assert "Scoped install keeps the global compatibility binstub unchanged" in stamped.stdout
    assert {path: path.read_bytes() if path.exists() else None for path in global_paths} == globals_before
    assert (cell / "payload-dir").is_file()
    for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
        assert (cell / "bin" / name).is_file()

    for root, marker in ((fixture["legacy"], "LEGACY"), (cell, "CELL")):
        python = _completed_slot({**fixture, "legacy": root})
        site = python.parent.parent / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{os.sys.version_info.major}.{os.sys.version_info.minor}/site-packages"
        )
        (site / "agent_bridge.py").write_text(
            "import json, os, sys\n"
            f"print(json.dumps({{'marker':{marker!r},"
            "'root':os.environ['AGENT_BRIDGE_INSTALL_DIR'],"
            "'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),"
            "'installationId':os.environ.get('AGENT_BRIDGE_INSTALLATION_ID'),"
            "'arguments':sys.argv[1:]}))\n",
            encoding="utf-8",
        )
    suffix = ".ps1" if shell == "powershell" else ""
    payload_entry = fixture["payload"] / "bin" / f"agent-bridge{suffix}"
    selected = _run(fixture, environment, _command(fixture, shell, payload_entry, "--help"), None)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    selected_record = json.loads(selected.stdout)
    assert selected_record["marker"] == "CELL"
    assert selected_record["root"] == str(cell)
    assert selected_record["context"] == environment["COPILOT_EXTENSIONS_CONTEXT"]
    assert selected_record["installationId"]

    if loss.startswith("deactivated-"):
        receipt = json.loads((cell / "installation-activation.json").read_text(encoding="utf-8"))
        changed = fixture["primitive"].compare_and_swap_activation(
            context=arguments["context"], expected_marketplace_id=receipt["marketplaceId"],
            expected_plugin_id="agent-bridge",
            expected_namespace_generation=receipt["namespaceGeneration"],
            expected_install_generation=receipt["installGeneration"],
            expected_activation_generation=receipt["generation"],
            activation_mode="legacy", activation_state="deactivated", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
            legacy_root=fixture["legacy"], environment=fixture["environment"],
        )
        assert changed["activationChanged"], changed
        pointer = cell / "payload-dir"
        pointer.rename(pointer.with_name("payload-dir.unavailable"))
    if "publisher" in loss:
        fixture["payload"].rename(fixture["payload"].with_name("publisher-unavailable"))
    if (loss == "pointer" or loss.endswith("and-pointer")) and existing_global:
        pointer = fixture["legacy"] / "payload-dir"
        pointer.rename(pointer.with_name("payload-dir.unavailable"))
    ordinary = environment.copy()
    for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_BRIDGE_INSTALL_DIR",
        "AGENT_BRIDGE_CONFIG_DIR", "AGENT_BRIDGE_PAYLOAD_ROOT", "AGENT_BRIDGE_INSTALLATION_ID",
    ):
        ordinary.pop(key, None)
    assert {path: path.read_bytes() if path.exists() else None for path in global_paths} == globals_before
    if not existing_global:
        assert not any(path.exists() for path in global_paths)
        return

    before = _snapshot(fixture["home"])
    for entry in global_paths:
        command = (
            [ordinary.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", str(entry), "--help"]
            if entry.suffix == ".cmd" else _command(fixture, shell, entry, "--help")
        )
        result = _run(fixture, ordinary, command, None)
        assert result.returncode == 0, result.stdout + result.stderr
        record = json.loads(result.stdout)
        if loss in {"available", "pointer"}:
            assert record == selected_record
        else:
            assert record == {
                "marker": "LEGACY", "root": str(fixture["legacy"]), "context": None,
                "installationId": None, "arguments": ["--help"],
            }
    after = _snapshot(fixture["home"])
    changed = {
        name for name in before.keys() | after.keys()
        if name not in shell_profiles and before.get(name) != after.get(name)
    }
    permitted = (
        cell.relative_to(fixture["home"]).as_posix() if loss in {"available", "pointer"} else
        fixture["legacy"].relative_to(fixture["home"]).as_posix()
    )
    assert changed <= {permitted, f"{permitted}/logs", f"{permitted}/logs/boot-trace.jsonl"}
    assert {path: path.read_bytes() if path.exists() else None for path in global_paths} == globals_before


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(60)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(("scope", "state", "entry"), [
    (scope, state, "payload")
    for scope in ("legacy", "selected-cell")
    for state in (
        "managed", "invalid", "foreign", "future", "missing", "standalone",
        "healthy-managed", "healthy-invalid", "healthy-exit",
    )
] + [
    ("selected-cell", "deactivation-standalone", "payload"),
    ("selected-cell", "deactivation-managed", "payload"),
    ("selected-cell", "deactivation-healthy", "payload"),
    ("selected-cell", "mode-changed-missing", "payload"),
    *[(scope, "deployment-changed-under-lock", "payload")
      for scope in ("legacy", "selected-cell")],
    ("legacy", "standalone-unicode", "payload"),
    *[("legacy", state, "payload") for state in (
        "missing-helper", "old-helper", "missing-resolver", "old-resolver",
    )],
    *[pytest.param(scope, state, "catalog", marks=pytest.mark.skipif(
        os.name != "nt", reason="Native Windows catalog command",
    )) for scope in ("legacy", "selected-cell")
      for state in ("standalone", "managed", "healthy-managed")],
    *[("legacy", state, entry) for entry in (
        "stamped-global", "stamped-unavailable", "stamped-no-pointer",
    )
      for state in ("missing", "standalone", "managed", "healthy-managed", "healthy-exit")],
    *[("selected-cell", state, "stamped-global")
      for state in ("standalone", "managed", "healthy-managed")],
    ("selected-cell", "healthy-managed", "stamped-no-pointer"),
    *[(scope, state, "payload")
      for scope in ("legacy", "selected-cell")
      for state in ("healthy-stale-current", "healthy-incomplete-current")],
    *[("legacy", state, entry)
      for state in ("healthy-stale-current", "healthy-incomplete-current")
      for entry in ("stamped-global", "stamped-unavailable", "stamped-no-pointer")],
    *[pytest.param("legacy", "failed-resolver", entry, marks=pytest.mark.skipif(
        os.name == "nt", reason="Bash resolver error propagation",
    )) for entry in ("payload", "stamped-no-pointer")],
    *[pytest.param("legacy", "standalone-no-flock", entry, marks=pytest.mark.skipif(
        os.name == "nt", reason="POSIX no-flock provisioning lock",
    )) for entry in ("payload", "stamped-global")],
])
def test_bridge_actual_first_use_publication(
    binding_fixture: dict, bridge_release: Path, shell: str, scope: str, state: str, entry: str,
    powershell_host,
) -> None:
    fixture = binding_fixture
    environment = _adopter(fixture, "agent-bridge", bridge_release)
    manifest = json.loads((fixture["payload"] / "payload-invocation.json").read_text())
    assert manifest["command"] == "agent-bridge" and not manifest.get("commands")
    catalog_cmd = manifest["windowsCatalogShim"] == "cmd"
    assert manifest["windowsCatalogShim"] in {"cmd", "powershell"}
    for key in (
        "COPILOT_PLUGIN_INSTALL_SMOKE", "AGENT_BRIDGE_NO_SELFPROVISION",
        "COPILOT_EXTENSIONS_BOOT_TRACE_LOG_PATH", "COPILOT_EXTENSIONS_BOOT_TRACE",
        "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS",
    ):
        environment.pop(key, None)
    if state == "standalone-no-flock":
        environment["COPILOT_EXT_NO_FLOCK"] = "1"
    if os.name == "nt":
        environment.pop("PSModulePath", None)
        environment["LOCALAPPDATA"] = str(fixture["home"] / "AppData/Local")
        environment["APPDATA"] = str(fixture["home"] / "AppData/Roaming")
    shell_profiles = _initialize_shell_profiles(fixture, environment)

    def snapshot_home() -> dict:
        # Shell-only controls prove these exact CLR profiles precede plugin
        # execution. Retain every other home byte and directory mtime.
        return {name: value for name, value in _snapshot(fixture["home"]).items()
                if name not in shell_profiles}

    if state == "standalone-unicode":
        root = fixture["home"] / "custom-\u00e9\u6d4b\u8bd5"
        root.mkdir()
        fixture["legacy"] = root
        fixture["arguments"]["legacy_root"] = root
    environment["AGENT_BRIDGE_INSTALL_DIR"] = str(fixture["legacy"])
    environment["AGENT_BRIDGE_CONFIG_DIR"] = str(fixture["legacy"])
    host = None
    retained = None
    if entry.startswith("stamped-"):
        if shell == "powershell":
            quote = lambda path: "'" + str(path).replace("'", "''") + "'"
            stamp = fixture["home"] / "bridge-stamp.ps1"
            stamp.write_text(
                f"& {quote(fixture['payload'] / 'scripts/install.ps1')} -Action stamp "
                f"-InstallDir {quote(fixture['legacy'])}\nexit $LASTEXITCODE\n",
                encoding="utf-8-sig",
            )
        else:
            stamp = fixture["payload"] / "scripts/install.sh"
        stamp_command = _command(fixture, shell, stamp)
        if shell == "bash":
            stamp_command += ["stamp", "--install-dir", str(fixture["legacy"])]
        stamped = _run(fixture, environment, stamp_command, host)
        assert stamped.returncode == 0, stamped.stdout + stamped.stderr
        if host:
            host.close()
            host = None
        retained = Path((fixture["legacy"] / "payload-dir").read_text(encoding="utf-8-sig").strip())
        for name in (
            "runtime-gate.ps1", "runtime-gate.sh", "resolve-runtime.ps1", "resolve-runtime.sh",
            "install.ps1", "install.sh", "installation-context/installation-context.ps1",
            "installation-context/installation-context.sh", "installation-context/json-query.awk",
        ):
            assert (retained / "scripts" / name).is_file(), name
    root, arguments = fixture["legacy"], fixture["arguments"]
    if scope == "selected-cell":
        root, arguments = _selected_cell(fixture, environment)
    healthy = state.startswith("healthy-") or state == "deactivation-healthy"
    readiness = state in {
        "missing-helper", "old-helper", "missing-resolver", "old-resolver", "failed-resolver",
    }
    policy_state = (
        "missing" if state in {"mode-changed-missing", "deployment-changed-under-lock"} else
        "standalone" if state in {
            "standalone-unicode", "standalone-no-flock", "deactivation-standalone",
        } or readiness else
        "managed" if ((state.startswith("healthy-") and state != "healthy-invalid") or
                      state in {"deactivation-managed", "deactivation-healthy"}) else
        "invalid" if state == "healthy-invalid" else state
    )
    standalone = policy_state in {"missing", "standalone"}
    _policy(fixture, root, arguments, policy_state)
    if scope == "selected-cell":
        # Opposite legacy policy proves that the selected cell is authoritative.
        _policy(
            fixture, fixture["legacy"], fixture["arguments"],
            "managed" if standalone else "standalone",
        )
    if state.startswith("deactivation-"):
        _write(fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": False, "marketplaces": {}},
        })
    _record_installer(fixture, shell)
    if retained is not None and retained != fixture["payload"]:
        _record_installer({**fixture, "payload": retained}, shell)
    if healthy:
        slot_fixture = {**fixture, "legacy": root}
        python = _completed_slot(slot_fixture)
        site = python.parent.parent / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{os.sys.version_info.major}.{os.sys.version_info.minor}/site-packages"
        )
        (site / "agent_bridge.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'arguments':sys.argv[1:],"
            "'root':os.environ['AGENT_BRIDGE_INSTALL_DIR'],'prefix':sys.prefix}))\n"
            + ('print("recorded runtime diagnostic", file=sys.stderr)\nsys.exit(29)\n'
               if state == "healthy-exit" else ""),
            encoding="utf-8",
        )
        if state in {"healthy-stale-current", "healthy-incomplete-current"}:
            (root / "last-known-good").write_text("1.0.0", encoding="utf-8")
            (root / "current-version").write_text("2.0.0", encoding="utf-8")
            if state == "healthy-incomplete-current":
                (root / "versions/2.0.0").mkdir()
        if scope == "selected-cell" and entry == "stamped-no-pointer":
            legacy_python = _completed_slot(fixture)
            legacy_site = legacy_python.parent.parent / (
                "Lib/site-packages" if os.name == "nt" else
                f"lib/python{os.sys.version_info.major}.{os.sys.version_info.minor}/site-packages"
            )
            (legacy_site / "agent_bridge.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
    suffix = "ps1" if shell == "powershell" else "sh"
    helper = fixture["payload"] / f"scripts/installation-context/installation-context.{suffix}"
    if shell == "powershell":
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        probe_script = fixture["home"] / "bridge-publication-probe.ps1"
        probe_script.write_text(
            f"& {quote(helper)} -Action probe-plugin-publication "
            f"-PayloadRoot {quote(fixture['payload'])} -PluginId agent-bridge "
            f"-LegacyRoot {quote(fixture['legacy'])}\nexit $LASTEXITCODE\n",
            encoding="utf-8-sig",
        )
        probe_command = _command(fixture, shell, probe_script)
    else:
        probe_command = _command(
            fixture, shell, helper,
            "probe-plugin-publication", "--payload-root", str(fixture["payload"]),
            "--plugin-id", "agent-bridge", "--legacy-root", str(fixture["legacy"]),
        )
    if shell == "powershell" and host is None:
        host = powershell_host(fixture, environment)
    before_probe = snapshot_home()
    probe = _run(fixture, environment, probe_command, host)
    assert probe.returncode == (0 if standalone else 3), probe.stdout + probe.stderr
    decision = json.loads(probe.stdout)
    assert decision["deployment"]["allowPluginPublication"] is standalone, decision
    diagnostic = {
        "missing": "deployment-default-standalone",
        "standalone": "deployment-explicit-standalone",
        "managed": "deployment-managed",
        "invalid": "deployment-policy-invalid",
        "foreign": "deployment-policy-invalid",
        "future": "deployment-policy-version-unsupported",
    }[policy_state]
    assert decision["deployment"]["reason"] == diagnostic, decision
    assert Path(decision["deployment"]["path"]) == root / "runtime-deployment.json", decision
    if state.startswith("deactivation-"):
        assert decision["status"] == "deactivation-required", decision
    assert snapshot_home() == before_probe

    if entry in {"stamped-unavailable", "stamped-no-pointer"}:
        fixture["payload"].rename(fixture["payload"].with_name("publisher-unavailable"))
    if entry == "stamped-no-pointer":
        pointer = fixture["legacy"] / "payload-dir"
        pointer.rename(pointer.with_name(pointer.name + ".unavailable"))
    mode_path = fixture["home"] / ".copilot-extensions/installation-mode.json"
    if state == "mode-changed-missing":
        resolver = fixture["payload"] / f"scripts/resolve-runtime.{suffix}"
        real_resolver = resolver.with_name("recorded-real-resolver." + suffix)
        resolver.rename(real_resolver)
        # Simulate one peer policy edit after the real resolver observes a cold
        # slot, including its early-return path. Admission remains canonical.
        mutation = (
            "# Trace contract: normal/read-only/ready-v1\n"
            "param([string]$TraceMode = 'normal')\n"
            f". {quote(real_resolver)} -TraceMode $TraceMode\n"
            "if ($TraceMode -eq 'ready') {\n"
            f"  [IO.File]::WriteAllText({quote(mode_path)}, '{{\"schema\":\"invalid\"}}')\n"
            "}\n"
            if shell == "powershell" else
            "# Trace contract: normal/read-only/ready-v1\n"
            f". {shlex.quote(str(real_resolver))}\n"
            "_real_resolver_status=$?\n"
            'if [[ "${AGENT_RT_RESOLVE_TRACE_MODE:-}" == ready ]]; then\n'
            f"  printf '%s' '{{\"schema\":\"invalid\"}}' > {shlex.quote(str(mode_path))}\n"
            'fi\nreturn "$_real_resolver_status"\n'
        )
        resolver.write_text(
            mutation,
            encoding="utf-8-sig" if shell == "powershell" else "utf-8",
        )
    if state == "deployment-changed-under-lock":
        _policy(fixture, root, arguments, "managed")
        policy_path = root / "runtime-deployment.json"
        peer_policy = fixture["home"] / "peer-runtime-deployment.json"
        policy_path.rename(peer_policy)
        resolver = fixture["payload"] / f"scripts/resolve-runtime.{suffix}"
        real_resolver = resolver.with_name("recorded-real-resolver." + suffix)
        resolver.rename(real_resolver)
        mutation = (
            "# Trace contract: normal/read-only/ready-v1\n"
            "param([string]$TraceMode = 'normal')\n"
            f". {quote(real_resolver)} -TraceMode $TraceMode\n"
            f"if ([IO.File]::Exists({quote(root / '.provision.lock')})) {{\n"
            f"  [IO.File]::Copy({quote(peer_policy)}, {quote(policy_path)}, $true)\n"
            "}\n"
            if shell == "powershell" else
            "# Trace contract: normal/read-only/ready-v1\n"
            f". {shlex.quote(str(real_resolver))}\n"
            "_real_resolver_status=$?\n"
            f"if [[ -f {shlex.quote(str(root / '.provision.lock'))} ]]; then\n"
            f"  cp {shlex.quote(str(peer_policy))} {shlex.quote(str(policy_path))}\n"
            'fi\nreturn "$_real_resolver_status"\n'
        )
        resolver.write_text(
            mutation,
            encoding="utf-8-sig" if shell == "powershell" else "utf-8",
        )
    if readiness:
        target = (
            fixture["legacy"] / f"bin/resolve-runtime.{suffix}"
            if state == "failed-resolver" and entry == "stamped-no-pointer" else
            helper if state.endswith("helper") else
            fixture["payload"] / f"scripts/resolve-runtime.{suffix}"
        )
        if state.startswith("missing-"):
            target.rename(target.with_name(target.name + ".unavailable"))
        elif state == "failed-resolver":
            real_resolver = target.with_name("recorded-real-resolver." + suffix)
            target.rename(real_resolver)
            target.write_text(
                "# Trace contract: normal/read-only/ready-v1\n"
                f". {shlex.quote(str(real_resolver))}\nreturn 19\n", encoding="utf-8",
            )
        else:
            original = target.read_text(encoding="utf-8-sig")
            old = (
                "probe-plugin-publication" if state == "old-helper" else
                "# Trace contract: normal/read-only/ready-v1"
            )
            assert old in original
            target.write_text(
                original.replace(old, "retired-plugin-publication" if state == "old-helper"
                                 else "# Older resolver without deferred tracing"),
                encoding="utf-8-sig" if shell == "powershell" else "utf-8",
            )
    entry_path = (
        fixture["home"] / ".local/bin/agent-bridge" if entry.startswith("stamped-") else
        fixture["payload"] / "bin/agent-bridge"
    ).with_name(
        "agent-bridge.cmd" if entry == "catalog" and catalog_cmd else
        "agent-bridge.ps1" if shell == "powershell" else "agent-bridge"
    )
    forwarded = (
        ["--help", 'spaces "quotes" & pipes | ; bangs !', "\u00e9\u6d4b\u8bd5",
         "", 'back\\"quote', "tail space\\"]
        if healthy and entry != "catalog" else ["--help"]
    )
    command = (
        [environment.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", str(entry_path), *forwarded]
        if entry == "catalog" and catalog_cmd else _command(fixture, shell, entry_path, *forwarded)
    )
    before = snapshot_home()
    result = _run(fixture, environment, command, None if entry == "catalog" else host)
    after = snapshot_home()
    if state == "mode-changed-missing":
        rechecked = _run(fixture, environment, probe_command, host)
        assert rechecked.returncode == 0, rechecked.stdout + rechecked.stderr
        blocked = json.loads(rechecked.stdout)
        assert blocked["status"] == "invalid", blocked
        assert blocked["deployment"]["allowPluginPublication"] is True, blocked
        assert blocked["deployment"]["reason"] == "deployment-default-standalone", blocked
        assert blocked["actualMode"] == decision["actualMode"] == "namespaced", blocked
        for key in ("context", "installGeneration", "runtimeRoot"):
            assert blocked[key] == decision[key], blocked
        assert snapshot_home() == after
    if host:
        host.close()
        assert snapshot_home() == after
    changed = sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
    relative = root.relative_to(fixture["home"]).as_posix()
    marker = root / ("installer-ran.json" if shell == "powershell" else "installer-ran")
    evidence = (
        f"scope={scope} state={state} entry={entry} root={root} rc={result.returncode}\n"
        f"changed={changed}\ninstallerReached={marker.exists()}\n"
        f"{result.stdout}{result.stderr}"
    )
    if state == "failed-resolver":
        assert result.returncode == 126, evidence
        assert "runtime resolver failed" in result.stderr, evidence
        assert not changed, evidence
        assert not marker.exists(), evidence
        return
    if state == "deployment-changed-under-lock":
        assert result.returncode == 3, evidence
        assert "deployment-managed" in result.stdout + result.stderr, evidence
        assert policy_path.read_bytes() == peer_policy.read_bytes(), evidence
        assert not marker.exists(), evidence
        assert not (root / ".provision-status").exists(), evidence
        assert set(changed) <= {
            relative, f"{relative}/logs", f"{relative}/logs/boot-trace.jsonl",
            f"{relative}/.provision.lock", f"{relative}/runtime-deployment.json",
        }, evidence
        return
    if state == "mode-changed-missing":
        assert result.returncode == 3, evidence
        assert "Runtime publication refused" in result.stdout + result.stderr, evidence
        assert changed == [mode_path.relative_to(fixture["home"]).as_posix()], evidence
        assert mode_path.read_bytes() == b'{"schema":"invalid"}', evidence
        assert not marker.exists(), evidence
        return
    missing_gate_refusal = (
        entry == "stamped-no-pointer" and (not healthy or scope == "selected-cell")
    ) or (entry == "stamped-unavailable" and not healthy)
    if missing_gate_refusal:
        assert not changed, evidence
        assert result.returncode == 126, evidence
        assert "retained runtime gate is unavailable" in result.stdout + result.stderr, evidence
        assert not marker.exists(), evidence
        return
    if readiness or not (standalone or healthy):
        assert not changed, evidence
        assert result.returncode == (126 if readiness and state != "old-helper" else 3), evidence
        expected = (
            "Runtime publication refused" if state == "old-helper" else
            "read-only runtime resolver is unavailable" if state == "old-resolver" else
            "installation-context runtime support is unavailable" if readiness else diagnostic
        )
        assert expected in result.stdout + result.stderr, evidence
        assert not marker.exists(), evidence
        return
    permitted = {relative, f"{relative}/logs", f"{relative}/logs/boot-trace.jsonl"}
    if standalone:
        permitted.update(
            f"{relative}/{name}"
            for name in (marker.name, ".provision-status", ".provision.lock")
        )
        assert result.returncode == 37, evidence
        assert marker.is_file(), evidence
        if shell == "powershell":
            record = json.loads(marker.read_text(encoding="utf-8"))
            assert record == {
                "action": "provision", "root": str(root), "environmentRoot": str(root),
            }, evidence
        assert "failed rc=37" in (root / ".provision-status").read_text(encoding="utf-8-sig")
        if state == "standalone-no-flock":
            assert not (root / ".provision.lock.pid").exists(), evidence
            assert not (root / ".provision.reap.mutex").exists(), evidence
            assert not (root / ".provision.lock").exists(), evidence
    else:
        assert result.returncode == (29 if state == "healthy-exit" else 0), evidence
        assert json.loads(result.stdout) == {
            "arguments": forwarded, "root": str(root), "prefix": str(python.parent.parent),
        }, evidence
        if state == "healthy-exit":
            assert "recorded runtime diagnostic" in result.stderr, evidence
        assert not marker.exists(), evidence
        assert not (root / ".provision-status").exists(), evidence
        assert not (root / ".provision.lock").exists(), evidence
    assert set(changed) <= permitted, evidence
    assert (root / "logs/boot-trace.jsonl").is_file(), evidence
