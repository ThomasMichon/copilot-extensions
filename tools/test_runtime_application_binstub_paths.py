"""Equivalent Windows file roots must not make published launchers stale."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from runtime_application_binstubs import global_binstubs


@pytest.mark.parametrize("ordinary,extended", [
    (r"C:\fixture app", r"\\?\C:\fixture app"),
    (r"\\server\share\fixture app", r"\\?\UNC\server\share\fixture app"),
])
def test_global_launchers_share_one_windows_file_root_spelling(
    ordinary: str, extended: str,
) -> None:
    expected = global_binstubs(
        "fixture-app", "FIXTURE_APP_HOME", ".fixture-app", root=Path(ordinary),
    )
    assert global_binstubs(
        "fixture-app", "FIXTURE_APP_HOME", ".fixture-app", root=Path(extended),
    ) == expected


def test_other_device_namespaces_keep_the_explicit_root_binding() -> None:
    root = Path(r"\\?\GLOBALROOT\Device\HarddiskVolume1\fixture")
    rendered = global_binstubs(
        "fixture-app", "FIXTURE_APP_HOME", ".fixture-app", root=root,
    )
    assert str(root) in rendered["fixture-app.ps1"]

@pytest.mark.parametrize("value", [
    r"\\?\C:\apps\fixture.",
    r"\\?\C:\apps\fixture ",
    r"\\?\C:\apps\NUL",
    r"\\?\C:\apps\com1.log",
    r"\\?\C:\apps\NUL .log",
    r"\\?\C:\apps\CONIN$",
    "\\\\?\\C:\\apps\\COM\u00b9",
    r"\\?\C:\apps\fixture\..\other",
    r"\\?\C:\apps\fixture:stream",
    r"\\?\UNC\server\share\fixture.",
    r"\\?\UNC\server\share\fixture ",
    r"\\?\UNC\server\share\LPT2.txt",
])
def test_extended_only_names_keep_the_explicit_root_binding(value: str) -> None:
    root = Path(value)
    rendered = global_binstubs(
        "fixture-app", "FIXTURE_APP_HOME", ".fixture-app", root=root,
    )
    assert str(root) in rendered["fixture-app.ps1"]
    assert f'set "FIXTURE_APP_HOME={root}"' in rendered["fixture-app.cmd"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD front door")
@pytest.mark.parametrize("deep", [False, True])
def test_extended_file_root_reaches_the_selected_cmd_slot(tmp_path: Path, deep: bool) -> None:
    root = tmp_path / "application"
    if deep:
        for index in range(6):
            root = root / (f"segment{index}" + "x" * 42)
    native = Path("\\\\?\\" + str(root))
    slot = native / "versions" / "1.0"
    slot.mkdir(parents=True)
    (native / "current-version").write_text("1.0\n", encoding="ascii")
    (slot / "fixture-app.cmd").write_bytes(b"@echo off\r\necho fixture-attached\r\n")
    front_door = tmp_path / "fixture-app.cmd"
    front_door.write_bytes(global_binstubs(
        "fixture-app", "FIXTURE_APP_HOME", ".fixture-app", root=native,
    )["fixture-app.cmd"].encode("utf-8"))
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(front_door)], cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key.upper() != "FIXTURE_APP_HOME"},
        capture_output=True, text=True, timeout=10, check=True,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW")),
    )
    assert result.stdout.strip() == "fixture-attached"
