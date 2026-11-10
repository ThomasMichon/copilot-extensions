"""Actual payload first-touch controls with no Vault runtime or backend."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from .test_bootstrap_check_reconcile_always_on import _clean_env, _make_fake_plugin
from .test_bootstrap_driver import native_bootstrap
from .test_identity_payload_gate import prepare_payload, prepare_runtime, invoke, launcher
from .test_install_snapshot_ps1 import _isolated_install_env, _stage_payload

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scoped", [False, True])
def test_native_cold_contract_query_never_provisions(tmp_path, scoped):
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path, "legacy")
    (home / ".agent-vault.json").write_text('{"enabled": false}', encoding="utf-8")
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_HOME": str(runtime),
    })
    arguments = ["invocation-contract", "--json"]
    if scoped:
        arguments = ["--configuration-root", str(home), *arguments]
    result = invoke(payload, env, home, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "schema": "agent-vault.invocation", "version": 1,
        "capabilities": [], "runtime_prepared": False,
    }
    assert not runtime.exists()


def test_native_batch_reads_real_cache_without_service(tmp_path, monkeypatch):
    from agent_vault.cache import PersistentCache

    home, payload, runtime, _identity, context = prepare_payload(tmp_path, "namespaced")
    (home / "native.json").write_text(json.dumps({
        "enabled": True, "kpdb": str(home / "unused.kdbx"), "group": "Example",
    }), encoding="utf-8")
    prepare_runtime(runtime, payload)
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    cache = PersistentCache(runtime / "cache")
    assert cache.put("Example/one", "password", "synthetic \u2603")
    assert cache.put("Example/two", "username", "synthetic-user")
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "COPILOT_EXTENSIONS_CONTEXT": context,
        "AGENT_VAULT_HOME": str(runtime), "AGENT_VAULT_CONFIG": str(home / "native.json"),
        "AGENT_VAULT_CACHE": "1", "AGENT_VAULT_NO_SELFPROVISION": "1",
    })
    requests = [
        {"entry": "Example/one", "field": "password"},
        {"entry": "Example/two", "field": "username"},
        {"entry": "Example/one", "field": "password"},
    ]
    result = subprocess.run(
        [*launcher(payload), "batch-get"], input=json.dumps(requests),
        env=env, cwd=home, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = json.loads(result.stdout)["results"]
    assert [(row["entry"], row["field"]) for row in records] == [
        (row["entry"], row["field"]) for row in requests
    ]
    assert [row["value"] for row in records] == [
        "synthetic \u2603", "synthetic-user", "synthetic \u2603",
    ]
    assert all(row["ok"] is True and row["source"] == "cache" for row in records)
    assert not (runtime / "run").exists()


@pytest.mark.parametrize("environment_database", [False, True])
def test_native_payload_configuration_root_keeps_caller_paths(tmp_path, environment_database):
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path, "legacy")
    project = tmp_path / "selected repository"
    project.mkdir()
    (project / ".agent-vault.json").write_text(
        json.dumps({"enabled": True, "group": "selected"}), encoding="utf-8",
    )
    (home / ".agent-vault.json").write_text(
        json.dumps({"enabled": False, "group": "wrong"}), encoding="utf-8",
    )
    (home / "native.json").write_text(
        json.dumps({"enabled": True, "kpdb": "machine.kdbx"}), encoding="utf-8",
    )
    (project / "native.json").write_text(
        json.dumps({"enabled": False}), encoding="utf-8",
    )
    prepare_runtime(runtime, payload)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_HOME": str(runtime),
        "AGENT_VAULT_CONFIG": "native.json",
        "AGENT_VAULT_NO_SELFPROVISION": "1",
        "_AGENT_WORKTREES_VAULT_RUNTIME_GUARD": "1",
    })
    expected_database = home / "machine.kdbx"
    if environment_database:
        env["KPDB"] = "${VAULT_TEST_DATABASE}"
        env["VAULT_TEST_DATABASE"] = "${VAULT_TEST_LITERAL}/chosen.kdbx"
        env["VAULT_TEST_LITERAL"] = "must-not-expand-recursively"
        expected_database = home / "${VAULT_TEST_LITERAL}" / "chosen.kdbx"
    result = invoke(payload, env, home, "--configuration-root", str(project), "which", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    selected = json.loads(result.stdout)
    assert selected["group"] == "selected"
    assert selected["kpdb"] == str(expected_database)
    (home / "native.json").write_text("{", encoding="utf-8")
    metadata = invoke(
        payload, env, home, "--configuration-root", str(project), "invocation-contract", "--json",
    )
    assert metadata.returncode == 0, metadata.stdout + metadata.stderr
    assert json.loads(metadata.stdout)["capabilities"] == [
        "configuration-root-v1", "batch-get-v1", "ssh-key-install-pin-v1",
        "authenticated-curl-v1",
    ]
    assert not (runtime / "run").exists()


@pytest.mark.parametrize("enabled,code", [(False, 2), (True, 1)])
def test_native_cold_configuration_root_respects_scope_and_no_provisioning(tmp_path, enabled, code):
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path, "legacy")
    project = tmp_path / "selected"
    project.mkdir()
    (project / ".agent-vault.json").write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    (home / ".agent-vault.json").write_text(json.dumps({"enabled": not enabled}), encoding="utf-8")
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_HOME": str(runtime),
        "AGENT_VAULT_NO_SELFPROVISION": "1",
    })
    result = invoke(payload, env, home, "--configuration-root", str(project), "unlock")
    assert result.returncode == code, result.stdout + result.stderr
    assert ("runtime not provisioned" if enabled else "inactive") in result.stderr
    assert not runtime.exists()


def _scoped_first_use_fixture(tmp_path):
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path, "legacy")
    project = tmp_path / "selected repository"
    project.mkdir()
    (project / ".agent-vault.json").write_text('{"enabled": true}', encoding="utf-8")
    (home / "native.json").write_text('{"enabled": true}', encoding="utf-8")
    (project / "native.json").write_text('{"enabled": false}', encoding="utf-8")
    report = json.dumps({
        "status": "ready", "actualMode": "legacy", "desiredMode": "legacy",
        "legacy": {"root": str(runtime)},
        "deployment": {
            "allowPluginPublication": True,
            "path": str(runtime / "runtime-deployment.json"),
        },
    })
    receipt = tmp_path / "driver.json"
    driver = payload / "libs" / "update-driver" / "scripts"
    driver.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (payload / "scripts" / "installation-context" / "installation-context.ps1").write_text(
            "Write-Output '" + report.replace("'", "''") + "'\nexit 0\n", encoding="utf-8",
        )
        destination = str(receipt).replace("'", "''")
        (driver / "first-use.ps1").write_text(
            "param($Root,$Payload,$RuntimeRoot,$Project,[switch]$Wait)\n"
            "@{project=$Project; config=$env:AGENT_VAULT_CONFIG; "
            "no_self=$env:AGENT_VAULT_NO_SELFPROVISION; "
            "guard=$env:_AGENT_WORKTREES_VAULT_RUNTIME_GUARD} | ConvertTo-Json -Compress | "
            f"Set-Content -Encoding UTF8 -LiteralPath '{destination}'\nexit 37\n",
            encoding="utf-8",
        )
    else:
        import shlex

        (payload / "scripts" / "installation-context" / "installation-context.sh").write_text(
            "#!/bin/sh\nprintf '%s\\n' " + shlex.quote(report) + "\n", encoding="utf-8",
        )
        (driver / "first-use.sh").write_text(
            "#!/bin/sh\n"
            f"exec {shlex.quote(sys.executable)} -c "
            + shlex.quote(
                "import json,os,sys\n"
                f"with open({str(receipt)!r},'w') as stream:\n"
                " json.dump({'project':sys.argv[1], 'config':os.environ.get('AGENT_VAULT_CONFIG'), "
                "'no_self':os.environ.get('AGENT_VAULT_NO_SELFPROVISION'), "
                "'guard':os.environ.get('_AGENT_WORKTREES_VAULT_RUNTIME_GUARD')},stream)\n"
                "sys.exit(37)\n"
            )
            + ' "$4"\n',
            encoding="utf-8",
        )
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_HOME": str(runtime),
        "AGENT_VAULT_CONFIG": "native.json",
        "AGENT_VAULT_NO_SELFPROVISION": "1",
        "_AGENT_WORKTREES_VAULT_RUNTIME_GUARD": "1",
    })
    return home, payload, runtime, project, receipt, env


def test_native_scoped_first_use_retains_discovery_and_native_file_separately(tmp_path):
    home, payload, runtime, project, receipt, env = _scoped_first_use_fixture(tmp_path)
    result = invoke(payload, env, home, "--configuration-root", str(project), "unlock")
    assert result.returncode == 37, result.stdout + result.stderr
    captured = json.loads(receipt.read_text(encoding="utf-8-sig"))
    assert captured["project"] == str(project)
    assert captured["config"] == str(home / "native.json")
    assert not captured["no_self"] and not captured["guard"]
    assert not runtime.exists()


@pytest.mark.skipif(os.name != "nt", reason="drive-relative paths are Windows-specific")
def test_native_cold_scope_rejects_drive_relative_before_provisioning(tmp_path):
    home, payload, runtime, _project, receipt, env = _scoped_first_use_fixture(tmp_path)
    result = invoke(payload, env, home, "--configuration-root", home.drive + ".", "unlock")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "existing absolute directory" in result.stderr
    assert not receipt.exists()
    assert not runtime.exists()


@pytest.mark.parametrize("arguments,enabled,code,diagnostic", [
    (["identity-key", "setup", "--require-running"], True, 1, "service_unavailable"),
    (["ssh-key", "prepare", "--require-running"], True, 1, "service_unavailable"),
    (["ssh-key", "install", "--require-running"], True, 1, "service_unavailable"),
    (["stop"], False, 1, "runtime not provisioned"),
    (["lock"], False, 1, "runtime not provisioned"),
    (["installer-readiness"], False, 0, "configuration-empty"),
])
def test_native_scoped_admission_preserves_no_provisioning_commands(
    tmp_path, arguments, enabled, code, diagnostic,
):
    home, payload, runtime, project, receipt, env = _scoped_first_use_fixture(tmp_path)
    if not enabled:
        for path in (project / ".agent-vault.json", home / "native.json"):
            path.write_text('{"enabled": false}', encoding="utf-8")
    result = invoke(payload, env, home, "--configuration-root", str(project), *arguments)
    assert result.returncode == code, result.stdout + result.stderr
    assert diagnostic in result.stdout + result.stderr
    assert not receipt.exists()
    assert not runtime.exists()


@pytest.mark.parametrize("dispatcher_aware", [False, True])
@pytest.mark.parametrize("operation", ["scope", "contract"])
@pytest.mark.parametrize("prepared", [False, True])
def test_native_user_shortcut_preserves_cold_scope_and_read_only_contract(
    tmp_path, dispatcher_aware, operation, prepared,
):
    import shlex

    home, payload, runtime, project, receipt, env = _scoped_first_use_fixture(tmp_path)
    if not dispatcher_aware:
        for suffix in ("ps1", "sh"):
            gate = payload / "scripts" / f"runtime-gate.{suffix}"
            gate.write_text(
                gate.read_text(encoding="utf-8").replace(
                    "# Configuration root contract: vault-context-v1", "# prior dispatcher",
                ),
                encoding="utf-8",
            )
    local_bin = tmp_path / "local-bin"
    engine = PLUGIN.parents[1] / "libs" / "installer-engine"
    if os.name == "nt":
        host = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = (
            "$ErrorActionPreference='Stop'; . " + quote(engine / "installer-engine.ps1") + "; "
            "function Write-Ok { param([string]$Message) }; "
            "Write-SimpleBinstub -CommandName agent-vault -ModuleName agent_vault "
            f"-RuntimeRoot {quote(runtime)} -LocalBin {quote(local_bin)} "
            f"-InstallBinDir {quote(runtime / 'bin')} -SnapshotInstallerPath 'scripts\\install.ps1' "
            "-NoSelfProvisionEnv AGENT_VAULT_NO_SELFPROVISION "
            f"-ResolverPs1Source {quote(payload / 'scripts' / 'resolve-runtime.ps1')} "
            f"-ResolverShSource {quote(payload / 'scripts' / 'resolve-runtime.sh')}"
        )
        build = [host, "-NoProfile", "-NonInteractive", "-Command", command]
        launch = [host, "-NoProfile", "-NonInteractive", "-File", str(local_bin / "agent-vault.ps1")]
    else:
        values = (
            "agent-vault", "agent_vault", runtime, local_bin, runtime / "bin",
            "scripts/install.sh", "AGENT_VAULT_NO_SELFPROVISION",
            payload / "scripts" / "resolve-runtime.ps1", payload / "scripts" / "resolve-runtime.sh",
        )
        command = (
            "set -e; _ok() { :; }; _fail() { printf '%s\\n' \"$*\" >&2; return 1; }; . "
            + shlex.quote(str(engine / "installer-engine.sh"))
            + "; write_simple_binstub " + " ".join(shlex.quote(str(value)) for value in values)
        )
        build = ["bash", "-c", command]
        launch = ["bash", str(local_bin / "agent-vault")]
    built = subprocess.run(
        build, cwd=home, env=env, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert built.returncode == 0, built.stdout + built.stderr
    (runtime / "payload-dir").write_text(str(payload), encoding="utf-8")
    if prepared:
        prepare_runtime(runtime, payload)
        (project / ".agent-vault.json").write_text(
            '{"enabled": true, "group": "selected"}', encoding="utf-8",
        )
    arguments = (
        ["--configuration-root", str(project), "which", "--json"]
        if prepared and operation == "scope" else
        ["--configuration-root", str(project), "unlock"]
        if operation == "scope" else ["invocation-contract", "--json"]
    )
    result = subprocess.run(
        [*launch, *arguments], cwd=home, env=env, capture_output=True, text=True,
        timeout=90, **no_window_kwargs(),
    )
    expected = 0 if prepared else (37 if operation == "scope" else 0) if dispatcher_aware else 2
    assert result.returncode == expected, result.stdout + result.stderr
    assert receipt.exists() is (not prepared and dispatcher_aware and operation == "scope")
    if receipt.exists():
        captured = json.loads(receipt.read_text(encoding="utf-8-sig"))
        assert captured["project"] == str(project)
        assert captured["config"] == str(home / "native.json")
    if prepared and operation == "scope":
        assert json.loads(result.stdout)["group"] == "selected"
    if prepared and operation == "contract":
        assert json.loads(result.stdout)["capabilities"] == [
            "configuration-root-v1", "batch-get-v1", "ssh-key-install-pin-v1",
            "authenticated-curl-v1",
        ]
    if not prepared and dispatcher_aware and operation == "contract":
        assert json.loads(result.stdout)["runtime_prepared"] is False
    assert (runtime / "versions").exists() is prepared


def test_native_payload_explains_missing_repository_before_unlock(tmp_path):
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path, "legacy")
    (payload / "scripts" / "runtime-activation.py").write_text(
        "import json\n"
        "print(json.dumps({'state': 'failed', 'reason': 'repository-context-missing', "
        "'detail': 'fixture owner has no repository context'}))\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_HOME": str(runtime),
    })
    result = invoke(payload, env, home, "unlock")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "repository context is missing" in result.stderr
    assert "<repo> vault unlock" in result.stderr
    assert "Traceback" not in result.stderr
    assert not runtime.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows PowerShell strict-mode contract")
@pytest.mark.parametrize("reason", [None, "repository-context-missing", "startup"])
def test_native_activation_failure_retains_diagnostic_under_strict_mode(tmp_path, reason):
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path, "legacy")
    failure = {"state": "failed", "detail": "fixture invalid configuration"}
    if reason == "repository-context-missing":
        failure["reason"] = reason
    reader = payload / "scripts" / "runtime-activation.py"
    if reason == "startup":
        reader.unlink()
    else:
        reader.write_text(
            "import json\n"
            f"print(json.dumps({failure!r}))\n"
            "raise SystemExit(3)\n",
            encoding="utf-8",
        )
    script = str(payload / "scripts" / "runtime-activation.ps1").replace("'", "''")
    project = str(tmp_path).replace("'", "''")
    python = str(sys.executable).replace("'", "''")
    command = (
        "Set-StrictMode -Version 2.0; $ErrorActionPreference = 'Stop'; "
        f". '{script}'; "
        f"try {{ Get-VaultRuntimeActivation -Project '{project}' -Python '{python}'; exit 90 }} "
        "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 3 }"
    )
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    result = subprocess.run(
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=home, env=env, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 3, result.stdout + result.stderr
    if reason == "startup":
        assert "can't open file" in result.stderr
    elif reason:
        assert "repository context is missing" in result.stderr
        assert "<repo> vault unlock" in result.stderr
    else:
        assert "Reader exit: 3; state: failed" in result.stderr
        assert "fixture invalid configuration" in result.stderr
    assert not runtime.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows activation selection")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize(
    "scenario", ["old-first", "3.10-only", "launcher", "unsupported", "missing", "explicit"],
)
def test_native_cold_activation_qualifies_reader_without_installing(tmp_path, host, scenario):
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path / "cold reader", "legacy")
    configuration = home / ".agent-vault.json"
    configuration.write_bytes(b'{"enabled":false}')
    retained = configuration.read_bytes()
    old = tmp_path / "old" / "python.exe"
    alias = tmp_path / "WindowsApps" / "python.exe"
    launcher_path = tmp_path / "launcher" / "py.exe"
    physical = Path(sys._base_executable)

    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    candidates = {
        "old-first": [old, alias, physical],
        "3.10-only": [physical],
        "launcher": [],
        "unsupported": [old, alias],
        "missing": [],
        "explicit": [old],
    }[scenario]
    helper = payload / "scripts" / "versioned_runtime_native.ps1"
    helper.write_text(
        helper.read_text(encoding="utf-8") + f"""
