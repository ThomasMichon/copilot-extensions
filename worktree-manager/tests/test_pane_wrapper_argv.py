"""Native pane ownership and flag-stripping argv regression coverage.

Migrated from plugins/agent-worktrees/tests/test_pane_wrapper_argv.py as part
of the Phase 3b Sub-slice 2a Step 2 cutover (efforts/active/worktree-manager-
control-plane/phase-3b-mux-relocation.md): Worktree Manager's bin/ is now the
sole copy of the interactive mux launch scripts, so their regression coverage
lives here instead of in agent-worktrees.

PowerShell's ``$x = if (cond) { @(...) } else { @() }`` (if-as-expression
assignment) silently unwraps a single-element array result to a bare scalar,
even though the branch itself forces array typing with ``@()``. The
wrapper's flag-stripping loop narrows ``$rest`` down to exactly the trailing
payload command once every known ``-AwWt``/``-AwProject``/``--aw-prompt-b64``/
``--aw-prompt-receipt-b64``/``-AwAhpTokenFile`` pair is stripped -- for the
common single-token payload case (e.g. bare ``copilot``) that narrowing
lands on exactly one remaining element, which used to collapse ``$rest`` to
a scalar string and silently corrupt the eventual ``& $rest[0] @($rest[1..])``
invocation (issue found via live test-drive on the context-handoff-overhaul
effort's Phase 6 mux-primitive validation).

This extracts and runs the real parsing loop (not a paraphrase) via a live
``pwsh`` subprocess, so a regression of the if-as-expression pattern is
caught even though it is invisible to any purely textual/source-based check.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

WRAPPER = Path(__file__).resolve().parents[1] / "bin" / "pane-wrapper.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="PowerShell array semantics are Windows-specific here")


def _run_parser(tmp_path: Path, args: list[str]) -> dict:
    """Extract the real flag-stripping loop and report the final ``$rest``."""
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 (pwsh) is required")

    source = WRAPPER.read_text(encoding="utf-8")
    loop = source.split("$rest = @($args)", 1)[1].split(
        "if ($rest.Count -eq 0) { exit 0 }", 1
    )[0]

    script = tmp_path / "parse.ps1"
    args_literal = ",".join(f"'{a}'" for a in args)
    script.write_text(
        "$args = @(" + args_literal + ")\n"
        "$rest = @($args)\n"
        + loop
        + "\n"
        "[pscustomobject]@{\n"
        "    Count = $rest.Count\n"
        "    Type  = $rest.GetType().Name\n"
        "    Items = @($rest)\n"
        "} | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NoLogo", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_single_token_payload_survives_as_array(tmp_path: Path):
    """The realistic failing shape: one payload token (``copilot``) trailing
    the three known control flags. ``$rest`` must remain an array so
    ``$rest[0]`` is the whole token, not its first character."""
    parsed = _run_parser(
        tmp_path,
        [
            "-AwWt", "some-worktree-id",
            "-AwProject", "proj",
            "--aw-prompt-b64", "cHJvbXB0",
            "--aw-prompt-receipt-b64", "cmVjZWlwdA==",
            "copilot",
        ],
    )
    assert parsed["Type"] != "String", (
        "$rest collapsed to a scalar string -- the if-as-expression unwrap regressed"
    )
    assert parsed["Count"] == 1
    items = parsed["Items"] if isinstance(parsed["Items"], list) else [parsed["Items"]]
    assert items == ["copilot"]


def test_multi_token_payload_still_correct(tmp_path: Path):
    """A multi-token payload (already worked before the fix) must still."""
    parsed = _run_parser(
        tmp_path,
        [
            "-AwWt", "some-worktree-id",
            "-AwProject", "proj",
            "--aw-prompt-b64", "cHJvbXB0",
            "--aw-prompt-receipt-b64", "cmVjZWlwdA==",
            "copilot", "--allow-all-tools", "-p", "hi",
        ],
    )
    assert parsed["Type"] != "String"
    assert parsed["Count"] == 4
    assert parsed["Items"] == ["copilot", "--allow-all-tools", "-p", "hi"]


def test_no_control_flags_bare_command_unaffected(tmp_path: Path):
    """No ``-AwWt``/prompt flags at all -- the loop never runs; ``$rest``
    should be exactly the original argv, untouched."""
    parsed = _run_parser(tmp_path, ["copilot"])
    assert parsed["Type"] != "String"
    assert parsed["Count"] == 1
    items = parsed["Items"] if isinstance(parsed["Items"], list) else [parsed["Items"]]
    assert items == ["copilot"]


QUERY_JOB = """
import ctypes, json, sys
from ctypes import wintypes
class BasicLimits(ctypes.Structure):
    _fields_ = [
        ('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
        ('max_working_set', ctypes.c_size_t), ('processes', wintypes.DWORD),
        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
        ('scheduling', wintypes.DWORD),
    ]
class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t),
    ]
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.QueryInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel.QueryInformationJobObject.restype = wintypes.BOOL
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.DuplicateHandle.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
]
kernel.DuplicateHandle.restype = wintypes.BOOL
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL
owner = kernel.OpenProcess(0x40, False, int(sys.argv[1]))
if not owner:
    raise ctypes.WinError(ctypes.get_last_error())
