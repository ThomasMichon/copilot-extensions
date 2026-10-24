"""A valid managed cell must retain its scope through fresh bootstrap repair."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs
from test_update_engine_repair import repair_case as shared_repair_case

repair_case = shared_repair_case
pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real scoped installer/publication policy lane; not promotion-unit evidence",
)


def _bash_host():
    if os.name != "nt":
        return shutil.which("bash")
    program_files = os.environ.get("ProgramFiles")
    candidate = Path(program_files) / "Git/bin/bash.exe" if program_files else None
    return str(candidate) if candidate is not None and candidate.is_file() else None


@pytest.mark.parametrize("explicit_option", [True, False])
def test_missing_bin_option_is_not_inferred_from_an_option_value(tmp_path, explicit_option):
    shell = _bash_host()
    if shell is None:
        pytest.skip("Bash is unavailable")
    installer = (
        Path(__file__).resolve().parents[2] / "plugins/agent-worktrees/scripts/install.sh"
    ).read_text(encoding="utf-8")
    prefix = installer.split("# === command-lifecycle: admission before staging ===", 1)[0]
    entry = tmp_path / "preparse.sh"
    entry.write_text(
        prefix + '\nprintf "%s\\n" "$__aw_provision_missing_runtime"\n', encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment["COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"] = "{}"
    result = subprocess.run(
        [shell, entry.as_posix(), "update", "--install-dir", tmp_path.as_posix(),
         "--project-name", "--provision-missing-runtime",
         *(["--provision-missing-runtime"] if explicit_option else [])],
        env=environment, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("true" if explicit_option else "false")


@pytest.fixture
def paired_reader(tmp_path):
    plugin = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees"
    reader = tmp_path / "read_installation_context.py"
    shutil.copy2(plugin / "scripts" / reader.name, reader)
    sdk = tmp_path / "installation-context"
    sdk.mkdir()
    (sdk / "installation_context.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "calls = 0\n"
        "def main(arguments):\n"
        "    global calls\n"
        "    calls += 1\n"
        "    with Path(__file__).with_suffix('.calls').open('a') as record:\n"
        "        record.write(arguments[0] + '\\n')\n"
        "    print(json.dumps({'arguments': arguments, 'ordinal': calls, "
        "'isolated': sys.flags.isolated, 'no_site': sys.flags.no_site, "
        "'no_bytecode': sys.dont_write_bytecode}, ensure_ascii=False))\n"
        "    if arguments[0] == os.environ.get('PAIR_FAILED_ACTION'):\n"
        "        sys.stderr.write('reader refused caf\\u00e9\\n')\n"
        "        return 23\n"
        "    return 0\n",
        encoding="utf-8",
    )
    return reader


def _assert_paired_response(result, failed_action, reader):
    assert result.returncode == (23 if failed_action else 0), result.stderr
    response = json.loads(result.stdout)
    if failed_action:
        assert response["failedAction"] == failed_action
        documents = [json.loads(response["output"])]
        assert result.stderr == "reader refused caf\u00e9\n"
    else:
        assert set(response) == {"validate", "status"}
        documents = [json.loads(response[name]) for name in ("validate", "status")]
        assert result.stderr == ""
    for document in documents:
        action = document["arguments"][0]
        assert document["ordinal"] == (1 if action == "validate" else 2)
        assert document["isolated"] == 1
        assert document["no_site"] == 1
        assert document["no_bytecode"] is True
        values = dict(zip(document["arguments"][1::2], document["arguments"][2::2]))
        assert values == {
            "--context": "opaque caf\u00e9 context", "--durable-home": "durable",
            **({
                "--expected-plugin-id": "agent-worktrees", "--expected-payload-root": "payload",
            } if action == "validate" else {
                "--plugin-id": "agent-worktrees", "--payload-root": "payload",
                "--legacy-root": "legacy",
            }),
        }
    assert (reader.parent / "installation-context/installation_context.calls").read_text().splitlines() == (
        ["validate"] if failed_action == "validate" else ["validate", "status"]
    )


@pytest.mark.parametrize("failed_action", ["", "validate", "status"])
def test_paired_reader_calls_both_sdk_actions_and_preserves_failures(paired_reader, failed_action):
    interpreter = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    result = subprocess.run(
        [interpreter, "-I", "-S", "-B", "-X", "utf8", str(paired_reader),
         "--context", "opaque caf\u00e9 context", "--durable-home", "durable",
         "--payload-root", "payload", "--plugin-id", "agent-worktrees", "--legacy-root", "legacy"],
        env={**os.environ, "PAIR_FAILED_ACTION": failed_action},
        capture_output=True, text=True, encoding="utf-8", timeout=10, **no_window_kwargs(),
    )
    _assert_paired_response(result, failed_action, paired_reader)


@pytest.mark.skipif(os.name != "nt", reason="native Windows paired context transport")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("failed_action", ["", "validate", "status"])
def test_windows_paired_reader_retains_both_queries_and_native_exit(
    paired_reader, host, failed_action,
):
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    plugin = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees"
    installer = (plugin / "scripts/install.ps1").read_text(encoding="utf-8")
    function = "function Invoke-InstallerContextQuery" + installer.split(
        "function Invoke-InstallerContextQuery", 1,
    )[1].split("\n    $contextDurableHome =", 1)[0]
    native = str(plugin / "scripts/versioned_runtime_native.ps1").replace("'", "''")
    entry = paired_reader.with_name("query.ps1")
    entry.write_text(
        "param([string]$Interpreter, [string]$ContextValue)\n"
        "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        "$OutputEncoding = [Console]::OutputEncoding\n"
        f". '{native}'\n"
        "$BootstrapPython = $Interpreter\n"
        + function
        + "\n$query = @{Action='status'; Context=$ContextValue; "
        "DurableHome='durable'; PayloadRoot='payload'; PluginId='agent-worktrees'; LegacyRoot='legacy'}\n"
        "Invoke-InstallerContextQuery $query -ValidateStatus\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(entry),
         "-Interpreter", str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
         "-ContextValue", "opaque caf\u00e9 context"],
        env={**os.environ, "PAIR_FAILED_ACTION": failed_action},
        capture_output=True, text=True, encoding="utf-8", timeout=10, **no_window_kwargs(),
    )
    _assert_paired_response(result, failed_action, paired_reader)


@pytest.mark.parametrize(
    "scenario", ["supplied", "unsupplied", "missing-value", "empty-value", "reader-failure"],
)
def test_context_reader_retains_explicit_interpreter_and_fallback(tmp_path, scenario):
    shell = _bash_host()
    if shell is None:
        pytest.skip("Bash is unavailable")
    installer = (
        Path(__file__).resolve().parents[2] / "plugins/agent-worktrees/scripts/install.sh"
    ).read_text(encoding="utf-8")
    reader = installer.split('    __aw_context_python=""', 1)[1].split(
        '    __aw_runtime_inputs=', 1,
    )[0]
    scripts = tmp_path / "scripts"
    helpers = scripts / "installation-context"
    helpers.mkdir(parents=True)
    (helpers / "installation_context.py").write_text(
        "import json, sys\n"
        + ("sys.stderr.write('reader refused\\n'); sys.exit(23)\n"
           if scenario == "reader-failure" else
           "print(json.dumps({'arguments': sys.argv[1:], 'isolated': sys.flags.isolated, "
           "'no_bytecode': sys.dont_write_bytecode, 'no_site': sys.flags.no_site}))\n"),
        encoding="utf-8",
    )
    (helpers / "installation-context.sh").write_text(
        "printf '%s\\n' shell \"$@\"\n", encoding="utf-8",
    )
    entry = tmp_path / "query.sh"
    entry.write_text(
        "set -eu\n"
        "SCRIPT_DIR=\"$1\"; shift\n"
        "__aw_args=(\"$@\")\n"
        "__aw_shell_path() { printf '%s\\n' \"$1\"; }\n"
        "__aw_context_helper=\"$SCRIPT_DIR/installation-context/installation-context.sh\"\n"
        '__aw_context_python=""' + reader
        + "\n__aw_context_query validate --context 'opaque caf\u00e9 context'\n",
        encoding="utf-8",
    )
    arguments = [] if scenario == "unsupplied" else ["--runtime-publication-python"]
    if scenario in {"supplied", "reader-failure"}:
        arguments.append(Path(getattr(sys, "_base_executable", sys.executable)).resolve().as_posix())
    elif scenario == "empty-value":
        arguments.append("")
    result = subprocess.run(
        [shell, entry.as_posix(), scripts.as_posix(), *arguments],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
        env={**os.environ, "OSTYPE": "linux-gnu"}, **no_window_kwargs(),
    )
    if scenario in {"missing-value", "empty-value"}:
        assert result.returncode == 1
        assert "requires a value" in result.stderr
        assert not result.stdout
    elif scenario == "reader-failure":
        assert result.returncode == 23
        assert result.stderr == "reader refused\n"
        assert not result.stdout
    elif scenario == "supplied":
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "arguments": ["validate", "--context", "opaque caf\u00e9 context"],
            "isolated": 1, "no_bytecode": True, "no_site": 1,
        }
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["shell", "validate", "--context", "opaque caf\u00e9 context"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows context reader")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("scenario", ["supplied", "unsupplied", "reader-failure", "unknown-field"])
def test_windows_context_reader_preserves_arguments_and_native_exit(tmp_path, host, scenario):
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    plugin = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees"
    installer = (plugin / "scripts/install.ps1").read_text(encoding="utf-8")
    function = "function Invoke-InstallerContextQuery" + installer.split(
        "function Invoke-InstallerContextQuery", 1,
    )[1].split("\n    $contextDurableHome =", 1)[0]
    (tmp_path / "portable.py").write_text(
        "import json, sys\n"
        + ("sys.stderr.write('reader refused\\n'); sys.exit(23)\n"
           if scenario == "reader-failure" else
           "print(json.dumps({'arguments': sys.argv[1:], 'isolated': sys.flags.isolated, "
           "'no_bytecode': sys.dont_write_bytecode, 'no_site': sys.flags.no_site}, "
           "ensure_ascii=False))\n"),
        encoding="utf-8",
    )
    (tmp_path / "native.ps1").write_text(
        "param($Action, $Context, $DurableHome, $ExpectedPluginId, $ExpectedPayloadRoot, "
        "$PayloadRoot, $PluginId, $LegacyRoot)\n"
        "[ordered]@{backend='native'; arguments=$PSBoundParameters} | "
        "ConvertTo-Json -Depth 3 -Compress\nexit 0\n",
        encoding="utf-8",
    )
    native_helper = str(plugin / "scripts/versioned_runtime_native.ps1").replace("'", "''")
    entry = tmp_path / "query.ps1"
    entry.write_text(
        "param([string]$Interpreter, [string]$ContextValue)\n"
        "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        "$OutputEncoding = [Console]::OutputEncoding\n"
        f". '{native_helper}'\n"
        "$BootstrapPython = $Interpreter\n"
        "$contextHelper = Join-Path $PSScriptRoot 'native.ps1'\n"
        "$contextPortableHelper = Join-Path $PSScriptRoot 'portable.py'\n"
        + function
        + "\n$query = @{Action='validate'; Context=$ContextValue; DurableHome='durable [literal]'; "
        "ExpectedPluginId='agent-worktrees'; ExpectedPayloadRoot=($ContextValue + ' payload'); "
        "PayloadRoot='payload'; PluginId='agent-worktrees'; LegacyRoot='legacy'}\n"
        + ("$query.Extra = 'blocked'\n" if scenario == "unknown-field" else "")
        + "$global:LASTEXITCODE = 0\nInvoke-InstallerContextQuery $query\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    context = "opaque caf\u00e9 context"
    command = [shell, "-NoProfile", "-NonInteractive", "-File", str(entry),
               "-ContextValue", context]
    if scenario != "unsupplied":
        command += ["-Interpreter", str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=10,
        **no_window_kwargs(),
    )
    if scenario == "reader-failure":
        assert result.returncode == 23, result.stderr
        assert result.stderr == "reader refused\n"
        assert not result.stdout
    elif scenario == "unknown-field":
        assert result.returncode != 0
        assert "Unknown installation-context query field: Extra" in result.stderr
        assert not result.stdout
    else:
        assert result.returncode == 0, result.stderr
        document = json.loads(result.stdout)
        values = {
            "context": context, "durable-home": "durable [literal]",
            "expected-plugin-id": "agent-worktrees",
            "expected-payload-root": context + " payload", "payload-root": "payload",
            "plugin-id": "agent-worktrees", "legacy-root": "legacy",
        }
        if scenario == "supplied":
            arguments = document["arguments"]
            assert arguments[0] == "validate"
            assert dict(zip(arguments[1::2], arguments[2::2])) == {
                "--" + key: value for key, value in values.items()
            }
            assert document["isolated"] == 1
            assert document["no_bytecode"] is True
            assert document["no_site"] == 1
        else:
            assert document["backend"] == "native"
            assert document["arguments"] == {
                "Action": "validate",
                **{"".join(part.capitalize() for part in key.split("-")): value
                   for key, value in values.items()},
            }


def _activate_scope(case, monkeypatch):
    primitive = case.primitive
    environment = dict(os.environ)
    durable = case.home / ".copilot-extensions"
    stamped = primitive.stamp_context(
        payload_version="1.0.0", payload_origin="installed",
        expected_namespace_generation=0, expected_install_generation=0,
        payload_root=case.incumbent, plugin_id="agent-worktrees",
        environment=environment, durable_home=durable,
    )
    primitive.compare_and_swap_activation(
        context=stamped["installReceipt"],
        expected_marketplace_id=stamped["marketplaceId"],
        expected_plugin_id="agent-worktrees",
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"],
        expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active",
        legacy_disposition="retained-inert",
        legacy_probe={"declared": True, "result": "present", "checkedAt": "2026-01-01T00:00:00Z"},
        legacy_root=case.runtime, environment=environment, durable_home=durable,
    )
    (durable / "installation-mode.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    }), encoding="utf-8")
    context = stamped["installReceipt"]
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
    arguments = {
        "payload_root": case.incumbent, "plugin_id": "agent-worktrees",
        "legacy_root": case.runtime, "context": context, "durable_home": durable,
        "environment": dict(os.environ),
    }
    decision = primitive.resolve_runtime_deployment(**arguments)
    assert decision["status"] == "ready" and decision["actualMode"] == "namespaced"
    root = Path(decision["runtimeRoot"])
    assert root != case.runtime
    (root / "runtime-deployment.json").write_text(json.dumps({
        "schema": primitive.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1, "managed": True,
        "owner": primitive._deployment_owner(decision, arguments),
    }), encoding="utf-8")
    return root, durable, Path(context)


def test_valid_scoped_update_reaches_only_its_own_construction(
    repair_case, monkeypatch, capsys,
):
    case = repair_case
    root, _durable, _context = _activate_scope(case, monkeypatch)
    before = case.manifest.read_bytes()
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), f"rc={rc}; {capsys.readouterr().err}"
    assert case.calls
    assert str(root) in case.calls[-1][0]
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()


@pytest.mark.parametrize("source_state", [
    "present", "missing-payload", "legacy-unbound", "legacy-snapshot",
])
def test_automatic_cell_selection_is_preserved_without_an_inherited_context(
    repair_case, monkeypatch, capsys, source_state,
):
    case = repair_case
    snapshot = None
    if source_state == "legacy-snapshot":
        from test_update_engine_snapshot import _historical_snapshot

        snapshot = _historical_snapshot(case)
    root, _durable, _context = _activate_scope(case, monkeypatch)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT")
    automatic = case.primitive.resolve_runtime_deployment(
        payload_root=case.incumbent, plugin_id="agent-worktrees",
        legacy_root=case.runtime, environment=dict(os.environ),
    )
    assert automatic["actualMode"] == "namespaced" and automatic["runtimeRoot"] == str(root)
    if source_state == "missing-payload":
        case.incumbent.rename(case.incumbent.with_name("retained-payload"))
    elif source_state in {"legacy-unbound", "legacy-snapshot"}:
        from test_update_engine_repair import _legacy_publication

        _legacy_publication(case)
        if snapshot is not None:
            manifest = json.loads(case.manifest.read_text())
            manifest["source"]["path"] = str(snapshot)
            case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    before = case.manifest.read_bytes()
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), f"rc={rc}; {capsys.readouterr().err}"
    assert str(root) in case.calls[-1][0]
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()
    assert "COPILOT_EXTENSIONS_CONTEXT" not in os.environ


@pytest.mark.parametrize("damage", [
    "foreign-source", "stale-install", "deactivated", "invalid-policy", "foreign-owner",
])
@pytest.mark.parametrize("explicit_context", [True, False], ids=["explicit", "automatic"])
def test_invalid_scoped_update_never_constructs_or_falls_back(
    repair_case, monkeypatch, damage, explicit_context,
):
    case = repair_case
    root, durable, context = _activate_scope(case, monkeypatch)
    if not explicit_context:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT")
    if damage == "foreign-source":
        namespace = root.parent.parent / "namespace.json"
        value = json.loads(namespace.read_text())
        value["source"]["fingerprint"] = "sha256:" + "0" * 64
        namespace.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "stale-install":
        value = json.loads(context.read_text())
        value["generation"] += 1
        context.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "deactivated":
        activation = root / "installation-activation.json"
        value = json.loads(activation.read_text())
        value.update(mode="legacy", state="deactivated")
        activation.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "invalid-policy":
        (durable / "installation-mode.json").write_text("{invalid", encoding="utf-8")
    else:
        deployment = root / "runtime-deployment.json"
        value = json.loads(deployment.read_text())
        value["owner"]["sourceFingerprint"] = "sha256:" + "0" * 64
        deployment.write_text(json.dumps(value), encoding="utf-8")
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert not case.effect.exists()
    assert not case.calls
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()
    assert not (root / "bin").exists()


def test_deactivation_required_cell_retains_management_repair(repair_case, monkeypatch):
    case = repair_case
    root, durable, _context = _activate_scope(case, monkeypatch)
    (durable / "installation-mode.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": False, "marketplaces": {}},
    }), encoding="utf-8")
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert case.effect.is_file()
    assert str(root) in case.calls[-1][0]
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()


def test_scoped_activation_change_during_repair_is_not_admitted(repair_case, monkeypatch):
    case = repair_case
    root, _durable, _context = _activate_scope(case, monkeypatch)
    incumbent = case.update._incumbent
    reads = []

    def change_activation(source, environment, payload):
        selected = incumbent(source, environment, payload)
        reads.append(selected)
        if len(reads) == 1:
            activation = root / "installation-activation.json"
            value = json.loads(activation.read_text())
            value["generation"] += 1
            activation.write_text(json.dumps(value), encoding="utf-8")
        return selected

    monkeypatch.setattr(case.update, "_incumbent", change_activation)
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert len(reads) == 2
    assert not case.calls
    assert not case.effect.exists()
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()
