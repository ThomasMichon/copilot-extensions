"""Copied production POSIX setup with a real, disposable selected runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import time

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import run_caller_owned

import test_native_seed_runtime as runtime_fixture

installed_native_runtime = runtime_fixture.installed_native_runtime
native_runtime_source = runtime_fixture.native_runtime_source
pytestmark = [
    pytest.mark.timeout(900),
    pytest.mark.skipif(os.name != "posix", reason="Native POSIX foreground setup"),
]

SEED = "literal native prompt ' \" \u03a9\n"
ARGUMENTS = ["--stdio", "", "two words", "literal;$()", "\u03a9", "--", "--allow-all"]


def _run(prefix, environment, cwd, *arguments):
    result = run_caller_owned(
        [*prefix, *arguments], env=environment, cwd=cwd, timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _plan(
    tmp_path, installed, bash, seed_kind, *, interactive=False, env_script=None,
    arguments=None, expect_native=True, bash_env=None, environment_known=True,
):
    root, plugin, prefix, environment = installed
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in (
        "default-setup.sh", "launch-command.sh", "native-seed-boundary.sh",
        "native-seed-reply.sh", "native-seed-setup.sh", "reconcile-machine-settings.sh",
    ):
        shutil.copy2(plugin / "scripts" / name, scripts / name)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    backend_source = tmp_path / "backend.py"
    backend_source.write_text(r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(os.environ["FIXTURE_CASE"])
deadline = time.monotonic() + 15
while True:
    observations = [
        json.loads(path.read_text())
        for path in Path(os.environ["FIXTURE_RECORD"]).parent.glob("launch-attempts/*/*.execution.json")
    ]
    if not os.environ["FIXTURE_SEEDED"] or any(
        event["phase"] == "process-spawned" and event["details"]["pid"] == os.getpid()
        for saved in observations for event in saved["events"]
    ):
        break
    if time.monotonic() >= deadline:
        raise RuntimeError("The actual backend never received its spawn receipt")
    time.sleep(0.01)
reply = {
    "pid": os.getpid(), "parent": os.getppid(), "argv": sys.argv[1:],
    "terminal": [os.isatty(fd) for fd in (0, 1, 2)],
    "foreground": os.tcgetpgrp(0) == os.getpgrp() if os.isatty(0) else None,
    "parent_command": subprocess.check_output(
        ["/bin/ps", "-p", str(os.getppid()), "-o", "comm="], text=True,
    ).strip(),
    "literal_env": os.environ["FIXTURE_LITERAL"],
    "execution_hint": json.loads(os.environ["AGENT_WORKTREES_LAUNCH_EXECUTION"])
        if "AGENT_WORKTREES_LAUNCH_EXECUTION" in os.environ else None,
    "inherited_sigint": str(signal.getsignal(signal.SIGINT)),
    "signal_mask": sorted(map(int, signal.pthread_sigmask(signal.SIG_BLOCK, []))),
}
def interrupted(_signal, _frame):
    (root / "interrupted").write_text("yes")
    raise SystemExit(130)
signal.signal(signal.SIGINT, interrupted)
print("BACKEND READY", flush=True)
reply["stdin"] = sys.stdin.readline()
(root / "backend.json").write_text(json.dumps(reply))
if os.environ["FIXTURE_INTERACTIVE"]:
    print("BACKEND INPUT CONSUMED", flush=True)
    while True:
        time.sleep(0.1)
print("backend stdout", flush=True)
print("backend stderr", file=sys.stderr, flush=True)
raise SystemExit(7)
''', encoding="utf-8")
    backend = tmp_path / "backend"
    backend.write_text(
        '#!/bin/sh\nprintf "%s\\0" "${PYTHONPATH+x}" "${PYTHONPATH-}" '
        '"${PYTHONHOME+x}" "${PYTHONHOME-}" "${VIRTUAL_ENV+x}" "${VIRTUAL_ENV-}" '
        '"${__PYVENV_LAUNCHER__+x}" "${__PYVENV_LAUNCHER__-}" > "$FIXTURE_CASE/backend-python-env"\n'
        'unset PYTHONPATH PYTHONHOME VIRTUAL_ENV __PYVENV_LAUNCHER__\nexec '
        + shlex.join([prefix[0], "-I", "-B", str(backend_source)]) + ' "$@"\n',
        encoding="utf-8",
    )
    backend.chmod(0o700)
    initialize = r'''
import json, sys
from pathlib import Path
from uuid import uuid4
from agent_worktrees import config as cfg, launch_execution, launch_seed_exec, launch_seed_state, tracking
checkout, root, backend = map(Path, sys.argv[1:4])
bash, seed_kind = sys.argv[4:6]
project = cfg.project_dir("native-posix")
project.mkdir(parents=True, exist_ok=True)
(project / "config.yaml").write_text(json.dumps({
    "repo_name": "native-posix", "machine": "fixture",
    "repos": {"native-posix": {"anchor": str(checkout), "default_branch": "dev"}},
}))
created = tracking.create_new_record(
    checkout.parent.name, "worktree/native-posix", str(checkout), "native-posix",
    "fixture", cfg.detect_platform(), checkout,
)
seed = launch_seed_state.stage(
    created.yaml_path, kind=seed_kind, text=sys.argv[6],
) if seed_kind else None
correlation = launch_execution.Correlation(uuid4().hex, uuid4().hex)
command = [
    bash, str(root / "scripts/default-setup.sh"), "--recovery",
    "--runtime-python", sys.executable, "--copilot-path", str(backend),
]
if sys.argv[8]:
    command += ["--env-script", sys.argv[8]]
command += json.loads(sys.argv[7])
if seed_kind == "new":
    command = [bash, str(root / "scripts/launch-command.sh"), "--recovery", "--", *command]
prepared = launch_seed_exec.deferred_command(
    command, created.yaml_path, seed_id=seed.seed_id if seed else None,
    admission_only=seed is None, correlation=correlation,
    environment={"BASH_ENV": sys.argv[10]} if sys.argv[12] == "1" else None,
    environment_known=sys.argv[11] == "1",
)
index = launch_seed_exec.native_setup_index(prepared)
assert (index is not None) == (bool(seed_kind) and not sys.argv[8] and sys.argv[9] == "1"), prepared
print(json.dumps({
    "record": str(created.yaml_path), "command": prepared,
    "copied": prepared[index] if index is not None else str(root / "scripts/default-setup.sh"),
    "seed_id": seed.seed_id if seed else None,
}))
'''
    plan = json.loads(_run(
        prefix, environment, checkout, "-c", initialize,
        str(checkout), str(root), str(backend), bash, seed_kind or "", SEED,
        json.dumps(ARGUMENTS if arguments is None else arguments),
        str(env_script) if env_script is not None else "",
        "1" if expect_native else "",
        str(bash_env) if bash_env is not None else "",
        "1" if environment_known else "",
        "1" if bash_env is not None else "",
    ))
    env = {
        **environment, "FIXTURE_CASE": str(tmp_path), "FIXTURE_RECORD": plan["record"],
        "FIXTURE_SEEDED": "1" if seed_kind else "", "FIXTURE_INTERACTIVE": "1" if interactive else "",
        "FIXTURE_LITERAL": "literal environment ' \u03a9\n\n",
        "AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED": "1",
    }
    if bash_env is not None:
        env["BASH_ENV"] = str(bash_env)
    return plan, env, checkout


def _observations(plan):
    return [
        json.loads(path.read_text())
        for path in Path(plan["record"]).parent.glob("launch-attempts/*/*.execution.json")
    ]


def _assert_return(plan, code, seeded):
    observations = _observations(plan)
    assert len(observations) == (2 if seeded else 1)
    for saved in observations:
        assert saved["events"][-1]["phase"] == "execution-returned", {
            "setup": saved["parent_id"] is None,
            "phases": [event["phase"] for event in saved["events"]],
        }
        assert saved["events"][-1]["details"]["exit_code"] == code
    if seeded:
        backend, = [saved for saved in observations if saved["parent_id"]]
        setup, = [saved for saved in observations if not saved["parent_id"]]
        assert backend["parent_id"] == setup["operation_id"]
        assert [event["phase"] for event in backend["events"]] == [
            "execution-requested", "seed-claimed", "process-spawned", "execution-returned",
        ]


@pytest.mark.parametrize("seed_kind", [None, "new", "resume"])
def test_copied_setup_preserves_native_wait_literal_streams_and_seed_custody(
    tmp_path, installed_native_runtime, native_bash, seed_kind,
):
    plan, environment, checkout = _plan(tmp_path, installed_native_runtime, native_bash, seed_kind)
    result = run_caller_owned(
        plan["command"], env=environment, cwd=checkout, input_text="retained stdin\n",
        timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 7, result.stdout + result.stderr + repr(_observations(plan))
    assert result.stdout == "BACKEND READY\nbackend stdout\n"
    assert result.stderr.splitlines().count("backend stderr") == 1
    if seed_kind:
        assert result.stderr.endswith("backend stderr\n"), result.stderr
    reply = json.loads((tmp_path / "backend.json").read_text())
    assert reply["argv"] == ARGUMENTS + (["--interactive", SEED] if seed_kind else [])
    assert reply["stdin"] == "retained stdin\n"
    assert reply["literal_env"] == environment["FIXTURE_LITERAL"]
    assert ("bash" if seed_kind else "python") in reply["parent_command"].lower()
    assert reply["terminal"] == [False, False, False]
    _assert_return(plan, 7, bool(seed_kind))
    if seed_kind:
        _root, _plugin, prefix, env = installed_native_runtime
        _run(
            prefix, env, checkout, "-c",
            "from pathlib import Path;from agent_worktrees import launch_seed_state;"
            "import sys;assert launch_seed_state.peek(Path(sys.argv[1]),read_only=True) is None",
            plan["record"],
        )
    assert not list(Path(plan["copied"]).parent.glob(".native-seed-*"))


@pytest.mark.parametrize("missing", [False, True])
def test_inherited_python_environment_and_execution_hint_survive_native_exec(
    tmp_path, installed_native_runtime, native_bash, missing,
):
    values = ["literal python path ' \u03a9\n\n", "literal python home", "", "literal mac launcher"]
    plan, environment, checkout = _plan(tmp_path, installed_native_runtime, native_bash, "new")
    # Set these after planning: macOS Python consumes __PYVENV_LAUNCHER__.
    environment.update(zip(("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"), values))
    environment["FIXTURE_PYVENV_LAUNCHER"] = environment.pop("__PYVENV_LAUNCHER__")
    if missing:
        environment.pop("PYTHONHOME", None)
        environment.pop("VIRTUAL_ENV", None)
    environment["AGENT_WORKTREES_LAUNCH_EXECUTION"] = json.dumps({
        "attempt_id": "f" * 32, "resolution_id": "e" * 32, "operation_id": "d" * 32,
    })
    result = run_caller_owned(
        ["/bin/sh", "-c",
         'export __PYVENV_LAUNCHER__="$FIXTURE_PYVENV_LAUNCHER"; exec "$@"', "launcher",
         *plan["command"]], env=environment, cwd=checkout, input_text="retained stdin\n",
        timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 7, result.stdout + result.stderr + repr(_observations(plan))
    fields = (tmp_path / "backend-python-env").read_bytes().decode("utf-8").split("\0")
    expected = [field for value in values for field in ("x", value)] + [""]
    if missing:
        expected[2:6] = ["", "", "", ""]
    assert fields == expected
    reply = json.loads((tmp_path / "backend.json").read_text())
    backend, = [saved for saved in _observations(plan) if saved["parent_id"]]
    assert reply["execution_hint"] == {
        key: backend[key] for key in ("attempt_id", "resolution_id", "operation_id")
    }
    _assert_return(plan, 7, True)


def test_native_control_utilities_do_not_change_a_restricted_backend_path(
    tmp_path, installed_native_runtime, native_bash,
):
    plan, environment, checkout = _plan(tmp_path, installed_native_runtime, native_bash, "new")
    environment["PATH"] = str(tmp_path / "no-utilities")
    backend = tmp_path / "backend"
    backend.write_text(
        backend.read_text().replace(
            "#!/bin/sh\n", '#!/bin/sh\nprintf "%s" "$PATH" > "$FIXTURE_CASE/backend-path"\n',
        ), encoding="utf-8",
    )
    result = run_caller_owned(
        plan["command"], env=environment, cwd=checkout, input_text="retained stdin\n",
        timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    diagnostics = None
    if result.returncode != 7:
        root, _plugin, _prefix, _env = installed_native_runtime
        probe = run_caller_owned(
            [native_bash, "-c", r'''
set -u
. "$1"
_aw_native_seed_runtime "$2" || exit $?
printf 'env lookup: '; builtin command -p -v env
printf 'sleep lookup: '; builtin command -p -v sleep
_aw_native_seed_control env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV -u __PYVENV_LAUNCHER__ \
    "${AW_NATIVE_SEED_RUNTIME[@]}" -m agent_worktrees.launch_seed_native --describe-contract
''', "probe", str(Path(plan["copied"]).with_name("native-seed-boundary.sh")), str(root)],
            env=environment, cwd=checkout, timeout=20,
            max_stdout_bytes=8192, max_stderr_bytes=8192,
        )
        diagnostics = {"code": probe.returncode, "stdout": probe.stdout, "stderr": probe.stderr}
    assert result.returncode == 7, {
        "stdout": result.stdout, "stderr": result.stderr, "observations": _observations(plan),
        "read_only_probe": diagnostics,
    }
    assert (tmp_path / "backend-path").read_text() == environment["PATH"]
    _assert_return(plan, 7, True)


def test_native_exec_preserves_non_utf8_environment_bytes_without_serializing_them(
    tmp_path, installed_native_runtime, native_bash,
):
    plan, environment, checkout = _plan(tmp_path, installed_native_runtime, native_bash, "new")
    environment["FIXTURE_NON_UTF8"] = "literal\udcff"
    environment["PYTHONPATH"] = "path\udcfe"
    backend = tmp_path / "backend"
    backend.write_text(
        backend.read_text().replace(
            "#!/bin/sh\n",
            '#!/bin/sh\nprintf "%s" "$FIXTURE_NON_UTF8" > "$FIXTURE_CASE/backend-raw-env"\n',
        ), encoding="utf-8",
    )
    result = run_caller_owned(
        plan["command"], env=environment, cwd=checkout, input_text="retained stdin\n",
        timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 7, result.stdout + result.stderr + repr(_observations(plan))
    assert (tmp_path / "backend-raw-env").read_bytes() == os.fsencode(environment["FIXTURE_NON_UTF8"])
    fields = (tmp_path / "backend-python-env").read_bytes().split(b"\0")
    assert fields[:2] == [b"x", os.fsencode(environment["PYTHONPATH"])]
    _assert_return(plan, 7, True)


def test_non_utf8_backend_arguments_keep_legacy_ownership_and_literal_bytes(
    tmp_path, installed_native_runtime, native_bash,
):
    arguments = ["--add-dir", "path\udcff", *ARGUMENTS]
    plan, environment, checkout = _plan(
        tmp_path, installed_native_runtime, native_bash, "new",
        arguments=arguments, expect_native=False,
    )
    result = run_caller_owned(
        plan["command"], env=environment, cwd=checkout, input_text="retained stdin\n",
        timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 7, result.stdout + result.stderr + repr(_observations(plan))
    reply = json.loads((tmp_path / "backend.json").read_text())
    assert reply["argv"] == arguments + ["--interactive", SEED]
    assert "python" in reply["parent_command"].lower()
    _assert_return(plan, 7, True)


@pytest.mark.parametrize("repo_trap, code", [("replace", 1), ("clear", 5)])
def test_legacy_env_script_early_exit_retains_the_actual_setup_return_and_seed(
    tmp_path, installed_native_runtime, native_bash, repo_trap, code,
):
    primer = tmp_path / "prime.sh"
    primer.write_text(
        ("trap 'exit 1' EXIT\n" if repo_trap == "replace" else "trap - EXIT\n") + "exit 5\n",
        encoding="utf-8",
    )
    plan, environment, checkout = _plan(
        tmp_path, installed_native_runtime, native_bash, "new", env_script=primer,
    )
    result = run_caller_owned(
        plan["command"], env=environment, cwd=checkout, timeout=60,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == code, result.stdout + result.stderr + repr(_observations(plan))
    assert not (tmp_path / "backend-python-env").exists()
    _assert_return(plan, code, False)
    _root, _plugin, prefix, env = installed_native_runtime
    _run(
        prefix, env, checkout, "-c",
        "from pathlib import Path;from agent_worktrees import launch_seed_state;import sys;"
        "seed=launch_seed_state.peek(Path(sys.argv[1]),read_only=True);"
        "assert seed.seed_id==sys.argv[2] and seed.handoff_id is None",
        plan["record"], plan["seed_id"],
    )


def test_selected_bash_env_early_exit_keeps_the_legacy_owner_and_final_status(
    tmp_path, installed_native_runtime, native_bash,
):
    primer = tmp_path / "bash-env.sh"
    primer.write_text("trap 'exit 1' EXIT\nexit 5\n", encoding="utf-8")
    plan, environment, checkout = _plan(
        tmp_path, installed_native_runtime, native_bash, "new",
        bash_env=primer, expect_native=False,
    )
    result = run_caller_owned(
        plan["command"], env=environment, cwd=checkout, timeout=60,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 1, result.stdout + result.stderr + repr(_observations(plan))
    assert not (tmp_path / "backend-python-env").exists()
    _assert_return(plan, 1, False)
    _root, _plugin, prefix, env = installed_native_runtime
    _run(
        prefix, env, checkout, "-c",
        "from pathlib import Path;from agent_worktrees import launch_seed_state;import sys;"
        "seed=launch_seed_state.peek(Path(sys.argv[1]),read_only=True);"
        "assert seed.seed_id==sys.argv[2] and seed.handoff_id is None",
        plan["record"], plan["seed_id"],
    )


@pytest.mark.parametrize("override", [None, ""])
def test_mux_server_initializer_uses_only_explicit_pane_overrides(
    tmp_path, installed_native_runtime, native_bash, monkeypatch, override,
):
    from agent_worktrees import sessions

    plan, environment, checkout = _plan(
        tmp_path, installed_native_runtime, native_bash, "new",
        environment_known=override is not None, expect_native=override == "", bash_env=override,
    )
    primer = tmp_path / "server-bash-env.sh"
    primer.write_text("trap 'exit 1' EXIT\nexit 5\n", encoding="utf-8")
    environment["BASH_ENV"] = str(primer)
    monkeypatch.setattr(sessions, "_mux_pane_cmd", lambda *_a, **_kw: list(plan["command"]))
    pane = sessions.build_mux_new_session_argv(
        "native-env", str(checkout), plan["command"],
        {"BASH_ENV": override} if override is not None else {}, mux="tmux",
    )
    for index, value in enumerate(pane[:-1]):
        if value == "-e":
            key, _, content = pane[index + 1].partition("=")
            environment[key] = content
    result = run_caller_owned(
        plan["command"], env=environment, cwd=checkout, input_text="retained stdin\n", timeout=60,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    if override is not None:
        assert result.returncode == 7, result.stdout + result.stderr + repr(_observations(plan))
        _assert_return(plan, 7, True)
        return
    assert result.returncode == 1, result.stdout + result.stderr + repr(_observations(plan))
    assert not (tmp_path / "backend-python-env").exists()
    _assert_return(plan, 1, False)
    _root, _plugin, prefix, env = installed_native_runtime
    _run(
        prefix, env, checkout, "-c",
        "from pathlib import Path;from agent_worktrees import launch_seed_state;import sys;"
        "seed=launch_seed_state.peek(Path(sys.argv[1]),read_only=True);"
        "assert seed.seed_id==sys.argv[2] and seed.handoff_id is None",
        plan["record"], plan["seed_id"],
    )


def test_opaque_shell_rendered_empty_override_retains_legacy_backend_and_receipts(
    tmp_path, installed_native_runtime, native_bash,
):
    plan, environment, checkout = _plan(
        tmp_path, installed_native_runtime, native_bash, "new",
        bash_env="", environment_known=False, expect_native=False,
    )
    environment["BASH_ENV"] = "''"
    result = run_caller_owned(
        plan["command"], env=environment, cwd=checkout, input_text="retained stdin\n", timeout=60,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert result.returncode == 7, result.stdout + result.stderr + repr(_observations(plan))
    backend = json.loads((tmp_path / "backend.json").read_text())
    assert "python" in backend["parent_command"].lower()
    assert backend["stdin"] == "retained stdin\n"
    _assert_return(plan, 7, True)


def test_selected_runtime_disappearing_before_setup_never_claims_or_replays_the_seed(
    tmp_path, installed_native_runtime, native_bash,
):
    plan, environment, checkout = _plan(tmp_path, installed_native_runtime, native_bash, "new")
    root, _plugin, prefix, env = installed_native_runtime
    selector = root / "current-version"
    original = selector.read_text()
    try:
        selector.write_text("unavailable")
        result = run_caller_owned(
            plan["command"], env=environment, cwd=checkout, timeout=30,
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
    finally:
        selector.write_text(original)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "no backend started" in result.stderr
    assert not _observations(plan)
    assert not (tmp_path / "backend.json").exists()
    _run(
        prefix, env, checkout, "-c",
        "from pathlib import Path;from agent_worktrees import launch_seed_state;import sys;"
        "seed=launch_seed_state.peek(Path(sys.argv[1]),read_only=True);"
        "assert seed.seed_id==sys.argv[2] and seed.handoff_id is None",
        plan["record"], plan["seed_id"],
    )


def test_copied_setup_keeps_real_terminal_and_sigint_beyond_the_finite_phase_deadline(
    tmp_path, installed_native_runtime, native_bash,
):
    import pty
    import select
    import termios

    plan, environment, checkout = _plan(
        tmp_path, installed_native_runtime, native_bash, "new", interactive=True,
    )
    pid, master = pty.fork()
    if pid == 0:
        os.chdir(checkout)
        os.execve(plan["command"][0], plan["command"], environment)
    output = b""
    waited = False

    def read_until(marker):
        nonlocal output
        deadline = time.monotonic() + 45
        while marker not in output.splitlines() and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 65536)
        assert marker in output.splitlines(), output.decode(errors="replace")

    try:
        read_until(b"BACKEND READY")
        # A foreground backend is not one of the ten-second finite providers.
        time.sleep(12)
        assert os.waitpid(pid, os.WNOHANG) == (0, 0)
        os.write(master, b"from terminal\n")
        read_until(b"BACKEND INPUT CONSUMED")
        os.write(master, b"\x03")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    pass
            ended, status = os.waitpid(pid, os.WNOHANG)
            if ended:
                waited = True
                assert os.waitstatus_to_exitcode(status) == 130, output.decode(errors="replace")
                break
            time.sleep(0.05)
        if not waited:
            reply = json.loads((tmp_path / "backend.json").read_text())
            modes = termios.tcgetattr(master)
            processes = subprocess.run([
                "/bin/ps", "-p", f"{pid},{reply['pid']}",
                "-o", "pid=,ppid=,pgid=,stat=,comm=",
            ], text=True, capture_output=True, timeout=2, **no_window_kwargs()).stdout
            pytest.fail(json.dumps({
                "output": output.decode(errors="replace"), "backend": reply,
                "isig": bool(modes[3] & termios.ISIG), "vintr": repr(modes[6][termios.VINTR]),
                "interrupted": (tmp_path / "interrupted").exists(),
                "observations": _observations(plan), "processes": processes,
            }, indent=2))
    finally:
        os.close(master)
        if not waited:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    reply = json.loads((tmp_path / "backend.json").read_text())
    assert reply["terminal"] == [True, True, True]
    assert reply["foreground"] is True
    assert reply["stdin"] == "from terminal\n"
    assert reply["parent"] == pid
    assert "bash" in reply["parent_command"]
    assert (tmp_path / "interrupted").read_text() == "yes"
    _assert_return(plan, 130, True)
