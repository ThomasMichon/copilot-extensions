"""Focused contract tests for repository test-runner admission."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "run-plugin-tests.py"
_previous_path = sys.path.copy()
sys.path.insert(0, str(SCRIPT.parent))
try:
    _spec = importlib.util.spec_from_file_location("run_plugin_tests", SCRIPT)
    assert _spec and _spec.loader
    runner = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = runner
    _spec.loader.exec_module(runner)

    import plugin_test_containment as containment
    import pytest_portfolio_guard as portfolio_guard
finally:
    sys.path[:] = _previous_path


def test_worker_launch_uses_canonical_headlessness(monkeypatch, tmp_path):
    ready = tmp_path / "ready"
    ready.touch()
    calls = []
    monkeypatch.setattr(containment, "no_window_kwargs", lambda: {"creationflags": 123})

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(containment.subprocess, "run", run)
    assert containment._worker_main([str(ready), "command", "argument"]) == 7
    assert calls == [
        (
            ["command", "argument"],
            {"check": False, "creationflags": 123, **containment._windows_stdio_kwargs()},
        )
    ]


def test_containment_owner_launch_preserves_process_contract(monkeypatch, tmp_path):
    captured = {}

    class FinishedProcess:
        pid = 123

        def poll(self):
            return 17

    def popen(command, **kwargs):
        captured.update(kwargs)
        assert command == containment._worker_command(
            ["command"], tmp_path / ".containment-ready"
        )
        return FinishedProcess()

    class Job:
        def __init__(self, limits):
            pass

        def assign(self, pid):
            assert pid == 123

        def close(self):
            captured["job_closed"] = True

    monkeypatch.setattr(containment, "no_window_kwargs", lambda: {"creationflags": 123})
    monkeypatch.setattr(containment.subprocess, "Popen", popen)
    monkeypatch.setattr(containment, "_WindowsJob", Job)
    monkeypatch.setattr(containment, "_terminate_posix_group", lambda pid: None)
    assert containment._run_contained_process(
        ["command"], cwd=tmp_path, env={"TEST": "value"}, sandbox=tmp_path,
        limits=containment.Limits(),
    ) == 17
    assert captured["creationflags"] == 123
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"] == {"TEST": "value"}
    assert {
        name: captured[name] for name in containment._windows_stdio_kwargs()
    } == containment._windows_stdio_kwargs()
    assert captured["start_new_session"] == (os.name != "nt")
    if os.name == "nt":
        assert captured["job_closed"]
    assert not (tmp_path / ".containment-ready").exists()


@pytest.mark.skipif(os.name != "nt", reason="real Windows console inheritance")
@pytest.mark.parametrize("timeout", [False, True], ids=["exit-and-streams", "timeout-tree"])
def test_windowless_runner_contains_bare_console_descendants(tmp_path, timeout, monkeypatch):
    """Exercise pythonw -> runner -> worker -> bare Python/PowerShell/git.

    No subprocess patch is installed: intentional CREATE_NEW_CONSOLE callers
    remain untouched. Console handles/visibility are OS observations, not flag
    assertions. The opt-in desktop lane additionally observes external terminal
    hosts, visible windows and foreground changes against a pre-launch baseline.
    """
    import ctypes
    from ctypes import wintypes

    from agent_procutil import no_window_kwargs, windowless_python

    pythonw = windowless_python(sys.executable)
    if Path(pythonw).name.lower() != "pythonw.exe":
        pytest.skip("windowless base interpreter unavailable")
    powershell = shutil.which("powershell.exe")
    git = shutil.which("git.exe")
    if not powershell or not git:
        pytest.skip("PowerShell and git are required console representatives")
    receipt = tmp_path / "descendants.json"
    result = tmp_path / "result.json"
    sandbox = tmp_path / "sandbox"
    console_probe = """
