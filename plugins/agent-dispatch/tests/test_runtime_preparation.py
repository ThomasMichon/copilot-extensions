"""Preparation and supervisor preservation use the existing native lifecycle."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests import source_publication_fixture as fixture
from tests.test_action_native_ps1 import installer_policy, run_ps
from tests.test_install_lifecycle_emit import _run as run_lifecycle_boundary
from tests.test_installer_required_cutover import _run, _update_script


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("broken", [False, True])
def test_prepare_qualifies_without_publishing_or_reconciling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ext: str, broken: bool,
) -> None:
    if ext == "sh" and os.name == "nt":
        pytest.skip("native POSIX installer; Windows uses PowerShell")
    original = fixture.script

    def render(shell: str) -> str:
        text = original(shell)
        if shell == "ps1":
            assert text.count("\nInitialize-SourceRuntime\n") == 1
            text = text.replace(
                "\nInitialize-SourceRuntime\n",
                "\nfunction Invoke-RuntimeBrokerAdoption { throw 'Forbidden broker mutation' }\n"
                "Initialize-SourceRuntime\n",
            )
        return text

    monkeypatch.setattr(fixture, "script", render)
    env, root, sources = fixture.stage(tmp_path, broken_b=broken)
    fixture.activate(env, root, sources[0][1].runtime_version)
    preserved = (
        "current-version", "last-known-good", "payload-dir", "stamped-version",
        "deploy-manifest.json", "supervisor.env", "supervise-service.sh",
        "supervise-service.ps1", "update-in-progress", "bin/resolve-runtime.sh",
    )
    for name in preserved[2:]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"existing {name}\n", encoding="utf-8")
    before = {name: (root / name).read_bytes() for name in preserved}
    (root / "action-controls.json").write_text(json.dumps({
        name: {"disabled": True}
        for name in ("runtime-activation", "coordinator-replacement", "supervisor-reconcile")
    }), encoding="utf-8")
    payload, identity = sources[1]
    slot = root / "versions" / identity.runtime_version
    marker = slot / ".install-complete.json"
    marker_before = marker.read_bytes()
    result = fixture.run(ext, payload / "scripts" / f"install.{ext}", ["prepare"], env)
    assert (result.returncode != 0) is broken, result.stdout + result.stderr
    assert before == {name: (root / name).read_bytes() for name in preserved}
    assert marker.read_bytes() == marker_before
    assert not (Path(env["HOME"]) / ".local" / "bin").exists()
    assert not list(root.glob("*.runtime-publication*"))


def test_native_driver_preserves_explicit_supervisor_option(tmp_path: Path) -> None:
    text = (fixture.SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    parameters = text[text.index("[CmdletBinding()]"):text.index("if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8")]
    start = text.index("if ($env:OS -eq 'Windows_NT' -and")
    end = text.index("# Raise the shared self-stage", start)
    script = parameters + """
