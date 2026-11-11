"""Regression tests for the process-exit shutdown workaround (_shutdown_exit).

Covers: os._exit() is used only on the affected runtime (never elsewhere),
atexit callbacks run before the hard exit, streams are flushed, SystemExit
raised by ``main`` is treated the same as a returned code, a non-int/None
code normalizes to 0/1, a non-SystemExit exception (and traceback-printing
failure) still reaches the hard exit, KeyboardInterrupt always exits cleanly
with code 130 on every runtime (hard-exit via os._exit() only on the
affected one; ordinary sys.exit() elsewhere), ``console_entry`` routes
through the same helper, and the installed console script is actually
mapped to ``console_entry`` in
``pyproject.toml`` (a packaging-level regression a unit test calling
``console_entry()`` directly would never catch) -- all without actually
terminating the test process (``os._exit`` and ``atexit._run_exitfuncs`` are
patched out).

The application-resource probe also runs in a bounded child with the affected
shutdown branch armed, so real hard exit cannot terminate the test runner.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest import mock
from agent_procutil import no_window_kwargs
import pytest

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

from worktree_manager import _shutdown_exit


@pytest.mark.parametrize("entry", ["module", "console"])
def test_real_entry_enables_fault_reporting_with_isolated_python(tmp_path, entry):
    program = """
import faulthandler,runpy,sys
from worktree_manager import _shutdown_exit
_shutdown_exit._AFFECTED_RUNTIME=False
assert not faulthandler.is_enabled()
sys.argv=['worktree-manager','--version']
try:
    if ENTRY == 'module':
        runpy.run_module('worktree_manager',run_name='__main__')
    else:
        from worktree_manager.__main__ import console_entry
        console_entry()
except SystemExit as error:
    assert error.code == 0
assert faulthandler.is_enabled()
faulthandler.dump_traceback()
""".replace("ENTRY", repr(entry))
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", program],
        cwd=tmp_path, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("worktree-manager ")
    assert 'File "<string>"' in result.stderr


def test_fault_reporting_preserves_an_existing_handler(monkeypatch):
    import faulthandler
    from worktree_manager.__main__ import _enable_fault_diagnostics

    monkeypatch.setattr(faulthandler, "is_enabled", lambda: True)
    with mock.patch.object(faulthandler, "enable") as enable:
        _enable_fault_diagnostics()
    enable.assert_not_called()


def test_fault_reporting_tolerates_embedded_stderr_without_descriptor(monkeypatch):
    import faulthandler
    from worktree_manager.__main__ import _enable_fault_diagnostics

    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    with mock.patch.object(faulthandler, "enable", side_effect=OSError("no descriptor")):
        _enable_fault_diagnostics()


@pytest.mark.parametrize("version_code", [0, 23])
def test_application_probe_finishes_before_affected_runtime_exit(tmp_path, version_code):
    root = Path(__file__).resolve().parents[2]
    probe = root / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "manager_application_probe.py"
    slot = tmp_path / "versions" / "1.2.3"
    launcher = slot / ".venv" / "package" / "_runtime_assets" / "bin" / "launch-session.ps1"
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    program = """
import runpy,sys
from pathlib import Path
from worktree_manager import _shutdown_exit,relocated_launch,runtime_identity
from worktree_manager import __main__ as cli
probe,slot,launcher=map(Path,sys.argv[1:4])
code=int(sys.argv[4])
_shutdown_exit._AFFECTED_RUNTIME=True
runtime_identity.running_application_slot=lambda:slot
relocated_launch._relocated_launch_script=lambda:launcher
if code:
    def failed_version(arguments):
        assert arguments==['--version']
        return code
    cli.main=failed_version
