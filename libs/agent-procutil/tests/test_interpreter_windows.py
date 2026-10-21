"""Native inner-home junction launch and selected-venv import context."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

import agent_procutil as pu
from agent_procutil._interpreter import physical_executable

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows interpreter contract")


def _guard_flags():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessMitigationPolicy.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
    ]
    flags = ctypes.c_ulong()
    if not kernel.GetProcessMitigationPolicy(
        kernel.GetCurrentProcess(), 16, ctypes.byref(flags), ctypes.sizeof(flags),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return flags.value


@pytest.mark.parametrize("require_guard", [False, True], ids=["native", "enforced"])
def test_inner_junction_launch_twice_with_selected_venv(tmp_path, record_property, require_guard):
    before = _guard_flags()
    record_property("redirection_guard_flags", before)
    if require_guard and not before & 1:
        pytest.skip("This worker does not inherit enforced RedirectionGuard; native-only proof")
    _winapi = pytest.importorskip("_winapi")
    base_python = physical_executable(sys._base_executable)
    assert base_python
    base_home = Path(base_python).parent
    base_pythonw = physical_executable(str(base_home / "pythonw.exe"))
    assert base_pythonw, "Native GUI execution requires a physical pythonw.exe"
    root = tmp_path / "selected"
    venv.EnvBuilder(with_pip=False).create(root)
    alias = tmp_path / "minor"
    _winapi.CreateJunction(str(base_home), str(alias))
    try:
        cfg = root / "pyvenv.cfg"
        cfg.write_text("\n".join(
            f"home = {alias}" if line.startswith("home =") else line
            for line in cfg.read_text(encoding="utf-8").splitlines()
        ) + "\n", encoding="utf-8")
        site = root / "Lib" / "site-packages"
        site.mkdir(parents=True, exist_ok=True)
        (site / "inner_home_context.py").write_text("identity = 'selected-venv'\n")
        original = str(root / "Scripts" / "python.exe")
        if before & 1:
            with pytest.raises(OSError) as blocked:
                os.stat(alias / "python.exe")
            assert blocked.value.winerror == 448
            for _ in range(2):
                failed = subprocess.run(
                    [base_python, "-I", "-c", "raise SystemExit('startup unexpectedly succeeded')"],
                    env={**os.environ, "__PYVENV_LAUNCHER__": original},
                    capture_output=True, timeout=15, **pu.no_window_kwargs(),
                )
                assert failed.returncode != 0
                assert b"error evaluating path" in failed.stderr
                for select in (pu.direct_python, pu.windowless_python, pu.windowless_python_env):
                    with pytest.raises(RuntimeError, match="physical Python home"):
                        select(original)
                with pytest.raises(RuntimeError, match="physical Python home"):
                    pu.windowless_python(original, require_windowless=True)
            # Only the disposable fixture models qualified runtime construction.
            # The selection API never rewrites an existing runtime's config.
            cfg.write_text(
                cfg.read_text(encoding="utf-8").replace(f"home = {alias}", f"home = {base_home}"),
                encoding="utf-8",
            )
        console, extra = pu.direct_python(original)
        gui = pu.windowless_python(original, require_windowless=True)
        assert gui == pu.windowless_python(original)
        assert os.path.samefile(console, base_python)
        assert os.path.samefile(gui, base_pythonw)
        assert extra == pu.windowless_python_env(original) == {"__PYVENV_LAUNCHER__": original}
        code = (
            "import ctypes,json,pathlib,sys,inner_home_context as context;"
            "k=ctypes.WinDLL('kernel32',use_last_error=True);"
            "k.GetCurrentProcess.restype=ctypes.c_void_p;"
            "k.GetProcessMitigationPolicy.argtypes="
            "[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_size_t];"
            "f=ctypes.c_ulong();"
            "assert k.GetProcessMitigationPolicy(k.GetCurrentProcess(),16,"
            "ctypes.byref(f),ctypes.sizeof(f));"
            "pathlib.Path(sys.argv[1]).write_text(json.dumps("
            "{'prefix':sys.prefix,'identity':context.identity,'flags':f.value}))"
        )
        for cycle in range(2):
            for intent, interpreter in (("console", console), ("gui", gui)):
                result = tmp_path / f"{intent}-{cycle}.json"
                child = subprocess.run(
                    [interpreter, "-I", "-c", code, str(result)],
                    env={**os.environ, **extra},
                    capture_output=True, timeout=15, **pu.no_window_kwargs(),
                )
                assert child.returncode == 0, child.stderr
                evidence = json.loads(result.read_text())
                assert os.path.normcase(evidence["prefix"]) == os.path.normcase(str(root))
                assert evidence["identity"] == "selected-venv"
                assert evidence["flags"] == before
        assert _guard_flags() == before
    finally:
        alias.rmdir()