$env:OS = 'Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED = '0'
function Enter-NativeInstallerDriver {
    param($Payload, $Root, $Plugin, $Action, $Options, $RetainedRuntime, $RetainedRuntimeSha256)
    [Console]::WriteLine(($Options | ConvertTo-Json -Compress))
    return @{ Participant=$false; ExitCode=0 }
}
""" + text[start:end]
    result = _run(
        tmp_path, "ps1", script, "update", "-InstallDir", str(tmp_path / "runtime"),
        "-RequireGracefulCutover", "-PreserveSupervisors",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    options = json.loads(result.stdout.strip())
    assert set(options) == {"RequireGracefulCutover", "PreserveSupervisors"}
    path = fixture.REPO / "libs/installer-engine/installer_driver.py"
    spec = importlib.util.spec_from_file_location("preparation_installer_driver", path)
    assert spec is not None and spec.loader is not None
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    arguments = driver.native_arguments(SimpleNamespace(
        plugin="agent-dispatch", action="update", root=tmp_path / "runtime", options=options,
    ))
    assert "-RequireGracefulCutover" in arguments
    assert "-PreserveSupervisors" in arguments


@pytest.mark.skipif(os.name == "nt", reason="POSIX constructor; PowerShell reuse is covered above")
@pytest.mark.parametrize("broken", [False, True])
def test_prepare_constructs_once_then_reuses_with_real_kernel_completion(
    tmp_path: Path, broken: bool,
) -> None:
    env, root, sources = fixture.stage(tmp_path)
    fixture.activate(env, root, sources[0][1].runtime_version)
    payload, identity = sources[1]
    slot = root / "versions" / identity.runtime_version
    (slot / ".install-complete.json").unlink()
    before = {name: (root / name).read_bytes() for name in ("current-version", "last-known-good")}
    binaries = tmp_path / "fake-uv"
    binaries.mkdir()
    log = tmp_path / "uv-calls.jsonl"
    uv = binaries / "uv"
    uv.write_text(
        f"#!{sys._base_executable}\n"
        "import json, os, pathlib, shutil, subprocess, sys\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "args = sys.argv[1:]\n"
        "with log.open('a') as stream: stream.write(json.dumps(args) + '\\n')\n"
        "if args[:3] == ['pip', 'install', '--show-settings']:\n"
        "    print('PipInstallSettings { settings: PipSettings {\\n"
        "    index_locations: IndexLocations {\\n        flat_index: [],\\n    },\\n"
        "    exclude_newer: ExcludeNewer {\\n        global: None,\\n"
        "        package: ExcludeNewerPackage({}),\\n    },\\n}, }')\n"
        "elif args == ['--version']:\n"
        "    print('uv 0.9.0')\n"
        "elif args[0] == 'venv':\n"
        "    target = next(arg for arg in args[1:] if not arg.startswith('-'))\n"
        "    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', target], check=True)\n"
        "elif args[:2] == ['pip', 'install']:\n"
        "    python = args[args.index('--python') + 1]\n"
        "    target = subprocess.check_output([python, '-I', '-c',\n"
        "        'import sysconfig; print(sysconfig.get_path(\"purelib\"))'], text=True).strip()\n"
        f"    shutil.copytree({str(payload / 'src' / 'agent_dispatch')!r},\n"
        "        pathlib.Path(target) / 'agent_dispatch', dirs_exist_ok=True)\n"
        "    if os.environ.get('FIXTURE_BROKEN_INSTALL') == '1':\n"
        "        (pathlib.Path(target) / 'agent_dispatch/embody.py').write_text(\n"
        "            \"raise RuntimeError('broken constructed runtime')\\n\")\n"
        "else:\n"
        "    raise SystemExit('unexpected fixture UV call: ' + repr(args))\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env["PATH"] = str(binaries) + os.pathsep + env["PATH"]
    env["UV_CMD"] = str(uv)
    env["FIXTURE_BROKEN_INSTALL"] = str(int(broken))
    (root / "action-controls.json").write_text(
        '{"runtime-activation":{"disabled":true}}', encoding="utf-8",
    )
    entry = payload / "scripts" / "install.sh"
    first = fixture.run("sh", entry, ["prepare"], env, cwd=payload)
    assert (first.returncode != 0) is broken, first.stdout + first.stderr
    assert before == {name: (root / name).read_bytes() for name in before}
    assert not (root / "deploy-manifest.json").exists()
    assert not (Path(env["HOME"]) / ".local" / "bin").exists()
    if broken:
        assert not (slot / ".install-complete.json").exists()
        assert "failed its health gate" in first.stderr
        return
    marker = (slot / ".install-complete.json").read_bytes()
    calls = log.read_bytes()
    assert any(json.loads(line)[:2] == ["pip", "install"] for line in calls.splitlines())
    second = fixture.run("sh", entry, ["prepare"], env, cwd=payload)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (slot / ".install-complete.json").read_bytes() == marker
    assert log.read_bytes() == calls
    assert before == {name: (root / name).read_bytes() for name in before}
    assert not (root / "deploy-manifest.json").exists()
    assert not (Path(env["HOME"]) / ".local" / "bin").exists()


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("outcome", ["success", "failed", "unrouted", "unhealthy"])
def test_preserved_supervisors_never_reconcile_or_fall_back(
    tmp_path: Path, ext: str, outcome: str,
) -> None:
    script = _update_script(tmp_path, ext, strict=True, outcome=outcome)
    script = ("PRESERVE_SUPERVISORS=1\n" if ext == "sh" else "$PreserveSupervisors=$true\n") + script
    result = _run(tmp_path, ext, script)
    output = result.stdout + result.stderr
    assert (result.returncode == 0) is (outcome == "success"), output
    assert "supervisor" not in result.stdout.splitlines()
    assert "restart" not in result.stdout.splitlines()
    assert "retired" not in result.stdout.splitlines()
    assert "cleared" in result.stdout.splitlines()


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("action,strict,no_service,no_supervisor,valid", [
    ("update", True, False, False, True),
    ("update", False, False, False, False),
    ("update", True, True, False, False),
    ("update", True, False, True, False),
    ("install", False, False, False, False),
    ("prepare", False, False, False, False),
])
def test_preservation_option_rejects_conflicting_or_unsafe_actions(
    tmp_path: Path, ext: str, action: str, strict: bool,
    no_service: bool, no_supervisor: bool, valid: bool,
) -> None:
    text = (fixture.SCRIPTS / f"install.{ext}").read_text(encoding="utf-8")
    if ext == "sh":
        parse = text[text.index('ACTION="${1:-status}"'):text.index('INSTALL_DIR="${INSTALL_DIR:-')]
        script = 'set -eu\n_fail() { echo "$1"; }\n' + parse + '\necho BOUND\n'
        args = [action, "--preserve-supervisors"]
        args += ["--require-graceful-cutover"] if strict else []
        args += ["--no-service"] if no_service else []
        args += ["--no-supervisor"] if no_supervisor else []
    else:
        parse = text[text.index("[CmdletBinding()]"):text.index("if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8")]
        start = text.index("if ($RequireGracefulCutover -and ($Action")
        end = text.index("if ($Action -eq 'restore')", start)
        script = parse + text[start:end] + '\nWrite-Output BOUND\n'
        args = [action, "-PreserveSupervisors"]
        args += ["-RequireGracefulCutover"] if strict else []
        args += ["-NoService"] if no_service else []
        args += ["-NoSupervisor"] if no_supervisor else []
    result = _run(tmp_path, ext, script, *args)
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert ("BOUND" in result.stdout.splitlines()) is valid


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("action,admitted", [
    ("supervisor-reconcile", True),
    ("runtime-activation", False),
    ("coordinator-replacement", False),
])
def test_preservation_preflight_does_not_ignore_other_action_holds(
    tmp_path: Path, ext: str, action: str, admitted: bool,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text(
        json.dumps({action: {"disabled": True}}), encoding="utf-8",
    )
    if ext == "sh":
        script = (
            f"INSTALL_DIR='{root}'; ACTION=update; PRESERVE_SUPERVISORS=1\n"
            f". '{fixture.SCRIPTS / 'source-runtime.sh'}'\n"
            "_maintenance_preflight update\n"
        )
        result = _run(tmp_path, ext, script)
    else:
        script = installer_policy(tmp_path)
        script += fixture.function("ps1", "Assert-InstallActions")
        script += "\n$Action='update'; $PreserveSupervisors=$true\nAssert-InstallActions\n"
        result = run_ps(tmp_path, script)
    assert (result.returncode == 0) is admitted, result.stdout + result.stderr
    if not admitted:
        assert action in result.stdout + result.stderr


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_preservation_rejects_legacy_runtime_dependency_before_construction(
    tmp_path: Path, ext: str,
) -> None:
    (tmp_path / ".venv").mkdir()
    if ext == "sh":
        script = (
            f"INSTALL_DIR='{tmp_path}'; PRESERVE_SUPERVISORS=1; VERSIONED_RUNTIME=1\n"
            + fixture.function("sh", "_ensure_runtime") + "\n_ensure_runtime\n"
        )
    else:
        script = (
            "$ErrorActionPreference='Stop'\n"
            f"$InstallDir='{tmp_path}'; $PreserveSupervisors=$true; $VersionedRuntime=$true\n"
            + fixture.function("ps1", "Install-Runtime")
            + "\ntry { Install-Runtime } catch {\n"
            "[Console]::Error.WriteLine($_.Exception.Message); exit 1\n}\n"
        )
    result = _run(tmp_path, ext, script)
    assert result.returncode != 0
    assert "without a legacy .venv path" in result.stdout + result.stderr
    assert (tmp_path / ".venv").is_dir()
    assert not (tmp_path / "versions").exists()


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("preserve", [False, True])
def test_real_runtime_boundary_omits_collection_only_when_preserving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ext: str, preserve: bool,
) -> None:
    if ext == "sh" and os.name == "nt":
        pytest.skip("native POSIX collection boundary; Windows uses PowerShell")
    original = fixture.script

    def render(shell: str) -> str:
        text = original(shell)
        if shell == "sh":
            before = "_versioned_gc() { :; }"
            after = '_versioned_gc() { printf "gc\\n" >> "$FIXTURE_COLLECTION_LOG"; }'
        else:
            before = "function Invoke-VersionedGc { param($KeepPrev) }"
            after = (
                "function Invoke-VersionedGc { param($KeepPrev)\n"
                '[IO.File]::AppendAllText($env:FIXTURE_COLLECTION_LOG, "gc`n")\n}'
                "\nfunction Invoke-RuntimeBrokerAdoption {\n"
                '[IO.File]::AppendAllText($env:FIXTURE_BROKER_LOG, "broker`n")\n}'
            )
        assert text.count(before) == 1
        return text.replace(before, after)

    monkeypatch.setattr(fixture, "script", render)
    env, root, sources = fixture.stage(tmp_path)
    fixture.activate(env, root, sources[0][1].runtime_version)
    log = tmp_path / "collection.log"
    env["FIXTURE_COLLECTION_LOG"] = str(log)
    broker_log = tmp_path / "broker.log"
    env["FIXTURE_BROKER_LOG"] = str(broker_log)
    env["FIXTURE_PRESERVE_SUPERVISORS"] = str(int(preserve))
    payload, target = sources[1]
    result = fixture.run(ext, payload / "scripts" / f"install.{ext}", ["update"], env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "current-version").read_text().strip() == target.runtime_version
    if ext == "ps1":
        assert broker_log.read_text().splitlines() == ["broker"]
    if preserve:
        assert not log.exists()
    else:
        assert log.read_text().splitlines() == ["gc"]


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("preserve", [False, True])
def test_real_top_level_boundary_omits_instance_reconcile_only_when_preserving(
    tmp_path: Path, ext: str, preserve: bool,
) -> None:
    result, events = run_lifecycle_boundary(
        tmp_path, "update", powershell=ext == "ps1", preserve=preserve,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [event["outcome"] for event in events] == ["begin", "ok"]
    log = tmp_path / "instance-reconcile.log"
    if preserve:
        assert not log.exists()
    else:
        assert log.read_text().splitlines() == ["reconcile"]
