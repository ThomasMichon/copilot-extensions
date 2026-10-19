"""Hidden lifecycle children retain diagnostics through native PowerShell dispatch."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_command_binding import (
    LIB, POWERSHELL, ROOT, _lifecycle_lock_path, _load, _resolve, _write,
    binding_fixture as binding_fixture,
)


@pytest.fixture
def human_lifecycle(tmp_path: Path) -> Path:
    """A fresh bootstrap helper has no installed runtime or site imports."""
    helper = tmp_path / "command_lifecycle.py"
    shutil.copyfile(LIB / "command_lifecycle.py", helper)
    package = tmp_path / "_lifecycle_human"
    package.mkdir()
    for target, source in (
        ("__init__.py", ROOT / "libs/update-telemetry/src/update_telemetry/human.py"),
        ("redact.py", ROOT / "libs/update-telemetry/src/update_telemetry/redact.py"),
        ("capabilities.py", ROOT / "libs/update-telemetry/src/update_telemetry/capabilities.py"),
    ):
        shutil.copyfile(source, package / target)
    return helper


def test_lifecycle_human_assets_match_canonical_sources() -> None:
    generator = _load(LIB / "generate.py", "human_lifecycle_generator")
    manifest = ROOT / "plugins/agent-worktrees/payload-invocation.json"
    generated = generator.expected_files(manifest)
    for parent in (
        manifest.parent / "scripts",
        manifest.parent / "src/agent_worktrees",
    ):
        for name, source in (
            ("__init__.py", ROOT / "libs/update-telemetry/src/update_telemetry/human.py"),
            ("redact.py", ROOT / "libs/update-telemetry/src/update_telemetry/redact.py"),
            ("capabilities.py", ROOT / "libs/update-telemetry/src/update_telemetry/capabilities.py"),
        ):
            assert generated[parent / "_lifecycle_human" / name] == source.read_text(encoding="utf-8")


def test_human_lifecycle_refuses_undeclared_powershell_before_admission(
    human_lifecycle: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _load(human_lifecycle, "undeclared_human_lifecycle")
    monkeypatch.delitem(sys.modules, "_payload_lifecycle_human", raising=False)
    def refuse_admission(*args, **kwargs):
        pytest.fail("encoding refusal must precede admission and process creation")
    monkeypatch.setattr(lifecycle, "legacy_lifecycle", refuse_admission)
    target = human_lifecycle.parent / "powershell.exe"
    with pytest.raises(ValueError, match="declared PowerShell UTF-8"):
        lifecycle.run_lifecycle(
            None, None, [str(target), "-File", str(target.with_suffix(".ps1"))],
            human_operation="install", payload_root=ROOT, plugin_id="example-agent",
        )


def test_human_failure_retains_real_pending_owner_and_releases_lease(
    human_lifecycle: Path, binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    lifecycle = _load(human_lifecycle, "real_human_lifecycle")
    monkeypatch.delitem(sys.modules, "_payload_lifecycle_human", raising=False)
    result = lifecycle.run_lifecycle(
        binding_fixture["binding"], binding_fixture["primitive"],
        [sys.executable, "-I", "-c", "print('NATIVE_FAILURE'); raise SystemExit(37)"],
        human_operation="install", **binding_fixture["arguments"],
    )
    assert result == 37
    assert _resolve(binding_fixture)["reason"] == "legacy-lifecycle-incomplete"
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()
    output = capsys.readouterr()
    assert output.out == ""
    assert "NATIVE_FAILURE [elapsed " in output.err
    assert "failed exit 37 [duration " in output.err.splitlines()[-1]
    assert "done exit" not in output.err


@pytest.mark.parametrize("human", [False, True])
def test_unattributed_lifecycle_warning_preserves_human_and_plain_contracts(
    human_lifecycle: Path, binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture, human: bool,
) -> None:
    lifecycle = _load(human_lifecycle, "unattributed_warning_lifecycle")
    monkeypatch.delitem(sys.modules, "_payload_lifecycle_human", raising=False)
    _write(binding_fixture["home"] / ".copilot/settings.json", {})
    result = lifecycle.run_lifecycle(
        binding_fixture["binding"], binding_fixture["primitive"],
        [sys.executable, "-I", "-c", "raise SystemExit(37)"],
        human_operation="install" if human else None, **binding_fixture["arguments"],
    )
    assert result == 37
    output = capfd.readouterr()
    assert output.out == ""
    lines = output.err.splitlines()
    warnings = [line for line in lines if "command ownership remains unresolved." in line]
    assert len(warnings) == 1
    if human:
        assert re.fullmatch(
            r"\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] agent-worktrees/install warning "
            r"Runtime source is unavailable; .+ \[elapsed \d+\.\d+s\]", warnings[0],
        )
        assert "failed exit 37 [duration " in lines[-1]
    else:
        assert len(lines) == 1
        assert warnings[0].startswith("Runtime source is unavailable; ")
    manifest = json.loads((binding_fixture["legacy"] / "deploy-manifest.json").read_text())
    assert manifest["commandLifecycle"]["state"] == "pending"
    assert manifest["commandLifecycle"]["owner"]["unattributed"] is True
    assert "commandBinding" not in manifest
    assert _resolve(binding_fixture)["status"] == "unresolved"
    assert not _lifecycle_lock_path(binding_fixture["legacy"]).exists()


@pytest.mark.parametrize("bounded", [False, True])
def test_human_duration_excludes_final_output_drain(
    human_lifecycle: Path, monkeypatch: pytest.MonkeyPatch, bounded: bool,
) -> None:
    lifecycle = _load(human_lifecycle, "human_drain_lifecycle")
    monkeypatch.delitem(sys.modules, "_payload_lifecycle_human", raising=False)
    human = lifecycle._human_output()
    clock = [0.0]
    released = []
    cleaned = []
    class Sink(io.StringIO):
        def write(self, text):
            if "[duration " in text:
                assert released == [True]
                assert cleaned == ([2] if bounded else [])
                assert clock[0] == 90 + (77 if bounded else 0)
            return super().write(text)
    stream = Sink()
    constructor = human.HumanOperation
    monkeypatch.setattr(
        human, "HumanOperation",
        lambda label: constructor(label, stream=stream, monotonic=lambda: clock[0]),
    )
    @contextmanager
    def lease(*args, **kwargs):
        yield SimpleNamespace(ticket={})
        released.append(True)
    @contextmanager
    def output():
        yield 42
        clock[0] += 88
    def wait(timeout=None):
        clock[0] = 2
        return 37
    def cleanup(timeout):
        cleaned.append(timeout)
        clock[0] += 77
    monkeypatch.setattr(lifecycle, "legacy_lifecycle", lease)
    monkeypatch.setattr(human, "native_output", output)
    monkeypatch.setattr(lifecycle.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(wait=wait))
    if bounded:
        monkeypatch.setattr(lifecycle, "os", SimpleNamespace(name="nt", environ=os.environ))
        monkeypatch.setattr(lifecycle, "_procutil", lambda root: SimpleNamespace(
            spawn_sync_in_kill_on_close_job=lambda *a, **kw: (
                SimpleNamespace(wait=wait), SimpleNamespace(terminate_and_wait=cleanup),
            ),
        ))
    result = lifecycle.run_lifecycle(
        None, None, [sys.executable, "-I", "-c", "pass"],
        timeout=10 if bounded else None, human_operation="update",
        payload_root=ROOT, plugin_id="example-agent",
    )
    assert result == 37
    assert released == [True]
    assert cleaned == ([2] if bounded else [])
    assert stream.getvalue().splitlines()[-1].endswith("failed exit 37 [duration 2.0s]")


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


@pytest.mark.parametrize("result", [0, 37])
def test_fresh_human_lifecycle_frames_child_and_preserves_exit(
    tmp_path: Path, human_lifecycle: Path, result: int,
) -> None:
    child = (
        "import sys; print('CHILD_STDOUT'); "
        "print('https://user:password@example.invalid/repo', file=sys.stderr); "
        "print('already framed', flush=True); "
        f"raise SystemExit({result})"
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import importlib.util, sys\n"
        "from contextlib import nullcontext\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        f"spec = importlib.util.spec_from_file_location('fresh_lifecycle', {str(human_lifecycle)!r})\n"
        "lifecycle = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(lifecycle)\n"
        "lifecycle.legacy_lifecycle = lambda *a, **kw: nullcontext(SimpleNamespace(ticket={}))\n"
        f"raise SystemExit(lifecycle.run_lifecycle(None, None, [sys.executable, '-I', '-c', {child!r}], "
        f"payload_root=Path({str(ROOT)!r}), plugin_id='example-agent', human_operation='update'))\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(driver)], capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert completed.returncode == result, completed.stderr
    assert completed.stdout == ""
    lines = completed.stderr.splitlines()
    assert len(lines) == 5, completed.stderr
    assert all(re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] example-agent/update .+ "
        r"\[(?:elapsed|duration) \d+\.\d+s\]", line,
    ) for line in lines), completed.stderr
    assert "password" not in completed.stderr
    assert "https://***@example.invalid/repo" in completed.stderr
    state = "done" if result == 0 else "failed"
    assert f"{state} exit {result} [duration " in lines[-1]


@pytest.mark.skipif(os.name != "nt" or POWERSHELL is None, reason="Windows PowerShell boundary")
@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("human", [False, True])
def test_command_binding_hidden_child_preserves_native_diagnostics(
    tmp_path: Path, human_lifecycle: Path, bounded: bool, human: bool,
) -> None:
    grandchild = (
        "[Console]::Out.WriteLine('GRANDCHILD_STDOUT'); "
        "[Console]::Error.WriteLine('GRANDCHILD_STDERR')"
    )
    encoded = base64.b64encode(grandchild.encode("utf-16le")).decode("ascii")
    child = tmp_path / "native-child.ps1"
    child.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        ". (Join-Path $PSScriptRoot 'installer-human-output.ps1')\n"
        "[Console]::Out.WriteLine('CHILD_STDOUT')\n"
        "[Console]::Error.WriteLine('CHILD_STDERR')\n"
        "[Console]::Out.WriteLine(('UNICODE_' + [char]0x00e9 + [char]0x96ea))\n"
        "$p = Start-Process -FilePath (Get-Process -Id $PID).Path "
        f"-NoNewWindow -PassThru -ArgumentList '-NoProfile -EncodedCommand {encoded}'\n"
        "$null = $p.Handle\n"
        "$p.WaitForExit()\n"
        "if ($p.ExitCode -ne 0) { exit $p.ExitCode }\n"
        "exit 37\n",
        encoding="utf-8",
    )
    initializer = tmp_path / "installer-human-output.ps1"
    shutil.copyfile(ROOT / "libs/installer-engine/installer-human-output.ps1", initializer)
    child.with_suffix(".human-output.json").write_text(json.dumps({
        "version": 1, "encoding": "utf-8", "entrypoint": child.name,
        "initializer": initializer.name,
        "entrypoint_sha256": hashlib.sha256(child.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "initializer_sha256": hashlib.sha256(initializer.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
    }), encoding="utf-8")
    driver = tmp_path / "lifecycle-driver.py"
    driver.write_text(
        "import importlib.util, os, sys\n"
        "from contextlib import nullcontext\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        f"spec = importlib.util.spec_from_file_location('native_lifecycle', "
        f"{str(human_lifecycle)!r})\n"
        "lifecycle = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(lifecycle)\n"
        "lifecycle.legacy_lifecycle = lambda *a, **kw: "
        "nullcontext(SimpleNamespace(ticket={}))\n"
        f"rc = lifecycle.run_lifecycle(None, None, "
        f"{[POWERSHELL, '-NoProfile', '-File', str(child)]!r}, "
        f"timeout={20 if bounded else None!r}, payload_root=Path({str(ROOT)!r}), "
        f"plugin_id='example-agent', human_operation={'install' if human else None!r})\n"
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
            f"& '{quoted[0]}' -I -X utf8 '{quoted[1]}' *> '{quoted[2]}'; exit $LASTEXITCODE",
        ],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 37, result.stdout + result.stderr
    raw = log.read_bytes()
    recorded = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    for marker in ("CHILD_STDOUT", "CHILD_STDERR", "GRANDCHILD_STDOUT", "GRANDCHILD_STDERR"):
        assert sum(
            (f" {marker} [elapsed " in line) if human
            else (line == marker or line.endswith(" : " + marker))
            for line in recorded.splitlines()
        ) == 1, recorded
    if human:
        assert "failed exit 37 [duration " in recorded.splitlines()[-1]
        # Assert producer bytes before an outer shell's locale-dependent native decoding.
        direct = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(driver)],
            capture_output=True, encoding="utf-8", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert direct.returncode == 37, direct.stderr
        assert direct.stdout == ""
        assert "UNICODE_\u00e9\u96ea [elapsed " in direct.stderr
