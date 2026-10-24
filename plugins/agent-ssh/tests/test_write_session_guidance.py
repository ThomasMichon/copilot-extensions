"""Tests for the agent-ssh session guidance writer."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPT = _PLUGIN / "scripts" / "write_session_guidance.py"
_SPEC = importlib.util.spec_from_file_location("ssh_guidance_writer_under_test", _SCRIPT)
assert _SPEC and _SPEC.loader
writer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = writer
_SPEC.loader.exec_module(writer)


def _target(home: Path) -> Path:
    return (
        home
        / ".copilot"
        / "session-state"
        / "session-1"
        / "instructions"
        / "agent-ssh"
        / "session-guidance.instructions.md"
    )


def test_combines_catalog_and_mesh_pointer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda root, stem, payload, **kwargs: (
            "## agent-ssh session command catalog\n\ncatalog"
            if stem == "emit-command-catalog"
            else "## SSH machine mesh available for this repo\n\nmesh"
        ),
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert _target(tmp_path).read_text(encoding="utf-8") == (
        "# Agent SSH session guidance\n\n"
        "## agent-ssh session command catalog\n\ncatalog\n\n"
        "## SSH machine mesh available for this repo\n\nmesh\n"
    )


def test_no_content_replaces_stale_guidance(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args, **kwargs: "")
    target = _target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stale guidance", encoding="utf-8")
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = target.read_text(encoding="utf-8")
    assert "stale guidance" not in content
    assert "unavailable" in content


def test_failed_snapshot_is_explicit_even_with_available_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(
        writer, "_run_contributor",
        lambda root, stem, *_a, **_kw: "catalog" if stem == "emit-command-catalog" else "",
    )
    assert writer.write_session_guidance(
        {"sessionId": "session-1", "cwd": str(tmp_path)}, home=tmp_path,
    )
    content = _target(tmp_path).read_text()
    assert "catalog" in content and "snapshot contributor failed or timed out" in content


def test_over_budget_content_writes_explicit_status(monkeypatch, tmp_path):
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda root, stem, payload, **kwargs: (
            "x" * 5000 if stem == "emit-command-catalog" else ""
        ),
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    assert "omitted because" in _target(tmp_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("length", [7, 4000, 4096 - len(writer._GUIDANCE_HEADER) - 1])
def test_combined_budget_preserves_fitting_catalog(monkeypatch, tmp_path, length):
    catalog = "c" * length
    monkeypatch.setattr(
        writer, "_run_contributor",
        lambda root, stem, payload, **kwargs: catalog if stem == "emit-command-catalog" else "x" * 5000,
    )
    assert writer.write_session_guidance({"sessionId": "session-1"}, home=tmp_path)
    content = _target(tmp_path).read_text()
    assert catalog in content and "SSH snapshot omitted" in content
    assert len(content.encode()) <= 4096


@pytest.mark.parametrize("session_id", ("", "../escape", "slash/value", "a" * 129))
def test_rejects_unsafe_session_ids(monkeypatch, tmp_path, session_id):
    monkeypatch.setattr(
        writer,
        "_run_contributor",
        lambda *args, **kwargs: pytest.fail("must not run for an unsafe session id"),
    )
    assert not writer.write_session_guidance({"sessionId": session_id}, home=tmp_path)
    assert not (tmp_path / ".copilot").exists()


@pytest.mark.parametrize("component", (".copilot", "session-state", "session-1"))
def test_rejects_ancestor_escape(monkeypatch, tmp_path, component):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args, **kwargs: "guidance")
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == ".copilot":
        link = tmp_path / ".copilot"
    elif component == "session-state":
        copilot = tmp_path / ".copilot"
        copilot.mkdir()
        link = copilot / "session-state"
    else:
        state = tmp_path / ".copilot" / "session-state"
        state.mkdir(parents=True)
        link = state / "session-1"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_resolve_runtime_error_fails_open(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, "_run_contributor", lambda *args, **kwargs: "guidance")
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path.name == "session-state":
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_validated_payload_cwd_accepts_absolute_existing_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    raw = json.dumps({"cwd": str(repo)}).encode("utf-8")
    assert writer._validated_payload_cwd(raw) == repo.resolve()


@pytest.mark.parametrize(
    "raw_cwd",
    (None, "", "relative/path", "does-not-exist-anywhere"),
)
def test_validated_payload_cwd_rejects_missing_relative_or_nonexistent(
    tmp_path, raw_cwd
):
    value = raw_cwd
    if raw_cwd == "does-not-exist-anywhere":
        value = str(tmp_path / raw_cwd)
    raw = json.dumps({"cwd": value}).encode("utf-8")
    assert writer._validated_payload_cwd(raw) is None


def test_validated_payload_cwd_rejects_unrelated_file_path(tmp_path):
    unrelated_file = tmp_path / "not-a-directory"
    unrelated_file.write_text("x", encoding="utf-8")
    raw = json.dumps({"cwd": str(unrelated_file)}).encode("utf-8")
    assert writer._validated_payload_cwd(raw) is None


def test_run_contributor_skips_subprocess_when_cwd_required_but_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        writer.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail(
            "subprocess must not run without a validated cwd"
        ),
    )
    result = writer._run_contributor(
        _PLUGIN,
        "emit-mesh-pointer",
        b'{"sessionId":"session-1"}',
        cwd=None,
        require_cwd=True,
    )
    assert result == ""


def test_mesh_pointer_is_invoked_with_require_cwd_flag(monkeypatch, tmp_path):
    calls = []

    def fake_contributor(root, stem, payload, *, cwd=None, require_cwd=False):
        calls.append((stem, cwd, require_cwd))
        return "context"

    monkeypatch.setattr(writer, "_run_contributor", fake_contributor)
    assert writer.write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    require_flags = {stem: require for stem, _cwd, require in calls}
    assert [stem for stem, _cwd, _require in calls] == [
        "emit-command-catalog", "emit-mesh-pointer",
    ]
    assert require_flags["emit-mesh-pointer"] is True
    assert require_flags["emit-command-catalog"] is False


def test_mesh_pointer_receives_validated_cwd_when_present(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = {}

    def fake_contributor(root, stem, payload, *, cwd=None, require_cwd=False):
        if stem == "emit-mesh-pointer":
            seen["cwd"] = cwd
            seen["require_cwd"] = require_cwd
        return "context"

    monkeypatch.setattr(writer, "_run_contributor", fake_contributor)
    assert writer.write_session_guidance(
        {"sessionId": "session-1", "cwd": str(repo)}, home=tmp_path
    )
    assert seen["cwd"] == repo.resolve()
    assert seen["require_cwd"] is True


def test_reparse_detection_without_link_creation():
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
    )
    assert writer._is_link_or_reparse(path)


def test_main_always_emits_empty_object(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("not json"))
    assert writer.main() == 0
    assert capsys.readouterr().out == "{}"


def test_powershell_wrapper_writes_bounded_session_file(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "machines.yaml").write_text("version: 1\nmachines: {}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps(
        {"sessionId": "session-1", "cwd": str(repo), "source": "copilot-cli"}
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(_PLUGIN),
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(_PLUGIN / "scripts" / "write-session-guidance.ps1"),
        ],
        cwd=home,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert content.startswith("# Agent SSH session guidance\n\n")
    assert "## agent-ssh session command catalog" in content
    assert "## SSH snapshot unavailable" in content
    assert not (home / ".agent-ssh").exists()
    assert len(content.encode("utf-8")) <= writer._GUIDANCE_MAX_BYTES


def test_actual_writer_and_cli_replace_snapshot_without_actuation(tmp_path):
    """Use a ready test interpreter, not an installer or the operator's runtime."""
    shell = (shutil.which("pwsh") or shutil.which("powershell.exe")) if os.name == "nt" else shutil.which("bash")
    if not shell:
        pytest.skip("native shell is unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(repo)], check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    home = tmp_path / "home"
    home.mkdir()
    payload_root = tmp_path / "payload"
    scripts = payload_root / "scripts"
    scripts.mkdir(parents=True)
    if os.name == "nt":
        dependency = payload_root / "libs" / "agent-procutil" / "src" / "agent_procutil"
        dependency.mkdir(parents=True)
        shutil.copyfile(
            _PLUGIN.parents[1] / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py",
            dependency / "__init__.py",
        )
    for name in (
        "write_session_guidance.py", "write-session-guidance.ps1",
        "emit-command-catalog.ps1", "emit-mesh-pointer.ps1",
        "write-session-guidance.sh", "emit-command-catalog.sh", "emit-mesh-pointer.sh",
    ):
        shutil.copyfile(_PLUGIN / "scripts" / name, scripts / name)
    (scripts / "install.ps1").write_text("throw 'installer must not run'", encoding="utf-8")
    (scripts / "install.sh").write_text("exit 91\n", encoding="utf-8")
    source_root = _PLUGIN.parents[1]
    source_paths = [str(_PLUGIN / "src"), *(
        str(path) for path in (source_root / "libs").glob("*/src")
    )]
    entry = payload_root / "ready-runtime.py"
    entry.write_text(
        "import os,runpy,sys\n"
        "assert os.environ.get('AGENT_SSH_NO_SELFPROVISION') == '1'\n"
        f"sys.path[:0] = {source_paths!r}\n"
        "runpy.run_module('agent_ssh',run_name='__main__')\n",
        encoding="utf-8",
    )
    binary = payload_root / "bin"
    binary.mkdir()
    quote = lambda text: "'" + str(text).replace("'", "''") + "'"
    (binary / "agent-ssh.ps1").write_text(
        f"& {quote(sys.executable)} {quote(entry)} @args\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    (binary / "agent-ssh").write_text(
        f'#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} {shlex.quote(str(entry))} "$@"\n',
        encoding="utf-8",
    )
    (binary / "agent-ssh").chmod(0o755)
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload_root), "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "COPILOT_EXTENSIONS_CONTEXT"):
        environment.pop(name, None)
    samples = []
    for alias in ("first-host", "second-host"):
        (repo / "machines.yaml").write_text(json.dumps({"machines": {"host": {
            "ssh": {"ready": True, "environments": [{"name": "linux", "alias": alias}]},
        }}}), encoding="utf-8")
        command = (
            [shell, "-NoProfile", "-File", str(scripts / "write-session-guidance.ps1")]
            if os.name == "nt" else [shell, str(scripts / "write-session-guidance.sh")]
        )
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=home, env=environment,
            input=json.dumps({"sessionId": "session-1", "cwd": str(repo)}),
            capture_output=True, text=True, timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        samples.append(time.monotonic() - started)
        assert completed.returncode == 0 and completed.stdout == "{}"
        content = _target(home).read_text(encoding="utf-8")
        assert "## agent-ssh session command catalog" in content
        assert "## SSH topology and local profile snapshot" in content
        data = json.loads(content.rsplit("```json\n", 1)[1].split("\n```", 1)[0])
        assert data["aliases"][0]["alias"] == alias
        assert data["aliases"][0]["profile"] == "missing"
        assert data["connectivity"] == data["effective_routes"] == "unobserved"
        assert len(content.encode()) <= 4096
    assert "first-host" not in content
    assert not (home / ".ssh").exists()
    assert not (home / ".agent-ssh").exists()
    print(json.dumps({
        "measurement": "ready-test-interpreter-native-writer",
        "platform": sys.platform, "samples_seconds": samples,
        "sessions": ["fresh", "resumed"], "aliases": 1, "fragments": 0,
        "boundary": "native-wrapper-to-exit", "model_admission": "unobserved",
        "cache": "not-controlled", "tail_qualification": "insufficient-samples",
        "cpu_and_child_spawn_counts": "unmeasured",
    }))