runpy.run_path(str(probe),run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", program,
         str(probe), str(slot), str(launcher), str(version_code)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == version_code, result.stdout + result.stderr
    if version_code:
        assert not result.stdout
    else:
        lines = result.stdout.splitlines()
        assert lines[0].startswith("worktree-manager ")
        assert json.loads(lines[-1]) == {"launcher_directory": str(launcher.parent.relative_to(slot))}


def test_windows_hard_exit_flushes_and_runs_atexit_then_exits() -> None:
    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs") as mock_atexit,
        mock.patch.object(_shutdown_exit.sys.stdout, "flush") as mock_out_flush,
        mock.patch.object(_shutdown_exit.sys.stderr, "flush") as mock_err_flush,
    ):
        _shutdown_exit.run_and_exit(lambda: 3)

    mock_atexit.assert_called_once()
    mock_out_flush.assert_called_once()
    mock_err_flush.assert_called_once()
    mock_exit.assert_called_once_with(3)


def test_windows_hard_exit_normalizes_non_int_code() -> None:
    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs"),
    ):
        _shutdown_exit.run_and_exit(lambda: None)
    mock_exit.assert_called_once_with(0)

    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs"),
    ):
        _shutdown_exit.run_and_exit(lambda: "boom")
    mock_exit.assert_called_once_with(1)


def test_windows_hard_exit_prints_non_int_message_before_normalizing() -> None:
    """A non-int, non-None code (e.g. a message string, matching what
    ``sys.exit("...")`` accepts) must still be printed to stderr -- exactly
    what plain ``sys.exit()`` would have done -- before being mapped to exit
    code 1, so the observable output doesn't silently regress.
    """
    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs"),
        mock.patch("builtins.print") as mock_print,
    ):
        _shutdown_exit.run_and_exit(lambda: "boom")
    mock_print.assert_called_once_with("boom", file=_shutdown_exit.sys.stderr)
    mock_exit.assert_called_once_with(1)


def test_hard_exit_happens_even_if_cleanup_raises() -> None:
    """os._exit() must run even if atexit callbacks or a stream flush raise --
    in real usage os._exit() terminates the process immediately, so any
    propagating exception never actually surfaces; here we just confirm the
    hard exit itself isn't skipped by an earlier failure.
    """
    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(
            _shutdown_exit.atexit, "_run_exitfuncs", side_effect=RuntimeError("boom")
        ),
    ):
        try:
            _shutdown_exit.run_and_exit(lambda: 4)
        except RuntimeError:
            pass
    mock_exit.assert_called_once_with(4)

    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs"),
        mock.patch.object(
            _shutdown_exit.sys.stdout, "flush", side_effect=RuntimeError("boom")
        ),
    ):
        try:
            _shutdown_exit.run_and_exit(lambda: 4)
        except RuntimeError:
            pass
    mock_exit.assert_called_once_with(4)


def test_windows_hard_exit_treats_systemexit_like_a_return_code() -> None:
    def main() -> int:
        raise SystemExit(7)

    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs"),
    ):
        _shutdown_exit.run_and_exit(main)
    mock_exit.assert_called_once_with(7)


def test_windows_non_systemexit_exception_still_hard_exits() -> None:
    """A non-SystemExit exception from ``main`` must not escape and fall
    through to normal interpreter shutdown on the affected runtime -- it's
    routed through the same hard-exit path with exit code 1, after printing
    the traceback.
    """

    def main() -> int:
        raise RuntimeError("boom")

    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs"),
        mock.patch("traceback.print_exc") as mock_print_exc,
    ):
        _shutdown_exit.run_and_exit(main)
    mock_print_exc.assert_called_once()
    mock_exit.assert_called_once_with(1)


def test_windows_hard_exit_happens_even_if_traceback_printing_fails() -> None:
    """If stderr is closed/broken and ``traceback.print_exc()`` itself
    raises, the hard exit must still happen with exit code 1 -- printing the
    traceback is best-effort and must never be able to defeat the whole
    workaround.
    """

    def main() -> int:
        raise RuntimeError("boom")

    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs"),
        mock.patch("traceback.print_exc", side_effect=OSError("broken pipe")),
    ):
        _shutdown_exit.run_and_exit(main)
    mock_exit.assert_called_once_with(1)


