"""Container setup progress survives an enclosing job's deadline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import _devcontainer_setup as container_setup


@pytest.mark.skipif(sys.platform != "win32", reason="unsupported native Windows entry")
def test_native_windows_entry_refuses_before_using_posix_signal_primitives() -> None:
    source = Path(__file__).with_name("run_tests_in_devcontainer.py")
    result = subprocess.run(
        [sys.executable, str(source), "--help"], capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    assert "requires POSIX signal support" in result.stderr
    assert "AttributeError" not in result.stderr


@pytest.mark.parametrize("exit_code", [0, 23])
def test_setup_stderr_is_visible_while_stdout_remains_parseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], exit_code: int,
) -> None:
    run = subprocess.run

    def setup(_argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return run(
            [sys.executable, "-c",
             "import sys; print('setup progress', file=sys.stderr, flush=True); "
             "print('{\"containerId\":\"fixture-container\"}'); "
             f"raise SystemExit({exit_code})"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs,
        )

    monkeypatch.setattr(subprocess, "run", setup)
    if exit_code:
        with pytest.raises(SystemExit, match=f"devcontainer up failed \\(exit {exit_code}\\)"):
            container_setup.bring_up(
                "fixture-devcontainer", tmp_path, "fixture", tmp_path / "devcontainer.json",
            )
    else:
        assert container_setup.bring_up(
            "fixture-devcontainer", tmp_path, "fixture", tmp_path / "devcontainer.json",
        ) == "fixture-container"
    captured = capfd.readouterr()
    assert "setup progress" in captured.err
    assert captured.out == ""
