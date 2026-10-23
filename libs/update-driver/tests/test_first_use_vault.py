"""Opt-in actual materialized Vault installation through the cold native handoff."""

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs
from update_driver import drain, foundation_status, status
from update_driver import first_use as provision
from update_driver.foundation_inputs import GENERATED, PACKAGES
from update_driver.foundation_inputs import inspect as inspect_foundation

from test_first_use_genesis import observe
from test_first_use_native import native_command


native_genesis = pytest.mark.skipif(
    not (
        (os.name == "nt" and os.environ.get("UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS") == "1")
        or (sys.platform == "linux" and os.environ.get("UPDATE_DRIVER_NATIVE_LINUX_GENESIS") == "1")
    ),
    reason="explicit private cold foundation and actual Vault installation downloads",
)


@pytest.fixture
def materialized_vault(owning_context):
    payload, installation, project = owning_context
    repository = Path(__file__).resolve().parents[3]
    shutil.copytree(
        repository / "plugins" / "agent-vault", payload, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
        ),
    )
    previous = sys.path[:]
    try:
        release = runpy.run_path(str(repository / "tools" / "materialize_main.py"))
        result = release["materialize"](payload.parents[1], canonical_root=repository)
    finally:
        sys.path[:] = previous
    assert not any(line.startswith("SKIP ") for line in result), result
    assert (payload / "scripts" / "installer_engine.py").is_file()
    assert (payload / "scripts" / "installation-context" / "installation_context.py").is_file()
    return payload, installation, project


def test_actual_materialized_vault_admits_without_external_source_or_installation_writes(
    materialized_vault,
):
    payload, installation, project = materialized_vault
    before = {str(path.relative_to(payload)): path.read_bytes()
              for path in payload.rglob("*") if path.is_file()}
    admission = provision.inspect(payload, installation, project)
    foundation = inspect_foundation(payload / "libs" / "update-driver")
    assert {package.name for package in foundation.packages} == set(PACKAGES)
    assert admission["plugin"] == "agent-vault" and admission["mode"] == "legacy"
    assert admission["source"]["kind"] == "directory"
    assert not installation.exists()
    assert {str(path.relative_to(payload)): path.read_bytes()
            for path in payload.rglob("*") if path.is_file()} == before


def assert_stamp_only(installation):
    pointer = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    for name in ("first-use.ps1", "first-use.sh"):
        assert (pointer / "libs" / "update-driver" / "scripts" / name).is_file()
    command = installation.parent / ".local" / "bin" / (
        "agent-vault.ps1" if os.name == "nt" else "agent-vault"
    )
    assert command.is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert not (installation / "service.ps1").exists()
    assert not (installation.parent / ".config" / "systemd").exists()


@pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native stamp execution is qualified only on Windows and Linux",
)
def test_actual_materialized_stamp_uses_owned_queue_without_building_vault(
    materialized_vault, area,
):
    payload, installation, project = materialized_vault
    before = {str(path.relative_to(payload)): path.read_bytes()
              for path in payload.rglob("*") if path.is_file()}
    root = area / "queue"
    request = provision.enqueue(
        payload, installation, project, root=root, installer_action="stamp",
    )
    assert not installation.exists()
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    assert json.loads(result.spec.source)["installer_action"] == "stamp"
    assert_stamp_only(installation)
    assert {str(path.relative_to(payload)): path.read_bytes()
            for path in payload.rglob("*") if path.is_file()} == before


def vault_command(payload):
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        return [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                str(payload / "bin" / "agent-vault.ps1"), "--help"]
    if sys.platform != "linux":
        pytest.skip("cold payload first use is not qualified on this platform")
    return [str(payload / "bin" / "agent-vault"), "--help"]