def test_windows_keyboard_interrupt_hard_exits_with_130() -> None:
    """``KeyboardInterrupt`` on the affected runtime must not propagate into
    normal interpreter shutdown either -- it's routed through the same
    hard-exit path using the conventional 128+SIGINT exit code (130).
    """

    def main() -> int:
        raise KeyboardInterrupt

    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", True),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs"),
    ):
        _shutdown_exit.run_and_exit(main)
    mock_exit.assert_called_once_with(130)


def test_unaffected_runtime_keyboard_interrupt_exits_cleanly_with_130() -> None:
    """Off the affected runtime, ``KeyboardInterrupt`` must still exit
    cleanly with the conventional 128+SIGINT exit code (130) -- via ordinary
    ``sys.exit()``, not the ``os._exit()`` bypass (that stays scoped to the
    affected runtime). Letting it propagate un-caught here left users on any
    non-3.13 runtime staring at a raw traceback (and, behind a .bat/cmd.exe
    wrapper, cmd's own "Terminate batch job (Y/N)?" prompt) on a plain
    Ctrl+C.
    """

    def main() -> int:
        raise KeyboardInterrupt

    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", False),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
    ):
        try:
            _shutdown_exit.run_and_exit(main)
        except SystemExit as exc:
            assert exc.code == 130
        else:
            raise AssertionError("expected SystemExit(130)")
    mock_exit.assert_not_called()


def test_unaffected_runtime_non_systemexit_exception_propagates_normally() -> None:
    """Off the affected runtime, an arbitrary exception from ``main`` must
    propagate exactly as it would have before this workaround existed (no
    os._exit, no swallowing) -- only SystemExit/return codes are
    special-cased.
    """

    def main() -> int:
        raise RuntimeError("boom")

    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", False),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
    ):
        try:
            _shutdown_exit.run_and_exit(main)
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected RuntimeError to propagate")
    mock_exit.assert_not_called()


def test_unaffected_runtime_never_hard_exits() -> None:
    with (
        mock.patch.object(_shutdown_exit, "_AFFECTED_RUNTIME", False),
        mock.patch.object(_shutdown_exit.os, "_exit") as mock_exit,
        mock.patch.object(_shutdown_exit.atexit, "_run_exitfuncs") as mock_atexit,
    ):
        try:
            _shutdown_exit.run_and_exit(lambda: 5)
        except SystemExit as exc:
            assert exc.code == 5
        else:
            raise AssertionError("expected SystemExit on the unaffected runtime")

    mock_exit.assert_not_called()
    mock_atexit.assert_not_called()


def test_console_entry_wraps_main() -> None:
    """The installed console script must route through the same helper as
    ``python -m worktree_manager`` -- the generated console-script wrapper
    calls the entry point directly and bypasses the
    ``if __name__ == "__main__":`` guard entirely. Assert the helper itself
    is invoked (not just that ``main`` eventually ran), so a regression that
    bypassed ``run_and_exit`` entirely would be caught.
    """
    from worktree_manager import __main__ as m

    with mock.patch.object(_shutdown_exit, "run_and_exit") as mock_run_and_exit:
        m.console_entry()

    mock_run_and_exit.assert_called_once_with(m.main)


def test_installed_console_script_maps_to_console_entry() -> None:
    """Packaging-level guard for the console-script coverage gap itself:
    a unit test that only calls ``console_entry()`` directly would still
    pass if ``pyproject.toml``'s ``[project.scripts]`` mapping regressed
    back to ``agent_worktrees.__main__:main`` -- the generated
    ``agent-worktrees`` executable calls whatever that mapping names, not
    whatever this test module imports. Parse the actual mapping instead.
    """
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["worktree-manager"] == "worktree_manager.__main__:console_entry"
