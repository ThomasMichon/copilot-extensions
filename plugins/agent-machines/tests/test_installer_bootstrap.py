"""Native setup seed controls; acquisition is always fixture-owned and inert."""
from __future__ import annotations

from collections.abc import Callable, Iterator
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import pytest

from .managed_command_fixture import HEADLESS, PLUGIN, REPO, snapshot
from .test_cell_lifecycle_bootstrap import (
    LifecycleFixture,
    installer_python as installer_python,
    lifecycle as lifecycle,
)

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]


@pytest.fixture
def bootstrap(lifecycle: LifecycleFixture) -> Iterator[LifecycleFixture]:
    for name in ("UV_INSTALL_DIR", "UV_UNMANAGED_INSTALL"):
        lifecycle.env.pop(name, None)
    scripts = lifecycle.payload / "scripts"
    for suffix in ("sh", "ps1"):
        shutil.copy2(PLUGIN / f"scripts/installer-bootstrap.{suffix}", scripts)
    if os.name != "nt":
        for name in ("sh", "cp", "chmod"):
            executable = shutil.which(name)
            assert executable
            (lifecycle.tools / name).symlink_to(executable)
    yield lifecycle
    for root in (lifecycle.tools, lifecycle.home / ".local/bin", lifecycle.home / "uv destination"):
        for name in ("uv", "uv.exe", "unavailable-uv", "unavailable-uv.exe"):
            (root / name).unlink(missing_ok=True)


def _ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(fixture: LifecycleFixture, operation: str) -> subprocess.CompletedProcess[str]:
    scripts = fixture.payload / "scripts"
    probe = (
        "import json,os,sys; print(json.dumps({"
        "'python':sys.executable,'version':list(sys.version_info[:3]),"
        "'stdin':sys.stdin.read(),'selector':os.environ.get('UV_PYTHON')}))"
    )
    if fixture.host == "bash":
        program = (
            f". {shlex.quote(str(scripts / 'installer-bootstrap.sh'))}\n"
            f"_initialize_machines_bootstrap {operation} || exit $?\n"
            f'"$MACHINES_BOOTSTRAP_PYTHON" -I -S -B -c {shlex.quote(probe)}\n'
        )
        command = [fixture.shell, "--noprofile", "--norc", "-c", program]
    else:
        program = (
            "$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2\n"
            f". {_ps(scripts / 'versioned_runtime_native.ps1')}\n"
            f". {_ps(scripts / 'installer-bootstrap.ps1')}\n"
            f"Initialize-MachinesBootstrap -Operation {operation}\n"
            f"& $script:MachinesInstallerSelection.Python -I -S -B -c {_ps(probe)}\n"
            "exit $LASTEXITCODE\n"
        )
        command = [fixture.shell, "-NoProfile", "-Command", program]
    return subprocess.run(
        command, input="caller input\n", capture_output=True, text=True,
        env=fixture.env, cwd=fixture.home, timeout=40, creationflags=HEADLESS,
    )


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_inspect_reuses_managed_bootstrap_without_acquisition(
    bootstrap: LifecycleFixture, record_property: Callable[[str, object], None],
) -> None:
    before = snapshot(bootstrap.payload.parents[1])
    samples = []
    for _ in range(2):
        started = time.monotonic()
        result = _run(bootstrap, "Inspect")
        samples.append(time.monotonic() - started)
        assert result.returncode == 0, result
        proof = json.loads(result.stdout)
        assert Path(proof["python"]).resolve() == bootstrap.python
        assert proof["version"] == [3, 11, 18]
        assert proof["stdin"] == "caller input\n"
        assert proof["selector"] == "unrelated-python"
        assert not bootstrap.poison.exists()
    calls = bootstrap.calls.read_text().splitlines()
    expected = [
        "--version", "--version",
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
    ]
    if bootstrap.host == "bash":
        expected.append("-I -S -B -c import sys; sys.exit(sys.version_info < (3, 10))")
    assert calls == expected * 2
    assert snapshot(bootstrap.payload.parents[1]) == before
    record_property("machines_installer_bootstrap", json.dumps({
        "shell": bootstrap.host, "materialized": bootstrap.support == bootstrap.payload / "scripts",
        "uv_calls": 3, "samples_seconds": samples, "network": "disabled",
    }, sort_keys=True))


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("scenario", ["missing-uv", "missing-provider", "missing-request", "unprepared"])
def test_inspect_refuses_without_fallback_or_acquisition(
    bootstrap: LifecycleFixture, scenario: str,
) -> None:
    if scenario == "missing-uv":
        uv = next(path for path in bootstrap.tools.iterdir() if path.stem == "uv")
        uv.rename(uv.with_name("unavailable-uv"))
    elif scenario in ("missing-provider", "missing-request"):
        name = "installer-instance-python-version.txt" if scenario == "missing-request" else (
            "bootstrap-uv.sh" if bootstrap.host == "bash" else "bootstrap-uv.ps1"
        )
        path = bootstrap.support / name
        path.rename(path.with_suffix(".unavailable"))
    else:
        store = bootstrap.home / "empty-store"
        store.mkdir()
        bootstrap.env["UV_PYTHON_INSTALL_DIR"] = str(store)
    before = snapshot(bootstrap.payload.parents[1])
    result = _run(bootstrap, "Inspect")
    assert result.returncode != 0, result
    assert not bootstrap.poison.exists()
    assert snapshot(bootstrap.payload.parents[1]) == before
    calls = bootstrap.calls.read_text().splitlines() if bootstrap.calls.exists() else []
    assert not any("install" in call or "pip" in call for call in calls)
    assert not (bootstrap.home / ".local").exists()


