"""Hidden lifecycle children retain diagnostics through native PowerShell dispatch."""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_command_binding import LIB, POWERSHELL, ROOT, _load


@pytest.mark.parametrize("bounded", [False, True])
def test_command_binding_lifecycle_forwards_explicit_streams(
    monkeypatch: pytest.MonkeyPatch, bounded: bool,
) -> None:
    lifecycle = _load(LIB / "command_lifecycle.py", "stdio_lifecycle_reference")
    lease = SimpleNamespace(ticket={})
    monkeypatch.setattr(lifecycle, "legacy_lifecycle", lambda *a, **kw: nullcontext(lease))
    monkeypatch.setattr(lifecycle, "os", SimpleNamespace(name="nt", environ={}))
    child = SimpleNamespace(wait=lambda timeout: 37)
    cleaned = []

    def spawn(command, **kwargs):
        assert kwargs["stdout"] is sys.stdout
        assert kwargs["stderr"] is sys.stderr
        if bounded:
            assert kwargs["require_job"] is True
            assert kwargs["allow_breakaway"] is False
            return child, SimpleNamespace(terminate_and_wait=cleaned.append)
        return child

    monkeypatch.setattr(lifecycle.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        lifecycle, "_procutil",
        lambda root: SimpleNamespace(spawn_sync_in_kill_on_close_job=spawn),
    )
    result = lifecycle.run_lifecycle(
        None, None, [sys.executable, "-c", "pass"],
        timeout=1 if bounded else None, payload_root=ROOT,
    )
    assert result == 37
    assert cleaned == ([2] if bounded else [])


@pytest.mark.skipif(os.name != "nt" or POWERSHELL is None, reason="Windows PowerShell boundary")
@pytest.mark.parametrize("bounded", [False, True])
def test_command_binding_hidden_child_preserves_native_diagnostics(
    tmp_path: Path, bounded: bool,
) -> None:
    grandchild = (
        "[Console]::Out.WriteLine('GRANDCHILD_STDOUT'); "
        "[Console]::Error.WriteLine('GRANDCHILD_STDERR')"
    )
    encoded = base64.b64encode(grandchild.encode("utf-16le")).decode("ascii")
    child = tmp_path / "native-child.ps1"
    child.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::Out.WriteLine('CHILD_STDOUT')\n"
        "[Console]::Error.WriteLine('CHILD_STDERR')\n"
        "$p = Start-Process -FilePath (Get-Process -Id $PID).Path "
        f"-NoNewWindow -PassThru -ArgumentList '-NoProfile -EncodedCommand {encoded}'\n"
        "$null = $p.Handle\n"
        "$p.WaitForExit()\n"
        "if ($p.ExitCode -ne 0) { exit $p.ExitCode }\n"
        "exit 37\n",
        encoding="utf-8",
    )
    driver = tmp_path / "lifecycle-driver.py"
    driver.write_text(
        "import importlib.util, os, sys\n"
        "from contextlib import nullcontext\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        f"spec = importlib.util.spec_from_file_location('native_lifecycle', "
        f"{str(LIB / 'command_lifecycle.py')!r})\n"
        "lifecycle = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(lifecycle)\n"
        "lifecycle.legacy_lifecycle = lambda *a, **kw: "
        "nullcontext(SimpleNamespace(ticket={}))\n"
        f"rc = lifecycle.run_lifecycle(None, None, "
        f"{[POWERSHELL, '-NoProfile', '-File', str(child)]!r}, "
        f"timeout={20 if bounded else None!r}, payload_root=Path({str(ROOT)!r}))\n"
        "sys.stdout.flush()\n"
        "sys.stderr.flush()\n"
        "os._exit(rc)\n",
        encoding="utf-8",
    )
    log = tmp_path / "native-dispatch.log"
    quoted = [str(path).replace("'", "''") for path in (sys.executable, driver, log)]
    result = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-Command",
            f"& '{quoted[0]}' -I '{quoted[1]}' *> '{quoted[2]}'; exit $LASTEXITCODE",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 37, result.stdout + result.stderr
    raw = log.read_bytes()
    recorded = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    for marker in ("CHILD_STDOUT", "CHILD_STDERR", "GRANDCHILD_STDOUT", "GRANDCHILD_STDERR"):
        assert sum(
            line == marker or line.endswith(" : " + marker)
            for line in recorded.splitlines()
        ) == 1, recorded
