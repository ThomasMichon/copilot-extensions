"""Embedded stdlib snapshot across the existing Windows native creation seam."""

from __future__ import annotations

import asyncio
import ctypes as c
import json
from pathlib import Path
import shutil
import sys

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable
from .owner_section_security import assert_private_section

from agent_mcp import owner_snapshot
from agent_mcp.owner_section_native import SectionNative

NATIVE = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("pwsh") is None,
    reason="native Windows and PowerShell required",
)
SOURCE = Path(owner_snapshot.__file__)
SCRIPTS = SOURCE.resolve().parents[3] / "agent-worktrees" / "scripts"


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def test_snapshot_decodes_without_an_installed_runtime(tmp_path):
    from agent_procutil.owned_process import run_caller_owned

    program = SOURCE.read_text(encoding="utf-8") + (
        "\npacket=(b'AMO1'+struct.pack('<I',2)+b'{}').ljust(SECTION_BYTES,b'\\0')\n"
        "try: decode(packet,'a'*64)\n"
        "except ValueError: pass\n"
        "else: raise AssertionError('malformed anonymous record accepted')\n"
        "assert not any(name.split('.')[0] in {'agent_mcp','agent_procutil','zdd'} "
        "for name in __import__('sys').modules)\n"
        "print('independent')\n"
    )
    result = run_caller_owned(
        [physical_executable(sys._base_executable), "-I", "-S", "-c", program],
        cwd=tmp_path, timeout=10, max_stdout_bytes=1024, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "independent\n"


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("retained", [True, False], ids=["delayed-reader", "missing-inheritance"])
async def test_native_publisher_exit_then_anonymous_snapshot(tmp_path, retained):
    program = SOURCE.read_text(encoding="utf-8") + (
        "\nimport sys\n"
        "assert sys.stdin.buffer.read(1)==b'!'\n"
        "assert not any(name.split('.')[0] in {'agent_mcp','agent_procutil','zdd'} "
        "for name in sys.modules)\n"
        "try:\n"
        " with WindowsOrigin(sys.argv[1]) as owner:\n"
        "  owner.require_alive()\n"
        "  assert owner.pid==os.getpid()\n"
        "  print(json.dumps({'pid':owner.pid,'birth':owner.birth}),flush=True)\n"
        "except FileNotFoundError:\n"
        " assert sys.argv[2]=='missing'\n"
        " print('missing',flush=True)\n"
        "assert sys.stdin.buffer.read()==b''\n"
    )
    script = tmp_path / "publication.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_literal(SCRIPTS / 'native-seed-process.ps1')}\n"
        f"Add-Type -Path {_literal(SOURCE.with_name('owner_section.cs'))}\n"
        "$publisher=[AgentMcp.NativeOwnerSection]::new()\n"
        "$child=$null\n"
        "try {\n"
        " $method=[AgentMcp.NativeOwnerSection].GetMethod('MapViewOfFile',"
        "[Reflection.BindingFlags]'NonPublic,Static')\n"
        " $view=$method.Invoke($null,@($publisher.InheritedHandle,[uint32]2,[uint32]0,"
        "[uint32]0,[UIntPtr]::new(4096)))\n"
        " if ($view -ne [IntPtr]::Zero -or [Runtime.InteropServices.Marshal]::GetLastWin32Error() "
        "-ne 5) { throw 'inherited section handle permits writing' }\n"
        f" $info=[Diagnostics.ProcessStartInfo]::new({_literal(physical_executable(sys._base_executable))})\n"
        " $info.UseShellExecute=$false; $info.CreateNoWindow=$true\n"
        f" $info.WorkingDirectory={_literal(tmp_path)}\n"
        " foreach ($arg in @("
        + ",".join(_literal(value) for value in [
            "-I", "-S", "-c", program,
        ])
        + ",$publisher.Binding,"
        + _literal("retained" if retained else "missing")
        + ")) { $info.ArgumentList.Add($arg) }\n"
        " $child=New-AwNativeSeedSuspendedProcess $info"
        + (" -InheritedHandles @($publisher.InheritedHandle)" if retained else "")
        + "\n"
        " foreach ($pair in @(@(($child.Process.Id+1),$child.Birth),"
        "@($child.Process.Id,'windows:1'))) {\n"
        "  $refused=$false\n"
        "  try { $null=$publisher.Publish($child.CreationHandle,$pair[0],$pair[1]) }\n"
        "  catch { $refused=$true }\n"
        "  if (-not $refused) { throw 'mismatched creation identity published' }\n"
        " }\n"
        " if (-not $publisher.Publish($child.CreationHandle,$child.Process.Id,$child.Birth)) "
        "{ throw 'publication unconfirmed' }\n"
        " $refused=$false\n"
        " try { $null=$publisher.Publish($child.CreationHandle,$child.Process.Id,$child.Birth) }\n"
        " catch { $refused=$true }\n"
        " if (-not $refused) { throw 'duplicate publication accepted' }\n"
        " $binding=$publisher.Binding\n"
        " $publisher.Dispose(); $publisher.Dispose()\n"
        " if (-not $child.Resume()) { throw 'resume unconfirmed' }\n"
        " @{pid=$child.Process.Id;birth=$child.Birth;binding=$binding} "
        "| ConvertTo-Json -Compress | Write-Output\n"
        "} finally { if ($child) { $child.Dispose() }; $publisher.Dispose() }\n",
        encoding="utf-8",
    )
    producer = await asyncio.create_subprocess_exec(
        shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File", str(script),
        cwd=tmp_path, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    native, handle = SectionNative(), None
    producer_handle = native.require(native.k.OpenProcess(0x101000, False, producer.pid))
    stderr = asyncio.create_task(producer.stderr.read())
    try:
        line = await asyncio.wait_for(producer.stdout.readline(), 20)
        if not line:
            assert await asyncio.wait_for(producer.wait(), 10) == 0, (
                await asyncio.wait_for(stderr, 10)
            ).decode(errors="replace")
        receipt = json.loads(line)
        handle = native.require(native.k.OpenProcess(0x101000, False, receipt["pid"]))
        assert await asyncio.to_thread(native.k.WaitForSingleObject, producer_handle, 10000) == 0
        if retained:
            mapping = native.require(native.k.OpenFileMappingW(
                0x20004, False, owner_snapshot.section_name(receipt["binding"]),
            ))
            try:
                assert_private_section(native, mapping)
            finally:
                native.close(mapping)
        # The publisher process is gone before the child can inspect the bytes.
        producer.stdin.write(b"!")
        await producer.stdin.drain()
        observed = await asyncio.wait_for(producer.stdout.readline(), 10)
        if retained:
            assert json.loads(observed) == {
                key: receipt[key] for key in ("pid", "birth")
            }
        else:
            assert observed.strip() == b"missing"
        producer.stdin.close()
        assert await asyncio.wait_for(producer.stdout.read(), 10) == b""
        assert await asyncio.wait_for(stderr, 10) == b""
        assert native.k.WaitForSingleObject(handle, 10000) == 0
        exit_code = c.c_uint32()
        query = native.k.GetExitCodeProcess
        query.argtypes, query.restype = [c.c_void_p, c.POINTER(c.c_uint32)], c.c_int32
        native.require(query(handle, c.byref(exit_code)))
        assert exit_code.value == 0
        assert not native.k.OpenFileMappingW(4, False, owner_snapshot.section_name(receipt["binding"]))
        assert c.get_last_error() == 2
    finally:
        producer.stdin.close()
        await asyncio.wait_for(producer.wait(), 10)
        await asyncio.wait_for(stderr, 10)
        native.close(handle)
        native.close(producer_handle)


@NATIVE
def test_native_publisher_refuses_an_exited_creation_handle(tmp_path):
    from agent_procutil.owned_process import run_caller_owned

    script = tmp_path / "exited.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_literal(SCRIPTS / 'native-seed-process.ps1')}\n"
        f"Add-Type -Path {_literal(SOURCE.with_name('owner_section.cs'))}\n"
        "$publisher=[AgentMcp.NativeOwnerSection]::new(); $child=$null\n"
        "try {\n"
        f" $info=[Diagnostics.ProcessStartInfo]::new({_literal(physical_executable(sys._base_executable))})\n"
        " $info.UseShellExecute=$false; $info.CreateNoWindow=$true\n"
        f" $info.WorkingDirectory={_literal(tmp_path)}\n"
        " foreach ($arg in @('-I','-S','-c','pass')) { $info.ArgumentList.Add($arg) }\n"
        " $child=New-AwNativeSeedSuspendedProcess $info\n"
        " if (-not $child.Resume()) { throw 'resume unconfirmed' }\n"
        " if (-not $child.Process.WaitForExit(10000)) { throw 'fixture did not exit' }\n"
        " if ($child.Process.ExitCode -ne 0) { throw 'fixture failed' }\n"
        " $refused=$false\n"
        " try { $null=$publisher.Publish($child.CreationHandle,$child.Process.Id,$child.Birth) }\n"
        " catch { $refused=$true }\n"
        " if (-not $refused) { throw 'dead owner published' }\n"
        "} finally { if ($child) { $child.Dispose() }; $publisher.Dispose() }\n",
        encoding="utf-8",
    )
    result = run_caller_owned(
        [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File", str(script)],
        cwd=tmp_path, timeout=20, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stdout + result.stderr