def stamp_vault(payload, installation, project):
    if os.name == "nt":
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
            str(payload / "scripts" / "install.ps1"), "-Action", "stamp",
            "-InstallDir", str(installation),
        ]
    else:
        command = [shutil.which("bash"), str(payload / "scripts" / "install.sh"),
                   "stamp", "--install-dir", str(installation)]
    result = subprocess.run(
        command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=90, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    if os.name == "nt":
        assert snapshot.is_relative_to(installation / "snapshots")
    else:
        assert snapshot == payload
    command = installation.parent / ".local" / "bin" / (
        "agent-vault.ps1" if os.name == "nt" else "agent-vault"
    )
    if os.name == "nt":
        return snapshot, [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(command), "--help",
        ]
    return snapshot, [shutil.which("bash"), str(command), "--help"]


@pytest.mark.parametrize(("entry", "reason"), [
    (entry, reason)
    for entry in ("payload", "primary")
    for reason in ("foundation-refused", "missing-driver", "no-self", "pinned")
] + [("primary", "old-dispatcher")])
def test_actual_cold_payload_command_uses_packaged_owner_or_refuses_without_plugin_mutation(
    materialized_vault, monkeypatch, reason, entry,
):
    payload, installation, project = materialized_vault
    if entry == "primary":
        payload, command = stamp_vault(payload, installation, project)
    else:
        command = vault_command(payload)
    root = installation.parent / ".copilot-extensions" / "update-driver"
    scripts = payload / "libs" / "update-driver" / "scripts"
    if reason == "foundation-refused":
        (scripts / "foundation-produce.ps1").write_text(
            "param([string]$Root,[string]$SourceHash)\n"
            "[IO.File]::WriteAllText((Join-Path $Root 'entered'), $SourceHash)\n"
            "exit 37\n", encoding="utf-8",
        )
        (scripts / "foundation-produce.sh").write_text(
            '#!/usr/bin/env bash\nprintf "%s" "$2" >"$1/entered"\n'
            "printf 'fixture foundation refusal\\n' >&2\nexit 37\n", encoding="utf-8",
        )
    elif reason == "missing-driver":
        shutil.rmtree(scripts)
    elif reason == "old-dispatcher":
        gate = payload / "scripts" / ("runtime-gate.ps1" if os.name == "nt" else "runtime-gate.sh")
        gate.write_text(
            "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'foreground-entered'), 'yes')\nexit 0\n"
            if os.name == "nt" else
            '#!/usr/bin/env bash\nprintf yes >"$(dirname "$0")/foreground-entered"\nexit 0\n',
            encoding="utf-8",
        )
    elif reason == "no-self":
        monkeypatch.setenv("AGENT_VAULT_NO_SELFPROVISION", "1")
    else:
        monkeypatch.setenv("AGENT_VAULT_PINNED_CONTEXT", "{}")
    before = {str(path.relative_to(payload)): path.read_bytes()
              for path in payload.rglob("*") if path.is_file()}
    before_installation = {str(path.relative_to(installation)): path.read_bytes()
                           for path in installation.rglob("*") if path.is_file()}
    result = subprocess.run(
        command, env=dict(os.environ), cwd=project,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        timeout=90, **no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not (installation / "versions").exists(), result.stdout + result.stderr
    assert not (installation / ".provision.lock").exists()
    assert not (installation / ".provision-status").exists()
    if entry == "payload":
        assert not installation.exists(), result.stdout + result.stderr
    if reason in {"no-self", "pinned"}:
        assert {str(path.relative_to(installation)): path.read_bytes()
                for path in installation.rglob("*") if path.is_file()} == before_installation
    assert {str(path.relative_to(payload)): path.read_bytes()
            for path in payload.rglob("*") if path.is_file()} == before
    if reason == "foundation-refused":
        assert (root / "entered").is_file(), result.stdout + result.stderr
        assert not status(root=root)
        assert list(root.glob("first-use-*.log"))
        assert "Foundation construction failed" in result.stderr or "fixture foundation refusal" in result.stderr
    else:
        assert not root.exists(), result.stdout + result.stderr


def cold_environment(installation, project, area):
    profile = installation.parent
    env = dict(os.environ)
    for key, relative in {
        "APPDATA": "AppData/Roaming", "LOCALAPPDATA": "AppData/Local",
        "PROGRAMDATA": "ProgramData", "PSModulePath": "powershell-modules",
        "XDG_CONFIG_HOME": ".config", "XDG_CACHE_HOME": ".cache",
        "XDG_DATA_HOME": ".local/share", "XDG_STATE_HOME": ".local/state",
        "XDG_RUNTIME_DIR": "run", "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp",
        "COPILOT_HOME": ".copilot", "UV_CACHE_DIR": "uv-cache",
        "UV_PYTHON_INSTALL_DIR": "python",
    }.items():
        directory = profile / relative
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    env.update(
        AGENT_VAULT_HOME=str(installation), UV_PYTHON_PREFERENCE="only-managed",
        UV_PYTHON_DOWNLOADS="automatic", UV_NO_PROGRESS="1",
    )
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    env.pop("UPDATE_DRIVER_WITNESS", None)
    if os.name == "nt":
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        isolation = subprocess.run(
            [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
             "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             "if (Get-Command python -CommandType Application -ErrorAction SilentlyContinue) { exit 92 }; exit 0"],
            env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert isolation.returncode == 0, "native qualification must not expose host Python"
    else:
        tools = area / "native-tools"
        tools.mkdir()
        for name in (
            "sh", "bash", "dirname", "basename", "uname", "readlink", "stat", "find", "sort",
            "sha256sum", "iconv", "cat", "mkdir", "sync", "awk", "date", "mktemp", "mv",
            "rm", "cp", "flock", "grep", "sleep", "setsid", "mkfifo", "rmdir", "curl",
            "tar", "gzip", "head", "chmod", "sed", "tr", "ps", "realpath", "cut",
            "wc", "od", "id", "getent", "hostname", "ln", "touch", "tail",
        ):
            executable = shutil.which(name)
            assert executable, f"native tool unavailable: {name}"
            (tools / name).symlink_to(executable)
        env["PATH"] = str(tools)
    return env


@pytest.mark.parametrize("entry", ["payload", "primary"])
def test_cold_commands_normalize_project_and_foreign_runtime_environment(
    materialized_vault, area, entry,
):
    payload, installation, project = materialized_vault
    if entry == "primary":
        payload, command = stamp_vault(payload, installation, project)
    else:
        command = vault_command(payload)
    scripts = payload / "libs" / "update-driver" / "scripts"
    if os.name == "nt":
        (scripts / "first-use.ps1").write_text(
            "param([string]$Root,[string]$Payload,[string]$RuntimeRoot,[string]$Project,[switch]$Wait)\n"
            "$id = if (Test-Path Env:AGENT_VAULT_INSTALLATION_ID) { $env:AGENT_VAULT_INSTALLATION_ID } else { '<absent>' }\n"
            "$pipe = if (Test-Path Env:AGENT_VAULT_PIPE) { $env:AGENT_VAULT_PIPE } else { '<absent>' }\n"
            "[IO.File]::WriteAllLines((Join-Path $Project 'handoff.txt'), "
            "@($Root,$Payload,$RuntimeRoot,$Project,$id,$pipe))\nexit 37\n",
            encoding="utf-8",
        )
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        script = command[command.index("-File") + 1]
        command = [
            command[0], "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            f"Set-Location -LiteralPath {quote(project)}; "
            f"[IO.Directory]::SetCurrentDirectory({quote(area)}); "
            f"& {quote(script)} --help; exit $LASTEXITCODE",
        ]
    else:
        (scripts / "first-use.sh").write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$1" "$2" "$3" "$4" '
            '"${AGENT_VAULT_INSTALLATION_ID-<absent>}" "${AGENT_VAULT_PIPE-<absent>}" '
            '>"$4/handoff.txt"\nexit 37\n', encoding="utf-8",
        )
        link = area / "project-link"
        link.symlink_to(project, target_is_directory=True)
        command = [shutil.which("bash"), "-c", 'cd -- "$1"; shift; exec "$@"',
                   "bash", str(link), *command]
    result = subprocess.run(
        command, env={**os.environ, "AGENT_VAULT_INSTALLATION_ID": "fixture-foreign",
                      "AGENT_VAULT_PIPE": "fixture-foreign-pipe"},
        cwd=area, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=90, **no_window_kwargs(),
    )
    assert result.returncode == 37, result.stdout + result.stderr
    assert (project / "handoff.txt").read_text(encoding="utf-8-sig").splitlines() == [
        str(installation.parent / ".copilot-extensions" / "update-driver"),
        str(payload), str(installation), str(project), "<absent>", "<absent>",
    ]
    assert not (installation.parent / ".copilot-extensions" / "update-driver").exists()


@native_genesis
def test_actual_vault_installs_and_dispatches_after_owning_source_removal(
    materialized_vault, area, native_window_observation,
):
    payload, installation, project = materialized_vault
    library = Path(__file__).resolve().parents[2]
    inputs = area / "foundation-source"
    for name in PACKAGES:
        shutil.copytree(
            library / name, inputs / name,
            ignore=lambda _path, names: {
                name for name in names if name in GENERATED
                or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
            },
        )
    source = inputs / "update-driver"
    root = area / "cold"
    profile = installation.parent
    env = cold_environment(installation, project, area)
    tools = Path(env["PATH"])

    def diagnostic():
        return "\n".join(
            f"{path.relative_to(root)}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in root.rglob("*.log")
        )

    def run_request(installer_action="provision"):
        worker = None
        descriptor = None
        command = native_command(
            root, payload, installation, project, source=source,
            client_only=installer_action == "provision",
        )
        if os.name == "nt":
            if installer_action == "provision":
                command[-1] = "Client-Only"
            command += ["-Mode", "LaUnCh"]
        if installer_action != "provision":
            command += (
                ["-InstallerAction", "StAmP"] if os.name == "nt"
                else ["--installer-action", installer_action]
            )
        launched = subprocess.run(
            command,
            env=env, cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", timeout=90, **no_window_kwargs(),
        )
        assert launched.returncode == 0, launched.stdout + launched.stderr
        try:
            if os.name == "nt":
                from test_foundation_genesis_process import pinned_worker
                worker = pinned_worker(json.loads(launched.stdout))
                exited = lambda: worker.poll() is not None
            else:
                from agent_procutil._caller_owned import _pidfd_exited
                from agent_procutil._linux_pidfd import open_pidfd
                assert launched.stdout.startswith("launched:"), launched.stdout
                descriptor = open_pidfd(int(launched.stdout.split()[0].partition(":")[2]))
                exited = lambda: _pidfd_exited(descriptor)
            observe(exited, 900, diagnostic)
        finally:
            if worker is not None:
                try:
                    worker.wait(timeout=900)
                finally:
                    worker.close()
            if descriptor is not None:
                try:
                    observe(lambda: _pidfd_exited(descriptor), 900, diagnostic)
                finally:
                    os.close(descriptor)

    run_request("stamp")
    stamped = status(root=root)
    assert len(stamped) == 1, diagnostic()
    stamp = stamped[0]
    assert stamp.state == "completed" and stamp.exit_code == 0, diagnostic()
    assert stamp.spec.argv == ("--installer-action", "stamp")
    assert json.loads(stamp.spec.source)["installer_action"] == "stamp"
    assert foundation_status(root=root).state == "completed"
    assert_stamp_only(installation)
    run_request()
    requests = status(root=root)
    assert len(requests) == 2, diagnostic()
    request = next(item for item in requests if item.id != stamp.id)
    assert request.state == "completed" and request.exit_code == 0, diagnostic()
    assert request.spec.argv == ("--client-only",)
    assert "service skipped" in request.log_path.read_text(errors="replace")
    assert foundation_status(root=root).state == "completed"
    selector = installation / "current-version"
    version = selector.read_text().strip()
    slot = installation / "versions" / version
    completion = slot / ".install-complete.json"
    completed = completion.read_bytes()
    assert json.loads(completed)["payload_hash"]
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # A real failed candidate must not replace the healthy selected runtime.
    project_file = payload / "pyproject.toml"
    project_file.write_text(re.sub(
        r'(?m)^version\s*=\s*"[^"]+"', 'version = "99.0.0"',
        project_file.read_text(),
    ), encoding="utf-8")
    manifest_path = payload / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = "99.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (payload / "src" / "agent_vault" / "__main__.py").write_text(
        'raise RuntimeError("fixture failed installed Vault entrypoint")\n', encoding="utf-8",
    )
    run_request()
    requests = status(root=root)
    assert len(requests) == 3, diagnostic()
    failed = next(item for item in requests if item.id not in (stamp.id, request.id))
    assert failed.state == "completed" and failed.exit_code != 0, diagnostic()
    assert "fixture failed installed Vault entrypoint" in failed.log_path.read_text(errors="replace")
    assert selector.read_text().strip() == version and completion.read_bytes() == completed
    assert not (installation / "versions" / "99.0.0" / ".install-complete.json").exists()
    # Source removal must not strand a nominally successful install.
    payload.rename(payload.with_name("removed-vault"))
    inputs.rename(area / "removed-foundation-source")
    command = profile / ".local" / "bin" / ("agent-vault.ps1" if os.name == "nt" else "agent-vault")
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(command), "--help"]
        if os.name == "nt" else [str(tools / "bash"), str(command), "--help"]
    )
    dispatch = subprocess.run(
        argv, env={**env, "AGENT_VAULT_NO_SELFPROVISION": "1"}, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    assert dispatch.returncode == 0 and "usage:" in dispatch.stdout, (
        dispatch.stdout, dispatch.stderr, diagnostic(),
    )
    origin = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_vault, json; print(json.dumps(agent_vault.__file__))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert origin.returncode == 0, origin.stdout + origin.stderr
    assert Path(json.loads(origin.stdout)).is_relative_to(slot)
    assert completion.read_bytes() == completed
    assert len(status(root=root)) == 3, "healthy dispatch must not submit installation work"
    assert not (installation / "service.ps1").exists()
    assert not (profile / ".config" / "systemd").exists()
