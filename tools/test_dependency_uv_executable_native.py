"""Real guarded process and cold SDK counterpart to portable UV identity tests."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import test_installer_dependency_policy as support

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows RedirectionGuard")
REPO = Path(__file__).resolve().parents[1]


def _guard_flags():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessMitigationPolicy.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
    ]
    value = ctypes.c_ulong()
    if not kernel.GetProcessMitigationPolicy(
        ctypes.c_void_p(-1), 16, ctypes.byref(value), ctypes.sizeof(value),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return value.value


def _plain(path):
    prefix = chr(92) * 2 + "?" + chr(92)
    return path[len(prefix):] if path.startswith(prefix) else path


def test_cold_sdk_installs_twice_from_a_guarded_caller_without_console_windows(
    tmp_path, record_property,
):
    before = _guard_flags()
    physical = support.policy._configuration.physical_executable
    uv = shutil.which("uv")
    assert uv, "native qualification requires the existing selected UV"
    base = physical(sys._base_executable)
    selected = physical(uv)
    assert base and selected
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    for name in ("installer_dependency_policy.py", "installer_dependency_config.py"):
        shutil.copyfile(support.PATH.with_name(name), sdk / name)
    script = tmp_path / "guarded.py"
    shutil.copyfile(REPO / "tools" / "fixtures" / "guarded_uv_install.py", script)
    sys.path.insert(0, str(REPO / "libs" / "agent-procutil" / "tests"))
    try:
        from test_spawn_daemon_windows_integration import _WindowObserver, _configure_win32
    finally:
        sys.path.pop(0)
    environment = dict(os.environ)
    for name in (
        "__PYVENV_LAUNCHER__", "PYTHONHOME", "UV_INTERNAL__PYTHONHOME",
        "VIRTUAL_ENV", "CONDA_PREFIX",
    ):
        environment.pop(name, None)
    with _WindowObserver(*_configure_win32()) as windows:
        result = subprocess.run(
            [_plain(base), "-I", "-B", str(script), str(sdk), str(tmp_path), _plain(selected)],
            capture_output=True, text=True, timeout=25, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    if "unsupported_guard" in observed:
        pytest.skip(f"OS cannot enforce child RedirectionGuard: {observed['unsupported_guard']}")
    assert observed["caller_guard"] & 1
    assert {key: value for key, value in observed.items() if key != "caller_guard"} == {
        "blocked_controls": 2, "installed_cycles": 2,
        "same_physical_tool_for_all_calls": True, "stricter_cutoff_preserved": True,
    }
    assert not windows.surfaced and not windows.focus_changes
    assert _guard_flags() == before
    record_property("guarded_uv_install", json.dumps(observed, sort_keys=True))
