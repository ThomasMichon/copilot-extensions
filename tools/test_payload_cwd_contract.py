"""Maintenance must leave replaceable payloads before it waits or builds."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("bash")


def _block(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"# === install-contract:v4 {name}")
    end_marker = f"# === end install-contract:v4 {name.split(' (')[0]} ==="
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


def _stage_copy_function() -> str:
    return _block(ROOT / "plugins/agent-worktrees/scripts/install.ps1", "payload-copy")


@pytest.mark.skipif(os.name != "nt", reason="Windows native payload copying")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("links", [False, True], ids=["ordinary", "links"])
def test_native_stage_copy_retains_provider_tree(
    tmp_path: Path, host: str, links: bool,
) -> None:
    powershell = shutil.which(host)
    if powershell is None:
        pytest.skip(f"{host} unavailable")
    source = tmp_path / "payload [literal] \u00e9"
    (source / ".hidden" / "nested").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "cached.pyc").write_bytes(b"retained staging input")
    (source / ".hidden" / "nested" / "payload.bin").write_bytes(bytes(range(256)))
    readonly = source / "readonly.txt"
    readonly.write_bytes(b"readonly payload")
    readonly.chmod(0o444)
    if links:
        try:
            (source / "file-link").symlink_to(readonly)
            (source / "directory-link").symlink_to(source / ".hidden", target_is_directory=True)
        except OSError as error:
            readonly.chmod(0o666)
            pytest.skip(f"symbolic links unavailable: {error}")
    expected = tmp_path / "provider"
    expected.mkdir()
    actual = tmp_path / "native"
    script = tmp_path / "copy.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n" + _stage_copy_function()
        + "\n[IO.File]::SetAttributes((Join-Path $args[0] '.hidden'), "
        "[IO.FileAttributes]::Directory -bor [IO.FileAttributes]::Hidden)\n"
        + "Copy-Item -LiteralPath $args[0] -Destination $args[1] -Recurse -Force\n"
        + "Copy-InstallerStageDirectory $args[0] $args[2]\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-File", str(script), str(source), str(expected), str(actual)],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
        baseline = expected / source.name
        paths = {path.relative_to(baseline) for path in baseline.rglob("*")}
        assert paths == {path.relative_to(actual) for path in actual.rglob("*")}
        for relative in paths:
            before, after = baseline / relative, actual / relative
            assert after.is_symlink() == before.is_symlink(), relative
            assert after.stat().st_file_attributes == before.stat().st_file_attributes, relative
            if before.is_file():
                assert after.read_bytes() == before.read_bytes(), relative
                assert after.stat().st_mtime_ns == before.stat().st_mtime_ns, relative
    finally:
        for directory in (source, expected, actual):
            for path in directory.rglob("*"):
                if path.is_file():
                    path.chmod(0o666)


@pytest.mark.skipif(os.name != "nt", reason="Windows native payload copying")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("failure", ["missing-source", "blocked-destination"])
def test_native_stage_copy_reports_io_failure(
    tmp_path: Path, host: str, failure: str,
) -> None:
    powershell = shutil.which(host)
    if powershell is None:
        pytest.skip(f"{host} unavailable")
    source, destination = tmp_path / "source", tmp_path / "destination"
    if failure == "blocked-destination":
        source.mkdir()
        (source / "payload").write_bytes(b"source")
        destination.write_bytes(b"not a directory")
    script = tmp_path / "copy.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n" + _stage_copy_function()
        + "\nCopy-InstallerStageDirectory $args[0] $args[1]\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(script), str(source), str(destination)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert result.stderr
    if failure == "blocked-destination":
        assert destination.read_bytes() == b"not a directory"


def _env(home: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_"))
    }
    env.update({
        "HOME": home.as_posix(),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        "COPILOT_PLUGIN_STAGED_FROM": "unrelated-parent-payload",
        "COPILOT_PLUGIN_INSTALL_SMOKE": "1",
        "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP": "8",
        "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "20",
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
    })
    return env


def _entry(tmp_path: Path, extension: str, contextual: bool = False) -> tuple[Path, Path]:
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-example"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    canonical = ROOT / "plugins" / "agent-worktrees" / "scripts" / f"install.{extension}"
    header = "param([string]$Action = 'install')\n" if extension == "ps1" else "#!/usr/bin/env bash\nset -e\n"
    if extension == "ps1":
        header += _stage_copy_function() + "\n"
    staging = _block(canonical, "self-stage")
    if contextual:
        text = canonical.read_text(encoding="utf-8")
        marker = (
            "    # The standard self-stage block is byte-identical"
            if extension == "ps1"
            else "    # The standard self-stage block is intentionally byte-identical"
        )
        staging = text[text.index(marker):text.index("# === install-contract:v4 self-stage")]
        if extension == "ps1":
            header += (
                "$ErrorActionPreference = 'Stop'\n"
                "$contextPayload = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path\n"
                "$RuntimeInputPlugin = $contextPayload\n"
                "$InstallDir = Join-Path $env:USERPROFILE 'runtime'\n"
                "$hostExe = (Get-Process -Id $PID).Path\n"
                "if ($true) {\n"
            )
        else:
            header += (
                'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
                'PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"\n'
                '__aw_origin_payload="$PLUGIN_DIR"\n'
                '__aw_runtime_inputs="$PLUGIN_DIR"\n'
                '__aw_install_root="$HOME/runtime"\n'
                'mkdir -p "$__aw_install_root"\n'
                'if true; then\n'
            )
    entry = scripts / f"install.{extension}"
    entry.write_text(
        header + staging + "\n"
        + _block(canonical, "smoke seam (test-only)") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload, entry


def _command(entry: Path, extension: str) -> list[str]:
    if extension == "ps1":
        assert PWSH
        return [PWSH, "-NoProfile", "-File", str(entry)]
    assert BASH
    return [BASH, entry.as_posix()]


def _reported_path(value: str) -> Path:
    if os.name == "nt" and value.startswith("/") and BASH:
        result = subprocess.run(
            [BASH, "-c", 'cygpath -w "$1"', "bash", value],
            capture_output=True, text=True, check=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        value = result.stdout.strip()
    return Path(value).resolve()


def _wait_for_smoke_receipt(
    receipt: Path,
    poll: Callable[[], int | None],
    timeout: float = 30,
) -> dict[str, object]:
    # A cold parent shell starts before the staged child's 20-second watchdog.
    deadline = time.monotonic() + timeout
    last_error: FileNotFoundError | json.JSONDecodeError | UnicodeDecodeError | None = None
    while True:
        child_exit = poll()
        try:
            data = json.loads(receipt.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as error:
            last_error = error
        else:
            if not isinstance(data, dict):
                pytest.fail(f"staged receipt is not a JSON object: {data!r}")
            return data
        if child_exit is not None:
            pytest.fail(f"child exited with code {child_exit}; no complete staged receipt: {last_error}")
        if time.monotonic() >= deadline:
            pytest.fail(f"receipt wait timed out; no complete staged receipt: {last_error}")
        time.sleep(0.05)


@pytest.mark.parametrize("initial", [None, "", '{"staged":'])
def test_smoke_receipt_waits_for_complete_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: str | None,
) -> None:
    receipt = tmp_path / "smoke.json"
    if initial is not None:
        receipt.write_text(initial, encoding="utf-8")

    def complete_receipt(_delay: float) -> None:
        receipt.write_text('{"staged": true}', encoding="utf-8")

    monkeypatch.setattr(time, "sleep", complete_receipt)
    assert _wait_for_smoke_receipt(receipt, lambda: None) == {"staged": True}


def test_smoke_receipt_rejects_incomplete_json_after_child_exit(tmp_path: Path) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_text("", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="child exited with code 0; no complete staged receipt"):
        _wait_for_smoke_receipt(receipt, lambda: 0)


def test_smoke_receipt_parses_after_observing_child_exit(tmp_path: Path) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_text('{"staged":', encoding="utf-8")

    def finish_child() -> int:
        receipt.write_text('{"staged": true}', encoding="utf-8")
        return 0

    assert _wait_for_smoke_receipt(receipt, finish_child) == {"staged": True}


def test_smoke_receipt_retries_partial_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_bytes(b'{"working_dir":"\xc3')

    def complete_receipt(_delay: float) -> None:
        receipt.write_bytes(b'{"working_dir":"\xc3\xa9"}')

    monkeypatch.setattr(time, "sleep", complete_receipt)
    assert _wait_for_smoke_receipt(receipt, lambda: None) == {"working_dir": "\u00e9"}


def test_smoke_receipt_rejects_partial_utf8_after_child_exit(tmp_path: Path) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_bytes(b'{"working_dir":"\xc3')
    with pytest.raises(pytest.fail.Exception, match="child exited with code 0; no complete staged receipt"):
        _wait_for_smoke_receipt(receipt, lambda: 0)


def test_smoke_receipt_timeout_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="receipt wait timed out; no complete staged receipt"):
        _wait_for_smoke_receipt(tmp_path / "smoke.json", lambda: None, timeout=0)


def test_smoke_receipt_rejects_non_object_json(tmp_path: Path) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_text("[]", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="not a JSON object"):
        _wait_for_smoke_receipt(receipt, lambda: None)


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("stage_flag", ["", "1", "context-install"])
@pytest.mark.parametrize("contextual", [False, True], ids=["legacy", "context"])
def test_inherited_stage_flag_cannot_pin_payload(
    tmp_path: Path, extension: str, stage_flag: str, contextual: bool,
) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, extension, contextual)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    # MSYS retains its launch CWD in an emulated-exec supervisor. Windows
    # launchers must supply HOME up front; native POSIX exercises relocation.
    launch_cwd = home if os.name == "nt" and extension == "sh" else payload
    env = _env(home)
    env.update({"TEMP": str(home), "TMP": str(home), "TMPDIR": str(home)})
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = stage_flag
    proc = subprocess.Popen(
        _command(entry, extension), cwd=launch_cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs,
    )
    try:
        receipt = home / ".agent-example" / "smoke.json"
        data = _wait_for_smoke_receipt(receipt, proc.poll)
        assert data["staged"] is True
        ran_from = data["ran_from"]
        working_dir = data["working_dir"]
        staged_from = data["staged_from"]
        assert isinstance(ran_from, str)
        assert isinstance(working_dir, str)
        assert isinstance(staged_from, str)
        assert _reported_path(ran_from).is_relative_to(home.resolve())
        assert _reported_path(working_dir) == home.resolve()
        assert staged_from.replace("\\", "/").endswith("/example/agent-example")
        if contextual:
            assert not (home / ".agent-worktrees").exists()
        assert proc.poll() is None
        backup = payload.with_name("replaced-payload")
        payload.rename(backup)
        payload.mkdir()
        assert proc.poll() is None
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 0, (stdout, stderr)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=25)


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_self_stage_watchdog_still_times_out(tmp_path: Path, extension: str) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    _, entry = _entry(tmp_path, extension)
    env = _env(home)
    env["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "1"
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        _command(entry, extension), cwd=home, env=env,
        capture_output=True, text=True, timeout=15, **kwargs,
    )
    assert result.returncode == 124, (result.stdout, result.stderr)
    assert "WATCHDOG-KILL" in (home / ".agent-example" / "reconcile.err.log").read_text()


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_unavailable_home_refuses_in_place_install(tmp_path: Path, extension: str) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    payload, entry = _entry(tmp_path, extension)
    home = tmp_path / "not-a-directory"
    home.write_text("file", encoding="utf-8")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    environment = _env(home)
    if extension == "ps1":
        # PowerShell on POSIX needs a real HOME just to initialize. Its installer
        # relocates using USERPROFILE, which remains the invalid target here.
        environment["HOME"] = str(tmp_path)
    proc = subprocess.run(
        _command(entry, extension), cwd=payload, env=environment,
        capture_output=True, text=True, timeout=20, **kwargs,
    )
    assert proc.returncode != 0
    assert "self-stage failed" in proc.stderr
    assert "running in place" not in proc.stdout + proc.stderr


def test_all_reconcile_launchers_choose_home() -> None:
    for script in (ROOT / "plugins").glob("*/scripts/bootstrap-check.ps1"):
        text = script.read_text(encoding="utf-8").replace("`\n", " ")
        for line in text.splitlines():
            if "Start-Process -FilePath 'conhost.exe'" in line:
                assert "-WorkingDirectory $env:USERPROFILE" in line, script
    for script in (ROOT / "plugins").glob("*/scripts/bootstrap-check.sh"):
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.search(r"^\s*(?:\(.*)?nohup bash ", line):
                assert line.lstrip().startswith("nohup bash "), script
                before = text[:text.index(line)].rstrip().splitlines()[-1]
                assert before.lstrip().startswith('cd "$HOME" || '), script


@pytest.mark.skipif(os.name == "nt" or not BASH, reason="native POSIX signal contract")
def test_reconcile_launch_survives_hup_with_home_cwd(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    receipt = home / "ready"
    completed = home / "completed"
    worker = home / "worker.sh"
    worker.write_text(
        'printf "%s\\n%s\\n" "$BASHPID" "$PWD" >"$HOME/ready"\n'
        'sleep 1\nprintf "done" >"$HOME/completed"\n', encoding="utf-8",
    )
    text = (ROOT / "plugins" / "agent-codespaces" / "scripts" / "bootstrap-check.sh").read_text()
    launch_start = text.index('cd "$HOME" || { echo "reconcile launch failed:')
    launch_end = text.index("\n", text.index("nohup bash ", launch_start))
    launch = text[launch_start:launch_end]
    parent = subprocess.Popen(
        [BASH, "-c", 'target=("$1")\n' + launch + "\nwait", "bash", str(worker)],
        cwd=payload, env={**os.environ, "HOME": str(home)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not receipt.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert receipt.exists()
        worker_pid, cwd = receipt.read_text().splitlines()
        assert Path(cwd).resolve() == home.resolve()
        assert int(worker_pid) != parent.pid
        payload.rename(tmp_path / "replaced-payload")
        payload.mkdir()
        os.killpg(parent.pid, signal.SIGHUP)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not completed.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert completed.read_text() == "done"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if not completed.exists():
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "nt" or not BASH, reason="MSYS Windows launch contract")
def test_windows_bash_refuses_payload_launch_cwd(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, "sh")
    proc = subprocess.run(
        _command(entry, "sh"), cwd=payload, env=_env(home),
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert proc.returncode != 0
    assert "launch from HOME or use install.ps1" in proc.stderr
    assert not (home / ".agent-example" / "smoke.json").exists()
