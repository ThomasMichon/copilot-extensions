"""Windows update PATH uses the physical prerequisite, not its private venv."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="explicit Windows bootstrap PATH qualification",
)
SOURCE = Path(__file__).resolve().parents[1] / "src"


def _literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("host", ["powershell", "pwsh"])
def test_update_python_path_prefers_the_physical_base_and_preserves_other_entries(tmp_path, host):
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    unrelated = str(tmp_path / "other tools")
    preferred = str(Path(sys._base_executable).resolve().parent)
    inherited = f"{unrelated};;{preferred.upper()}\\;{preferred};{unrelated}\\secondary"
    helper = re.search(
        r"^function Add-BootstrapPythonPath\b.*?^}",
        (SOURCE.parent / "bootstrap.ps1").read_text(encoding="utf-8"),
        re.M | re.S,
    )
    assert helper is not None
    program = (
        "import json,os,sys;"
        f"sys.path.insert(0,{str(SOURCE)!r});"
        "from worktree_manager.update_python_path import finish_update;"
        "assert finish_update(['update','--wait'],0)==0;"
        "print(json.dumps({'python':sys._base_executable,'path':os.environ['PATH']}))"
    )
    script = (
        "$ErrorActionPreference='Stop';\n"
        + helper[0] + "\n"
        f"if ((Add-BootstrapPythonPath '' {_literal(preferred)}) -cne {_literal(preferred)}) "
        "{ throw 'An initially empty PATH gained an empty component' };\n"
        f"$env:PATH=Add-BootstrapPythonPath {_literal(inherited)} {_literal(preferred)};\n"
        f"$details=(& {_literal(sys.executable)} -I -S -B -c {_literal(program)}) | ConvertFrom-Json;\n"
        "if ($LASTEXITCODE -ne 0) { throw 'Python completion failed' };\n"
        "$python=$details.python;$env:PATH=$details.path;\n"
        "@{python=$python;path=$env:PATH;selected=(Get-Command python -CommandType Application | Select-Object -First 1).Source;"
        "}"
        " | ConvertTo-Json -Compress\n"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=tmp_path, env={**os.environ, "PATH": "", "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"},
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    assert Path(details["python"]).samefile(sys._base_executable)
    assert Path(details["selected"]).samefile(sys._base_executable)
    assert details["path"].split(";") == [
        preferred, unrelated, "", preferred.upper() + "\\", unrelated + "\\secondary",
    ]


def test_concurrent_publication_serializes_the_registry_read_modify_verify(tmp_path):
    original = str(tmp_path / "existing tools")
    (tmp_path / "registry.json").write_text(json.dumps([original, 2]), encoding="utf-8")
    program = r"""
import json, sys, time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

root, source, role = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, source)
from worktree_manager import config_write_lock as locks
from worktree_manager import update_python_path as publication
Path.home = classmethod(lambda cls: root)
registry = root / "registry.json"
attempt = locks._try_lock_file_once
def acquire(stream):
    acquired = attempt(stream)
    if not acquired:
        (root / "second-blocked").touch()
    return acquired
locks._try_lock_file_once = acquire
reads = 0
def read(*args):
    global reads
    reads += 1
    if role == "second":
        (root / "second-entered").touch()
    value = tuple(json.loads(registry.read_text(encoding="utf-8")))
    if role == "first" and reads == 1:
        (root / "first-read").touch()
        deadline = time.monotonic() + 10
        while not (root / "release-first").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("first publisher was not released")
            time.sleep(0.01)
    return value
def write(*args):
    registry.write_text(json.dumps([args[-1], args[-2]]), encoding="utf-8")
sys.modules["winreg"] = SimpleNamespace(
    HKEY_CURRENT_USER=1, KEY_READ=1, KEY_WRITE=2, REG_SZ=1, REG_EXPAND_SZ=2,
    CreateKeyEx=lambda *args: nullcontext(object()),
    QueryValueEx=read, SetValueEx=write,
)
publication._notify_environment_change = lambda: None
assert publication._publish_user_path(str(root / role))
"""

    def wait_for(*names):
        deadline = time.monotonic() + 10
        while not any((tmp_path / name).exists() for name in names):
            if time.monotonic() >= deadline:
                pytest.fail(f"Publisher did not reach {names}")
            time.sleep(0.01)

    processes = []
    try:
        for role in ("first", "second"):
            processes.append(subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", program, str(tmp_path), str(SOURCE), role],
                cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ))
            if role == "first":
                wait_for("first-read")
        wait_for("second-blocked", "second-entered")
        assert not (tmp_path / "second-entered").exists()
        (tmp_path / "release-first").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stdout + stderr
    finally:
        (tmp_path / "release-first").touch()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
    assert (tmp_path / "second-entered").exists()
    assert json.loads((tmp_path / "registry.json").read_text(encoding="utf-8")) == [
        ";".join([str(tmp_path / "second"), str(tmp_path / "first"), original]), 2,
    ]
