"""Native seed/source/construction controls, with no production acquisition."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import pytest

from .test_containers_legacy_runtime_uninstall import cases
from .test_uninstall_bootstrap import Removal, removal as removal

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
REPO = Path(__file__).resolve().parents[3]
HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CONSTRUCTION_PS_HOSTS: list[str] = [
    host for host in cases.HOSTS if Path(host).stem != "bash"
]
if os.name != "nt" and (core := shutil.which("pwsh")):
    CONSTRUCTION_PS_HOSTS.append(core)


@pytest.fixture
def seed(removal: Removal, materialized: bool) -> Removal:
    payload = removal.root.parent / "source/plugins/agent-containers"
    payload.parent.mkdir(parents=True)
    removal.payload.rename(payload)
    removal.payload = payload
    scripts = payload / "scripts"
    support = scripts
    if not materialized:
        support = payload.parents[1] / "libs/installer-engine"
        shutil.copytree(REPO / "libs/installer-engine", support)
        for name in (
            "bootstrap-uv.sh", "bootstrap-uv.ps1", "installer-instance-python-version.txt",
            "bootstrap-capture.ps1", "bootstrap-capture.exe",
        ):
            (scripts / name).unlink(missing_ok=True)
        capture = payload.parents[1] / "libs/agent-procutil/src/agent_procutil/native_capture.sh"
        capture.parent.mkdir(parents=True)
        shutil.copy2(REPO / "libs/agent-procutil/src/agent_procutil/native_capture.sh", capture)
        (scripts / "native_capture.sh").unlink(missing_ok=True)
        for suffix in ("sh", "ps1"):
            provider = support / f"bootstrap-uv.{suffix}"
            text = provider.read_text(encoding="utf-8")
            needle = '    exec "$@"\n' if suffix == "sh" else "    $result = & $InvokeCaptured @options\n"
            observation = (
                '    printf "%s\\n" "${*:2}" >> "$CONTAINERS_TEST_UV_CALLS"\n' if suffix == "sh" else
                "    [IO.File]::AppendAllText($env:CONTAINERS_TEST_UV_CALLS, "
                '($Arguments -join \' \') + "`n", [Text.UTF8Encoding]::new($false))\n'
            )
            assert text.count(needle) == 1
            provider.write_text(text.replace(needle, observation + needle), encoding="utf-8", newline="\n")
    if os.name != "nt":
        tools = Path(removal.env["PATH"])
        for name in ("sh", "cp", "chmod", "grep", "sed", "head", "tr", "uname", "date", "find"):
            executable = shutil.which(name)
            assert executable
            (tools / name).symlink_to(executable)
    if materialized:
        for suffix in ("sh", "ps1"):
            provider = support / f"bootstrap-uv.{suffix}"
            text = (REPO / "libs/installer-engine" / provider.name).read_text(encoding="utf-8")
            needle = '    exec "$@"\n' if suffix == "sh" else "    $result = & $InvokeCaptured @options\n"
            observation = (
                '    printf "%s\\n" "${*:2}" >> "$CONTAINERS_TEST_UV_CALLS"\n' if suffix == "sh" else
                "    [IO.File]::AppendAllText($env:CONTAINERS_TEST_UV_CALLS, "
                '($Arguments -join \' \') + "`n", [Text.UTF8Encoding]::new($false))\n'
            )
            assert text.count(needle) == 1
            provider.write_text(text.replace(needle, observation + needle), encoding="utf-8", newline="\n")
    for name in ("UV_INSTALL_DIR", "UV_UNMANAGED_INSTALL", "XDG_BIN_HOME", "XDG_DATA_HOME"):
        removal.env.pop(name, None)
    removal.env["CONTAINERS_TEST_SUPPORT"] = str(support)
    return removal


def _ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(seed: Removal, host: str, operation: str) -> subprocess.CompletedProcess[str]:
    scripts = seed.payload / "scripts"
    probe = (
        "import json,sys; print(json.dumps({'python':sys.executable,"
        "'version':list(sys.version_info[:3]),'stdin':sys.stdin.read()}))"
    )
    if Path(host).stem == "bash":
        code = (
            f". {shlex.quote(str(scripts / 'installer-bootstrap.sh'))}\n"
            f"_initialize_containers_bootstrap {operation} || exit $?\n"
            f'"$CONTAINERS_BOOTSTRAP_PYTHON" -I -S -B -c {shlex.quote(probe)}\n'
        )
        command = [host, "--noprofile", "--norc", "-c", code]
    else:
        code = (
            "$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2\n"
            f". {_ps(scripts / 'versioned_runtime_native.ps1')}\n"
            f". {_ps(scripts / 'installer-bootstrap.ps1')}\n"
            f"Initialize-ContainersBootstrap -Operation {operation}\n"
            f"& $script:ContainersInstallerSelection.Python -I -S -B -c {_ps(probe)}\n"
            "exit $LASTEXITCODE\n"
        )
        command = [host, "-NoProfile", "-Command", code]
    return subprocess.run(
        command, input="caller input\n", capture_output=True, text=True,
        env=seed.env, cwd=seed.root.parent, timeout=40, creationflags=HEADLESS,
    )


@pytest.mark.parametrize("host", cases.HOSTS)
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("operation", ["Inspect", "Prepare"])
def test_seed_selects_managed_python_without_using_ambient(
    seed: Removal, host: str, operation: str, record_property: Callable[[str, object], None],
) -> None:
    _, python, _ = cases.prepared_python_inputs("cpython@3.11.18")
    samples = []
    for _ in range(2):
        started = time.monotonic()
        result = _run(seed, host, operation)
        samples.append(time.monotonic() - started)
        assert result.returncode == 0, result.stdout + result.stderr
        proof = json.loads(result.stdout)
        assert Path(proof["python"]).resolve() == python
        assert proof["version"] == [3, 11, 18]
        assert proof["stdin"] == "caller input\n"
        assert not seed.poison.exists()
    calls = seed.calls.read_text(encoding="utf-8").splitlines()
    assert calls.count(
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18"
    ) == 2
    installs = [call for call in calls if call.startswith("python install")]
    assert len(installs) == (2 if operation == "Prepare" and Path(host).stem == "bash" else 0)
    assert not any("pip" in call for call in calls)
    uv_calls = [call for call in calls if not call.startswith("-I -S -B -c ")]
    expected = ["--version", "--version"]
    if operation == "Prepare" and Path(host).stem == "bash":
        expected.append("python install --no-bin --no-registry cpython@3.11.18")
    expected.append(
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18"
    )
    assert uv_calls == expected * 2
    record_property("containers_installer_seed", json.dumps({
        "shell": Path(host).stem, "operation": operation,
        "materialized": seed.env["CONTAINERS_TEST_SUPPORT"] == str(seed.payload / "scripts"),
        "samples_seconds": samples, "uv_calls": len(uv_calls), "network": "disabled",
    }, sort_keys=True))


@pytest.mark.parametrize("host", cases.HOSTS)
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("failure", ["missing-uv", "missing-request", "unprepared"])
def test_inspect_refuses_without_acquisition_or_ambient_fallback(
    seed: Removal, host: str, failure: str,
) -> None:
    if failure == "missing-uv":
        seed.uv.rename(seed.uv.with_name("unavailable-" + seed.uv.name))
    elif failure == "missing-request":
        Path(seed.env["CONTAINERS_TEST_SUPPORT"], "installer-instance-python-version.txt").unlink()
    else:
        store = seed.root.parent / "empty-store"
        store.mkdir()
        seed.env["UV_PYTHON_INSTALL_DIR"] = str(store)
    before = {str(path): path.read_bytes() for path in seed.root.rglob("*") if path.is_file()}
    result = _run(seed, host, "Inspect")
    assert result.returncode != 0, result.stdout + result.stderr
    assert not seed.poison.exists()
    calls = seed.calls.read_text().splitlines() if seed.calls.exists() else []
    assert not any("install" in call or "pip" in call for call in calls)
    assert {str(path): path.read_bytes() for path in seed.root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("host", cases.HOSTS)
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_explicit_cold_seed_uses_native_prerequisite_owner(seed: Removal, host: str) -> None:
    hidden = seed.uv.with_name("unavailable-" + seed.uv.name)
    seed.uv.rename(hidden)
    seed.env["CONTAINERS_TEST_UV"] = str(hidden)
    destination = seed.root.parent / "user tools"
    seed.env["UV_INSTALL_DIR"] = str(destination)
    if Path(host).stem == "bash":
        downloader = Path(seed.env["PATH"]) / "curl"
        downloader.write_text(
            "#!/bin/sh\ncat <<'INERT_INSTALLER'\n"
            'mkdir -p "$UV_INSTALL_DIR"\n'
            'cp "$CONTAINERS_TEST_UV" "$UV_INSTALL_DIR/uv"\n'
            'chmod +x "$UV_INSTALL_DIR/uv"\n'
            "INERT_INSTALLER\n", encoding="utf-8",
        )
        downloader.chmod(0o700)
    else:
        entry = seed.payload / "scripts/installer-bootstrap.ps1"
        text = entry.read_text()
        start = text.index("if ($PrepareUv) {")
        entry.write_text(text[:start] + (
            "if ($PrepareUv) {\n"
            " [void][IO.Directory]::CreateDirectory($env:UV_INSTALL_DIR)\n"
            " [IO.File]::Copy($env:CONTAINERS_TEST_UV,"
            " (Join-Path $env:UV_INSTALL_DIR 'uv.exe'))\nexit 0\n}\n"
        ), encoding="utf-8")
    result = _run(seed, host, "Prepare")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / seed.uv.name).is_file()
    assert not seed.poison.exists()
    assert (
        "python install --no-bin --no-registry cpython@3.11.18" in seed.calls.read_text()
    ) == (Path(host).stem == "bash")


@pytest.mark.parametrize("host", cases.HOSTS)
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("action", ["install", "stamp"])
def test_actual_installer_prepares_before_source_identity(
    seed: Removal, host: str, action: str,
) -> None:
    scripts = seed.payload / "scripts"
    proof = seed.root.parent / "source-proof.json"
    seed.env.update(
        CONTAINERS_SOURCE_PROOF=str(proof),
        COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN="fixture",
        COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET="fixture",
    )
    (scripts / "versioned_runtime_source.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['CONTAINERS_SOURCE_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'isolated':sys.flags.isolated,"
        "'bytecode':sys.dont_write_bytecode}),encoding='utf-8')\n"
        "print(json.dumps({'runtime_version':'1.0.0'}))\n", encoding="utf-8",
    )
    if Path(host).stem == "bash":
        entry = scripts / "init.sh"
        text = entry.read_text()
        marker = "\n_assert_source_runtime_identity() {"
        command = [host, str(entry), action]
    else:
        entry = scripts / "init.ps1"
        text = entry.read_text()
        marker = "\nfunction Assert-SourceRuntimeIdentity {"
        command = [host, "-NoProfile", "-File", str(entry), "-Action", action]
    entry.write_text(text[:text.index(marker)] + "\nexit 0\n", encoding="utf-8", newline="\n")
    result = subprocess.run(
        command, capture_output=True, text=True, env=seed.env, cwd=seed.root.parent,
        timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _, python, _ = cases.prepared_python_inputs("cpython@3.11.18")
    observed = json.loads(proof.read_text())
    assert Path(observed["python"]).resolve() == python
    assert observed["isolated"] == 1 and observed["bytecode"]
    assert not seed.poison.exists()
    assert ("python install " in seed.calls.read_text()) == (
        action == "install" and Path(host).stem == "bash"
    )


@pytest.mark.parametrize("host", cases.HOSTS)
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_constructor_uses_independent_managed_application_floor(seed: Removal, host: str) -> None:
    scripts = seed.payload / "scripts"
    _, helper, _ = cases.prepared_python_inputs("cpython@3.10.20")
    Path(seed.env["CONTAINERS_TEST_SUPPORT"], "installer-instance-python-version.txt").write_text(
        "cpython@3.10.20\n", encoding="ascii",
    )
    selected = _run(seed, host, "Inspect")
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert Path(json.loads(selected.stdout)["python"]).resolve() == helper
    slot = seed.root.parent / "application"
    if Path(host).stem == "bash":
        command = [host, str(scripts / "runtime-construction.sh"), str(seed.uv), str(slot)]
    else:
        command = [host, "-NoProfile", "-File", str(scripts / "runtime-construction.ps1"),
                   "-Uv", str(seed.uv), "-VenvDir", str(slot)]
    result = subprocess.run(
        command, capture_output=True, text=True, env=seed.env,
        timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    checked = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import json,sys;print(json.dumps({'version':list(sys.version_info[:2]),'prefix':sys.prefix}))"],
        capture_output=True, text=True, env=seed.env, timeout=15, creationflags=HEADLESS,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    observed = json.loads(checked.stdout)
    assert observed["version"] == [3, 11] and Path(observed["prefix"]) == slot
    assert not seed.poison.exists()


@pytest.mark.parametrize("host", CONSTRUCTION_PS_HOSTS)
def test_powershell_construction_removes_foreign_environment_keys(host: str) -> None:
    source = (REPO / "plugins/agent-containers/scripts/runtime-construction.ps1").read_text(
        encoding="utf-8",
    )
    start = source.index("$environment = Get-UvBootstrapEnvironment")
    environment = source[start:source.index("\n$python = Join-Path", start)]
    program = (
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2\n"
        f". {_ps(REPO / 'libs/installer-engine/bootstrap-uv.ps1')}\n"
        "$keys=@((Get-UvBootstrapEnvironment).Keys)+@('UV_CONFIG_FILE')\n"
        "foreach ($key in $keys) { [Environment]::SetEnvironmentVariable($key,'foreign','Process') }\n"
        + environment + "\n"
        "$remaining=@($keys | Where-Object {\n"
        " [Environment]::GetEnvironmentVariables('Process').Contains($_)\n"
        "})\n"
        "if ($remaining.Count) { throw ('Foreign environment keys remain: '+($remaining -join ',')) }\n"
        "if ($env:UV_NO_CONFIG -cne '1') { throw 'Construction configuration is not isolated' }\n"
        "'isolated'\n"
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", program],
        capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "isolated"
