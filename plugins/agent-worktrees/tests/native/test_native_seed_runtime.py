"""Opt-in copied consumers call a real current runtime in a disposable root."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agent_procutil.owned_process import run_caller_owned
from agent_procutil import no_window_kwargs
from agent_worktrees.push_timeout import run_bounded

import test_python_runtime_install as runtime_fixture

pytestmark = [
    pytest.mark.timeout(900),
    pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native Windows required"),
]
native_runtime_source = runtime_fixture.native_runtime_source


def _ps(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(scope="module")
def installed_native_runtime(tmp_path_factory, native_runtime_source):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("Real runtime construction requires uv")
    owned = tmp_path_factory.mktemp("native-seed-runtime")
    home = owned / "home"
    home.mkdir()
    root = home / ".agent-worktrees"
    original, _original_commit = native_runtime_source
    source = owned / "consumer-source"
    shutil.copytree(
        original.parents[1], source,
        ignore=shutil.ignore_patterns(".git", "tests", "__pycache__", "*.pyc"),
    )
    plugin = source / "plugins/agent-worktrees"
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("GIT_", "COPILOT_EXTENSIONS_", "AGENT_WORKTREES_"))
        and key not in ("COPILOT_PLUGIN_ROOT", "COPILOT_PLUGIN_STAGED_FROM", "VIRTUAL_ENV",
                        "PYTHONPATH", "PYTHONHOME", "TMUX", "TMUX_PANE", "PSMUX_PANE")
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        AGENT_WORKTREES_STATUS_MONITOR="0", COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
    )
    git = shutil.which("git")
    assert git, "Real runtime provenance requires Git"
    hooks = owned / "empty-hooks"
    hooks.mkdir()
    git_prefix = [
        git, "-C", str(source), "-c", f"core.hooksPath={hooks}",
        "-c", "core.autocrlf=false", "-c", "core.longpaths=true",
        "-c", "commit.gpgsign=false",
        "-c", "user.name=Runtime test", "-c", "user.email=runtime-test@example.invalid",
    ]
    for arguments in (
        ["init", "-q", "--template", str(hooks)],
        ["add", "--all", "--force", "."],
        ["commit", "-qm", "Runtime-only consumer fixture"],
    ):
        outcome = run_caller_owned(
            [*git_prefix, *arguments], env=environment, cwd=owned, timeout=30,
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    committed = run_caller_owned(
        [*git_prefix, "rev-parse", "HEAD"], env=environment, cwd=owned, timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert committed.returncode == 0, committed.stdout + committed.stderr
    commit = committed.stdout.strip()
    result_path = owned / "installed.json"
    command = [
            sys.executable, "-I", "-B", str(plugin / "scripts/runtime_install.py"),
            "--root", str(root), "--plugin", str(plugin), "--payload", str(plugin),
            "--base-python", sys.executable, "--result", str(result_path), "--uv", uv,
    ]
    log = owned / "installer.log"
    with log.open("w", encoding="utf-8") as output:
        try:
            built = subprocess.run(
                command, env=environment, cwd=owned, timeout=600,
                stdout=output, stderr=subprocess.STDOUT, **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            pytest.fail("Construction did not settle:\n" + log.read_text(encoding="utf-8"))
    assert built.returncode == 0, log.read_text(encoding="utf-8")
    installed = json.loads(result_path.read_text(encoding="utf-8"))
    slot = root / "versions" / installed["runtime_version"]
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    prefix = [str(python), "-I", "-B", "-X", "utf8"]
    checked = run_caller_owned(
        [*prefix, "-c",
         "import json;from agent_worktrees._build_info import BUILD_INFO;"
         "print(json.dumps(BUILD_INFO))"],
        env=environment, cwd=owned, timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["commit"] == commit
    assert (root / "current-version").read_text().strip() == slot.name
    yield root, plugin, prefix, environment


@pytest.mark.parametrize("setup", [False, True], ids=["backend", "copied-setup"])
@pytest.mark.parametrize("seed_kind", [None, "new", "resume"], ids=["no-seed", "new", "resume"])
def test_current_runtime_records_real_native_consumer_lifetime(
    tmp_path, installed_native_runtime, setup, seed_kind,
):
    root, plugin, prefix, environment = installed_native_runtime
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    seed_text = "literal native seed\nsingle ' double \" \u03a9"
    seed_stage = (
        "from agent_worktrees import launch_seed_state;"
        f"seed=launch_seed_state.stage(created.yaml_path,kind={seed_kind!r},text={seed_text!r});"
        if seed_kind else "seed=None;"
    )
    initialize = (
        "import json,sys;from pathlib import Path;from uuid import uuid4;"
        "from agent_worktrees import tracking,session_shell,config as cfg;"
        "checkout,source,root=map(Path,sys.argv[1:]);"
        "project=cfg.project_dir('native-fixture');project.mkdir(parents=True,exist_ok=True);"
        "(project/'config.yaml').write_text(json.dumps({'repo_name':'native-fixture','machine':'fixture',"
        "'repos':{'native-fixture':{'anchor':str(checkout),'default_branch':'dev'}}}),encoding='utf-8');"
        f"created=tracking.create_new_record({tmp_path.name!r},'worktree/native-fixture',"
        "str(checkout),'native-fixture','fixture','win32',checkout);"
        + seed_stage +
        "attempt,resolution=uuid4().hex,uuid4().hex;"
        "shell=session_shell.prepare(created.yaml_path,source,installation_roots=(root,),attempt_id=attempt);"
        "print(json.dumps({'record':str(created.yaml_path),'copied':str(shell.directory),"
        "'attempt':attempt,'resolution':resolution,'seed_id':seed.seed_id if seed else None}))"
    )
    # Use the real copy owner; no finite callback or runtime selector is replaced.
    prepared = run_caller_owned(
        [*prefix, "-c", initialize, str(checkout),
         str(plugin / "scripts/default-setup.ps1"), str(root)],
        env=environment, cwd=checkout, timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    preparation = json.loads(prepared.stdout)
    record_path, copied = (Path(preparation[key]) for key in ("record", "copied"))
    correlation = (
        f"-LaunchAttemptId {_ps(preparation['attempt'])} "
        f"-LaunchResolutionId {_ps(preparation['resolution'])} "
    )
    seed_flag = f"-LaunchSeedId {_ps(preparation['seed_id'])} " if seed_kind else ""
    captured = tmp_path / "backend.json"
    backend = tmp_path / "backend.ps1"
    backend.write_text(
        "$ready=$false\n$deadline=[DateTime]::UtcNow.AddSeconds(15)\n"
        "while (-not $ready -and [DateTime]::UtcNow -lt $deadline) {\n"
        f"  foreach ($file in (Get-ChildItem -LiteralPath {_ps(copied.parent)} -Filter '*.execution.json')) {{\n"
        "    $saved=Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json\n"
        "    foreach ($event in $saved.events) {\n"
        "      if ($event.phase -eq 'process-spawned' -and $event.details.pid -eq $PID) { $ready=$true }\n"
        "    }\n  }\n"
        "  if (-not $ready) { Start-Sleep -Milliseconds 20 }\n}\n"
        "if (-not $ready) { throw 'Fixture did not receive its actual spawn receipt' }\n"
        "$reply=@{args=@($args);pid=$PID;owner=[int]$env:FIXTURE_OWNER_PID}"
        " | ConvertTo-Json -Compress\n"
        f"[IO.File]::WriteAllText({_ps(captured)},$reply)\nexit 7\n",
        encoding="utf-8",
    )
    argv = ["", "single ' quote", 'double " quote', "; $literal", "\u03a9",
            "2026-01-01T00:00:00Z", "--", "--allow-all"]
    driver = tmp_path / "driver.ps1"
    invocation = (
        f"& {_ps(copied / 'default-setup.ps1')} -Recovery "
        f"-LaunchSeedPreparation {'seed' if seed_kind else 'admission'} -LaunchSeedNativeRoot {_ps(root)} "
        f"-LaunchSeedRecord {_ps(record_path)} -CopilotPath {_ps(backend)} "
        f"-RuntimePython {_ps(prefix[0])} "
        + correlation + seed_flag +
        f"-NativeArgumentsJson {_ps(json.dumps({'args': argv}))}\nexit $LASTEXITCODE\n"
        if setup else
        f". {_ps(copied / 'native-seed-boundary.ps1')}\n"
        f"$code=Invoke-AwNativeSeedBackend -Root {_ps(root)} -Record {_ps(record_path)} "
        + correlation +
        (f"-SeedId {_ps(preparation['seed_id'])} " if seed_kind else "") +
        f"-Command @({_ps(shutil.which('pwsh'))},'-NoProfile','-File',{_ps(backend)},"
        + ",".join(_ps(arg) for arg in argv) + ")\nexit $code\n"
    )
    driver.write_text(
        "$env:FIXTURE_OWNER_PID=[string]$PID\n"
        f"$env:PATH={_ps(Path(shutil.which('pwsh')).parent)}\n" + invocation,
        encoding="utf-8",
    )
    try:
        completed = run_bounded(
            [shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(driver)],
            env=environment, cwd=checkout, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        evidence = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in copied.parent.glob("*.execution.json")
        ]
        pytest.fail(
            f"Native consumer timed out: {exc.stdout!r}\n{exc.stderr!r}\n"
            f"Execution observations: {evidence!r}; backend observed={captured.exists()}"
        )
    assert completed.returncode == 7, completed.stdout + completed.stderr + repr([
        json.loads(path.read_text(encoding="utf-8"))
        for path in copied.parent.glob("*.execution.json")
    ])
    assert not completed.stderr
    reply = json.loads(captured.read_text(encoding="utf-8"))
    expected = argv
    if seed_kind:
        expected = [*argv, "--interactive", seed_text]
    assert reply["args"] == expected
    assert (reply["pid"] == reply["owner"]) is (setup and not seed_kind)
    observations = list(copied.parent.glob("*.execution.json"))
    assert len(observations) == (2 if setup and seed_kind else 1)
    executions = [json.loads(path.read_text(encoding="utf-8")) for path in observations]
    assert seed_text not in json.dumps(executions)
    execution, = [
        saved for saved in executions
        if not setup or not seed_kind or saved["parent_id"] is not None
    ]
    assert [event["phase"] for event in execution["events"]] == [
        "execution-requested", *(["seed-claimed"] if seed_kind else []),
        "process-spawned", "execution-returned",
    ]
    spawn, = [event["details"] for event in execution["events"] if event["phase"] == "process-spawned"]
    assert spawn["pid"] == reply["pid"]
    assert spawn["process_start_time"]
    assert spawn["boundary"] == ("launcher-command" if setup and not seed_kind else "backend-command")
    assert execution["events"][-1]["details"]["exit_code"] == 7
    if setup and seed_kind:
        outer, = [saved for saved in executions if saved["parent_id"] is None]
        assert execution["parent_id"] == outer["operation_id"]
        assert [event["phase"] for event in outer["events"]] == [
            "execution-requested", "process-spawned", "execution-returned",
        ]
        assert outer["events"][1]["details"]["pid"] == reply["owner"]
        assert outer["events"][-1]["details"]["exit_code"] == 7
    if seed_kind:
        finished = run_bounded(
            [*prefix, "-c",
             "import sys;from pathlib import Path;from agent_worktrees import launch_seed_state;"
             "assert launch_seed_state.peek(Path(sys.argv[1]),read_only=True) is None",
             str(record_path)],
            env=environment, cwd=checkout, timeout=30,
        )
        assert finished.returncode == 0, finished.stdout + finished.stderr


def test_current_runtime_refuses_a_different_installation_root(tmp_path, installed_native_runtime):
    _root, _plugin, prefix, environment = installed_native_runtime
    other = tmp_path / "different-installation"
    result = run_caller_owned(
        [*prefix, "-m", "agent_worktrees.launch_seed_native", "--contract-version", "1",
         "--root", str(other), "--phase", "prepare", "--record", str(tmp_path / "missing.yaml"),
         "--", "never-start.exe"],
        env=environment, cwd=tmp_path, timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 3
    assert not other.exists()
    assert not list(tmp_path.glob("launch-attempts"))
