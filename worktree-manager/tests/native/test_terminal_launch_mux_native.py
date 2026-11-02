"""Real PSMux terminal transport after Python preparation, in fixture Jobs."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

import pytest
from agent_procutil import no_window_kwargs

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows ConPTY and PSMux"),
    pytest.mark.timeout(90),
]


def _until(predicate, console, label):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        assert not console.exited(console.process.process), console.output.decode("utf-8", "replace")
        time.sleep(.025)
    raise AssertionError(f"{label}: {console.output.decode('utf-8', 'replace')}")


def test_real_mux_input_resize_control_c_detach_rejoin_and_independent_child_exit(tmp_path):
    from _terminal_console_fixture import Console
    from test_terminal_launch_native import _quote, _scripts, _wait
    from test_pane_args_file import _test_env

    mux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    assert mux, "PSMux is required for this explicitly selected native packet"
    shell = shutil.which("pwsh")
    assert shell, "PowerShell 7 is required"
    env = _test_env(tmp_path)
    names = ["ce-handoff-" + uuid4().hex for _ in range(2)]
    pane = tmp_path / "pane.ps1"
    pane.write_text(
        "$ErrorActionPreference='Stop'\n$root=$args[0]\n$name=$args[1]\n"
        "[Console]::TreatControlCAsInput=$true\n"
        "[IO.File]::WriteAllText((Join-Path $root ($name+'.ready')),[string]$PID)\n"
        "[Console]::WriteLine('MUX-READY-'+$name)\n"
        "while($true){\n"
        "  $key=[Console]::ReadKey($true)\n"
        "  if($key.KeyChar -eq [char]3){\n"
        "    [IO.File]::AppendAllText((Join-Path $root ($name+'.control')),'C')\n"
        "  } elseif($key.KeyChar -eq 'i'){\n"
        "    [IO.File]::AppendAllText((Join-Path $root ($name+'.input')),'I')\n"
        "  } elseif($key.KeyChar -eq 'r'){\n"
        "    [IO.File]::WriteAllText((Join-Path $root ($name+'.size')),"
        "(@{width=[Console]::WindowWidth;height=[Console]::WindowHeight}|ConvertTo-Json))\n"
        "  } elseif($key.KeyChar -eq 'q'){exit 17}\n"
        "}\n", encoding="utf-8",
    )

    def command(*args):
        return subprocess.run(
            [mux, *args], env=env, cwd=tmp_path, capture_output=True,
            text=True, encoding="utf-8", timeout=5, **no_window_kwargs(),
        )

    def launch(stack, index, *, create):
        root = tmp_path / ("caller-" + uuid4().hex)
        root.mkdir()
        argv, _ = _scripts(root, "selected", "helper")
        script = root / "selected \u65e5.ps1"
        startup = ""
        if create:
            startup = (
                f"& {_quote(mux)} new-session -d -s {_quote(names[index])} "
                f"-c {_quote(tmp_path)} {_quote(shell)} -NoProfile -NoLogo "
                f"-File {_quote(_quote(pane))} {_quote(_quote(tmp_path))} {_quote(names[index])}\n"
                "if($LASTEXITCODE -ne 0){throw 'Fixture session creation failed'}\n"
            )
        script.write_text(
            "$ErrorActionPreference='Stop'\n" + startup +
            f"[IO.File]::WriteAllText({_quote(root / 'attaching')},[string]$PID)\n"
            f"& {_quote(mux)} attach-session -t {_quote(names[index])}\n",
            encoding="utf-8",
        )
        caller_env = dict(env, WORKTREE_MANAGER_ROOT=str(root / "manager"))
        console = stack.enter_context(Console(argv, env=caller_env, cwd=root))
        ready = _wait(root / "prepared.json", console)
        pinned = console.pin_descendants()
        assert {ready["pid"], ready["outer"]} <= pinned.keys()
        request = Path(ready["request"])
        (root / "release").touch()
        _until(lambda: (root / "attaching").exists(), console, "attachment did not begin")
        assert not request.parent.exists()
        assert all(console.exited(handle) for _, handle in pinned.values())
        pane_ready = tmp_path / (names[index] + ".ready")
        _until(lambda: pane_ready.exists(), console, "mux pane did not start")
        rendered = ("MUX-READY-" + names[index]).encode()
        _until(lambda: rendered in console.output, console, "mux client did not render its pane")
        return console, root, console.pin(int(pane_ready.read_text(encoding="utf-8-sig")))

    with ExitStack() as stack:
        try:
            first, first_root, first_pane = launch(stack, 0, create=True)
            second, second_root, second_pane = launch(stack, 1, create=True)
            for index, console in enumerate((first, second)):
                console.send(b"i\x03")
                _until(lambda: (tmp_path / (names[index] + ".input")).exists(), console, "stdin lost")
                _until(lambda: (tmp_path / (names[index] + ".control")).exists(), console, "Ctrl+C lost")
            first.resize(118, 37)
            first.send(b"r")
            size_path = tmp_path / (names[0] + ".size")
            _until(lambda: size_path.exists(), first, "resize was not delivered")
            size = json.loads(size_path.read_text(encoding="utf-8-sig"))
            assert size == {"width": 118, "height": 36}
            first.send(b"\x02d")
            assert first.wait() == 0
            assert (first_root / "host-result.json").exists()
            assert not first.exited(first_pane)
            rejoined, rejoined_root, rejoined_pane = launch(stack, 0, create=False)
            rejoined.send(b"i")
            input_path = tmp_path / (names[0] + ".input")
            _until(lambda: input_path.read_text(encoding="utf-8-sig") == "II", rejoined, "rejoin input lost")
            assert not rejoined.exited(rejoined_pane)
            assert not second.exited(second_pane)
            rejoined.send(b"q")
            assert rejoined.wait() == 0
            assert rejoined.exited(rejoined_pane, 5000)
            assert (rejoined_root / "host-result.json").exists()
            assert not second.exited(second_pane)
            second.send(b"q")
            assert second.wait() == 0
            assert second.exited(second_pane, 5000)
            assert (second_root / "host-result.json").exists()
        finally:
            for name in names:
                exists = command("has-session", "-t", name)
                assert exists.returncode in (0, 1), exists.stderr
                if exists.returncode == 0:
                    stopped = command("kill-session", "-t", name)
                    assert stopped.returncode == 0, stopped.stderr
