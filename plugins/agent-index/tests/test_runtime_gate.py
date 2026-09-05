from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN / "src"
DEFAULT_FAKE_RUNTIME = (
    "import json, os, sys\n"
    "command = sys.argv[1] if len(sys.argv) > 1 else 'status'\n"
    "role = os.environ.get('AGENT_INDEX_ROLE')\n"
    "if command == 'role':\n"
    "    if '--json' in sys.argv:\n"
    "        print(json.dumps({'role': role, 'setup_required': False, "
    "'state': 'ready'}))\n"
    "    else:\n"
    "        print(role or 'unconfigured')\n"
    "elif command == 'status':\n"
    "    print(json.dumps({'schema':'agent-index.lifecycle',"
    "'schema_version':1,'version':'9.9.9','plugin':'agent-index',"
    "'state':'setup_required','setup_required':True,'configured':False,"
    "'role':None,'running':False,'runtime':{'state':'ready'}}))\n"
    "elif command in ('version', '--version'):\n"
    "    print('9.9.9')\n"
)


def test_installer_readiness_uses_supported_update_arguments() -> None:
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    readiness = json.loads(
        (PLUGIN / "installer-readiness.json").read_text(encoding="utf-8")
    )
    runtime_module = next(
        (
            module
            for module in readiness["modules"]
            if module["id"] == "agent-index/runtime"
        ),
        None,
    )
    assert runtime_module is not None, (
        "installer-readiness must declare agent-index/runtime"
    )
    installer = runtime_module["installer"]

    assert "zeroDowntimeUpdate" not in manifest
    assert installer["windows"]["arguments"] == ["update"]
    assert installer["linux"]["arguments"] == ["update"]
    assert installer["wsl"]["arguments"] == ["update"]


def test_runtime_gates_serialize_provisioning() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(
        encoding="utf-8"
    )
    assert 'flock 9' in posix
    assert 'mkdir "$PROVISION_LOCK_DIR"' in posix
    assert "[IO.File]::Open(" in powershell
    assert "[IO.FileShare]::None" in powershell


def test_posix_runtime_gate_supports_stock_macos_bash() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    assert "mapfile" not in posix
    assert "while IFS= read -r field; do" in posix


def test_runtime_gates_use_validated_lock_reentry() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(
        encoding="utf-8"
    )

    assert "lockReentry" in posix
    assert "lockReentry" in powershell
    assert "AGENT_INDEX_CELL_TRANSACTION_TOKEN:-" not in posix.split(
        "AGENT_RT_LOCK_REENTRY=", 1
    )[1].split("_runtime_state()", 1)[0]


def test_namespaced_session_ensure_is_background_coalesced() -> None:
    posix_gate = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(
        encoding="utf-8"
    )
    powershell_gate = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(
        encoding="utf-8"
    )
    posix_hook = (PLUGIN / "scripts" / "ensure-service.sh").read_text(
        encoding="utf-8"
    )
    powershell_hook = (PLUGIN / "scripts" / "ensure-service.ps1").read_text(
        encoding="utf-8"
    )

    assert "service-ensure-kick" in posix_gate
    assert "service-ensure-kick" in powershell_gate
    assert "service-ensure-kick" in (
        PLUGIN / "scripts" / "cell-runtime.py"
    ).read_text(encoding="utf-8")
    assert "__cell-service-ensure" in posix_hook
    assert "__cell-service-ensure" in powershell_hook
    assert '"$CELL_RUNTIME" service-ensure \\\n' not in posix_gate
    assert "$cellRuntime service-ensure `" not in powershell_gate
    assert '"$CELL_PYTHON" -I -X utf8 "$CELL_RUNTIME"' in posix_gate
    assert "& $cellPython -I -X utf8 $cellRuntime" in powershell_gate
    assert "namespaced deploy/recovery requires the owning cell transaction" in posix_gate
    assert (
        "namespaced deploy/recovery requires the owning cell transaction"
        in powershell_gate
    )


def test_activation_import_checks_use_target_slot_python() -> None:
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    posix_activate = posix.split("_versioned_activate() {", 1)[1].split(
        "_versioned_current() {", 1
    )[0]
    ps_activate = powershell.split("function Invoke-VersionedActivate {", 1)[1].split(
        "function Get-VersionedCurrent {", 1
    )[0]
    assert 'local py="$VENV_DIR/bin/python"' in posix_activate
    assert 'py="$LINK_DIR/bin/python"' not in posix_activate
    assert "$py = $VenvPython" in ps_activate
    assert "else { $LinkPython }" not in ps_activate