Rename-Item Function:\\Invoke-Hidden Invoke-ActivationRealHidden
$script:activationDiscoveries=0
$script:activationReaderCalls=0
function Get-Command {{
    param($Name,[switch]$All,$CommandType,$ErrorAction)
    $script:activationDiscoveries++
    if ($Name -eq 'python3') {{
        foreach ($path in @({','.join(literal(path) for path in candidates)})) {{
            [pscustomobject]@{{Source=$path;CommandType='Application'}}
        }}
    }} elseif ($Name -eq 'py' -and {literal(scenario)} -eq 'launcher') {{
        [pscustomobject]@{{Source={literal(launcher_path)};CommandType='Application'}}
    }}
}}
function Invoke-Hidden {{
    param($FilePath,$ArgList,[ref]$ErrorOutput,[switch]$StreamOutput,[hashtable]$Environment)
    if ($StreamOutput) {{ throw 'activation entered an installer driver' }}
    $capture=@{{}}
    if ($PSBoundParameters.ContainsKey('ErrorOutput')) {{ $capture.ErrorOutput=$ErrorOutput }}
    if ($PSBoundParameters.ContainsKey('Environment')) {{ $capture.Environment=$Environment }}
    if ($FilePath -eq {literal(launcher_path)}) {{
        $global:LASTEXITCODE=1
        if ($ArgList[0] -eq '-3.10') {{
            $global:LASTEXITCODE=0
            return (ConvertTo-Json -Compress {literal(physical)})
        }}
        return
    }}
    if ($ArgList -contains '-c') {{
        $version=if ($FilePath -eq {literal(old)}) {{ '(3,9)' }} else {{ '(3,10)' }}
        $prefix='import sys;sys.version_info=' + $version + ';'
        return Invoke-ActivationRealHidden -FilePath {literal(physical)} -ArgList @(
            '-I','-S','-B','-c',($prefix+$ArgList[[array]::IndexOf($ArgList,'-c')+1]),
            $ArgList[-2],$ArgList[-1]
        ) @capture
    }}
    $script:activationReaderCalls++
    Invoke-ActivationRealHidden -FilePath $FilePath -ArgList $ArgList @capture
}}
""",
        encoding="utf-8",
    )
    command = f"""