def _inert_preparation(fixture: LifecycleFixture, *, missing_uv: bool) -> Path:
    prepared = fixture.home / "prepared"
    acquisition = fixture.home / "acquired"
    fixture.env["MACHINES_TEST_PREPARED"] = str(prepared)
    fixture.env["MACHINES_TEST_ACQUIRED"] = str(acquisition)
    uv = next(path for path in fixture.tools.iterdir() if path.stem == "uv")
    if missing_uv:
        hidden = uv.with_name("unavailable-uv" + uv.suffix)
        uv.rename(hidden)
        fixture.env["MACHINES_TEST_UV"] = str(hidden)
    if fixture.host == "bash":
        provider = fixture.support / "bootstrap-uv.sh"
        text = provider.read_text()
        needle = '    exec "$@"\n'
        assert text.count(needle) == 1
        provider.write_text(text.replace(needle, (
            '    if [[ "${2:-}" == python && "${3:-}" == install ]]; then\n'
            '        printf prepared > "$MACHINES_TEST_PREPARED"; return 0\n'
            '    fi\n' + needle
        )))
        downloader = fixture.tools / "curl"
        downloader.write_text(
            "#!/bin/sh\ncat <<'INERT_INSTALLER'\n"
            'destination="${UV_UNMANAGED_INSTALL:-$UV_INSTALL_DIR}"\n'
            'mkdir -p "$destination"\n'
            'cp "$MACHINES_TEST_UV" "$destination/uv"\n'
            'chmod +x "$destination/uv"\n'
            'printf acquired > "$MACHINES_TEST_ACQUIRED"\n'
            "INERT_INSTALLER\n",
        )
        downloader.chmod(0o700)
    else:
        provider = fixture.support / "bootstrap-uv.ps1"
        text = provider.read_text()
        needle = "    $result = & $InvokeCaptured @options\n"
        assert text.count(needle) == 1
        provider.write_text(text.replace(needle, (
            "    if ($Arguments[0] -eq 'python' -and $Arguments[1] -eq 'find' -and\n"
            "        -not [IO.File]::Exists($env:MACHINES_TEST_PREPARED)) {\n"
            "        return [pscustomobject]@{ExitCode=2; Stdout=''; Stderr='No interpreter found for fixture'}\n"
            "    }\n"
            "    if ($Arguments[0] -eq 'python' -and $Arguments[1] -eq 'install') {\n"
            "        [IO.File]::WriteAllText($env:MACHINES_TEST_PREPARED, 'prepared')\n"
            "        return [pscustomobject]@{ExitCode=0; Stdout=''; Stderr=''}\n"
            "    }\n" + needle
        )))
        helper = fixture.payload / "scripts/installer-bootstrap.ps1"
        text = helper.read_text()
        start = text.index("if ($PrepareUv) {")
        helper.write_text(text[:start] + (
            "if ($PrepareUv) {\n"
            "    $directory = $env:UV_INSTALL_DIR\n"
            "    if ($env:UV_UNMANAGED_INSTALL) { $directory = $env:UV_UNMANAGED_INSTALL }\n"
            "    [void][IO.Directory]::CreateDirectory($directory)\n"
            "    [IO.File]::Copy($env:MACHINES_TEST_UV, (Join-Path $directory "
            + ("'uv.exe'" if os.name == "nt" else "'uv'") + "))\n"
            "    [IO.File]::WriteAllText($env:MACHINES_TEST_ACQUIRED, 'acquired')\n"
            "    exit 0\n}\n"
        ))
    return acquisition


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("missing_uv", [False, True], ids=["uv-present", "cold-uv"])
def test_prepare_reaches_declared_managed_python_without_ambient_interpreter(
    bootstrap: LifecycleFixture, missing_uv: bool,
) -> None:
    acquisition = _inert_preparation(bootstrap, missing_uv=missing_uv)
    before = snapshot(bootstrap.payload.parents[1])
    result = _run(bootstrap, "Prepare")
    assert result.returncode == 0, result
    proof = json.loads(result.stdout)
    assert Path(proof["python"]).resolve() == bootstrap.python
    assert proof["stdin"] == "caller input\n"
    assert acquisition.exists() == missing_uv
    assert not bootstrap.poison.exists()
    assert snapshot(bootstrap.payload.parents[1]) == before
    calls = bootstrap.calls.read_text().splitlines()
    assert calls.count("python install --no-bin --no-registry cpython@3.11.18") == 1
    assert not any("pip" in call for call in calls)


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("setting", ["UV_INSTALL_DIR", "UV_UNMANAGED_INSTALL"])
def test_prepare_and_inspect_discover_uv_at_configured_destination(
    bootstrap: LifecycleFixture, setting: str,
) -> None:
    directory = bootstrap.home / "uv destination"
    bootstrap.env[setting] = str(directory)
    if setting == "UV_UNMANAGED_INSTALL":
        bootstrap.env["UV_INSTALL_DIR"] = str(bootstrap.home / "unused destination")
    acquisition = _inert_preparation(bootstrap, missing_uv=True)
    for operation in ("Prepare", "Inspect"):
        result = _run(bootstrap, operation)
        assert result.returncode == 0, result.stdout + result.stderr
        assert Path(json.loads(result.stdout)["python"]).resolve() == bootstrap.python
    assert acquisition.is_file()
    assert (directory / ("uv.exe" if os.name == "nt" else "uv")).is_file()
    assert not (bootstrap.home / ".local/bin").exists()
    assert not (bootstrap.home / "unused destination").exists()
    assert not bootstrap.poison.exists()