def _fixture(tmp_path: Path, shell: str) -> tuple[Path, dict[str, str]]:
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    config = tmp_path / ".agent-index" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "corpus:\n  sources:\n    - name: git:test\n",
        encoding="utf-8",
    )
    payload = tmp_path / f"payload-{shell}"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    (scripts / "cell-runtime.py").write_text(
        "import json, os, sys\n"
        "if len(sys.argv) > 1 and sys.argv[1] == 'launch-validate':\n"
        "    print(json.dumps({'status': 'ready', "
        "'runtimeVersion': '9.9.9+host', "
        "'interpreter': os.environ.get('TEST_PYTHON')}))\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_INDEX_HOME"] = str(tmp_path / f"home-{shell}")
    env["PYTHONPATH"] = os.pathsep.join(
        (
            str(SOURCE_ROOT),
            str(PLUGIN / "libs" / "agent-procutil" / "src"),
            str(PLUGIN / "libs" / "zdd" / "src"),
        )
    )
    env.pop("AGENT_INDEX_ROLE", None)
    env.pop("AGENT_INDEX_CONFIG", None)
    env.pop("AGENT_INDEX_EFFECTIVE_CONFIG", None)
    env.pop("AGENT_INDEX_CONFIG_DATA_B64", None)
    env.pop("AGENT_INDEX_REPO", None)
    shutil.copy2(
        PLUGIN / "scripts" / "resolve_effective_config.py",
        scripts / "resolve_effective_config.py",
    )
    shutil.copy2(
        PLUGIN / "scripts" / "resolve-activation-role.py",
        scripts / "resolve-activation-role.py",
    )
    if shell == "bash":
        shutil.copy2(PLUGIN / "scripts" / "runtime-gate.sh", scripts / "runtime-gate.sh")
        context_dir = scripts / "installation-context"
        context_dir.mkdir()
        shutil.copy2(
            PLUGIN / "scripts" / "installation-context" / "json-query.awk",
            context_dir / "json-query.awk",
        )
        (context_dir / "installation-context.sh").write_text(
            """#!/usr/bin/env bash
set -eu
if [[ "$1" == status ]]; then
  if [[ -n "${TEST_INSTALLATION_STATUS:-}" ]]; then
    printf '%s\\n' "$TEST_INSTALLATION_STATUS"
  else
    printf '%s\\n' '{"status":"ready","reason":"policy-default-false","actualMode":"legacy","desiredMode":"legacy","policy":{"state":"valid","enabled":false},"installationMode":{"marketplaces":{}}}'
  fi
  exit 0
fi
root="${TEST_CELL_ROOT:?}"
printf '{"pluginRoot":"%s","versionsRoot":"%s/versions","snapshotsRoot":"%s/snapshots","stateRoot":"%s/state","runRoot":"%s/run","logsRoot":"%s/logs","cacheRoot":"%s/cache","namespaceGeneration":1,"generation":1}\\n' "$root" "$root" "$root" "$root" "$root" "$root" "$root"
""",
            encoding="utf-8",
        )
        (scripts / "resolve-runtime.sh").write_text(
            'AGENT_RT_PY="${TEST_PYTHON:-}"\n', encoding="utf-8"
        )
        env["AGENT_INDEX_HOME"] = Path(env["AGENT_INDEX_HOME"]).as_posix()
        env["PYTHONPATH"] = ":".join(
            (
                SOURCE_ROOT.as_posix(),
                (PLUGIN / "libs" / "agent-procutil" / "src").as_posix(),
                (PLUGIN / "libs" / "zdd" / "src").as_posix(),
            )
        )
        env["TEST_PYTHON"] = _fake_runtime(
            tmp_path,
            DEFAULT_FAKE_RUNTIME,
        ).as_posix()
        return scripts / "runtime-gate.sh", env
    shutil.copy2(PLUGIN / "scripts" / "runtime-gate.ps1", scripts / "runtime-gate.ps1")
    context_dir = scripts / "installation-context"
    context_dir.mkdir()
    (context_dir / "installation-context.ps1").write_text(
        """$ErrorActionPreference = 'Stop'
if ($args[0] -eq 'status') {
    if ($env:TEST_INSTALLATION_STATUS) {
        Write-Output $env:TEST_INSTALLATION_STATUS
    } else {
        [ordered]@{
            status = 'ready'
            reason = 'policy-default-false'
            actualMode = 'legacy'
            desiredMode = 'legacy'
            policy = [ordered]@{ state = 'valid'; enabled = $false }
            installationMode = [ordered]@{ marketplaces = [ordered]@{} }
        } | ConvertTo-Json -Compress -Depth 4
    }
    exit 0
}
$root = $env:TEST_CELL_ROOT
[ordered]@{
    pluginRoot = $root
    versionsRoot = Join-Path $root 'versions'
    snapshotsRoot = Join-Path $root 'snapshots'
    stateRoot = Join-Path $root 'state'
    runRoot = Join-Path $root 'run'
    logsRoot = Join-Path $root 'logs'
    cacheRoot = Join-Path $root 'cache'
    namespaceGeneration = 1
    generation = 1
} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $env:TEST_PYTHON\n", encoding="utf-8"
    )
    env["TEST_PYTHON"] = str(
        _fake_runtime(tmp_path, DEFAULT_FAKE_RUNTIME)
    )
    return scripts / "runtime-gate.ps1", env


def _run(
    shell: str,
    script: Path,
    env: dict[str, str],
    *args: str,
    cwd: Path | None = None,
):
    run_cwd = cwd or script.parents[2]
    if shell == "bash":
        if os.name == "nt":
            pytest.skip("POSIX runtime-gate test")
        return subprocess.run(
            ["bash", script.as_posix(), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=run_cwd,
        )
    executable = (
        shutil.which("powershell.exe")
        if shell == "powershell"
        else shutil.which("pwsh")
    )
    if not executable:
        pytest.skip(f"{shell} is not installed")
    return subprocess.run(
        [executable, "-NoProfile", "-File", str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=run_cwd,
    )


def _fake_runtime(
    tmp_path: Path,
    main_source: str,
    *,
    runtime_root: Path | None = None,
) -> Path:
    slot = (runtime_root or (tmp_path / "fake-runtime")) / "versions" / "9.9.9"
    interpreter = (
        slot / "Scripts" / "python.exe"
        if os.name == "nt"
        else slot / "bin" / "python"
    )
    if not interpreter.is_file():
        venv.EnvBuilder(with_pip=False).create(slot)
    site_result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-X",
            "utf8",
            "-c",
            (
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    package = Path(site_result.stdout.strip()) / "agent_index"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(main_source, encoding="utf-8")
    return interpreter


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_dispatch_companion_mode_is_non_provisioning(
    tmp_path: Path, shell: str
) -> None:
    script, environment = _fixture(tmp_path, shell)

    result = _run(
        shell,
        script,
        environment,
        "__dispatch-companion-mode",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "supported": True,
        "mode": "legacy",
    }


def _real_setup_runtime(
    tmp_path: Path,
    runtime_root: Path,
    role: str,
) -> Path:
    slot = runtime_root / "versions" / f"9.9.9+{role}"
    interpreter = (
        slot / "Scripts" / "python.exe"
        if os.name == "nt"
        else slot / "bin" / "python"
    )
    venv.EnvBuilder(with_pip=False).create(slot)
    site_result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-X",
            "utf8",
            "-c",
            (
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    site_packages = Path(site_result.stdout.strip())
    shutil.copytree(
        SOURCE_ROOT / "agent_index",
        site_packages / "agent_index",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    dependency_paths = {
        str(Path(value).resolve())
        for value in sys.path
        if value
        and Path(value).is_dir()
        and (
            "site-packages" in Path(value).parts
            or "dist-packages" in Path(value).parts
        )
    }
    dependency_paths.update(
        {
            str((PLUGIN / "libs" / "agent-procutil" / "src").resolve()),
            str((PLUGIN / "libs" / "zdd" / "src").resolve()),
        }
    )
    (site_packages / "test-runtime-dependencies.pth").write_text(
        "".join(f"{value}\n" for value in sorted(dependency_paths)),
        encoding="utf-8",
    )
    torch = site_packages / "torch"
    torch.mkdir()
    (torch / "__init__.py").write_text(
        "class _Cuda:\n"
        "    @staticmethod\n"
        "    def is_available():\n"
        "        return False\n"
        "cuda = _Cuda()\n",
        encoding="utf-8",
    )
    (site_packages / "sitecustomize.py").write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "capture = os.environ.get('TEST_SETUP_ROLE_CAPTURE')\n"
        "if capture and 'setup' in sys.argv:\n"
        "    Path(capture).write_text("
        "os.environ.get('AGENT_INDEX_ROLE', '<unset>'), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return interpreter


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize(
    ("setup_args", "expected_role", "expected_indexer"),
    [
        (("--single", "--force"), "host", "local-box"),
        (("--indexer", "remote-box", "--ssh", "remote-ssh"), "client", "remote-box"),
    ],
    ids=["single", "indexer"],
)
def test_fresh_namespaced_setup_reaches_role_writing(
    tmp_path: Path,
    shell: str,
    setup_args: tuple[str, ...],
    expected_role: str,
    expected_indexer: str,
) -> None:
    if shell == "bash" and (os.name == "nt" or shutil.which("bash") is None):
        pytest.skip("POSIX runtime-gate test")
    script, env = _fixture(tmp_path, shell)
    cell_root = tmp_path / "cell" / "plugins" / "agent-index"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    capture = tmp_path / "setup-role.txt"
    provisioned_role = cell_root / "provisioned-role.txt"
    runtime = _real_setup_runtime(tmp_path, cell_root, expected_role)
    cell_runtime = script.parent / "cell-runtime.py"
    cell_runtime.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['TEST_CELL_ROOT'])\n"
        "if len(sys.argv) > 1 and sys.argv[1] == 'launch-validate':\n"
        "    marker = root / 'provisioned-role.txt'\n"
        "    if not marker.is_file():\n"
        "        print(json.dumps({'status': 'absent', 'runtimeVersion': None, "
        "'interpreter': None}))\n"
        "        raise SystemExit(0)\n"
        "    role = os.environ.get('AGENT_INDEX_ROLE', '')\n"
        "    if marker.read_text(encoding='utf-8').strip() != role:\n"
        "        raise SystemExit(1)\n"
        "    print(json.dumps({'status': 'ready', "
        "'runtimeVersion': f'9.9.9+{role}', "
        "'interpreter': os.environ['TEST_PYTHON']}))\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    if shell == "bash":
        installer = script.parent / "install.sh"
        installer.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "[ \"${1:-}\" = cell-provision ] || exit 2\n"
            "printf '%s\\n' \"${AGENT_INDEX_ROLE:-}\" > "
            "\"$TEST_CELL_ROOT/provisioned-role.txt\"\n",
            encoding="utf-8",
            newline="\n",
        )
        installer.chmod(0o700)
        env["TEST_PYTHON"] = runtime.as_posix()
        env["TEST_CELL_ROOT"] = cell_root.as_posix()
    else:
        (script.parent / "install.ps1").write_text(
            "param([string]$Action)\n"
            "if ($Action -cne 'cell-provision') { exit 2 }\n"
            "[IO.Directory]::CreateDirectory($env:TEST_CELL_ROOT) | Out-Null\n"
            "[IO.File]::WriteAllText(\n"
            "    (Join-Path $env:TEST_CELL_ROOT 'provisioned-role.txt'),\n"
            "    [string]$env:AGENT_INDEX_ROLE\n"
            ")\n"
            "exit 0\n",
            encoding="utf-8",
        )
        env["TEST_PYTHON"] = str(runtime)
        env["TEST_CELL_ROOT"] = str(cell_root)
    env.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "AGENT_INDEX_MACHINE": "local-box",
            "TEST_SETUP_ROLE_CAPTURE": str(capture),
            "TEST_INSTALLATION_STATUS": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "context": str(context),
                    "marketplaceId": "example--1234",
                    "policy": {"state": "valid", "enabled": True},
                }
            ),
        }
    )

    result = _run(
        shell,
        script,
        env,
        "setup",
        *setup_args,
        "--repo",
        str(repo),
        "--yes",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["role"] == expected_role
    assert payload["indexer"] == expected_indexer
    assert capture.read_text(encoding="utf-8") == expected_role
    assert provisioned_role.read_text(encoding="utf-8").strip() == expected_role
    machine_config = cell_root / "config" / "config.yaml"
    assert f"role: {expected_role}" in machine_config.read_text(encoding="utf-8")
    repo_config = repo / ".agent-index" / "config.yaml"
    assert f"machine: {expected_indexer}" in repo_config.read_text(encoding="utf-8")


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_bootstrap_probe_ignores_current_repo_shadow_and_uses_slot_origin(
    tmp_path: Path,
    shell: str,
) -> None:
    if shell == "bash":
        if os.name == "nt":
            pytest.skip("POSIX bootstrap probe test")
        executable = shutil.which("bash")
        if executable is None:
            pytest.skip("bash is unavailable")
    else:
        executable = shutil.which("powershell.exe") or shutil.which("pwsh")
        if executable is None:
            pytest.skip("PowerShell is unavailable")

    payload = tmp_path / f"payload-{shell}"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    suffix = "sh" if shell == "bash" else "ps1"
    bootstrap = scripts / f"bootstrap-check.{suffix}"
    shutil.copy2(PLUGIN / "scripts" / bootstrap.name, bootstrap)
    plugin_name = "agent-index-bootstrap-probe"
    (payload / "plugin.json").write_text(
        json.dumps({"name": plugin_name}),
        encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )

    profile = tmp_path / f"profile-{shell}"
    install = profile / f".{plugin_name}"
    slot = install / "versions" / "9.9.9"
    profile.mkdir()
    slot.parent.mkdir(parents=True)
    venv.EnvBuilder(with_pip=False).create(slot)
    interpreter = (
        slot / "Scripts" / "python.exe"
        if os.name == "nt"
        else slot / "bin" / "python"
    )
    site_result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-X",
            "utf8",
            "-c",
            (
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    selected_marker = tmp_path / f"selected-{shell}"
    selected_package = Path(site_result.stdout.strip()) / "agent_index"
    selected_package.mkdir()
    (selected_package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(selected_marker)!r}).write_text('ok')\n",
        encoding="utf-8",
    )

    (install / "config.yaml").write_text("role: host\n", encoding="utf-8")
    (install / "deploy-manifest.json").write_text(
        json.dumps({"source": {"version": "9.9.9"}}),
        encoding="utf-8",
    )
    (install / "current-version").write_text("9.9.9\n", encoding="utf-8")
    if shell == "bash":
        (scripts / "resolve-runtime.sh").write_text(
            f"AGENT_RT_PY={shlex.quote(str(interpreter))}\n",
            encoding="utf-8",
        )
        command = [executable, str(bootstrap)]
    else:
        quoted = str(interpreter).replace("'", "''")
        (scripts / "resolve-runtime.ps1").write_text(
            f"$AgentRtPy = '{quoted}'\n",
            encoding="utf-8",
        )
        command = [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bootstrap),
        ]

    malicious_repo = tmp_path / f"repo-{shell}"
    shadow = malicious_repo / "agent_index"
    shadow.mkdir(parents=True)
    shadow_marker = tmp_path / f"shadow-{shell}"
    (shadow / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(shadow_marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "HOME": str(profile),
        "USERPROFILE": str(profile),
        "AGENT_INDEX_ROLE": "host",
        "PYTHONPATH": str(malicious_repo),
        "PYTHONUTF8": "1",
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)

    result = subprocess.run(
        command,
        cwd=malicious_repo,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert selected_marker.read_text(encoding="utf-8") == "ok"
    assert not shadow_marker.exists()


def test_coordinator_python_invocations_force_utf8() -> None:
    coordinator = (PLUGIN / "scripts" / "cell-runtime.py").read_text(
        encoding="utf-8"
    )
    posix_installer = (PLUGIN / "scripts" / "install.sh").read_text(
        encoding="utf-8"
    )
    powershell_installer = (PLUGIN / "scripts" / "install.ps1").read_text(
        encoding="utf-8"
    )

    assert '"-I",\n        "-X",\n        "utf8"' in coordinator
    assert 'exec "$CELL_PYTHON" -I -X utf8' in posix_installer
    assert "& $cellPython -I -X utf8 @cellArgs" in powershell_installer
    assert 'unset PYTHONPATH PYTHONHOME\n    cd "$PLUGIN_DIR"' in posix_installer
    assert "Remove-Item Env:PYTHONHOME" in powershell_installer
    assert "Set-Location -LiteralPath $PluginDir" in powershell_installer


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5 test")
def test_windows_powershell5_dispatches_from_non_ascii_path(
    tmp_path: Path,
) -> None:
    if shutil.which("powershell.exe") is None:
        pytest.skip("Windows PowerShell 5 is unavailable")
    unicode_root = tmp_path / "célula-索引"
    script, env = _fixture(unicode_root, "powershell")
    cell_root = unicode_root / "célula" / "plugins" / "agent-index"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    env["AGENT_INDEX_ROLE"] = "host"
    env["TEST_CELL_ROOT"] = str(cell_root)
    env["TEST_INSTALLATION_STATUS"] = json.dumps(
        {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
            "desiredMode": "namespaced",
            "context": str(context),
            "marketplaceId": "example--1234",
            "policy": {"state": "valid", "enabled": True},
        }
    )
    captured = unicode_root / "capturé.json"
    env["TEST_PYTHON"] = str(
        _fake_runtime(
            unicode_root,
            "import json, os\n"
            "from pathlib import Path\n"
            f"Path({str(captured)!r}).write_text(json.dumps({{\n"
            "  'context': os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),\n"
            "  'home': os.environ.get('AGENT_INDEX_HOME'),\n"
            "}, ensure_ascii=False), encoding='utf-8')\n",
            runtime_root=cell_root,
        )
    )
    shadow = unicode_root / "repo-shadow" / "agent_index"
    shadow.mkdir(parents=True)
    sentinel = unicode_root / "shadow-ran"
    (shadow / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = str(shadow.parent)

    result = _run("powershell", script, env, "capture")

    assert result.returncode == 0, result.stderr
    values = json.loads(captured.read_text(encoding="utf-8"))
    assert values == {"context": str(context), "home": str(cell_root)}
    assert not sentinel.exists()


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_inactive_repository_blocks_before_runtime_mutation(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    (tmp_path / ".agent-index" / "config.yaml").unlink()
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])

    status = _run(shell, script, env, "status")
    search = _run(shell, script, env, "search", "anything", "--json")

    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["state"] == "inactive"
    assert search.returncode == 2
    assert json.loads(search.stdout)["state"] == "inactive"
    assert not home.exists()


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_forwarded_config_allows_remote_host_dispatch(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    (tmp_path / ".agent-index" / "config.yaml").unlink()
    env["AGENT_INDEX_CONFIG_DATA_B64"] = base64.urlsafe_b64encode(
        json.dumps({"indexers": [{"machine": "remote-host"}]}).encode("utf-8")
    ).decode("ascii")
    env["AGENT_INDEX_MACHINE"] = "remote-host"
    env["TEST_PYTHON"] = ""

    result = _run(shell, script, env, "role", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["role"] == "host"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_forwarded_read_never_provisions_missing_host_runtime(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    (tmp_path / ".agent-index" / "config.yaml").unlink()
    env["AGENT_INDEX_CONFIG_DATA_B64"] = base64.urlsafe_b64encode(
        json.dumps({"indexers": [{"machine": "remote-host"}]}).encode("utf-8")
    ).decode("ascii")
    env["AGENT_INDEX_MACHINE"] = "remote-host"
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])

    result = _run(shell, script, env, "search", "anything", "--json")

    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "runtime_unavailable"
    assert payload["role"] == "host"
    assert not home.exists()


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_installer_readiness_never_provisions_at_session_start(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])

    result = _run(shell, script, env, "installer-readiness")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "configuration-empty"
    assert "does not provision" in payload["detail"]
    assert not home.exists()


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_status_without_runtime_is_non_mutating_setup_required(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])

    result = _run(shell, script, env, "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"
    assert payload["runtime"]["state"] == "absent"
    assert payload["schema_version"] == 1
    assert payload["version"] == "9.9.9"
    assert not home.exists()


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("runtime_state", ["stamped", "broken"])
def test_status_classifies_non_runnable_runtime(
    tmp_path: Path, shell: str, runtime_state: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])
    home.mkdir(parents=True)
    if runtime_state == "stamped":
        (home / "payload-dir").write_text("payload", encoding="utf-8")
    else:
        (home / "current-version").write_text("1.0.0\n", encoding="utf-8")
        slot = home / "versions" / "1.0.0"
        slot.mkdir(parents=True)
        (slot / "python-placeholder").write_text("", encoding="utf-8")

    result = _run(shell, script, env, "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["runtime"]["state"] == runtime_state
    assert payload["setup_required"] is True


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_complete_runtime_without_role_reports_setup_required(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)

    result = _run(shell, script, env, "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"
    assert payload["runtime"]["state"] == "ready"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_operational_command_without_role_is_blocked(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)

    result = _run(shell, script, env, "search", "anything", "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_configured_role_with_missing_runtime_is_not_called_dormant(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    env["AGENT_INDEX_ROLE"] = "client"

    result = _run(shell, script, env, "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "runtime_unavailable"
    assert payload["setup_required"] is False
    assert payload["role"] == "client"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("role", ["client", "host"])
def test_configured_roles_are_reported_without_mutation(
    tmp_path: Path, shell: str, role: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["AGENT_INDEX_ROLE"] = role

    result = _run(shell, script, env, "role", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"role": role, "setup_required": False, "state": "ready"}


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_noninteractive_setup_requires_explicit_role(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""

    result = _run(shell, script, env, "setup", "--yes", "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"
    assert "explicit role choice" in payload["error"]
    assert "setup --single --yes" in payload["setup"]["noninteractive"][0]


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_authored_indexers_do_not_replace_noninteractive_setup_choice(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    repo = tmp_path / "repo"
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("indexers:\n  - machine: box-a\n", encoding="utf-8")

    result = _run(
        shell, script, env, "setup", "--yes", "--repo", str(repo), "--json"
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("indexer_args", [["--indexer"], ["--indexer", "--yes"], ["--indexer="]])
def test_missing_indexer_value_does_not_provision(
    tmp_path: Path, shell: str, indexer_args: list[str]
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])

    result = _run(shell, script, env, "setup", *indexer_args, "--yes", "--json")

    assert result.returncode == 2
    assert json.loads(result.stdout)["state"] == "setup_required"
    assert not (home / "versions").exists()


@pytest.mark.parametrize("explicit_false", [False, True])
def test_real_resolver_absent_or_explicit_false_preserves_legacy(
    tmp_path: Path, explicit_false: bool
) -> None:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh is not installed")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    shutil.copy2(PLUGIN / "scripts" / "runtime-gate.ps1", scripts / "runtime-gate.ps1")
    shutil.copy2(
        PLUGIN / "scripts" / "resolve_effective_config.py",
        scripts / "resolve_effective_config.py",
    )
    shutil.copy2(
        PLUGIN / "scripts" / "resolve-activation-role.py",
        scripts / "resolve-activation-role.py",
    )
    shutil.copytree(
        PLUGIN / "scripts" / "installation-context",
        scripts / "installation-context",
    )
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $null\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    if explicit_false:
        policy = profile / ".copilot-extensions" / "installation-mode.json"
        policy.parent.mkdir()
        policy.write_text(
            json.dumps(
                {
                    "schema": "copilot-extensions.installation-mode",
                    "version": 1,
                    "installationMode": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
    legacy = tmp_path / "legacy"
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    active = tmp_path / ".agent-index" / "config.yaml"
    active.parent.mkdir()
    active.write_text(
        "corpus:\n  sources:\n    - name: git:test\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "USERPROFILE": str(profile),
        "HOME": str(profile),
        "AGENT_INDEX_HOME": str(legacy),
        "AGENT_INDEX_NO_SELFPROVISION": "1",
    }
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)

    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(scripts / "runtime-gate.ps1"), "status"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "setup_required"
    assert not legacy.exists()
    assert not (profile / ".copilot-extensions" / "marketplaces").exists()


def test_posix_existing_regular_policy_is_not_treated_as_absent(
    tmp_path: Path,
) -> None:
    if os.name == "nt" or shutil.which("bash") is None:
        pytest.skip("POSIX runtime-gate test")
    script, env = _fixture(tmp_path, "bash")
    assert (
        'if [ -e "$POLICY" ] || [ -L "$POLICY" ]; then\n'
        "    POLICY_PRESENT=1\n"
        "fi"
    ) in script.read_text(encoding="utf-8")
    profile = tmp_path / "profile"
    policy = profile / ".copilot-extensions" / "installation-mode.json"
    policy.parent.mkdir(parents=True)
    policy.write_text("{}\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    getent = fake_bin / "getent"
    getent.write_text(
        "#!/bin/sh\n"
        'printf "tester:x:1:1::%s:/bin/sh\\n" "$TEST_PROFILE_HOME"\n',
        encoding="utf-8",
        newline="\n",
    )
    getent.chmod(0o700)
    env["PATH"] = f"{fake_bin.as_posix()}:{env.get('PATH', '')}"
    env["TEST_PROFILE_HOME"] = profile.as_posix()
    env["TEST_INSTALLATION_STATUS"] = json.dumps(
        {
            "status": "provenance-blocked",
            "reason": "policy-invalid",
            "actualMode": "legacy",
            "desiredMode": "namespaced",
            "policy": {"state": "invalid", "enabled": False},
            "installationMode": {"marketplaces": {}},
        }
    )

    result = _run("bash", script, env, "status", cwd=tmp_path)

    assert result.returncode == 126
    assert "installation context blocks invocation" in result.stderr


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("migration-required", "legacy-state-present"),
        ("maintenance", "plugin-maintenance"),
        ("orphaned-transfer", "legacy-ownership-orphaned"),
        ("provenance-blocked", "context-plugin-mismatch"),
        ("revalidation-required", "install-generation-changed"),
    ],
)
def test_blocked_installation_states_never_fall_back_to_legacy(
    tmp_path: Path, status: str, reason: str
) -> None:
    script, env = _fixture(tmp_path, "pwsh")
    env["TEST_INSTALLATION_STATUS"] = json.dumps(
        {
            "status": status,
            "reason": reason,
            "actualMode": "legacy",
            "desiredMode": "namespaced",
            "policy": {"state": "valid", "enabled": True},
        }
    )
    if status == "provenance-blocked":
        context = tmp_path / "foreign" / "install.json"
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    marker = tmp_path / "legacy-ran.json"
    fake = tmp_path / "fake-agent-index"
    package = fake / "agent_index"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = str(fake)

    result = _run("pwsh", script, env, "version")

    assert result.returncode == 126
    assert "blocks invocation" in result.stderr
    assert not marker.exists()


def test_active_cell_dispatch_sets_cell_roots_and_context(tmp_path: Path) -> None:
    script, env = _fixture(tmp_path, "pwsh")
    cell_root = tmp_path / "cell" / "plugins" / "agent-index"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    env["AGENT_INDEX_ROLE"] = "host"
    env["TEST_CELL_ROOT"] = str(cell_root)
    env["TEST_INSTALLATION_STATUS"] = json.dumps(
        {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
            "desiredMode": "namespaced",
            "context": str(context),
            "marketplaceId": "example--1234",
            "policy": {"state": "valid", "enabled": True},
        }
    )
    captured = tmp_path / "captured.json"
    env["TEST_PYTHON"] = str(
        _fake_runtime(
            tmp_path,
            "import json, os\n"
            "from pathlib import Path\n"
            f"Path({str(captured)!r}).write_text(json.dumps({{\n"
            "  key: os.environ.get(key) for key in (\n"
            "    'COPILOT_EXTENSIONS_CONTEXT', 'AGENT_INDEX_HOME',\n"
            "    'AGENT_INDEX_STATE_DIR', 'AGENT_INDEX_RUN_DIR',\n"
            "    'AGENT_INDEX_LOG_DIR', 'AGENT_INDEX_CACHE_DIR',\n"
            "    'AGENT_INDEX_CONFIG_ROOT', 'AGENT_INDEX_ENGINE_HOME',\n"
            "    'AGENT_INDEX_HOST', 'AGENT_INDEX_PORT',\n"
            "    'AGENT_INDEX_ENGINE_PORT', 'AGENT_INDEX_ENGINE_MODE',\n"
            "    'AGENT_INDEX_ROUTING_DIR', 'AGENT_INDEX_INSTALLATION_ID',\n"
            "    'AGENT_INDEX_BACKUP_DIR', 'AGENT_INDEX_BACKUP_MOUNT_ROOT')\n"
            "}))\n",
            runtime_root=cell_root,
        )
    )
    shadow = tmp_path / "malicious-repo" / "agent_index"
    shadow.mkdir(parents=True)
    sentinel = tmp_path / "shadow-ran"
    (shadow / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = str(shadow.parent)

    result = _run("pwsh", script, env, "capture")

    assert result.returncode == 0, result.stderr
    values = json.loads(captured.read_text(encoding="utf-8"))
    assert values["COPILOT_EXTENSIONS_CONTEXT"] == str(context)
    assert values["AGENT_INDEX_HOME"] == str(cell_root)
    assert values["AGENT_INDEX_STATE_DIR"] == str(cell_root / "state")
    assert values["AGENT_INDEX_RUN_DIR"] == str(cell_root / "run")
    assert values["AGENT_INDEX_LOG_DIR"] == str(cell_root / "logs")
    assert values["AGENT_INDEX_CACHE_DIR"] == str(cell_root / "cache")
    assert values["AGENT_INDEX_CONFIG_ROOT"] == str(cell_root / "config")
    assert values["AGENT_INDEX_ENGINE_HOME"] == str(cell_root / "engine")
    assert values["AGENT_INDEX_HOST"] == "127.0.0.1"
    assert values["AGENT_INDEX_PORT"] == "0"
    assert values["AGENT_INDEX_ENGINE_PORT"] == "0"
    assert values["AGENT_INDEX_ENGINE_MODE"] == "external"
    assert values["AGENT_INDEX_ROUTING_DIR"] == str(cell_root / "run" / "zdd")
    assert values["AGENT_INDEX_INSTALLATION_ID"] == "example--1234/agent-index"
    assert values["AGENT_INDEX_BACKUP_DIR"] == str(cell_root / "backups")
    assert values["AGENT_INDEX_BACKUP_MOUNT_ROOT"] == str(cell_root)
    assert not sentinel.exists()

    captured.unlink()
    engine_start = _run("pwsh", script, env, "engine", "start")
    assert engine_start.returncode == 2
    assert "does not provision or start" in engine_start.stderr
    assert not captured.exists()


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("command", ["status", "search", "setup"])
def test_active_cell_preserves_original_repository_before_safe_cwd(
    tmp_path: Path,
    shell: str,
    command: str,
) -> None:
    if shell == "bash" and (os.name == "nt" or shutil.which("bash") is None):
        pytest.skip("POSIX runtime-gate test")
    script, env = _fixture(tmp_path, shell)
    cell_root = tmp_path / "cell" / "plugins" / "agent-index"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    repository = tmp_path / f"repo-{shell}-{command}"
    nested = repository / "nested" / "work"
    nested.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", str(repository)],
        check=True,
        capture_output=True,
        text=True,
    )
    repo_config = repository / ".agent-index" / "config.yaml"
    repo_config.parent.mkdir()
    repo_config.write_text(
        "corpus:\n  sources:\n    - name: git:test\n",
        encoding="utf-8",
    )
    captured = tmp_path / f"repo-context-{shell}-{command}.json"
    runtime_source = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(captured)!r}).write_text(json.dumps({{\n"
        "  'repo': os.environ.get('AGENT_INDEX_REPO'),\n"
        "  'runtime_version': os.environ.get('AGENT_INDEX_RUNTIME_VERSION'),\n"
        "  'cwd': str(Path.cwd()),\n"
        "  'command': sys.argv[1] if len(sys.argv) > 1 else 'status',\n"
        "}), encoding='utf-8')\n"
        "if len(sys.argv) > 1 and sys.argv[1] == 'setup':\n"
        "    raise SystemExit(7)\n"
    )
    env.update(
        {
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "AGENT_INDEX_ROLE": "host",
            "TEST_CELL_ROOT": str(cell_root),
            "TEST_INSTALLATION_STATUS": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "context": str(context),
                    "marketplaceId": "example--1234",
                    "policy": {"state": "valid", "enabled": True},
                }
            ),
            "TEST_PYTHON": str(
                _fake_runtime(
                    tmp_path,
                    runtime_source,
                    runtime_root=cell_root,
                )
            ),
        }
    )
    env.pop("AGENT_INDEX_REPO", None)
    arguments = (
        (command, "--single", "--yes")
        if command == "setup"
        else ((command, "query") if command == "search" else (command,))
    )

    result = _run(shell, script, env, *arguments, cwd=nested)

    if command == "setup":
        assert result.returncode == 7
    else:
        assert result.returncode == 0, result.stderr
    values = json.loads(captured.read_text(encoding="utf-8"))
    assert Path(values["repo"]).resolve() == repository.resolve()
    assert Path(values["cwd"]).resolve() == cell_root.resolve()
    assert values["runtime_version"] == "9.9.9+host"
    assert values["command"] == command


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_active_cell_rejects_validator_interpreter_outside_selected_slot(
    tmp_path: Path,
    shell: str,
) -> None:
    if shell == "bash" and (os.name == "nt" or shutil.which("bash") is None):
        pytest.skip("POSIX runtime-gate test")
    script, env = _fixture(tmp_path, shell)
    cell_root = tmp_path / "cell" / "plugins" / "agent-index"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    env["AGENT_INDEX_ROLE"] = "host"
    env["TEST_CELL_ROOT"] = str(cell_root)
    env["TEST_INSTALLATION_STATUS"] = json.dumps(
        {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
            "desiredMode": "namespaced",
            "context": str(context),
            "marketplaceId": "example--1234",
            "policy": {"state": "valid", "enabled": True},
        }
    )
    marker = tmp_path / "outside-runtime-ran"
    env["TEST_PYTHON"] = str(
        _fake_runtime(
            tmp_path,
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        )
    )
    env["AGENT_INDEX_NO_SELFPROVISION"] = "1"

    result = _run(shell, script, env, "capture")

    assert result.returncode == 1
    assert "runtime is not ready" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_namespaced_deploy_requires_live_cell_transaction(
    tmp_path: Path,
    shell: str,
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    cell_root = tmp_path / "cell" / "plugins" / "agent-index"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    env["AGENT_INDEX_ROLE"] = "host"
    env["TEST_CELL_ROOT"] = str(cell_root)
    env["TEST_INSTALLATION_STATUS"] = json.dumps(
        {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
            "desiredMode": "namespaced",
            "context": str(context),
            "marketplaceId": "example--1234",
            "policy": {"state": "valid", "enabled": True},
        }
    )

    result = _run(shell, script, env, "deploy", "--recover", "--json")

    assert result.returncode == 126
    assert "requires the owning cell transaction" in result.stderr


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("command", ["start", "serve"])
def test_namespaced_public_start_is_blocked_by_runtime_gate(
    tmp_path: Path,
    shell: str,
    command: str,
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    cell_root = tmp_path / "cell" / "plugins" / "agent-index"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    env["AGENT_INDEX_ROLE"] = "host"
    env["TEST_CELL_ROOT"] = str(cell_root)
    env["TEST_INSTALLATION_STATUS"] = json.dumps(
        {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
            "desiredMode": "namespaced",
            "context": str(context),
            "marketplaceId": "example--1234",
            "policy": {"state": "valid", "enabled": True},
        }
    )

    result = _run(shell, script, env, command)

    assert result.returncode == 126
    assert "public start/serve is unavailable" in result.stderr