@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh"])
def test_powershell_stderr_failure_still_emits_unavailable(tmp_path, shell_name):
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip("selected PowerShell is unavailable")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    emitter = scripts / "emit-mesh-pointer.ps1"
    shutil.copyfile(_PLUGIN / "scripts" / emitter.name, emitter)
    (binary / "agent-ssh.ps1").write_text(
        "[Console]::Error.WriteLine('runtime refused')\nexit 126\n", encoding="utf-8",
    )
    completed = subprocess.run(
        [shell, "-NoProfile", "-File", str(emitter)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert completed.returncode == 0
    assert "SSH snapshot unavailable" in json.loads(completed.stdout)["additionalContext"]


def _pid_running(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    status = Path("/proc") / str(pid) / "stat"
    if status.exists() and status.read_text().split(") ", 1)[1].startswith("Z"):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("parent_exits", [False, True])
def test_contributor_timeout_reaps_real_descendant(tmp_path, monkeypatch, parent_exits):
    pid_file = tmp_path / "descendant.pid"
    code = (
        "import os,subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "creationflags=0x08000000 if os.name=='nt' else 0)\n"
        f"Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        f"{'raise SystemExit(0)' if parent_exits else 'time.sleep(60)'}\n"
    )
    monkeypatch.setattr(writer, "_contributor_argv", lambda *_: [sys.executable, "-c", code])
    monkeypatch.setattr(writer, "_SUBPROCESS_TIMEOUT_S", 2)
    try:
        assert writer._run_contributor(
            _PLUGIN, "emit-mesh-pointer", b"{}", cwd=tmp_path,
        ) == ""
        assert pid_file.exists()
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while _pid_running(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not _pid_running(pid)
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            if _pid_running(pid):
                os.kill(pid, signal.SIGTERM)


def test_worktrees_lookup_timeout_reaps_nested_resolver(tmp_path, monkeypatch):
    from agent_ssh import _worktrees

    pid_file = tmp_path / "resolver-child.pid"
    resolver = tmp_path / "resolver.py"
    resolver.write_text(
        "import os,subprocess,sys,time\nfrom pathlib import Path\n"
        "assert os.environ['AGENT_WORKTREES_NO_SELFPROVISION']=='1'\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "creationflags=0x08000000 if os.name=='nt' else 0)\n"
        f"Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    command = tmp_path / ("peer.ps1" if os.name == "nt" else "peer")
    if os.name == "nt":
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command.write_text(
            f"& {quote(sys.executable)} {quote(resolver)} @args\n", encoding="utf-8",
        )
    else:
        command.write_text(
            f'#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} {shlex.quote(str(resolver))} "$@"\n',
            encoding="utf-8",
        )
        command.chmod(0o755)
    original = shutil.which
    monkeypatch.setattr(
        _worktrees.shutil, "which",
        lambda name: str(command) if name == "agent-worktrees" else original(name),
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    try:
        assert _worktrees.state_root(tmp_path, None) == (None, "unavailable")
        assert pid_file.exists()
        assert not _pid_running(int(pid_file.read_text()))
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            if _pid_running(pid):
                os.kill(pid, signal.SIGTERM)


def test_powershell_wrapper_skips_mesh_pointer_without_valid_payload_cwd(tmp_path):
    """A missing/invalid payload cwd must never fall back to the process cwd.

    The process cwd here IS a repo with machines.yaml; the payload omits cwd
    entirely, so the repository-sensitive mesh pointer must be skipped rather
    than leaking this unrelated repo's mesh via ambient process cwd.
    """
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "machines.yaml").write_text("version: 1\nmachines: {}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    payload = json.dumps({"sessionId": "session-1", "source": "copilot-cli"})
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(_PLUGIN),
    }
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(_PLUGIN / "scripts" / "write-session-guidance.ps1"),
        ],
        cwd=repo,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "{}"
    content = _target(home).read_text(encoding="utf-8")
    assert "## SSH topology and local profile snapshot" not in content
    assert "## SSH snapshot unavailable" not in content


def test_hook_and_projection_contracts():
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    writer_entries = [
        entry
        for entry in entries
        if "write-session-guidance" in entry.get("bash", "")
    ]
    assert len(writer_entries) == 1
    writer_entry = writer_entries[0]
    assert "write-session-guidance" in writer_entry["powershell"]
    assert writer_entry["timeoutSec"] == 45
    for shell in ("bash", "powershell"):
        assert "COPILOT_PLUGIN_ROOT" in writer_entry[shell]
        assert "PLUGIN_ROOT" in writer_entry[shell]
        assert "CLAUDE_PLUGIN_ROOT" in writer_entry[shell]

    declaration = json.loads(
        (_PLUGIN / "instruction-projections.json").read_text(encoding="utf-8")
    )
    assert declaration["projections"][0]["legacyMarkers"] == []
    pointer = (
        _PLUGIN / "instructions" / "session-guidance.instructions.md"
    ).read_text(encoding="utf-8")
    assert "already-disclosed session folder" in pointer
    assert "instructions/agent-ssh/session-guidance.instructions.md" in pointer
    assert "~/.copilot/session-state" not in pointer
