"""Native suspended creation inside the existing PowerShell lifetime owner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

from agent_procutil._interpreter import physical_executable
from agent_procutil.owned_process import run_caller_owned
import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None, reason="Native PowerShell required",
)
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _driver(tmp_path, body, *, stdin=""):
    path = tmp_path / "driver.ps1"
    path.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_literal(SCRIPTS / 'native-seed-boundary.ps1')}\n"
        f". {_literal(SCRIPTS / 'native-seed-process.ps1')}\n"
        + body, encoding="utf-8",
    )
    return run_caller_owned(
        [shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(path)],
        cwd=tmp_path, input_text=stdin, timeout=30,
        max_stdout_bytes=8192, max_stderr_bytes=8192,
    )


def _info(tmp_path, program, arguments=()):
    return (
        f"$info=[Diagnostics.ProcessStartInfo]::new({_literal(physical_executable(sys._base_executable))})\n"
        "$info.UseShellExecute=$false; $info.CreateNoWindow=$true\n"
        "$info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true\n"
        f"$info.WorkingDirectory={_literal(tmp_path)}\n"
        "foreach ($arg in @("
        + ",".join(_literal(value) for value in ["-I", "-X", "utf8", "-c", program, *arguments])
        + ")) { $info.ArgumentList.Add($arg) }\n"
    )


@pytest.mark.parametrize("redirect", [True, False], ids=["byte-pipes", "inherited-handles"])
def test_suspended_creation_preserves_actual_identity_stdio_and_existing_wait(tmp_path, redirect):
    marker, receipt = tmp_path / "executed", tmp_path / "creation.json"
    arguments = ["", "single ' quote", 'double " quote', "\\", "end\\\\", "space \\", "\u03a9"]
    program = (
        "import ctypes,json,os,pathlib,sys\n"
        f"pathlib.Path({str(marker)!r}).touch()\n"
        "kernel=ctypes.WinDLL('kernel32',use_last_error=True)\n"
        "kernel.GetCurrentProcess.restype=ctypes.c_void_p\n"
        "kernel.GetProcessTimes.argtypes=[ctypes.c_void_p]+[ctypes.POINTER(ctypes.c_ulonglong)]*4\n"
        "times=[ctypes.c_ulonglong() for _ in range(4)]\n"
        "assert kernel.GetProcessTimes(kernel.GetCurrentProcess(),*[ctypes.byref(t) for t in times])\n"
        "print(json.dumps({'pid':os.getpid(),'birth':'windows:'+str(times[0].value),"
        "'argv':sys.argv[1:],'stdin':sys.stdin.read(),'cwd':os.getcwd(),"
        "'env':os.environ.get('NATIVE_CREATION_FIXTURE')}))\n"
        "sys.stderr.write('native stderr\\n')\nsys.exit(7)\n"
    )
    result = _driver(
        tmp_path, _info(tmp_path, program, arguments)
        + ("" if redirect else "$info.RedirectStandardOutput=$false; $info.RedirectStandardError=$false\n")
        + "$info.Environment['NATIVE_CREATION_FIXTURE']='literal = value'\n"
        "$child=New-AwNativeSeedSuspendedProcess $info\n"
        "try {\n"
        "  [Threading.Thread]::Sleep(100)\n"
        f"  if (Test-Path -LiteralPath {_literal(marker)}) {{ throw 'child executed before publication' }}\n"
        "  if ($child.CreationHandle -eq [IntPtr]::Zero) { throw 'missing actual creation handle' }\n"
        f"  @{{pid=$child.Process.Id;birth=$child.Birth}} | ConvertTo-Json -Compress | "
        f"Set-Content -LiteralPath {_literal(receipt)}\n"
        "  if (-not $child.Resume()) { throw 'native resume was not confirmed' }\n"
        "  $refused=$false; try { $null=$child.Resume() } catch { $refused=$true }\n"
        "  if (-not $refused) { throw 'duplicate resume accepted' }\n"
        "  $transfers=@()\n"
        "  if ($child.StandardOutput) { $transfers += [pscustomobject]@{Source=$child.StandardOutput; "
        "Task=$child.StandardOutput.CopyToAsync([Console]::OpenStandardOutput()); "
        "Draining=$false; Complete=$false} }\n"
        "  if ($child.StandardError) { $transfers += [pscustomobject]@{Source=$child.StandardError; "
        "Task=$child.StandardError.CopyToAsync([Console]::OpenStandardError()); "
        "Draining=$false; Complete=$false} }\n"
        "  Wait-AwNativeSeedBackend $child.Process $transfers\n"
        "  $code=$child.Process.ExitCode\n"
        "} finally { $child.Dispose() }\nexit $code\n",
        stdin="small native frame\n",
    )
    assert result.returncode == 7, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert {key: observed[key] for key in ("pid", "birth")} == json.loads(
        receipt.read_text(encoding="utf-8"),
    )
    assert observed["argv"] == arguments
    assert observed["stdin"] == "small native frame\n"
    assert Path(observed["cwd"]).samefile(tmp_path)
    assert observed["env"] == "literal = value"
    assert result.stderr == "native stderr\n"


def test_unpublished_child_is_settled_without_execution(tmp_path):
    marker = tmp_path / "must-not-execute"
    result = _driver(
        tmp_path, _info(tmp_path, f"from pathlib import Path; Path({str(marker)!r}).touch()")
        + "$child=New-AwNativeSeedSuspendedProcess $info\n"
        "$createdPid=$child.Process.Id\n"
        "$child.Dispose(); $child.Dispose()\n"
        "$alive=$true\n"
        "try { $probe=[Diagnostics.Process]::GetProcessById($createdPid); "
        "try { $alive=-not $probe.HasExited } finally { $probe.Dispose() } }\n"
        "catch [ArgumentException] { $alive=$false }\n"
        "if ($alive) { throw 'unpublished native child survived' }\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("included", [True, False])
def test_only_explicit_section_handle_survives_all_producer_closes(tmp_path, included):
    name = "Local\\seed-section-" + os.urandom(16).hex()
    program = (
        "import ctypes\n"
        "k=ctypes.WinDLL('kernel32',use_last_error=True)\n"
        "k.OpenFileMappingW.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_wchar_p]\n"
        "k.OpenFileMappingW.restype=ctypes.c_void_p\n"
        "k.MapViewOfFile.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,"
        "ctypes.c_ulong,ctypes.c_size_t]\nk.MapViewOfFile.restype=ctypes.c_void_p\n"
        "k.UnmapViewOfFile.argtypes=[ctypes.c_void_p]\nk.CloseHandle.argtypes=[ctypes.c_void_p]\n"
        f"h=k.OpenFileMappingW(4,False,{name!r})\n"
        + (
            "assert h,ctypes.get_last_error()\n"
            "view=k.MapViewOfFile(h,4,0,0,4096)\nassert view,ctypes.get_last_error()\n"
            "try: assert ctypes.string_at(view,4)==b'seed'\n"
            "finally: k.UnmapViewOfFile(view); k.CloseHandle(h)\n"
            "print('retained')\n"
            if included else "assert not h and ctypes.get_last_error()==2\nprint('excluded')\n"
        )
    )
    result = _driver(
        tmp_path, _info(tmp_path, program)
        + f"$name={_literal(name)}\n"
        "$writer=[IO.MemoryMappedFiles.MemoryMappedFile]::CreateNew($name,4096)\n"
        "$view=$writer.CreateViewAccessor(); $view.Write(0,[uint32]0x64656573); $view.Dispose()\n"
        "$reader=[IO.MemoryMappedFiles.MemoryMappedFile]::OpenExisting("
        "$name,[IO.MemoryMappedFiles.MemoryMappedFileRights]::Read,"
        "[IO.HandleInheritability]::Inheritable)\n"
        "$child=$null\n"
        "try {\n"
        "  $child=New-AwNativeSeedSuspendedProcess $info "
        + ("-InheritedHandles @($reader.SafeMemoryMappedFileHandle.DangerousGetHandle())" if included else "")
        + "\n"
        "  $reader.Dispose(); $writer.Dispose()\n"
        "  if (-not $child.Resume()) { throw 'native resume was not confirmed' }\n"
        "  $output=$child.StandardOutput.CopyToAsync([Console]::OpenStandardOutput())\n"
        "  $stderrCopy=$child.StandardError.CopyToAsync([Console]::OpenStandardError())\n"
        "  $child.Process.WaitForExit()\n"
        "  [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($output,$stderrCopy))\n"
        "  if ($child.Process.ExitCode -ne 0) { "
        "throw ('native section reader failed: '+$child.Process.ExitCode) }\n"
        "} finally { if ($child) { $child.Dispose() }; $reader.Dispose(); $writer.Dispose() }\n"
        "$missing=$false\n"
        "try { $extra=[IO.MemoryMappedFiles.MemoryMappedFile]::OpenExisting($name); "
        "$extra.Dispose() } catch [IO.FileNotFoundException] { $missing=$true }\n"
        "if (-not $missing) { throw 'section survived the exact native child' }\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ("retained\n" if included else "excluded\n")