Set-StrictMode -Version 2
$ErrorActionPreference='Stop'
$env:OS='Windows_NT'
$env:LOCALAPPDATA={literal(tmp_path / 'no-installations')}
$env:ProgramFiles=$env:LOCALAPPDATA
${{env:ProgramFiles(x86)}}=$env:LOCALAPPDATA
$before=$env:PATH
. {literal(payload / 'scripts' / 'runtime-activation.ps1')}
$failure=$null
$activation=$null
try {{
    $activation=Get-VaultRuntimeActivation -Project {literal(home)} -RuntimeRoot {literal(runtime)} {
        '-Python ' + literal(physical) if scenario == 'explicit' else ''
    }
}} catch {{ $failure=$_.Exception.Message }}
@{{activation=$activation;failure=$failure;pathUnchanged=($before -ceq $env:PATH);
   discoveries=$script:activationDiscoveries;readerCalls=$script:activationReaderCalls}} |
    ConvertTo-Json -Compress
"""
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        env=env, cwd=home, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    assert details["pathUnchanged"] is True
    if scenario in ("unsupported", "missing"):
        assert details["activation"] is None
        assert "Python 3.10+ reader" in details["failure"]
        assert details["readerCalls"] == 0
    else:
        assert details["failure"] is None
        assert details["activation"]["state"] == "configuration-empty"
        assert details["readerCalls"] == 1
    if scenario == "explicit":
        assert details["discoveries"] == 0
    assert configuration.read_bytes() == retained
    assert not runtime.exists()
    assert not (home / ".copilot-extensions").exists()


@pytest.mark.skipif(os.name == "nt", reason="native POSIX activation selection")
@pytest.mark.parametrize("scenario", ["old-first", "unsupported", "missing", "explicit"])
def test_posix_cold_activation_qualifies_reader_without_installing(tmp_path, scenario):
    import shlex

    host = shutil.which("bash")
    if host is None:
        pytest.skip("bash unavailable")
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path / "cold reader", "legacy")
    configuration = home / ".agent-vault.json"
    configuration.write_bytes(b'{"enabled":false}')
    retained = configuration.read_bytes()
    old, supported = tmp_path / "old", tmp_path / "supported"
    old.mkdir()
    supported.mkdir()
    (old / "python3").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (old / "python3").chmod(0o700)
    (supported / "python3").write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n', encoding="utf-8",
    )
    (supported / "python3").chmod(0o700)
    paths = {
        "old-first": [old, supported],
        "unsupported": [old],
        "missing": [],
        "explicit": [old],
    }[scenario]
    # dirname is the shell helper's ordinary prerequisite, not a Python fallback.
    utilities = tmp_path / "utilities"
    utilities.mkdir()
    dirname = shutil.which("dirname")
    if dirname is None:
        pytest.skip("dirname unavailable")
    (utilities / "dirname").symlink_to(dirname)
    command = (
        ". " + shlex.quote(str(payload / "scripts" / "runtime-activation.sh"))
        + "; before=$PATH; vault_runtime_activation " + shlex.quote(str(home))
        + " " + shlex.quote(sys.executable if scenario == "explicit" else "")
        + '; code=$?; test "$PATH" = "$before" || exit 91; exit "$code"'
    )
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "PATH": os.pathsep.join(map(str, [*paths, utilities])),
        "AGENT_VAULT_HOME": str(runtime),
    })
    result = subprocess.run(
        [host, "-c", command], cwd=home, env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == (3 if scenario in ("unsupported", "missing") else 2), result.stderr
    details = json.loads(result.stdout)
    assert details["state"] == ("failed" if result.returncode == 3 else "configuration-empty")
    if result.returncode == 3:
        assert "Python 3.10+ reader" in details["detail"]
    assert configuration.read_bytes() == retained
    assert not runtime.exists()
    assert not (home / ".copilot-extensions").exists()


@pytest.mark.skipif(os.name == "nt", reason="native POSIX installer argument validation")
@pytest.mark.parametrize("configuration", [False, True, "malformed"])
@pytest.mark.parametrize(
    "arguments,code,message",
    [(["--not-a-valid-option"], 2, "Unknown option"),
     (["--install-dir"], 1, "--install-dir requires a directory")],
)
def test_native_installer_rejects_arguments_before_activation(
    tmp_path, configuration, arguments, code, message,
):
    home, payload, _runtime, _identity, _context = prepare_payload(tmp_path, "legacy")
    (tmp_path / ".agent-vault.json").write_text(
        "{" if configuration == "malformed" else json.dumps({"enabled": configuration}),
        encoding="utf-8",
    )
    script = payload / "scripts" / "install.sh"
    shutil.copyfile(PLUGIN / "scripts" / "install.sh", script)
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    result = subprocess.run(
        ["bash", str(script), "update", *arguments], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert message in result.stderr
    assert not (home / ".agent-vault").exists()
    assert not (home / ".copilot-extensions").exists()


@pytest.mark.skipif(os.name != "nt", reason="real Windows cold activation entry")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_actual_cold_activation_uses_path_python_without_runtime(tmp_path, host):
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path / "actual cold reader", "legacy")
    configuration = home / ".agent-vault.json"
    configuration.write_bytes(b'{"enabled":false}')
    console_receipt = home / "reader-console.json"
    reader = payload / "scripts" / "runtime-activation.py"
    reader.write_text(
        reader.read_text(encoding="utf-8").replace(
            "import argparse\n",
            "import argparse\nimport ctypes\nimport json\nfrom pathlib import Path\n"
            "_kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
            "_kernel.GetConsoleWindow.restype = ctypes.c_void_p\n"
            f"Path({str(console_receipt)!r}).write_text(json.dumps("
            "{'console_window': _kernel.GetConsoleWindow()}), encoding='utf-8')\n",
            1,
        ),
        encoding="utf-8",
    )
    script = str(payload / "scripts" / "runtime-activation.ps1").replace("'", "''")
    project = str(home).replace("'", "''")
    selected_root = str(runtime).replace("'", "''")
    command = (
        "Set-StrictMode -Version 2; $ErrorActionPreference='Stop'; $before=$env:PATH; "
        "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; "
        "public static class ConsoleProbe { [DllImport(\"kernel32.dll\")] "
        "public static extern IntPtr GetConsoleWindow(); }'; "
        "if ([ConsoleProbe]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 92 }; "
        f". '{script}'; Get-VaultRuntimeActivation -Project '{project}' -RuntimeRoot '{selected_root}' "
        "| ConvertTo-Json -Compress; if ($before -cne $env:PATH) { exit 91 }"
    )
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "PATH": str(Path(sys._base_executable).parent),
    })
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        env=env, cwd=home, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "configuration-empty"
    assert json.loads(console_receipt.read_text(encoding="utf-8"))["console_window"] is None
    assert configuration.read_bytes() == b'{"enabled":false}'
    assert not runtime.exists()
    assert not (home / ".copilot-extensions").exists()


@pytest.mark.parametrize("configuration", [None, False, "invalid", "malformed"])
@pytest.mark.parametrize("entry", ["bootstrap", "readiness", "update"])
def test_native_inactive_or_invalid_first_touch_never_installs(tmp_path, configuration, entry):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    _, payload, _, _, _ = prepare_payload(tmp_path, "legacy")
    (tmp_path / ".agent-vault.json").unlink()
    if configuration is not None:
        (project / ".agent-vault.json").write_text(
            "{" if configuration == "malformed"
            else json.dumps({"enabled": configuration}), encoding="utf-8",
        )
    existing = home / ".agent-vault" / "retained-custody.json"
    if configuration is False:
        existing.parent.mkdir()
        existing.write_bytes(b'{"fixture":"retained service and database custody"}')
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    suffix = "ps1" if os.name == "nt" else "sh"
    if entry == "bootstrap":
        argv = native_bootstrap(payload, project, project)
    else:
        filename = ("installer-readiness." if entry == "readiness" else "install.") + suffix
        shutil.copyfile(PLUGIN / "scripts" / filename, payload / "scripts" / filename)
        if os.name == "nt":
            argv = [
                str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                "-NoProfile", "-NonInteractive", "-File", str(payload / "scripts" / filename),
            ]
        else:
            argv = ["bash", str(payload / "scripts" / filename)]
        if entry == "update":
            argv.append("update")
    result = subprocess.run(
        argv, cwd=project, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    invalid = configuration in ("invalid", "malformed")
    assert (result.returncode != 0) is invalid, result.stdout + result.stderr
    if entry == "readiness":
        assert json.loads(result.stdout)["state"] == ("failed" if invalid else "configuration-empty")
    elif entry == "bootstrap":
        assert json.loads(result.stdout) == {}
    if configuration is False:
        assert existing.read_bytes() == b'{"fixture":"retained service and database custody"}'
        assert list(existing.parent.iterdir()) == [existing]
    else:
        assert not (home / ".agent-vault").exists()
    assert not (home / ".copilot-extensions").exists()
    assert not (project / "handoff").exists()
    assert not (payload / "scripts" / "inline-entered").exists()


@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_actual_command_first_touch_inactive_without_runtime(tmp_path, mode):
    home, payload, runtime, _identity, context = prepare_payload(tmp_path, mode)
    (tmp_path / ".agent-vault.json").unlink(missing_ok=True)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_HOME": str(runtime),
    })
    if context:
        env["COPILOT_EXTENSIONS_CONTEXT"] = context
    result = invoke(payload, env, tmp_path, "get", "Example", "password")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "inactive" in result.stderr
    readiness = invoke(payload, env, tmp_path, "installer-readiness")
    assert readiness.returncode == 0, readiness.stderr
    assert json.loads(readiness.stdout)["state"] == "configuration-empty"
    assert not (runtime / "versions").exists()
    assert not (runtime / ".provision-status").exists()


def test_actual_payload_reader_uses_bundled_sdk_without_site_packages(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, "agent-vault")
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(payload / "scripts" / "runtime-activation.py"),
         "--cwd", str(project)], env=env, cwd=project, capture_output=True,
        text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "configured"


@pytest.mark.parametrize("layout", ["retained", "application", "deployed", "mixed-owner", "service-cwd"])
def test_installed_reader_resolves_owner_without_payload_or_source(tmp_path, layout):
    import venv

    import agent_procutil
    import configuration_context
    import configuration_layers

    home, project, knowledge, staging = (
        tmp_path / name for name in ("home", "project", "knowledge", "stage")
    )
    for path in (home, project, knowledge, staging):
        path.mkdir()
    slots = {
        name: home / ("." + name) / "versions" / "1.0.0"
        for name in ("agent-vault", "agent-worktrees")
    }
    packages = {}
    environments = {}
    for name, slot in slots.items():
        nested = (
            name == "agent-vault" and layout == "application"
            or name == "agent-worktrees" and layout == "mixed-owner"
        )
        environment = slot / ".venv" if nested else slot
        environments[name] = environment
        venv.EnvBuilder(with_pip=False).create(environment)
        packages[name] = (
            environment / "Lib" / "site-packages" if os.name == "nt"
            else next((environment / "lib").glob("python*/site-packages"))
        )
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": "1.0.0", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
        }), encoding="utf-8")
        (slot.parent.parent / "current-version").write_text("1.0.0", encoding="utf-8")
    installed = packages["agent-vault"] / "agent_vault"
    installed.mkdir()
    for name in ("__init__.py", "config.py", "configuration.py", "configuration_scope.py", "invocation.py", "_peer_launch.py"):
        shutil.copyfile(PLUGIN / "src" / "agent_vault" / name, installed / name)
    if layout == "service-cwd":
        shutil.copytree(
            PLUGIN / "src" / "agent_vault", installed, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    for package in (agent_procutil, configuration_context, configuration_layers):
        shutil.copytree(
            Path(package.__file__).parent, packages["agent-vault"] / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    if layout == "service-cwd":
        import importlib

        for name in ("zdd", "single_instance_lease", "update_driver"):
            package = importlib.import_module(name)
            shutil.copytree(
                Path(package.__file__).parent, packages["agent-vault"] / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
    if layout not in ("deployed", "mixed-owner"):
        bootstrap = slots["agent-vault"] / ".runtime-bootstrap"
        bootstrap.mkdir()
        for source in (PLUGIN / "scripts").glob("versioned_runtime*.py"):
            shutil.copyfile(source, bootstrap / source.name)
    else:
        deployed = home / ".agent-vault" / "bin"
        deployed.mkdir()
        for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
            shutil.copyfile(PLUGIN / "scripts" / name, deployed / name)
    owner = packages["agent-worktrees"] / "agent_worktrees"
    owner.mkdir()
    (owner / "__init__.py").write_text("", encoding="utf-8")
    (owner / "__main__.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:] == ['state-root', '--configuration', '--json']\n"
        "report = json.loads(Path(__file__).with_name('report.json').read_text())\n"
        "assert Path.cwd().is_relative_to(Path(report['context']['base_root']))\n"
        "print(json.dumps(report))\n",
        encoding="utf-8",
    )
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "installed-reader-fixture", "execution_machine": "fixture",
            "installation_cell": "legacy", "binding": "bound",
            "base_root": str(project), "knowledge_root": str(knowledge),
        },
    }
    (owner / "report.json").write_text(json.dumps(report), encoding="utf-8")
    python = environments["agent-vault"] / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    for name in tuple(env):
        if name.startswith(("AGENT_VAULT", "VAULT_")) or name in (
            "AGENT_RT_ROOT", "AGENT_CONFIGURATION_CONTEXT", "PYTHONPATH", "PYTHONHOME",
            "COPILOT_EXTENSIONS_CONTEXT", "KPDB",
        ):
            env.pop(name)
    env["AGENT_VAULT_CONFIG"] = str(home / "absent-global.json")
    caller = project / "local" if layout == "service-cwd" else project
    if caller != project:
        caller.mkdir()
    reader = (
        "import json, sys\nfrom pathlib import Path\n"
        "from agent_vault.configuration import runtime_enabled\n"
        "from agent_vault.config import resolve_context\n"
        "assert Path(sys.prefix) == Path(sys.argv[2])\n"
        "enabled = runtime_enabled(sys.argv[1])\n"
        "selected = resolve_context(sys.argv[1], strict=True)\n"
        "print(json.dumps({'enabled': enabled, 'group': selected.group}))\n"
    )
    if layout == "service-cwd":
        reader = (
            "import json, os, sys\nfrom pathlib import Path\n"
            "from agent_vault import service, config, configuration, launch\n"
            "state = Path.cwd()\nproject = sys.argv[1]\n"
            "captured = {}\n"
            "def capture(argv, **kwargs):\n"
            " captured.update(argv=argv, **kwargs)\n"
            "launch.spawn_daemon = capture\n"
            "os.chdir(project)\n"
            "launch.spawn_service(['--foreground'])\n"
            "assert captured['cwd'] == state\n"
            "os.chdir(state)\n"
            "def probe(args):\n"
            " assert Path.cwd() == state\n"
            " selected = config.resolve_context(strict=True)\n"
            " print(json.dumps({'enabled':configuration.runtime_enabled(), 'group':selected.group}))\n"
            "service._run_resident = probe\n"
            "sys.argv = ['agent_vault.service', *captured['argv'][6:]]\n"
            "service.main()\n"
        )
    for enabled in (True, False, None, "invalid"):
        config_path = (
            caller / ".agent-vault.json" if layout == "service-cwd"
            else knowledge / ".agent-vault.json"
        )
        if enabled is None:
            config_path.unlink()
        else:
            config_path.write_text(json.dumps({
                "enabled": enabled, "group": "portable-group",
            }), encoding="utf-8")
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", reader, str(caller), str(environments["agent-vault"])],
            cwd=slots["agent-vault"].parent.parent if layout == "service-cwd" else staging,
            env=env, capture_output=True, text=True, timeout=20,
            **no_window_kwargs(),
        )
        if enabled == "invalid":
            assert result.returncode != 0
            assert "enabled must be a boolean" in result.stderr
        elif layout == "service-cwd" and enabled is not True:
            assert result.returncode == 2, result.stdout + result.stderr
            assert "inactive" in result.stderr
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert json.loads(result.stdout) == {
                "enabled": enabled is True,
                "group": "portable-group" if enabled is not None else None,
            }
    assert not (home / ".agent-vault" / "scripts").exists()
    assert not (slots["agent-vault"] / "Lib" / "scripts").exists()
    assert not (installed.parents[1] / "scripts").exists()


@pytest.mark.parametrize("layout", ["namespaced", "legacy"])
def test_cold_namespaced_capture_and_legacy_capture_keep_slots(tmp_path, layout):
    import venv
    import configuration_context
    import configuration_layers
    from .test_identity_payload_gate import load_module

    home, payload, runtime, _identity, context = prepare_payload(tmp_path, layout)
    peer_payload = payload.parent / "agent-worktrees"
    (peer_payload / "scripts").mkdir(parents=True)
    source_peer = PLUGIN.parent / "agent-worktrees"
    shutil.copytree(
        source_peer / "scripts" / "installation-context",
        peer_payload / "scripts" / "installation-context",
    )
    for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
        shutil.copyfile(source_peer / "scripts" / name, peer_payload / "scripts" / name)
    shutil.copyfile(source_peer / "plugin.json", peer_payload / "plugin.json")
    peer = home / ".agent-worktrees"
    if context:
        primitive = load_module(
            payload / "scripts" / "installation-context" / "installation_context.py",
            "cold_capture_installation",
        )
        own = json.loads(Path(context).read_text(encoding="utf-8"))
        namespace = json.loads(Path(own["namespaceReceipt"]).read_text(encoding="utf-8"))
        environment = {"HOME": str(home), "USERPROFILE": str(home)}
        stamped = primitive.stamp_context(
            payload_version="1.0.0", payload_origin="installed",
            expected_namespace_generation=namespace["generation"], expected_install_generation=0,
            payload_root=peer_payload, plugin_id="agent-worktrees", environment=environment,
            copilot_home=home / ".copilot", durable_home=home / ".copilot-extensions",
        )
        primitive.compare_and_swap_activation(
            context=stamped["installReceipt"], expected_marketplace_id=stamped["marketplaceId"],
            expected_plugin_id="agent-worktrees",
            expected_namespace_generation=stamped["namespaceGeneration"],
            expected_install_generation=stamped["generation"], expected_activation_generation=0,
            activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
            legacy_root=home / ".agent-worktrees", environment=environment,
            durable_home=home / ".copilot-extensions",
        )
        peer = Path(stamped["pluginRoot"])
    slots = {}
    for name, owner in (("agent_vault", runtime), ("agent_worktrees", peer)):
        slot = owner / "versions" / "1.0.0"
        venv.EnvBuilder(with_pip=False).create(slot)
        site = (
            slot / "Lib" / "site-packages" if os.name == "nt"
            else next((slot / "lib").glob("python*/site-packages"))
        )
        package = site / name
        package.mkdir()
        if name == "agent_vault":
            if not context:
                bootstrap = slot / ".runtime-bootstrap"
                bootstrap.mkdir()
                for source in (PLUGIN / "scripts").glob("versioned_runtime*.py"):
                    shutil.copyfile(source, bootstrap / source.name)
            for filename in ("__init__.py", "config.py", "configuration.py",
                             "configuration_scope.py", "invocation.py", "_peer_launch.py"):
                shutil.copyfile(PLUGIN / "src" / name / filename, package / filename)
            for source in (PLUGIN / "src" / name).glob("_installation_context*.py"):
                shutil.copyfile(source, package / source.name)
            for dependency in (configuration_context, configuration_layers):
                shutil.copytree(
                    Path(dependency.__file__).parent, site / dependency.__name__,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
        else:
            (package / "__init__.py").write_text("", encoding="utf-8")
            report = {
                "schema": "agent-worktrees.configuration", "version": 1,
                "context": {
                    "repository_id": "cold-capture", "execution_machine": "fixture",
                    "installation_cell": str(peer.parent.parent) if context else "legacy",
                    "binding": "unbound",
                    "base_root": str(tmp_path),
                },
            }
            (package / "__main__.py").write_text(
                "import json,sys\n"
                "assert sys.argv[1:] == ['state-root','--configuration','--json']\n"
                "print(" + repr(json.dumps(report)) + ")\n", encoding="utf-8",
            )
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": "1.0.0", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
        }), encoding="utf-8")
        (owner / "current-version").write_text("1.0.0", encoding="utf-8")
        slots[name] = slot
    before = {
        path: path.read_bytes()
        for slot in slots.values() for path in slot.rglob("*") if path.is_file()
    }
    python = slots["agent_vault"] / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home), "AGENT_RT_ROOT": str(runtime),
        "AGENT_VAULT_HOME": str(runtime), "COPILOT_EXTENSIONS_CONTEXT": context,
    })
    result = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import json;from agent_vault.configuration import service_configuration_snapshot;"
         "print(json.dumps(service_configuration_snapshot()))"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["root"] == str(tmp_path)
    assert {
        path: path.read_bytes()
        for slot in slots.values() for path in slot.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("shell", ["python", "powershell", "pwsh"])
def test_invalid_activation_retains_safe_reader_diagnostic(tmp_path, shell):
    if shell != "python" and (os.name != "nt" or shutil.which(shell) is None):
        pytest.skip("native Windows PowerShell reader")
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, "agent-vault")
    (project / ".agent-vault.json").write_text(
        '{"enabled":"private-configuration-sentinel"}', encoding="utf-8",
    )
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    if shell == "python":
        argv = [
            sys.executable, "-I", "-B", str(payload / "scripts" / "runtime-activation.py"),
            "--cwd", str(project),
        ]
    else:
        entry = tmp_path / "read-activation.ps1"
        entry.write_text(
            "param($Reader, $Project, $Python)\n"
            "$ErrorActionPreference = 'Stop'\n"
            ". $Reader\n"
            "Get-VaultRuntimeActivation -Project $Project -Python $Python\n",
            encoding="utf-8",
        )
        argv = [
            shutil.which(shell), "-NoProfile", "-NonInteractive", "-File", str(entry),
            str(payload / "scripts" / "runtime-activation.ps1"), str(project), sys.executable,
        ]
    result = subprocess.run(
        argv, cwd=project, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    assert result.returncode != 0
    diagnostic = json.loads(result.stdout)["detail"] if shell == "python" else result.stderr
    assert "Reader failure: " in diagnostic, result.stdout + result.stderr
    assert "_validate_config_data:" in diagnostic
    assert "private-configuration-sentinel" not in diagnostic
    assert str(project) not in diagnostic
    assert not (home / ".agent-vault").exists()


def test_relative_config_reader_retains_project_after_staging_to_home(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, "agent-vault")
    (tmp_path / ".agent-vault.json").unlink()
    (project / "activation.json").write_text('{"enabled":true}', encoding="utf-8")
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "AGENT_VAULT_CONFIG": "activation.json",
    })
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(payload / "scripts" / "runtime-activation.py"),
         "--cwd", str(project)], env=env, cwd=home, capture_output=True,
        text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "configured"


@pytest.mark.skipif(os.name != "nt", reason="native Windows marketplace self-staging")
@pytest.mark.timeout(90)
def test_relative_config_survives_actual_marketplace_self_staging(tmp_path):
    import runpy

    prepared = _stage_payload(tmp_path / "prepared")
    previous_path = sys.path[:]
    try:
        release = runpy.run_path(str(PLUGIN.parents[1] / "tools" / "materialize_main.py"))
        changes = release["materialize_installer_engine_ref_into"](
            source_consumer_dir=PLUGIN, dest_consumer_dir=prepared,
            canonical_root=PLUGIN.parents[1],
        )
    finally:
        sys.path[:] = previous_path
    assert not any(line.startswith("SKIP ") for line in changes), changes
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    env = _isolated_install_env(home)
    payload = home / ".copilot" / "installed-plugins" / "example" / "agent-vault"
    shutil.copytree(prepared, payload)
    (home / ".copilot" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }), encoding="utf-8")
    (project / "activation.json").write_text('{"enabled":true}', encoding="utf-8")
    env.update(AGENT_VAULT_CONFIG="activation.json", COPILOT_PLUGIN_ROOT=str(payload))
    env.pop("COPILOT_PLUGIN_STAGED_FROM", None)
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-File",
         str(payload / "scripts" / "install.ps1"), "-Action", "stamp",
         "-InstallDir", str(home / ".agent-vault")],
        cwd=project, env=env, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    runtime = home / ".agent-vault"
    assert (runtime / "payload-dir").is_file(), result.stdout + result.stderr
    assert not (runtime / "current-version").exists()
    assert not (runtime / "service.ps1").exists()


@pytest.mark.skipif(os.name != "nt", reason="native installed-payload schedule staging")
@pytest.mark.parametrize("selection", ["repo-opt-in", "invalid-config", "unavailable-project"])
def test_schedule_staging_retains_project_for_activation_and_service_snapshot(tmp_path, selection):
    from .test_payload_invocation import _installer_function

    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    env = _isolated_install_env(home)
    env.pop("AGENT_VAULT_CONFIG", None)
    env.pop("COPILOT_PLUGIN_STAGED_FROM", None)
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    (project / ".agent-vault.json").write_text(
        '{"enabled":true}' if selection != "invalid-config" else '{"enabled":"invalid"}',
        encoding="utf-8",
    )
    if selection == "unavailable-project":
        env["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] = str(project / "unavailable")
    prepared = _make_fake_plugin(tmp_path / "prepared", "agent-vault")
    (tmp_path / "prepared" / ".agent-vault.json").unlink()
    shutil.copyfile(
        PLUGIN / "scripts" / "versioned_runtime_native.ps1",
        prepared / "scripts" / "versioned_runtime_native.ps1",
    )
    payload = home / ".copilot" / "installed-plugins" / "example" / "agent-vault"
    shutil.copytree(prepared, payload)
    runtime = home / ".agent-vault"
    (runtime / "bin").mkdir(parents=True)
    interpreter = runtime / "snapshot.ps1"
    interpreter.write_text(
        "$project=$args[-1]; $global:LASTEXITCODE=0\n"
        "@{root=$project; config=(Join-Path $project '.agent-vault.json')} | ConvertTo-Json -Compress\n",
        encoding="utf-8-sig",
    )
    (runtime / "bin" / "resolve-runtime.ps1").write_text(
        f"$AgentRtPy='{interpreter}'\n", encoding="utf-8-sig",
    )
    report = tmp_path / "schedule-report.json"
    env["FIXTURE_SCHEDULE_REPORT"] = str(report)
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")
    prefix = source[:source.index("# === install-contract:v4 smoke seam")]
    # Keep the actual entry, staging and activation reader; replace only the
    # post-stage installation setup and external task/interpreter boundaries.
    harness = """
