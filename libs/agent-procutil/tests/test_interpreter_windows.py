"""Native inner-home junction launch and selected-venv import context."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import venv

import pytest

import agent_procutil as pu
from agent_procutil._interpreter import physical_executable

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows interpreter contract")


def test_caller_owned_accepts_deep_selected_interpreter(tmp_path):
    from agent_procutil.owned_process import run_caller_owned

    root = tmp_path
    while len(str(root / "Scripts" / "python.exe")) <= 280:
        root /= "selected-" + "x" * 40
    venv.EnvBuilder(with_pip=False).create(root)
    interpreter = root / "Scripts" / "python.exe"
    assert len(str(interpreter)) > 260
    (root / "Lib" / "site-packages" / "selected_context.py").write_text(
        "VALUE = 'selected-venv'\n",
    )
    for cycle in range(2):
        result = run_caller_owned(
            [
                str(interpreter), "-I", "-c",
                "import json,sys,selected_context;"
                "print(json.dumps([sys.prefix,selected_context.VALUE,sys.argv[1]]));"
                "sys.stderr.write('diagnostic');sys.exit(7)",
                f"cycle {cycle}",
            ],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
        assert result.returncode == 7
        assert json.loads(result.stdout) == [str(root), "selected-venv", f"cycle {cycle}"]
        assert result.stderr == "diagnostic"

    result = run_caller_owned(
        ["cmd.exe", "/d", "/c", "echo bare-command"],
        timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0
    assert result.stdout == "bare-command\n" and result.stderr == ""
    result = run_caller_owned(
        [
            str(Path(sys.executable).with_suffix("")), "-I", "-c",
            "import sys;print(sys.prefix)",
        ],
        timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == sys.prefix and result.stderr == ""


@pytest.mark.parametrize("retained", [False, True])
def test_output_relay_accepts_deep_selected_interpreter(tmp_path, monkeypatch, retained):
    from agent_procutil import _output_relay

    root = tmp_path
    while len(str(root / "Scripts" / "python.exe")) <= 280:
        root /= "selected-" + "x" * 40
    venv.EnvBuilder(with_pip=False).create(root)
    interpreter = root / "Scripts" / "python.exe"
    assert len(str(interpreter)) > 260
    monkeypatch.setattr(
        _output_relay, "sys", SimpleNamespace(executable=str(interpreter)),
    )
    if retained:
        monkeypatch.setattr(
            _output_relay, "_retained_source_bytes",
            Path(_output_relay.__file__).read_bytes(), raising=False,
        )
    for cycle in range(2):
        output = tmp_path / f"output-{cycle}"
        with output.open("wb") as stream:
            relay = _output_relay.ByteRelay(stream.fileno())
            try:
                assert relay.offer(b"native relay output")
                deadline = time.monotonic() + 10
                while not relay.end():
                    assert time.monotonic() < deadline, "native relay did not finish"
                    time.sleep(.01)
                relay.close()
            finally:
                relay.abort(10)
        assert output.read_bytes() == b"native relay output"


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
    # CreateJunction adds its own NT prefix; readlink-derived paths already have one.
    junction_target = str(base_home)
    if junction_target.startswith("\\\\?\\UNC\\"):
        junction_target = "\\\\" + junction_target[8:]
    elif junction_target.startswith("\\\\?\\"):
        junction_target = junction_target[4:]
    _winapi.CreateJunction(junction_target, str(alias))
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
