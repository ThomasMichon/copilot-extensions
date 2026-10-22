"""The Windows bootstrap remains present until its selected Manager has finished."""

from __future__ import annotations

import json
from itertools import product
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from worktree_manager import self_install


def test_posix_bootstrap_retains_process_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = self_install._runtime_installer()
    seen = []

    class Replaced(BaseException):
        pass

    def replace(executable: str, argv: list[str]) -> None:
        seen.append((executable, argv))
        raise Replaced

    monkeypatch.setattr(installer, "os", SimpleNamespace(name="posix", execv=replace))
    command = ["/selected/python", "-I", "-S", "launcher.py", "-m", "worktree_manager", "update"]
    with pytest.raises(Replaced):
        installer._dispatch_manager(command)
    assert seen == [(command[0], command)]


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("console_streams", list(product([False, True], repeat=3)))
def test_windows_bootstrap_waits_for_observer_and_preserves_exit(
    monkeypatch: pytest.MonkeyPatch, interrupted: bool, console_streams: tuple[bool, bool, bool],
) -> None:
    installer = self_install._runtime_installer()
    seen, waits = [], []

    def wait() -> int:
        waits.append(True)
        if interrupted and len(waits) == 1:
            raise KeyboardInterrupt
        return 43

    def launch(argv: list[str], **kwargs: object) -> SimpleNamespace:
        seen.append((argv, kwargs))
        return SimpleNamespace(wait=wait)

    monkeypatch.setattr(installer, "os", SimpleNamespace(name="nt"))
    streams = {name: SimpleNamespace(console=console)
               for name, console in zip(("stdin", "stdout", "stderr"), console_streams, strict=True)}
    monkeypatch.setattr(installer, "sys", SimpleNamespace(**streams))
    monkeypatch.setattr(installer, "_windows_console_stream", lambda stream: stream.console)
    monkeypatch.setattr(
        installer, "subprocess", SimpleNamespace(Popen=launch, CREATE_NO_WINDOW=0x08000000),
    )
    command = ["selected-python.exe", "-I", "-S", "launcher.py", "-m", "worktree_manager", "update"]
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            installer._dispatch_manager(command)
        assert len(waits) == 2
    else:
        assert installer._dispatch_manager(command) == 43
        assert len(waits) == 1
    assert seen == [(command, {
        **streams, "creationflags": 0 if any(console_streams) else 0x08000000,
    })]


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="opt-in native Windows process/window qualification",
)
@pytest.mark.parametrize(("code", "input_text"), [
    (0, "bootstrap input \u00e9"), (43, "bootstrap input \u00e9"), (43, None),
])
def test_native_windowless_bootstrap_waits_for_selected_manager(
    tmp_path: Path, code: int, input_text: str | None,
) -> None:
    from agent_procutil.owned_process import run_owned

    repo = Path(__file__).resolve().parents[2]
    observer = runpy.run_path(str(
        repo / "libs/agent-procutil/tests/test_spawn_daemon_windows_integration.py"
    ))
    parent = getattr(sys, "_base_executable", sys.executable)
    installer = repo / "worktree-manager/scripts/runtime_install.py"
    arguments = ["-m", "worktree_manager", "update", "--project", "example with spaces"]
    if input_text is None:
        with open(os.devnull, encoding="utf-8") as null:
            assert null.isatty()
            assert not self_install._runtime_installer()._windows_console_stream(null)
    with observer["_WindowObserver"](*observer["_configure_win32"]()) as windows:
        for cycle in range(2):
            marker = tmp_path / f"completed-{cycle}.json"
            child = tmp_path / f"selected-manager-{cycle}.py"
            child.write_text(
                "import json,pathlib,sys,time\n"
                "data=sys.stdin.read()\n"
                "print('selected Manager stdout \\u00e9',flush=True)\n"
                "print('selected Manager stderr \\u00e9',file=sys.stderr,flush=True)\n"
                "time.sleep(.2)\n"
                f"pathlib.Path({str(marker)!r}).write_text("
                "json.dumps({'argv':sys.argv[1:],'stdin':data}),encoding='utf-8')\n"
                f"raise SystemExit({code})\n",
                encoding="utf-8",
            )
            command = [parent, "-I", "-S", "-B", "-X", "utf8", str(child), *arguments]
            outer = (
                "import json,runpy,sys;"
                "dispatch=runpy.run_path(sys.argv[1])['_dispatch_manager'];"
                "raise SystemExit(dispatch(json.loads(sys.argv[2])))"
            )
            result = run_owned(
                [parent, "-I", "-S", "-B", "-X", "utf8", "-c", outer,
                 str(installer), json.dumps(command)],
                timeout=15, input_text=input_text,
            )
            assert result.returncode == code, result.stderr
            assert result.stdout == "selected Manager stdout \u00e9\n"
            assert result.stderr == "selected Manager stderr \u00e9\n"
            assert json.loads(marker.read_text(encoding="utf-8")) == {
                "argv": arguments, "stdin": input_text or "",
            }
    assert windows.surfaced == {}
    assert windows.focus_changes == {}
