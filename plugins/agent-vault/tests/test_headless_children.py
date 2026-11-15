"""Console helpers stay windowless without changing credential I/O."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import agent_procutil
import pytest

from agent_vault import gcm, keepassxc, prompt


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize(
    "operation",
    ["verify", "read", "identity", "groups", "add", "edit", "import", "export"],
)
def test_keepass_children_preserve_captured_io(monkeypatch, windows, operation):
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: windows)
    monkeypatch.setattr(
        keepassxc.KeePassXCBackend, "_find_cli", staticmethod(lambda: "keepassxc-cli"),
    )
    backend = keepassxc.KeePassXCBackend()
    backend.set_password("test.kdbx", "test-master")
    monkeypatch.setattr(backend, "_identity_listing", lambda *_args: set())
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="test-result", stderr="")

    monkeypatch.setattr(keepassxc.subprocess, "run", run)
    if operation == "verify":
        assert backend.verify_password("test.kdbx", "test-master")
    elif operation == "read":
        assert backend.get_entry("test.kdbx", "entry") == "test-result"
    elif operation == "identity":
        backend._create_identity_key_locked(
            "test.kdbx", "entry", "test-value", time.monotonic() + 10,
        )
    elif operation == "groups":
        backend._ensure_parent_groups("test.kdbx", "parent/entry")
    elif operation == "add":
        assert backend.add_entry("test.kdbx", "entry", password="test-value")[0]
    elif operation == "edit":
        assert backend.edit_password("test.kdbx", "entry", "test-value")[0]
    elif operation == "import":
        assert backend.import_attachment("test.kdbx", "entry", "test.bin", b"test")[0]
    else:
        assert backend.export_attachment("test.kdbx", "entry", "test.bin")[0] == b""
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert kwargs.get("creationflags", 0) == agent_procutil.no_window_flags()
    assert kwargs["capture_output"] and kwargs["text"]
    assert 0 < kwargs["timeout"] <= 10
    assert kwargs["input"].startswith("test-master\n")
    assert "test-master" not in argv and "test-value" not in argv


@pytest.mark.parametrize("windows", [False, True])
def test_password_dialog_keeps_gui_but_not_console(monkeypatch, windows):
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: windows)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="test-password\r\n", stderr="")

    monkeypatch.setattr(prompt.subprocess, "run", run)
    assert prompt.prompt_password("Test password", "Test dialog") == "test-password"
    argv, kwargs = calls[0]
    assert argv[:3] == ["powershell.exe", "-NoProfile", "-Command"]
    assert '$form.ShowDialog()' in argv[3]
    assert "Test dialog" in argv[3]
    assert kwargs.get("creationflags", 0) == agent_procutil.no_window_flags()
    assert kwargs["capture_output"] and kwargs["timeout"] == prompt.PROMPT_TIMEOUT


@pytest.mark.parametrize("windows", [False, True])
def test_gcm_and_git_fallback_remain_noninteractive(monkeypatch, windows):
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: windows)
    monkeypatch.setattr(gcm, "_get_gcm_path", lambda: "git-credential-manager")
    monkeypatch.setattr(gcm.shutil, "which", lambda _name: "git")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        output = "" if len(calls) == 1 else "username=test-user\npassword=test-token\n"
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    monkeypatch.setattr(gcm.subprocess, "run", run)
    result = gcm.git_credential_fill("https", host="example.test", allow_prompt=False)
    assert result is not None and result["username"] == "test-user"
    assert [argv[0] for argv, _kwargs in calls] == ["git-credential-manager", "git"]
    for _argv, kwargs in calls:
        assert kwargs.get("creationflags", 0) == agent_procutil.no_window_flags()
        assert kwargs["env"]["GCM_INTERACTIVE"] == "never"
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["capture_output"] and kwargs["text"]


@pytest.mark.skipif(sys.platform != "win32", reason="native console-state qualification")
def test_keepass_helper_from_windowless_parent(tmp_path):
    parent = Path(sys.executable).with_name("pythonw.exe")
    if not parent.is_file():
        pytest.skip("GUI-subsystem Python is not available")
    result_path = tmp_path / "console-results.json"
    script = """
import json, pathlib, sys
from agent_vault.keepassxc import KeePassXCBackend
backend = KeePassXCBackend()
backend._cli_path = str(pathlib.Path(sys.executable).with_name('python.exe'))
backend.set_password('test.kdbx', 'test-master')
results = []
for _ in range(2):
    result = backend._run('test.kdbx', [
        '-c', 'import ctypes; print(int(bool(ctypes.windll.kernel32.GetConsoleWindow())))'
    ])
    results.append([result.returncode, result.stdout.strip()])
pathlib.Path(sys.argv[1]).write_text(json.dumps(results))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(Path(p).resolve()) for p in sys.path if p)
    result = subprocess.run(
        [str(parent), "-c", script, str(result_path)],
        capture_output=True, timeout=30, env=env,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0
    assert json.loads(result_path.read_text()) == [[0, "0"], [0, "0"]]