@pytest.fixture
def installer(
    bootstrap: LifecycleFixture,
) -> LifecycleFixture:
    scripts = bootstrap.payload / "scripts"
    for source in (PLUGIN / "scripts").rglob("*"):
        if source.is_file() and "__pycache__" not in source.parts:
            target = scripts / source.relative_to(PLUGIN / "scripts")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    for name in ("plugin.json", "pyproject.toml", "payload-invocation.json"):
        shutil.copy2(PLUGIN / name, bootstrap.payload / name)
    if os.name != "nt":
        for name in ("bash", "sed", "head", "tr", "awk", "readlink", "ln", "stat", "date", "git"):
            executable = shutil.which(name)
            assert executable, name
            if not (bootstrap.tools / name).exists():
                (bootstrap.tools / name).symlink_to(executable)
    # Policy is independently covered; this fixture never inspects the host account.
    (scripts / "installation-context/legacy-entrypoint-probe.sh").write_text(
        '#!/bin/bash\n[[ -x "$COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON" ]]\nexit $?\n',
    )
    (scripts / "installation-context/legacy-entrypoint-probe.ps1").write_text(
        "if (-not [IO.File]::Exists($env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON)) { exit 94 }\nexit 0\n",
    )
    return bootstrap


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("action", ["install", "stamp"])
def test_actual_installer_selects_before_source_identity(
    installer: LifecycleFixture, action: str,
) -> None:
    bootstrap = installer
    scripts = bootstrap.payload / "scripts"
    if action == "install":
        _inert_preparation(bootstrap, missing_uv=False)
    elif bootstrap.host == "bash":
        (scripts / "installation-context/legacy-entrypoint-probe.sh").write_text("exit 0\n")
    proof = bootstrap.home / "source-proof.json"
    bootstrap.env["MACHINES_SOURCE_PROOF"] = str(proof)
    bootstrap.env["COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"] = "fixture-source"
    (scripts / "versioned_runtime_source.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['MACHINES_SOURCE_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'argv':sys.argv[1:],'isolated':sys.flags.isolated,"
        "'bytecode':sys.dont_write_bytecode}),encoding='utf-8')\n"
        "print(json.dumps({'runtime_version':'1.0.0'}))\n",
    )
    if bootstrap.host == "bash":
        entry = scripts / "init.sh"
        text = entry.read_text()
        marker = "\n_initialize_installer_identity || exit 1\n"
        assert text.count(marker) == 1
        entry.write_text(text.replace(marker, (
            "\n_initialize_installer_identity || exit 1\nexit 0\n"
        )))
        command = [bootstrap.shell, str(entry), action]
    else:
        entry = scripts / "init.ps1"
        text = entry.read_text()
        marker = "\nInitialize-MachinesInstallerIdentity\n"
        assert text.count(marker) == 1
        entry.write_text(text.replace(marker, (
            "\nInitialize-MachinesInstallerIdentity\nexit 0\n"
        )))
        command = [bootstrap.shell, "-NoProfile", "-File", str(entry), "-Action", action]
    result = subprocess.run(
        command, capture_output=True, text=True, env=bootstrap.env,
        cwd=bootstrap.home, timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(proof.read_text())
    assert Path(observed["python"]).resolve() == bootstrap.python
    assert observed["isolated"] == 1 and observed["bytecode"]
    assert observed["argv"][:2] == ["--payload", str(bootstrap.payload)]
    assert not bootstrap.poison.exists()
    assert (bootstrap.home / "prepared").exists() == (action == "install")
    assert not (bootstrap.home / "acquired").exists()


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_cell_watchdog_uses_selected_python_before_any_source_helper(
    installer: LifecycleFixture,
) -> None:
    _inert_preparation(installer, missing_uv=False)
    installer.env["AGENT_MACHINES_INSTALL_DEADLINE_SEC"] = "20"
    worker = installer.payload / "scripts/cell_installer.py"
    worker.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "def main(request):\n"
        "    lifetime=request.pop('lifetime')\n"
        "    sys.path.insert(0,str(Path(__file__).with_name('installation-context')/'daemon_instance_runtime.pyz'))\n"
        "    from agent_procutil._shell_caller_owned import serve\n"
        "    def admitted():\n"
        "        Path(os.environ['LIFECYCLE_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'isolated':sys.flags.isolated,"
        "'bytecode':sys.dont_write_bytecode,'request':request}),encoding='utf-8')\n"
        "        return 37\n"
        "    return serve(admitted,**lifetime)\n",
    )
    result = subprocess.run(
        [installer.shell, "-NoProfile", "-File", str(worker.with_name("init.ps1")),
         "-Action", "cell-provision", "-Context", str(installer.home / "context.json"),
         "-ExpectedMarketplaceId", "fixture-market"],
        capture_output=True, text=True, env=installer.env, cwd=installer.home,
        timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode == 37, result.stdout + result.stderr
    observed = json.loads(installer.proof.read_text())
    assert Path(observed["python"]).resolve() == installer.python
    assert observed["isolated"] == 1 and observed["bytecode"]
    assert "-CellWatchdogChild" in observed["request"]["argv"]
    assert 0 < observed["request"]["timeout"] < 20
    assert not installer.poison.exists()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("scenario", ["missing-context", "invalid-deadline"])
def test_invalid_cell_request_never_prepares_prerequisites(
    installer: LifecycleFixture, scenario: str,
) -> None:
    if installer.host == "bash":
        command = [installer.shell, str(installer.payload / "scripts/init.sh"), "cell-provision"]
        context_flags = ["--context", str(installer.home / "context.json"),
                         "--expected-marketplace-id", "fixture-market"]
    else:
        command = [installer.shell, "-NoProfile", "-File",
                   str(installer.payload / "scripts/init.ps1"), "-Action", "cell-provision"]
        context_flags = ["-Context", str(installer.home / "context.json"),
                         "-ExpectedMarketplaceId", "fixture-market"]
    if scenario == "invalid-deadline":
        command += context_flags
        installer.env["AGENT_MACHINES_INSTALL_DEADLINE_SEC"] = "invalid"
    result = subprocess.run(
        command, capture_output=True, text=True, env=installer.env, cwd=installer.home,
        timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not installer.calls.exists()
    assert not installer.poison.exists()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_native_stamp_binstub_does_not_select_installer_python(
    installer: LifecycleFixture,
) -> None:
    scripts = installer.payload / "scripts"
    suffix = "sh" if installer.host == "bash" else "ps1"
    (scripts / f"installation-context/legacy-entrypoint-probe.{suffix}").write_text("exit 0\n")
    uv = next(path for path in installer.tools.iterdir() if path.stem == "uv")
    uv.rename(uv.with_name("unavailable-uv" + uv.suffix))
    if installer.host == "bash":
        # Retain the existing no-flock transaction prerequisite, not eager UV selection.
        installer.env["COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"] = str(installer.python)
        for name in ("mv", "cmp"):
            executable = shutil.which(name)
            assert executable
            (installer.tools / name).symlink_to(executable)
        command = [installer.shell, str(scripts / "init.sh"), "stamp"]
    else:
        command = [installer.shell, "-NoProfile", "-File", str(scripts / "init.ps1"),
                   "-Action", "stamp-binstub"]
    result = subprocess.run(
        command,
        capture_output=True, text=True, env=installer.env, cwd=installer.home,
        timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    root = installer.home / ".agent-machines"
    assert Path((root / "payload-dir").read_text().strip()) == installer.payload
    command_name = "agent-machines.cmd" if os.name == "nt" else "agent-machines"
    assert (installer.home / ".local/bin" / command_name).is_file()
    assert not installer.calls.exists()
    assert not installer.poison.exists()


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("deadline", ["implicit", "plugin", "generic", "disabled"])
def test_bootstrap_forwarding_preserves_watchdog_deadline_mode(
    installer: LifecycleFixture, deadline: str,
) -> None:
    installer.env.pop("AGENT_MACHINES_INSTALL_DEADLINE_SEC", None)
    installer.env.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC", None)
    if deadline == "generic":
        installer.env["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "20"
    elif deadline != "implicit":
        installer.env["AGENT_MACHINES_INSTALL_DEADLINE_SEC"] = "0" if deadline == "disabled" else "20"
    entry = installer.payload / "scripts/init.ps1"
    text = entry.read_text()
    start = text.index("                $__wdDeadline = 480\n")
    end = text.index("                $__selfStageArgs =", start)
    decision = text[start:end]
    marker = "\n# The dependency-light cell-slot runner"
    assert text.count(marker) == 1
    entry.write_text(text.replace(marker, (
        "\n$__selfStageName = 'agent-machines'\n" + decision
        + "\n@{ explicit=$__wdExplicit; seconds=$__wdDeadline } | ConvertTo-Json -Compress\nexit 0\n"
        + marker
    )))
    result = subprocess.run(
        [installer.shell, "-NoProfile", "-File", str(entry), "-Action", "stamp"],
        capture_output=True, text=True, env=installer.env, cwd=installer.home,
        timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed["explicit"] == (deadline != "implicit")
    if deadline == "disabled":
        assert observed["seconds"] == 0
    else:
        assert 0 < observed["seconds"] <= (480 if deadline == "implicit" else 20)
    assert not installer.poison.exists()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_application_constructor_uses_independent_managed_requirement(
    bootstrap: LifecycleFixture,
) -> None:
    suffix = "sh" if bootstrap.host == "bash" else "ps1"
    engine = bootstrap.support / f"installer-engine.{suffix}"
    shutil.copy2(REPO / f"libs/installer-engine/installer-engine.{suffix}", engine)
    if suffix == "ps1":
        engine.write_text(engine.read_text().replace(
            "    return [pscustomobject]@{ ExitCode = $exitCode; Output = $output }",
            "    if ($exitCode -ne 0) { [Console]::Error.WriteLine($output) }\n"
            "    return [pscustomobject]@{ ExitCode = $exitCode; Output = $output }",
        ))
    slot = bootstrap.home / "application"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    entry = (PLUGIN / f"scripts/init.{suffix}").read_text()
    if bootstrap.host == "bash":
        start = entry.index("    (\n        unset UV_PYTHON")
        end = entry.index("    if [[ ! -x", start)
        program = (
            "set -euo pipefail\n"
            f'. {shlex.quote(str(bootstrap.payload / "scripts/installer-bootstrap.sh"))}\n'
            "_initialize_machines_bootstrap Inspect\n"
            f"VENV_DIR={shlex.quote(str(slot))}\n"
            + entry[start:end]
            + '[[ "$UV_PYTHON" == unrelated-python && "$UV_CONFIG_FILE" == missing-relative.toml ]]\n'
        )
        command = [bootstrap.shell, "--noprofile", "--norc", "-c", program]
    else:
        start = entry.index("    . (Join-Path (Get-MachinesInstallerSupport) 'bootstrap-uv.ps1')")
        end = entry.index("    if (-not (Test-Path $VenvPython))", start)
        program = (
            "$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2\n"
            f". {_ps(bootstrap.payload / 'scripts/versioned_runtime_native.ps1')}\n"
            f". {_ps(bootstrap.payload / 'scripts/installer-bootstrap.ps1')}\n"
            "Initialize-MachinesBootstrap -Operation Inspect\n"
            "function Write-Step {}\nfunction Write-Ok {}\nfunction Write-Warn {}\n"
            f"$VenvDir={_ps(slot)}\n$VenvPython={_ps(python)}\n$WheelProfile=''\n"
            + entry[start:end]
            + "if ($env:UV_PYTHON -cne 'unrelated-python' -or\n"
            "    $env:UV_CONFIG_FILE -cne 'missing-relative.toml') { throw 'caller environment changed' }\n"
            "exit 0\n"
        )
        command = [bootstrap.shell, "-NoProfile", "-Command", program]
    result = subprocess.run(
        command, capture_output=True, text=True, env=bootstrap.env, cwd=bootstrap.home,
        timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (slot / "pyvenv.cfg").is_file()
    observed = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import json,sys; print(json.dumps([sys.version_info[:2],sys.prefix,sys.base_prefix]))"],
        capture_output=True, text=True, env=bootstrap.env, cwd=bootstrap.home,
        timeout=15, creationflags=HEADLESS,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    version, prefix, base = json.loads(observed.stdout)
    assert version == [3, 11] and Path(prefix) != Path(base)
    expected_base = bootstrap.python.parent if os.name == "nt" else bootstrap.python.parent.parent
    assert Path(base).resolve() == expected_base
    assert not bootstrap.poison.exists()