$TaskName='fixture'; $TaskLauncher=Join-Path $InstallDir 'service.ps1'
$RunDir=Join-Path $InstallDir 'run'; $SocketPath='fixture'; $PipePath='fixture'
$PidFile='fixture'; $LogFile='fixture'; $installationId=''; $LinkPython=''
$script:registered=0
function Write-Ok { param($m) }; function Write-Skip { param($m) }
function Write-Warn { param($m) throw $m }; function Write-Fail { param($m) throw $m }
function Get-WindowsScheduledTaskPolicy { 'enabled' }
function Get-SchedulingPolicyTask { param($Policy) $null }
function Start-ScheduledTask { throw 'task-only must not start task' }
function powershell.exe { throw 'task-only must not ensure service' }
function Stop-ScheduledTask { throw 'task-only must not stop task' }
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) @{} }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User) [pscustomobject]@{Delay=''} }
function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,
    [switch]$StartWhenAvailable,$ExecutionTimeLimit,$MultipleInstances,$RestartCount,$RestartInterval) @{} }
function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Description) $script:registered++ }
"""
    (payload / "scripts" / "install.ps1").write_text(
        prefix + harness + _installer_function("Register-AgentVaultTask")
        + "\nRegister-AgentVaultTask\n"
        + "@{project=$env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT; registered=$script:registered;"
        " cwd=(Get-Location).ProviderPath; staged=$env:COPILOT_PLUGIN_STAGED_FROM} | ConvertTo-Json -Compress |"
        " Set-Content -LiteralPath $env:FIXTURE_SCHEDULE_REPORT\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File",
         str(payload / "scripts" / "install.ps1"), "schedule", "-InstallDir", str(runtime)],
        cwd=project, env=env, capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    if selection != "repo-opt-in":
        assert result.returncode != 0, result.stdout + result.stderr
        assert not report.exists()
        assert not (runtime / "service.ps1").exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(report.read_text(encoding="utf-8-sig"))
    assert actual["registered"] == 1
    assert Path(actual["project"]) == project
    assert Path(actual["cwd"]) == home
    assert Path(actual["staged"]) == payload
    launcher = (runtime / "service.ps1").read_text(encoding="utf-8-sig")
    assert f"$env:AGENT_VAULT_CONFIG = '{project / '.agent-vault.json'}'" in launcher
    assert str(project) in launcher


def test_automatic_cell_selection_supplies_qualified_activation_owner(tmp_path):
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path, "namespaced")
    (tmp_path / ".agent-vault.json").unlink()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / ".agent-vault.json").write_text('{"enabled":true}', encoding="utf-8")
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "activation-fixture",
            "execution_machine": "fixture",
            "installation_cell": str(runtime.parent.parent),
            "binding": "bound", "base_root": str(tmp_path),
            "knowledge_root": str(knowledge),
        },
    }
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_NO_SELFPROVISION": "1",
        "AGENT_CONFIGURATION_CONTEXT": json.dumps(report),
    })
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_VAULT_HOME", "AGENT_RT_ROOT"):
        env.pop(key, None)
    result = invoke(payload, env, tmp_path, "get", "Example", "password")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "runtime not provisioned" in result.stderr
    suffix = "ps1" if os.name == "nt" else "sh"
    script = payload / "scripts" / ("installer-readiness." + suffix)
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoProfile", "-NonInteractive", "-File", str(script)]
        if os.name == "nt" else ["bash", str(script)]
    )
    readiness = subprocess.run(
        argv, cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=30, **no_window_kwargs(),
    )
    assert readiness.returncode == 1, readiness.stdout + readiness.stderr
    assert json.loads(readiness.stdout)["state"] == "failed"
    assert "runtime is not installed" in json.loads(readiness.stdout)["detail"]
    assert not (runtime / "versions").exists()