import ctypes, os
k = ctypes.windll.kernel32
k.GetConsoleWindow.restype = ctypes.c_void_p
window = k.GetConsoleWindow()
assert not window or not ctypes.windll.user32.IsWindowVisible(window)
"""
    ps_script = (
        'Add-Type \'using System; using System.Runtime.InteropServices; '
        'public class ConsoleProbe { [DllImport("kernel32.dll")] '
        'public static extern IntPtr GetConsoleWindow(); }\'; '
        'if ([ConsoleProbe]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 9 }; '
        f"& '{git.replace(chr(39), chr(39) * 2)}' --version; "
        '[Console]::Error.WriteLine("nested-stderr"); exit 7'
    )
    child = console_probe + f"""
import json, subprocess, sys, time
from pathlib import Path
assert os.environ['COPILOT_EXTENSIONS_TEST_CONTAINED'] == '1'
assert Path.cwd() == Path({str(tmp_path)!r})
Path({str(receipt)!r}).write_text(json.dumps({{
    'pid': os.getpid(), 'window': window,
}}))
if {timeout!r}:
    time.sleep(120)
else:
    for cycle in range(2):
        probe = subprocess.run(
            [{powershell!r}, '-NoProfile', '-NonInteractive', '-Command', {ps_script!r}],
            capture_output=True, text=True, timeout=10,
        )
        assert probe.returncode == 7, (probe.returncode, probe.stdout, probe.stderr)
        assert 'git version' in probe.stdout
        assert 'nested-stderr' in probe.stderr
    print('nested-stdout')
    print('nested-stderr', file=sys.stderr)
    sys.exit(7)
"""
    command = console_probe + f"""
import subprocess, sys
assert sys.stdin.readline().strip() == 'nested-input'
probe = subprocess.run([sys.executable, '-c', {child!r}], timeout=25)
sys.exit(17 if probe.returncode == 7 else 18)
"""
    owner = textwrap.dedent(f"""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, {str(SCRIPT.parent)!r})
        import plugin_test_containment as containment
        sandbox = Path({str(sandbox)!r})
        env = containment.isolated_environment(os.environ, sandbox)
        rc = containment.run_contained(
            [{sys.executable!r}, '-c', {command!r}],
            cwd=Path({str(tmp_path)!r}), env=env, sandbox=sandbox,
            limits=containment.Limits(wall_seconds={5 if timeout else 20},
                                      poll_seconds=0.05, max_processes=32,
                                      max_memory_mb=512, max_temp_mb=32),
        )
        Path({str(result)!r}).write_text(json.dumps({{'returncode': rc}}))
    """)
    streams = tmp_path / "streams.json"
    launcher = f"""