job = wintypes.HANDLE()
try:
    if not kernel.DuplicateHandle(
        owner, int(sys.argv[2]), kernel.GetCurrentProcess(), ctypes.byref(job),
        0, False, 2,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
finally:
    kernel.CloseHandle(owner)
limits = ExtendedLimits()
size = ctypes.sizeof(limits)
try:
    if not kernel.QueryInformationJobObject(job, 9, ctypes.byref(limits), size, None):
        raise ctypes.WinError(ctypes.get_last_error())
finally:
    kernel.CloseHandle(job)
assert limits.basic.flags == 0x2000, hex(limits.basic.flags)
assert limits.basic.processes == 0
assert limits.process_memory == limits.job_memory == 0
"""


def _pwsh() -> str:
    executable = shutil.which("pwsh")
    assert executable, "PowerShell 7 is required for native pane ownership validation"
    return executable


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(params=["manager", "worktrees"])
def pane_wrapper(request, tmp_path, monkeypatch):
    if request.param == "manager":
        return WRAPPER
    root = WRAPPER.parents[2]
    monkeypatch.syspath_prepend(str(root / "tools"))
    from materialize_launch_wrapper_assets import materialize_into

    consumer = root / "plugins" / "agent-worktrees"
    destination = tmp_path / "packaged worktrees"
    destination.mkdir()
    log = materialize_into(
        source_consumer_dir=consumer, dest_consumer_dir=destination,
        canonical_root=root,
    )
    manifest = json.loads((consumer / "launch-wrapper-assets.json").read_text("utf-8"))
    assert len(log) == len(manifest["files"]) and all(line.startswith("OK") for line in log)
    wrapper = destination / "bin" / WRAPPER.name
    assert wrapper.read_bytes() == WRAPPER.read_bytes()
    return wrapper


def test_native_job_binding_does_not_compile_and_uses_kernel_abi(tmp_path, pane_wrapper):
    source = pane_wrapper.read_text("utf-8")
    function = source.split("function Set-AwPaneKillOnCloseJob {", 1)[1].split(
        "\nSet-AwPaneKillOnCloseJob", 1
    )[0]
    child = tmp_path / "query.py"
    child.write_text(
        QUERY_JOB + "\nprint(json.dumps({'size': size, 'flags': limits.basic.flags}))\n",
        encoding="utf-8",
    )
    script = tmp_path / "bind.ps1"
    script.write_text(
        "function Add-Type { throw 'The pane hot path must not compile C#.' }\n"
        "function Set-AwPaneKillOnCloseJob {" + function + "\n"
        "Set-AwPaneKillOnCloseJob\n"
        "if (-not $script:AwPaneJobHandle) { throw 'Pane job was not installed.' }\n"
        f"& {_quote(sys.executable)} {_quote(child)} $PID $script:AwPaneJobHandle\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("WORKTREE_NO_PANE_JOB", None)
    result = subprocess.run(
        [_pwsh(), "-NoProfile", "-NoLogo", "-File", str(script)],
        env=env, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "size": 144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112,
        "flags": 0x2000,
    }


def test_actual_wrapper_exit_reaps_its_native_child(tmp_path, pane_wrapper):
    from ctypes import wintypes

    child = tmp_path / "owned child.py"
    ready = tmp_path / "ready.json"
    child.write_text(
        "import json, os, pathlib, sys, time\n"
        "ready = pathlib.Path(sys.argv[1])\n"
        "temporary = ready.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid': os.getpid()}))\n"
        "temporary.replace(ready)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("WORKTREE_NO_PANE_JOB", None)
    env["WORKTREE_PANE_MIN_RUNTIME"] = "0"
    env["WORKTREE_PANE_WAIT_TIMEOUT"] = "0"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = subprocess.Popen(
        [_pwsh(), "-NoProfile", "-NoLogo", "-File", str(pane_wrapper),
         sys.executable, str(child), str(ready)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **no_window_kwargs(),
    )
    handle = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"Owned native child did not start: {stdout!r}; {stderr!r}")
        pid = json.loads(ready.read_text())["pid"]
        handle = kernel.OpenProcess(0x100001, False, pid)
        assert handle, ctypes.WinError(ctypes.get_last_error())
        assert kernel.WaitForSingleObject(handle, 0) == 258
        process.kill()
        process.communicate(timeout=5)
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if handle:
            if kernel.WaitForSingleObject(handle, 0) == 258:
                assert kernel.TerminateProcess(handle, 1)
                assert kernel.WaitForSingleObject(handle, 5000) == 0
            assert kernel.CloseHandle(handle)
