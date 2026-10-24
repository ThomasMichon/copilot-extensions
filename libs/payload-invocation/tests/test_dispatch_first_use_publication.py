"""On-demand actual Dispatch launchers must admit cold publication before writes."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_adopter_publication import _adopter, _command
from test_command_binding import POWERSHELL, _snapshot, _write, binding_fixture as binding_fixture
from test_bridge_first_use_publication import _initialize_shell_profiles
from test_installer_publication import _policy
from test_native_publication import _completed_slot, _run
from test_simple_first_use_publication import _release_payload

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.timeout(60),
]

@pytest.fixture(scope="module")
def dispatch_release(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _release_payload(tmp_path_factory.mktemp("dispatch-release"), "agent-dispatch")


def _selected_cell(fixture: dict, environment: dict[str, str]) -> tuple[Path, dict]:
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
        expected_plugin_id="agent-dispatch",
        expected_namespace_generation=cell["namespaceGeneration"],
        expected_install_generation=cell["generation"],
        expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active",
        legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        legacy_root=fixture["legacy"], environment=fixture["environment"],
    )
    _write(fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    })
    environment["COPILOT_EXTENSIONS_CONTEXT"] = cell["installReceipt"]
    return Path(cell["pluginRoot"]), {**fixture["arguments"], "context": cell["installReceipt"]}


@pytest.mark.parametrize(("state", "entry"), [
    (state, entry)
    for state in (
        "standalone", "managed", "healthy", "healthy-lkg", "cell-managed",
        "cell-standalone", "cell-healthy", "cell-healthy-lkg",
        "invalid", "foreign", "future", "old-resolver",
        "missing-admission", "changed-under-lock", "mode-changed-under-lock",
        "custom-standalone", "custom-healthy",
    )
    for entry in ("global", "payload", "board")
] + [
    (state, entry) for state in ("upstream-first", "upstream-policy-change", "upstream-mode-change")
    for entry in ("global", "board")
] + [
    pytest.param(
        state, "global",
        marks=pytest.mark.skipif(os.name != "nt", reason="Actual Windows PowerShell 5 stderr forwarding"),
    )
    for state in ("standalone-stderr", "upstream-stderr")
] + [
    pytest.param(
        "standalone-no-flock", entry,
        marks=pytest.mark.skipif(os.name == "nt", reason="Native POSIX fallback-lock route"),
    )
    for entry in ("global", "payload", "board")
])
def test_dispatch_actual_first_use_publication(
    binding_fixture: dict, dispatch_release: Path, tmp_path: Path, state: str, entry: str,
) -> None:
    if os.name == "nt" and POWERSHELL is None:
        pytest.skip("Native PowerShell is required")
    environment = _adopter(binding_fixture, "agent-dispatch", dispatch_release)
    if os.name == "nt":
        environment.pop("PSModulePath", None)
        environment["LOCALAPPDATA"] = str(binding_fixture["home"] / "AppData/Local")
        environment["APPDATA"] = str(binding_fixture["home"] / "AppData/Roaming")
    shell_profiles = _initialize_shell_profiles(binding_fixture, environment)
    def snapshot_home() -> dict:
        return {name: value for name, value in _snapshot(binding_fixture["home"]).items()
                if name not in shell_profiles}
    payload = binding_fixture["payload"]
    specification = json.loads((payload / "payload-invocation.json").read_text(encoding="utf-8"))
    assert specification["command"] == "agent-dispatch"
    shell = "powershell" if os.name == "nt" else "bash"
    catalog = _run(binding_fixture, environment, _command(binding_fixture, shell, "emit-command-catalog"))
    assert catalog.returncode == 0, catalog.stdout + catalog.stderr
    catalog_context = json.loads(catalog.stdout)["additionalContext"]
    catalog_command = json.loads(catalog_context.split("```json\n", 1)[1].split("\n```", 1)[0])["commands"][0]
    assert catalog_command["id"] == "agent-dispatch"
    catalog_entry = Path(catalog_command["argv"][0])
    assert catalog_entry.is_file()
    if os.name == "nt":
        assert catalog_command["shell"] == "cmd"
        assert catalog_entry.suffix == ".cmd"
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    temporary = tmp_path / "installer-work"
    temporary.mkdir()
    environment.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
    environment["AGENT_DISPATCH_PROVISION_OFFLINE"] = "1"
    if state == "standalone-no-flock":
        environment["COPILOT_EXT_NO_FLOCK"] = "1"
    environment.pop("AGENT_DISPATCH_NO_SELFPROVISION", None)
    stamp_command = [*_command(binding_fixture, shell, "install"), "stamp"]
    if state.startswith("custom-"):
        default_root = binding_fixture["legacy"]
        custom_root = binding_fixture["home"] / "custom runtime"
        custom_root.mkdir()
        binding_fixture["legacy"] = custom_root
        binding_fixture["arguments"]["legacy_root"] = custom_root
        environment["AGENT_DISPATCH_INSTALL_DIR"] = str(custom_root)
        stamp_command += ["-InstallDir" if os.name == "nt" else "--install-dir", str(custom_root)]
    stamped = _run(
        binding_fixture, environment, stamp_command,
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    legacy = binding_fixture["legacy"]
    root, arguments = (
        _selected_cell(binding_fixture, environment)
        if state.startswith("cell-") else (legacy, binding_fixture["arguments"])
    )
    policy_state = (
        state if state in ("invalid", "foreign", "future") else
        "standalone" if state.endswith("standalone") or state in (
            "changed-under-lock", "mode-changed-under-lock", "standalone-no-flock", "standalone-stderr",
        ) or state.startswith("upstream-") else "managed"
    )
    _policy(binding_fixture, root, arguments, policy_state)
    if state.startswith("custom-"):
        _policy(binding_fixture, default_root, {
            **binding_fixture["arguments"], "legacy_root": default_root,
        }, "managed")
    if root != legacy:
        _policy(
            binding_fixture, legacy, binding_fixture["arguments"],
            "managed" if policy_state == "standalone" else "standalone",
        )
    mode_change = state in ("mode-changed-under-lock", "upstream-mode-change")
    mode_path = binding_fixture["home"] / ".copilot-extensions/installation-mode.json"
    if mode_change:
        (root / "runtime-deployment.json").unlink()
        _write(mode_path, {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": False, "marketplaces": {}},
        })

    # Preserve the real installer (including source checks and lifecycle
    # admission); stop at acquisition of the first expensive external tool.
    record = binding_fixture["home"] / "uv-acquisition-record"
    installer = payload / ("scripts/install.ps1" if os.name == "nt" else "scripts/install.sh")
    source = installer.read_text(encoding="utf-8")
    seam = "    $UvCommand = Ensure-Uv -InstallRoot $InstallDir" if os.name == "nt" else "    _ensure_uv || exit 1"
    assert source.count(seam) == 1
    instrumented = source.replace(
        seam,
        f"    [IO.File]::WriteAllText('{str(record).replace(chr(39), chr(39) * 2)}', $InstallDir)\n"
        + ("    [Console]::Error.WriteLine('local acquisition diagnostic')\n" if state == "standalone-stderr" else "")
        + "    exit 37"
        if os.name == "nt" else
        f'    printf "%s\\n" "$INSTALL_DIR" > {shlex.quote(str(record))}\n    exit 37',
    )
    installer.write_text(instrumented, encoding="utf-8")
    snapshot = Path((legacy / "payload-dir").read_text(encoding="utf-8-sig").strip())
    if snapshot != payload:
        (snapshot / "scripts" / installer.name).write_text(instrumented, encoding="utf-8")
    for key in (
        "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
        "COPILOT_PLUGIN_INSTALL_STAGED", "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
    ):
        environment.pop(key, None)

    helper = payload / "scripts/installation-context" / (
        "installation-context.ps1" if os.name == "nt" else "installation-context.sh"
    )
    admission_command = (
        [POWERSHELL, "-NoProfile", "-File", str(helper), "-Action", "probe-plugin-publication",
         "-PayloadRoot", str(payload), "-PluginId", "agent-dispatch", "-LegacyRoot", str(legacy)]
        if os.name == "nt" else
        ["bash", str(helper), "probe-plugin-publication", "--payload-root", str(payload),
         "--plugin-id", "agent-dispatch", "--legacy-root", str(legacy)]
    )
    admission = _run(binding_fixture, environment, admission_command)
    assert admission.returncode == (0 if policy_state == "standalone" else 3), (
        admission.stdout + admission.stderr
    )
    decision = json.loads(admission.stdout)
    assert Path(decision["runtimeRoot"] or decision["legacy"]["root"]) == root
    assert decision["deployment"]["allowPluginPublication"] is (policy_state == "standalone")

    healthy = "healthy" in state
    python = None
    if healthy:
        python = _completed_slot({**binding_fixture, "legacy": root})
        site = python.parent.parent / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        package = site / "agent_dispatch"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        for name, label in (("__main__.py", "main"), ("board_cli.py", "board")):
            (package / name).write_text(
                "import json, os, sys\n"
                'print(json.dumps({"python": sys.executable, "arguments": sys.argv[1:], '
                f'"entry": "{label}", "root": os.environ["AGENT_DISPATCH_INSTALL_DIR"]' + "}))\n",
                encoding="utf-8",
            )
        if state.endswith("lkg"):
            (root / "current-version").write_text("unavailable", encoding="utf-8")
            (root / "last-known-good").write_text("1.0.0", encoding="utf-8")
    if state == "old-resolver":
        resolver = (
            root / "bin" if entry != "payload" else payload / "scripts"
        ) / ("resolve-runtime.ps1" if os.name == "nt" else "resolve-runtime.sh")
        resolver.write_text(resolver.read_text(encoding="utf-8").replace(
            "# Trace contract: normal/read-only/ready-v1", "# old resolver",
        ), encoding="utf-8")
    if state == "missing-admission":
        missing = (
            root / "bin/installation-context" if entry != "payload" else
            payload / "scripts/installation-context"
        ) / helper.name
        missing.unlink()
    if state in ("changed-under-lock", "mode-changed-under-lock"):
        peer = tmp_path / "peer-decision.json"
        if mode_change:
            policy_path = mode_path
            _write(peer, {
                "schema": "copilot-extensions.installation-mode", "version": 1,
                "installationMode": {"enabled": True, "marketplaces": {}},
            })
        else:
            _policy(binding_fixture, root, arguments, "managed")
            policy_path = root / "runtime-deployment.json"
            peer.write_bytes(policy_path.read_bytes())
            _policy(binding_fixture, root, arguments, "standalone")
        resolver = (
            root / "bin" if entry != "payload" else payload / "scripts"
        ) / ("resolve-runtime.ps1" if os.name == "nt" else "resolve-runtime.sh")
        real_resolver = resolver.with_name("real-" + resolver.name)
        resolver.rename(real_resolver)
        if os.name == "nt":
            def quote(path: Path) -> str:
                return "'" + str(path).replace("'", "''") + "'"

            mutation = (
                "# Trace contract: normal/read-only/ready-v1\n"
                "param([string]$TraceMode = 'normal')\n"
                f". {quote(real_resolver)} -TraceMode $TraceMode\n"
                f"if ([IO.File]::Exists({quote(root / '.provision.lock')})) {{\n"
                f"  [IO.File]::Copy({quote(peer)}, {quote(policy_path)}, $true)\n"
                "}\n"
            )
        else:
            mutation = (
                "# Trace contract: normal/read-only/ready-v1\n"
                f". {shlex.quote(str(real_resolver))}\n"
                f"if [[ -f {shlex.quote(str(root / '.provision.lock'))} ]]; then\n"
                f"  cp {shlex.quote(str(peer))} {shlex.quote(str(policy_path))}\n"
                "fi\n"
            )
        resolver.write_text(mutation, encoding="utf-8")
    if state.startswith("upstream-"):
        environment.pop("AGENT_DISPATCH_PROVISION_OFFLINE", None)
        network_record = binding_fixture["home"] / "network-record"
        bootstrap_record = binding_fixture["home"] / "bootstrap-record"
        bootstrap = tmp_path / ("bootstrap.ps1" if os.name == "nt" else "bootstrap.sh")
        url = "https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/dispatch/bootstrap." + (
            "ps1" if os.name == "nt" else "sh"
        )
        peer = tmp_path / "download-peer-policy.json"
        download_policy_path = root / "runtime-deployment.json"
        if mode_change:
            download_policy_path = mode_path
            _write(peer, {
                "schema": "copilot-extensions.installation-mode", "version": 1,
                "installationMode": {"enabled": True, "marketplaces": {}},
            })
        else:
            _policy(binding_fixture, root, arguments, "managed")
            peer.write_bytes(download_policy_path.read_bytes())
            _policy(binding_fixture, root, arguments, "standalone")
        if os.name == "nt":
            def quote(path: Path) -> str:
                return "'" + str(path).replace("'", "''") + "'"

            bootstrap.write_text(
                "param([string]$Action, [string]$InstallDir)\n"
                f"[IO.File]::WriteAllText({quote(bootstrap_record)}, "
                "(@{action=$Action; root=$InstallDir} | ConvertTo-Json -Compress))\n"
                + ("[Console]::Error.WriteLine('upstream diagnostic')\n" if state == "upstream-stderr" else "")
                + "exit 0\n",
                encoding="utf-8",
            )
            substitute = (
                "function Invoke-WebRequest {\n"
                "  param($Uri, [switch]$UseBasicParsing, $TimeoutSec, $OutFile)\n"
                f"  if ($Uri -cne '{url}') {{ throw 'unexpected bootstrap URL' }}\n"
                f"  [IO.File]::WriteAllText({quote(network_record)}, $Uri)\n"
                f"  [IO.File]::Copy({quote(bootstrap)}, $OutFile)\n"
                + (f"  [IO.File]::Copy({quote(peer)}, {quote(download_policy_path)}, $true)\n"
                   if state in ("upstream-policy-change", "upstream-mode-change") else "")
                + "}\n"
            )
            gate = root / "bin/runtime-gate.ps1"
            gate.write_text(substitute + gate.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            def quote(path: Path) -> str:
                return shlex.quote(str(path))

            bootstrap.write_text(
                '#!/bin/sh\n[ "$1" = provision ] && [ "$2" = --install-dir ] || exit 91\n'
                f'printf \'{{"action":"%s","root":"%s"}}\' "$1" "$3" > {quote(bootstrap_record)}\nexit 0\n',
                encoding="utf-8",
            )
            curl = binding_fixture["home"] / "test-bin/curl"
            curl.write_text(
                f"#!/bin/sh\n[ \"$2\" = '{url}' ] || exit 92\n"
                f'printf "%s" "$2" > {quote(network_record)}\n'
                f'cp {quote(bootstrap)} "$4"\n'
                + (f"cp {quote(peer)} {quote(download_policy_path)}\n"
                   if state in ("upstream-policy-change", "upstream-mode-change") else ""),
                encoding="utf-8",
            )
            curl.chmod(0o755)
    suffix = ".cmd" if os.name == "nt" else ""
    wrapper = (
        binding_fixture["home"] / ".local/bin" / (
            ("agent-dispatch-board" if entry == "board" else "agent-dispatch") + suffix
        ) if entry != "payload" else catalog_entry
    )
    forwarded = ["version", "argument with spaces"]
    before = _snapshot(root)
    legacy_before = _snapshot(legacy)
    home_before = snapshot_home()
    command = (
        [os.environ["COMSPEC"], "/d", "/s", "/c", str(wrapper), *forwarded]
        if os.name == "nt" else [str(wrapper), *forwarded]
    )
    if state in ("standalone-stderr", "upstream-stderr"):
        windows_ps5 = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        assert windows_ps5.is_file()
        command = [str(windows_ps5), "-NoProfile", "-File", str(wrapper.with_suffix(".ps1")), *forwarded]
    result = subprocess.run(
        command, env=environment, cwd=binding_fixture["home"],
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    after = _snapshot(root)
    legacy_after = _snapshot(legacy)
    home_after = snapshot_home()
    changed = sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
    legacy_changed = sorted(
        name for name in legacy_before.keys() | legacy_after.keys()
        if legacy_before.get(name) != legacy_after.get(name)
    )
    home_changed = sorted(
        name for name in home_before.keys() | home_after.keys()
        if home_before.get(name) != home_after.get(name)
    )
    selected_top = sorted({name.split("/", 1)[0] for name in changed})
    legacy_top = sorted({name.split("/", 1)[0] for name in legacy_changed})
    recorded_root = record.read_text(encoding="utf-8").strip() if record.exists() else None
    evidence = (
        f"entry={entry} state={state} rc={result.returncode} "
        f"selected_inventory={len(before)}->{len(after)} "
        f"selected_changes={len(changed)}:{selected_top} "
        f"legacy_inventory={len(legacy_before)}->{len(legacy_after)} "
        f"legacy_changes={len(legacy_changed)}:{legacy_top} installer_root={recorded_root}"
        f" home_changes={len(home_changed)}"
        f"\n{result.stdout}{result.stderr}"
    )
    if root != legacy:
        assert not legacy_changed, evidence
    if healthy:
        assert result.returncode == 0, evidence
        assert json.loads(result.stdout.splitlines()[-1]) == {
            "python": str(python), "arguments": forwarded,
            "entry": "board" if entry == "board" else "main", "root": str(root),
        }
        assert not record.exists(), evidence
        assert not (root / ".provision.lock").exists(), evidence
        assert not (root / ".provision-status").exists(), evidence
        assert set(changed) <= {".", "logs", "logs/boot-trace.jsonl"}, evidence
        prefix = root.relative_to(binding_fixture["home"]).as_posix()
        assert set(home_changed) <= {prefix + ("/" + name if name != "." else "") for name in changed}, evidence
    elif state.startswith("upstream-"):
        assert network_record.read_text(encoding="utf-8") == url, evidence
        assert not record.exists(), evidence
        if state in ("upstream-policy-change", "upstream-mode-change"):
            assert result.returncode == 3, evidence
            assert ("migration-required" if mode_change else "deployment-managed") in result.stderr, evidence
            assert not bootstrap_record.exists(), evidence
        else:
            assert result.returncode == 1, evidence
            assert json.loads(bootstrap_record.read_text(encoding="utf-8")) == {
                "action": "provision", "root": str(root),
            }, evidence
            if state == "upstream-stderr":
                assert "upstream diagnostic" in result.stderr, evidence
                assert "falling back" not in result.stderr, evidence
                assert (root / ".provision-status").read_text() == "failed rc=1", evidence
    elif state in ("changed-under-lock", "mode-changed-under-lock"):
        assert result.returncode == 3, evidence
        assert ("migration-required" if mode_change else "deployment-managed") in result.stderr, evidence
        assert not record.exists(), evidence
        assert not (root / ".provision-status").exists(), evidence
        assert set(changed) <= {
            ".", "runtime-deployment.json", "logs", "logs/boot-trace.jsonl", ".provision.lock",
        }, evidence
        prefix = root.relative_to(binding_fixture["home"]).as_posix()
        allowed_home = {prefix + ("/" + name if name != "." else "") for name in changed}
        if mode_change:
            allowed_home.add(mode_path.relative_to(binding_fixture["home"]).as_posix())
        assert set(home_changed) <= allowed_home, evidence
    elif state == "custom-standalone":
        assert result.returncode == 126, evidence
        assert "scoped first-use provisioning is unavailable" in result.stderr, evidence
        assert not home_changed, evidence
        assert not record.exists(), evidence
    elif policy_state == "standalone" and root == legacy:
        assert result.returncode == 37, evidence
        assert record.read_text(encoding="utf-8").strip() == str(root), evidence
        if state == "standalone-no-flock":
            assert not (root / ".provision.lock").exists(), evidence
            assert not (root / ".provision.lock.pid").exists(), evidence
            assert not (root / ".provision.reap.mutex").exists(), evidence
        else:
            assert (root / ".provision.lock").is_file(), evidence
        assert (root / "logs/boot-trace.jsonl").is_file(), evidence
        if state == "standalone-stderr":
            assert "local acquisition diagnostic" in result.stderr, evidence
            assert (root / ".provision-status").read_text() == "failed rc=37", evidence
    elif state == "cell-standalone":
        # Native Dispatch installation-cell staging is not yet supported.
        # An admitted policy cannot authorize writes through its legacy installer.
        assert result.returncode == 126, evidence
        assert "installation-cell first-use provisioning is unavailable" in result.stderr, evidence
        assert not home_changed, evidence
        assert not record.exists(), evidence
    else:
        assert not home_changed, evidence
        assert not changed, evidence
        assert not record.exists(), evidence
        assert result.returncode != 0, evidence
        if state == "old-resolver":
            assert result.returncode == 126, evidence
            assert "read-only runtime resolver is unavailable" in result.stderr, evidence
        elif state == "missing-admission":
            assert result.returncode == 126, evidence
            assert "required runtime admission support is unavailable" in result.stderr, evidence
        else:
            assert "Runtime publication refused" in result.stdout + result.stderr, evidence
            if policy_state == "managed":
                assert "deployment-managed" in result.stdout + result.stderr, evidence