import ctypes, json, subprocess, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPT.parent)!r})
from plugin_test_containment import no_window_kwargs
assert not ctypes.windll.kernel32.GetConsoleWindow()
probe = subprocess.run(
    [{sys.executable!r}, '-c', {owner!r}],
    input='nested-input\\n', capture_output=True, text=True,
    timeout=25, **no_window_kwargs(),
)
Path({str(streams)!r}).write_text(json.dumps({{
    'returncode': probe.returncode, 'stdout': probe.stdout, 'stderr': probe.stderr,
}}))
"""
    desktop = os.environ.get("MUX_CHILD_WINDOWS_E2E") == "1"
    stop = threading.Event()
    visible: set[int] = set()
    focused: set[int] = set()
    owned: set[int] = set()
    roots: set[int] = set()
    if desktop:
        session = wintypes.DWORD()
        assert ctypes.windll.kernel32.ProcessIdToSessionId(
            os.getpid(), ctypes.byref(session),
        )
        assert session.value != 0, "desktop observation cannot run in Windows session zero"
        monkeypatch.syspath_prepend(
            str(SCRIPT.parent.parent / "worktree-manager" / "tests"),
        )
        from mux_windows_observer import descendants, processes, windows

        baseline_windows, baseline_foreground = windows()
        baseline_pids = set(processes())
        real_popen = subprocess.Popen

        def popen(argv, **kwargs):
            process = real_popen(argv, **kwargs)
            roots.add(process.pid)
            return process

        monkeypatch.setattr(subprocess, "Popen", popen)

        def observe() -> None:
            while not stop.is_set():
                table = processes()
                for pid in roots.copy():
                    owned.update(descendants(pid, table))
                current_windows, foreground_pid = windows()
                terminals = {"windowsterminal.exe", "openconsole.exe", "conhost.exe"}
                for hwnd, pid in current_windows.items():
                    if pid in owned or (
                        hwnd not in baseline_windows
                        and table.get(pid, ("", 0))[0].lower() in terminals
                    ):
                        visible.add(hwnd)
                if foreground_pid in owned or (
                    foreground_pid != baseline_foreground
                    and table.get(foreground_pid, ("", 0))[0].lower() in terminals
                ):
                    focused.add(foreground_pid)
                for pid, (name, _) in table.items():
                    if pid not in baseline_pids and name.lower() == "openconsole.exe":
                        visible.add(pid)
                stop.wait(.01)

    with ThreadPoolExecutor(max_workers=1) as executor:
        monitor = executor.submit(observe) if desktop else None
        try:
            completed = subprocess.run(
                [pythonw, "-c", launcher], cwd=tmp_path, capture_output=True,
                text=True, timeout=30, **no_window_kwargs(),
            )
            if desktop:
                assert roots and owned, "observer never identified runner fixture processes"
                deadline = time.monotonic() + 5
                while owned & processes().keys() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert not (owned & processes().keys()), "runner fixture descendants leaked"
        finally:
            stop.set()
            if monitor is not None:
                monitor.result(timeout=3)
    assert not visible, "runner child or external terminal host surfaced a window"
    assert not focused, "runner child or external terminal host stole focus"
    assert completed.returncode == 0, completed.stderr
    captured = json.loads(streams.read_text())
    assert captured["returncode"] == 0, captured["stderr"]
    assert json.loads(result.read_text())["returncode"] == (124 if timeout else 17)
    identity = json.loads(receipt.read_text())
    assert not identity["window"]
    if timeout:
        assert "wall-clock limit exceeded" in captured["stderr"]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x00100000, False, identity["pid"])
        if handle:
            try:
                assert kernel32.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel32.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87  # PID no longer exists.
    else:
        assert "nested-stdout" in captured["stdout"]
        assert "nested-stderr" in captured["stderr"]


def test_default_environment_redirects_all_mutable_roots(tmp_path: Path) -> None:
    env = containment.isolated_environment(
        {
            "PATH": os.environ.get("PATH", ""),
            "HOME": "/real/home",
            "AGENT_WORKTREES_HOME": "/real/agent-worktrees",
            "AGENT_WORKTREES_PROJECTS_YAML": "/real/projects.yaml",
            "GH_TOKEN": "not-a-real-token",
            "COPILOT_AGENT_SESSION_ID": "live-session",
            "AGENT_WORKTREES_OWNER_REF": "live-owner",
        },
        tmp_path,
    )

    assert env["PATH"] == os.environ.get("PATH", "")
    assert "GH_TOKEN" not in env
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "AGENT_WORKTREES_OWNER_REF" not in env
    for name in containment.ROOT_ENV_NAMES:
        Path(env[name]).resolve().relative_to(tmp_path.resolve())
    assert "AGENT_CONTAINERS_CONFIG" not in env
    Path(env["AGENT_WORKTREES_PROJECTS_YAML"]).resolve().relative_to(
        tmp_path.resolve()
    )


def test_optional_file_override_is_validated_only_when_present(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(os.environ, tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in containment.OPTIONAL_FILE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    portfolio_guard.validate_contained_environment()

    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(tmp_path.parent / "config.yaml"))
    with pytest.raises(pytest.UsageError, match="AGENT_CONTAINERS_CONFIG"):
        portfolio_guard.validate_contained_environment()


def test_host_state_opt_in_preserves_credentials_not_session_affinity(
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(
        {
            "HOME": "/real/home",
            "GH_TOKEN": "not-a-real-token",
            "COPILOT_AGENT_SESSION_ID": "live-session",
            "AGENT_WORKTREES_OWNER_REF": "live-owner",
        },
        tmp_path,
        allow_explicit_tiers=True,
        allow_host_state=True,
    )

    assert env["HOME"] == "/real/home"
    assert env["GH_TOKEN"] == "not-a-real-token"
    assert env[containment.ALLOW_HOST_STATE_ENV] == "1"
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "AGENT_WORKTREES_OWNER_REF" not in env
    for name in containment.ALWAYS_SANDBOX_ENV_NAMES:
        Path(env[name]).resolve().relative_to(tmp_path.resolve())


def test_host_state_requires_explicit_tiers_at_environment_boundary(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="allow_host_state requires allow_explicit_tiers",
    ):
        containment.isolated_environment(
            os.environ,
            tmp_path,
            allow_host_state=True,
        )


def test_host_state_still_fails_closed_on_escaped_temp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(
        os.environ,
        tmp_path,
        allow_explicit_tiers=True,
        allow_host_state=True,
    )
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TMPDIR", str(tmp_path.parent))

    with pytest.raises(pytest.UsageError, match="TMPDIR"):
        portfolio_guard.validate_contained_environment()


def test_admission_fails_fast_with_live_holder(monkeypatch, tmp_path: Path) -> None:
    class BusyLease:
        def acquire(self) -> None:
            raise runner.AlreadyRunningError(tmp_path / "runner.lock", 123)

    monkeypatch.setattr(runner, "SingleInstance", lambda *_args, **_kwargs: BusyLease())

    with pytest.raises(runner.AlreadyRunningError) as exc:
        runner._acquire_admission(0)
    assert exc.value.holder_pid == 123


def test_admission_wait_is_bounded_and_retries(monkeypatch) -> None:
    class EventuallyAvailableLease:
        calls = 0

        def acquire(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise runner.AlreadyRunningError(Path("runner.lock"), 123)

    lease = EventuallyAvailableLease()
    clock = iter((10.0, 10.0))
    monkeypatch.setattr(runner, "SingleInstance", lambda *_args, **_kwargs: lease)
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    assert runner._acquire_admission(1.0) is lease
    assert lease.calls == 2


def test_heavy_run_holds_admission_for_all_targets(monkeypatch) -> None:
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda _wait: events.append("acquire") or Lease(),
    )
    monkeypatch.setattr(
        runner,
        "run_plugin",
        lambda name, *_args, **_kwargs: events.append(f"run:{name}") or 0,
    )

    assert runner.main(["alpha", "beta"]) == 0
    assert events == ["acquire", "run:alpha", "run:beta", "release"]


def test_unexpected_runner_error_does_not_wedge_remaining_plugins(monkeypatch) -> None:
    """Regression (coverage-guided-ci full-matrix local validation pass,
    2026-10-05): a single plugin's own contained-run failure must never
    propagate past the per-plugin loop and abort the rest of ``--all``/
    ``--changed`` -- only ``ContainmentError``/``CalledProcessError`` were
    ever caught here, so any OTHER exception type (e.g. a transient
    OS-level resource issue after many sequential heavy runs) would
    silently truncate validation of every plugin after the one that
    raised it."""
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    def _run_plugin(name: str, *_args, **_kwargs) -> int:
        if name == "beta":
            raise MemoryError("simulated unexpected failure, not ContainmentError")
        events.append(f"run:{name}")
        return 0

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda _wait: events.append("acquire") or Lease(),
    )
    monkeypatch.setattr(runner, "run_plugin", _run_plugin)

    # Exit code 1 (a real failure was recorded), but every target was
    # still attempted -- "gamma" (after the raising "beta") must have run.
    assert runner.main(["alpha", "beta", "gamma"]) == 1
    assert events == ["acquire", "run:alpha", "run:gamma", "release"]


def test_guards_also_take_heavy_admission(monkeypatch) -> None:
    # `--guards` still reaches `_ensure_venv()` and so can rebuild/delete
    # the SHARED on-disk venv a concurrent bare admitted run may be
    # relying on mid-execution -- it is not exempt.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait: acquire_calls.append(wait) or Lease(),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--guards"]) == 0
    assert acquire_calls == [0.0]


def test_collect_only_also_takes_heavy_admission(monkeypatch) -> None:
    # `--collect-only` likewise reaches `_ensure_venv()` and so can
    # rebuild/delete the SHARED on-disk venv -- it is not exempt either.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait: acquire_calls.append(wait) or Lease(),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--collect-only"]) == 0
    assert acquire_calls == [0.0]


def test_prepare_only_takes_heavy_admission(monkeypatch) -> None:
    # Like --guards/--collect-only, --prepare-only is NOT exempt: it can
    # rebuild/delete the SHARED on-disk venv a concurrent bare admitted
    # run may be relying on mid-execution.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait: acquire_calls.append(wait) or Lease(),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--prepare-only"]) == 0
    assert acquire_calls == [0.0]


def test_run_plugin_prepare_only_never_invokes_pytest(monkeypatch, tmp_path: Path) -> None:
    """The whole point of `--prepare-only`: it must build/update the venv
    and return WITHOUT ever importing a single test module or
    `conftest.py` -- unlike `--collect-only`, which still runs pytest's
    own collection (and therefore executes that module-level code)."""
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    ensure_venv_calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        runner, "_ensure_venv",
        lambda name, uv, *, reinstall: ensure_venv_calls.append((name, reinstall)) or Path("fake-py"),
    )
    monkeypatch.setattr(
        runner, "run_contained",
        lambda *_a, **_k: pytest.fail("--prepare-only must never invoke pytest"),
    )

    rc = runner.run_plugin(
        "alpha", "uv",
        reinstall=False, kexpr=None, limits=runner.Limits(
            wall_seconds=60, max_processes=10, max_memory_mb=100,
            max_temp_mb=100, poll_seconds=0.1,
        ),
        plugin_timeout=60.0, test_timeout=10.0, max_files_per_subsuite=25,
        prepare_only=True,
    )
    assert rc == 0
    assert ensure_venv_calls == [("alpha", False)]


def test_host_state_requires_explicit_tier_opt_in(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        runner.main(["--allow-host-state"])
    assert exc.value.code == 2
    assert "--allow-host-state requires --allow-explicit-tiers" in capsys.readouterr().err


def test_admission_wait_rejects_infinite_value(monkeypatch, capsys) -> None:
    # `float("inf")` parses cleanly and isn't `< 0`, so it would otherwise
    # slip past a bare negative check and poll forever under contention,
    # defeating the documented bounded-wait contract.
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    with pytest.raises(SystemExit) as exc:
        runner.main(["alpha", "--admission-wait", "inf"])
    assert exc.value.code == 2
    assert "admission_wait must be a non-negative, finite number" in capsys.readouterr().err


def test_admission_wait_rejects_nan_value(monkeypatch, capsys) -> None:
    # `float("nan")` also parses cleanly, and `nan < 0` is False, so a
    # bare negative check alone would silently accept it too.
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    with pytest.raises(SystemExit) as exc:
        runner.main(["alpha", "--admission-wait", "nan"])
    assert exc.value.code == 2
    assert "admission_wait must be a non-negative, finite number" in capsys.readouterr().err



class _FakeCompletedProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_changed_plugins_always_includes_cross_plugin_contract_testers(monkeypatch) -> None:
    """A PR touching only plugin 'foo' must still schedule any registered
    cross-plugin contract tester (regression: ThomasMichon/copilot-extensions
    #4166 broke copilot-extensions-harness's own marketplace-wide contract
    test without touching a single file under that plugin, so the PR's own
    --changed-scoped CI never ran it)."""
    calls = iter([
        _FakeCompletedProcess("plugins/foo/src/foo.py\n"),
        _FakeCompletedProcess(""),
    ])
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: next(calls))
    monkeypatch.setattr(
        runner, "_has_suite",
        lambda name: name in {"foo", "copilot-extensions-harness"},
    )
    result = runner.changed_plugins("origin/main")
    assert result == sorted({"foo", "copilot-extensions-harness"})


def test_changed_plugins_empty_diff_schedules_nothing(monkeypatch) -> None:
    """No real change (e.g. a docs-only or non-plugin diff) must not force
    the contract testers to run either -- only an actual plugin change
    should pull them in."""
    calls = iter([
        _FakeCompletedProcess(""),
        _FakeCompletedProcess(""),
    ])
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: next(calls))
    monkeypatch.setattr(runner, "_has_suite", lambda name: True)
    assert runner.changed_plugins("origin/main") == []
