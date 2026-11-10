"""Unit routing contracts, not evidence that a native Windows shell ran."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import owned_process
from native_shell_support import bash_path
from test_update_entry import PLUGIN, _arguments
from test_update_entry import update_entry_case as shared_update_entry_case

update_entry_case = shared_update_entry_case


@pytest.mark.parametrize("project_position", ["before", "after"])
@pytest.mark.parametrize("selection", ["configured", "old-engine", "unconfigured", "broken-helper"])
def test_posix_project_controller_precedes_manager_bootstrap(
    tmp_path, native_bash, project_position, selection,
) -> None:
    home = tmp_path / "home"
    tools = tmp_path / "tools"
    runtime = home / ".agent-worktrees"
    (runtime / "bin").mkdir(parents=True)
    tools.mkdir()
    (runtime / "current-version").write_text("selected\n")
    engine = tools / "engine"
    engine.write_text(
        '#!/bin/sh\n'
        'case " $* " in\n'
        '  *" -c "*) case "$*" in *"json.loads"*) printf "/tmp/probe-cache" ;; '
        '*) printf \'"%s"\\n\' "/tmp/probe-cache" ;; esac; exit 0 ;;\n'
        '  *" get update-controller "*) printf "%s\\0" "$@" > "$PROBE_ARGS"; '
        'case "$PROBE_MODE" in old-engine) exit 2 ;; unconfigured) exit 0 ;; esac; '
        'printf "tools/update.py\\n"; exit 0 ;;\n'
        'esac\n'
        'printf "%s\\0" "$@" > "$UPDATE_ARGS"\nexit 47\n'
    )
    engine.chmod(0o755)
    (runtime / "bin" / "resolve-runtime.sh").write_text(
        "AW_PY=" + shlex.quote(bash_path(native_bash, engine)) + "\n",
    )
    uname = tools / "uname"
    uname.write_text('#!/bin/sh\nprintf "Linux\\n"\n')
    uname.chmod(0o755)
    startup = tmp_path / "bash-env"
    startup.write_text('uname() { printf "Linux\\n"; }\n')
    python = tools / "python3"
    python.write_text(
        "#!/bin/sh\nexit 1\n" if selection == "broken-helper" else
        "#!/bin/sh\nexec " + shlex.quote(bash_path(native_bash, sys.executable)) + ' "$@"\n',
    )
    python.chmod(0o755)
    for name in ("curl", "git", "pwsh", "powershell.exe"):
        unavailable = tools / name
        unavailable.write_text('#!/bin/sh\nexit 96\n')
        unavailable.chmod(0o755)
    arguments = (
        ["--project", "sample", "update", "--force", "--skip-modules", "a b"]
        if project_position == "before"
        else ["update", "--force", "--project", "sample", "--skip-modules", "a b"]
    )
    env = os.environ.copy()
    env.update({
        "HOME": bash_path(native_bash, home),
        "USERPROFILE": str(home),
        "PATH": os.pathsep.join([str(tools), str(Path(native_bash).parent), env["PATH"]]),
        "BASH_ENV": bash_path(native_bash, startup),
        "PROBE_ARGS": bash_path(native_bash, tmp_path / "probe.args"),
        "UPDATE_ARGS": bash_path(native_bash, tmp_path / "update.args"),
        "PROBE_MODE": selection,
        "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        "WORKTREE_MANAGER_BOOTSTRAP_REPO": "-disabled-for-test",
        "WORKTREE_MANAGER_ROOT": bash_path(native_bash, home / ".worktree-manager"),
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": bash_path(
            native_bash, home / "providers",
        ),
    })
    result = owned_process.run_owned(
        [native_bash, bash_path(native_bash, PLUGIN / "bin" / "agent-worktrees"), *arguments],
        timeout=40, env=env,
    )
    expected = {"configured": 47, "old-engine": 126, "unconfigured": 126, "broken-helper": 125}
    assert result.returncode == expected[selection], result.stderr
    if selection != "broken-helper":
        assert _arguments(tmp_path / "probe.args")[-4:] == [
            "--project", "sample", "get", "update-controller",
        ]
    if selection == "configured":
        assert _arguments(tmp_path / "update.args")[-len(arguments):] == arguments
    else:
        assert not (tmp_path / "update.args").exists()


@pytest.mark.parametrize("flags", [
    ["--runtime-backend", "agent-worktrees=cpu"],
    ["--runtime-backend=agent-worktrees=cpu"],
])
def test_posix_runtime_backend_reaches_engine_before_bootstrap(tmp_path, native_bash, flags):
    home, tools = tmp_path / "home", tmp_path / "tools"
    runtime = home / ".agent-worktrees"
    (runtime / "bin").mkdir(parents=True)
    tools.mkdir()
    (runtime / "current-version").write_text("selected\n", encoding="utf-8")
    engine = tools / "engine"
    engine.write_text(
        '#!/bin/sh\n'
        'case " $* " in\n'
        '  *" -c "*) case "$*" in *"json.loads"*) printf "/tmp/probe-cache" ;; '
        '*) printf \'"%s"\\n\' "/tmp/probe-cache" ;; esac; exit 0 ;;\n'
        '  *" get update-controller "*) exit 0 ;;\n'
        'esac\n'
        'printf "%s\\0" "$@" > "$UPDATE_ARGS"\nexit 29\n',
        encoding="utf-8",
    )
    engine.chmod(0o755)
    (runtime / "bin" / "resolve-runtime.sh").write_text(
        "AW_PY=" + shlex.quote(bash_path(native_bash, engine)) + "\n", encoding="utf-8",
    )
    for name in ("curl", "git"):
        command = tools / name
        command.write_text('#!/bin/sh\nprintf touched > "$BOOTSTRAP_CALLED"\nexit 91\n', encoding="utf-8")
        command.chmod(0o755)
    startup = tmp_path / "bash-env"
    startup.write_text(
        'uname() { printf "Linux\\n"; }\n'
        'curl() { printf touched > "$BOOTSTRAP_CALLED"; return 91; }\n'
        'git() { printf touched > "$BOOTSTRAP_CALLED"; return 91; }\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    for key in tuple(env):
        if any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTHORIZATION")):
            env.pop(key)
    env.update({
        "HOME": bash_path(native_bash, home), "USERPROFILE": str(home), "AGENT_HOME": str(home),
        "COPILOT_HOME": str(home / ".copilot"),
        "PATH": os.pathsep.join([str(tools), str(Path(native_bash).parent), env["PATH"]]),
        "BASH_ENV": bash_path(native_bash, startup),
        "UPDATE_ARGS": bash_path(native_bash, tmp_path / "update.args"),
        "BOOTSTRAP_CALLED": bash_path(native_bash, tmp_path / "bootstrap-called"),
        "AGENT_SCRATCH_ROOT": bash_path(native_bash, tmp_path),
        "TMPDIR": bash_path(native_bash, tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
        "AGENT_WORKTREES_NO_SELFPROVISION": "1",
        "WORKTREE_MANAGER_BOOTSTRAP_REPO": "-disabled-for-test",
        "WORKTREE_MANAGER_ROOT": bash_path(native_bash, home / ".worktree-manager"),
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": bash_path(native_bash, home / "providers"),
    })
    arguments = ["--project", "sample", "update", *flags]
    result = owned_process.run_owned(
        [native_bash, bash_path(native_bash, PLUGIN / "bin" / "agent-worktrees"), *arguments],
        timeout=40, env=env,
    )
    assert result.returncode == 29, result.stderr
    assert _arguments(tmp_path / "update.args")[-len(arguments):] == arguments
    assert not (tmp_path / "bootstrap-called").exists()


@pytest.mark.skipif(sys.platform == "darwin", reason="native identity pin fixture requires pidfd or Windows")
def test_posix_controller_probe_timeout_retires_its_descendants(tmp_path, native_bash, monkeypatch):
    from agent_procutil import no_window_kwargs

    helpers = PLUGIN.parents[1] / "libs" / "agent-procutil" / "tests"
    monkeypatch.syspath_prepend(str(helpers))
    from caller_owned_fixture import identity
    from test_caller_owned_native import PinnedProcess

    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    (payload / "bin").mkdir(parents=True)
    (scripts / "installation-context").mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}', encoding="utf-8")
    shutil.copy2(PLUGIN / "bin" / "agent-worktrees", payload / "bin")
    entry = (PLUGIN / "bin" / "agent-worktrees-update-entry.sh").read_text(encoding="utf-8")
    assert entry.count("--timeout 30") == 2
    (payload / "bin" / "agent-worktrees-update-entry.sh").write_text(
        entry.replace("--timeout 30", "--timeout 5"), encoding="utf-8",
    )
    shutil.copy2(PLUGIN / "scripts" / "update_controller_probe.py", scripts)
    shutil.copy2(
        PLUGIN / "scripts" / "installation-context" / "daemon_instance_runtime.pyz",
        scripts / "installation-context",
    )
    home, tools = tmp_path / "home", tmp_path / "tools"
    runtime = home / ".agent-worktrees"
    (runtime / "bin").mkdir(parents=True)
    tools.mkdir()
    (runtime / "current-version").write_text("selected\n", encoding="utf-8")
    slow = tmp_path / "slow-engine.py"
    slow.write_text(
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, os.environ['PROBE_HELPERS'])\n"
        "from caller_owned_fixture import identity\n"
        "options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}\n"
        "child = subprocess.Popen([sys.executable, '-I', '-S', '-c', "
        "'import time; time.sleep(30)'], **options)\n"
        "root = Path(os.environ['PROBE_ROOT'])\n"
        "pending = root / 'pending.json'\n"
        "pending.write_text(json.dumps([identity(), identity(child.pid)]), encoding='utf-8')\n"
        "pending.replace(root / 'ready.json')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    engine = tools / "engine"
    engine.write_text(
        '#!/bin/sh\ncase " $* " in\n'
        '  *" -c "*) case "$*" in *"json.loads"*) printf "/tmp/probe-cache" ;; '
        '*) printf \'"%s"\\n\' "/tmp/probe-cache" ;; esac; exit 0 ;;\n'
        'esac\nexec ' + shlex.quote(bash_path(native_bash, sys.executable))
        + ' -I -S -B ' + shlex.quote(bash_path(native_bash, slow)) + '\n',
        encoding="utf-8",
    )
    engine.chmod(0o755)
    (runtime / "bin" / "resolve-runtime.sh").write_text(
        "AW_PY=" + shlex.quote(bash_path(native_bash, engine)) + "\n", encoding="utf-8",
    )
    python = tools / "python3"
    python.write_text("#!/bin/sh\nexec " + shlex.quote(bash_path(native_bash, sys.executable)) + ' "$@"\n')
    python.chmod(0o755)
    startup = tmp_path / "bash-env"
    startup.write_text('uname() { printf "Linux\\n"; }\n')
    env = {
        **os.environ,
        "HOME": bash_path(native_bash, home), "USERPROFILE": str(home),
        "PATH": os.pathsep.join([str(tools), str(Path(native_bash).parent), os.environ["PATH"]]),
        "BASH_ENV": bash_path(native_bash, startup),
        "PROBE_HELPERS": str(helpers), "PROBE_ROOT": str(tmp_path),
        "WORKTREE_MANAGER_BOOTSTRAP_REPO": "-disabled-for-test",
    }
    process = subprocess.Popen(
        [native_bash, bash_path(native_bash, payload / "bin" / "agent-worktrees"),
         "--project", "sample", "update"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    pins = []
    try:
        deadline = time.monotonic() + 10
        ready = tmp_path / "ready.json"
        while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.is_file(), "the controller probe did not start its descendants"
        pins = [PinnedProcess(row) for row in json.loads(ready.read_text(encoding="utf-8"))]
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 124, stdout + stderr
        assert all(pin.gone() for pin in pins), "controller probe descendants survived timeout"
    finally:
        if process.poll() is None:
            parent = PinnedProcess(identity(process.pid))
            try:
                parent.kill()
            finally:
                parent.close()
            process.wait(timeout=5)
        for pin in pins:
            try:
                pin.kill()
            finally:
                pin.close()
        process.stdout.close()
        process.stderr.close()


def _windows_shell_double(tmp, monkeypatch) -> None:
    tools = tmp / "tools"
    uname = tools / "uname"
    uname.write_text('#!/bin/sh\nprintf "%s\\n" "MINGW64_NT-fixture"\n')
    uname.chmod(0o755)
    convert = tools / "cygpath"
    convert.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$CYGPATH_ARGS"\n'
        'printf "%s\\n" \'C:\\fixture\\agent-worktrees-update-entry.ps1\'\n',
    )
    convert.chmod(0o755)
    host = tools / "pwsh"
    host.write_text(
        '#!/bin/sh\nprintf "%s\\0" "$@" > "$WINDOWS_ENTRY_ARGS"\n'
        'printf "%s" "$MSYS2_ARG_CONV_EXCL" > "$WINDOWS_CONVERSION"\n'
        'printf "%s" "$AGENT_WORKTREES_INTERNAL_UPDATE_PAYLOAD" > "$WINDOWS_PAYLOAD_MODE"\n'
        'exit 37\n',
    )
    host.chmod(0o755)
    monkeypatch.setenv("CYGPATH_ARGS", str(tmp / "cygpath.args"))
    monkeypatch.setenv("WINDOWS_ENTRY_ARGS", str(tmp / "windows.args"))
    monkeypatch.setenv("WINDOWS_CONVERSION", str(tmp / "windows.conversion"))
    monkeypatch.setenv("WINDOWS_PAYLOAD_MODE", str(tmp / "windows-payload-mode"))


@pytest.mark.parametrize("front", ["global", "payload"])
def test_update_entry_git_sh_delegates_before_windows_provider_parsing(
    update_entry_case, native_bash, monkeypatch, front,
) -> None:
    tmp, home, _resolver = update_entry_case
    _windows_shell_double(tmp, monkeypatch)
    registry = home / ".agent-worktrees/control-plane-providers.d"
    registry.mkdir()
    (registry / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager",
        "provider_root": r"C:\selected\manager",
        "command": [r"C:\selected\bin\worktree-manager.cmd"],
    }))
    relative = "bin/agent-worktrees" if front == "global" else "bin/payload/agent-worktrees"
    entry = PLUGIN / relative
    args = ["--project", "sample", "update", "--force", "--skip-modules",
            "module with spaces", "/literal/value"]
    result = subprocess.run(
        [native_bash, str(entry), *args], capture_output=True, timeout=15,
    )
    assert result.returncode == 37, result.stderr.decode()
    assert _arguments(tmp / "windows.args") == [
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        r"C:\fixture\agent-worktrees-update-entry.ps1", *args,
    ]
    assert _arguments(tmp / "cygpath.args") == [
        "-w", "--", str(PLUGIN / "bin/agent-worktrees-update-entry.ps1"),
    ]
    assert (tmp / "windows.conversion").read_text() == "*"
    assert (tmp / "windows-payload-mode").read_text() == ("1" if front == "payload" else "0")
    assert not (tmp / "resolved").exists()
    assert not (tmp / "python.args").exists()
    assert not (tmp / "manager-called").exists()
    assert not (tmp / "curl.args").exists()


@pytest.mark.parametrize("args", [
    ["status"], ["update", "--no-manager"], ["unknown", "update"],
    ["update", "--request-status", "synthetic-request"],
    ["update", "--request-status=synthetic-request"],
    ["update", "--recovery-status"],
    ["update", "--recover-after-restart"],
    ["update", "--recover-for-repair"],
])
def test_update_entry_git_sh_does_not_redirect_other_dispatch(
    update_entry_case, native_bash, monkeypatch, args,
) -> None:
    tmp, _home, _resolver = update_entry_case
    _windows_shell_double(tmp, monkeypatch)
    result = subprocess.run(
        [native_bash, str(PLUGIN / "bin/agent-worktrees"), *args],
        capture_output=True, timeout=15,
    )
    assert result.returncode == 19
    assert not (tmp / "windows.args").exists()
    assert (tmp / "resolved").exists()
