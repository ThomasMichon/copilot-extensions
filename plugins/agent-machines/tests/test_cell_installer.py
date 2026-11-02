"""Ordinary release identity and bounded cell installer ownership controls."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
VERSION = "1.2.3"


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("cell_installer_test_subject", SCRIPTS / "cell_installer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / "payload"
    root.mkdir()
    (root / "plugin.json").write_text(json.dumps({"name": "agent-machines", "version": VERSION}))
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n')
    (root / "source.py").write_text("value = 1\n")
    return root


def test_release_hash_binds_copied_content_and_membership(helper, payload, tmp_path):
    expected = helper.payload_identity(payload, VERSION)
    copied = tmp_path / "snapshot"
    shutil.copytree(payload, copied)
    (copied / helper.OWNER_MARKER).write_text("separately validated publication owner")
    (copied / "snapshot-provenance.json").write_text("{}")
    assert helper.payload_identity(copied, VERSION) == expected
    (copied / "source.py").write_text("value = 2\n")
    assert helper.payload_identity(copied, VERSION) != expected
    (copied / "source.py").write_text("value = 1\n")
    (copied / "added.py").write_text("new = True\n")
    assert helper.payload_identity(copied, VERSION) != expected


@pytest.mark.parametrize("filename", ("plugin.json", "pyproject.toml"))
def test_release_hash_rejects_replaced_version_without_source_metadata(helper, payload, filename):
    path = payload / filename
    path.write_text(path.read_text().replace(VERSION, "9.9.9"))
    assert not (payload / "runtime-source.json").exists()
    with pytest.raises(RuntimeError, match="package version changed"):
        helper.payload_identity(payload, VERSION)


def test_release_hash_excludes_only_root_publication_owner(helper, payload):
    expected = helper.payload_identity(payload, VERSION)
    nested = payload / "nested"
    nested.mkdir()
    (nested / helper.OWNER_MARKER).write_text("runtime input, not a root sidecar")
    assert helper.payload_identity(payload, VERSION) != expected


def test_release_hash_also_binds_source_identity_metadata(helper, payload):
    expected = helper.payload_identity(payload, VERSION)
    metadata = payload / "runtime-source.json"
    metadata.write_text('{"source":"first"}')
    first = helper.payload_identity(payload, VERSION)
    assert first != expected
    metadata.write_text('{"source":"second"}')
    assert helper.payload_identity(payload, VERSION) != first


@pytest.mark.parametrize("relative", (
    "__pycache__/cached.pyc", ".venv/cached.py", ".git/config", "build/generated.py",
    "dist/generated.whl", "project.egg-info/metadata", "nested/snapshot-provenance.json",
))
def test_release_hash_binds_generated_and_cached_bytes(helper, payload, relative):
    expected = helper.payload_identity(payload, VERSION)
    path = payload / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"first")
    first = helper.payload_identity(payload, VERSION)
    assert first != expected
    path.write_bytes(b"second")
    assert helper.payload_identity(payload, VERSION) != first
    path.rename(path.with_name("renamed"))
    assert helper.payload_identity(payload, VERSION) != first


@pytest.mark.parametrize("directory", (False, True))
def test_release_hash_rejects_linked_payload_inputs(helper, payload, directory, monkeypatch):
    link = payload / "linked"
    if directory:
        link.mkdir()
    else:
        link.write_text("linked bytes")
    original = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path: (
        SimpleNamespace(st_mode=0o120777) if path == link else original(path)
    ))
    with pytest.raises(RuntimeError, match="ordinary files and directories"):
        helper.payload_identity(payload, VERSION)


def test_release_hash_propagates_incomplete_inventory(helper, payload, monkeypatch):
    def incomplete(*_args, onerror, **_kwargs):
        onerror(PermissionError("controlled incomplete walk"))
        yield

    monkeypatch.setattr(helper.os, "walk", incomplete)
    with pytest.raises(PermissionError, match="controlled incomplete walk"):
        helper.payload_identity(payload, VERSION)


@pytest.fixture
def owned_runner(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=7, stdout="result\n", stderr="diagnostic\n")

    package = ModuleType("agent_procutil")
    module = ModuleType("agent_procutil.owned_process")
    module.run_caller_owned = run
    monkeypatch.setitem(sys.modules, "agent_procutil", package)
    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", module)
    return calls, module


def _request(payload):
    return {"argv": ["host", "-File", "init.ps1"], "cwd": str(payload.parent),
            "payload": str(payload), "timeout": 480}


def test_watchdog_uses_whole_tree_owner_and_preserves_output(helper, payload, owned_runner, capsys, monkeypatch):
    monkeypatch.delenv(helper.HASH_ENV, raising=False)
    calls, _ = owned_runner
    assert helper.watchdog(_request(payload)) == 7
    argv, options = calls[0]
    assert argv == _request(payload)["argv"]
    assert 0 < options["timeout"] <= 480
    assert options["cwd"] == str(payload.parent)
    assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 16 * 1024 * 1024
    assert options["env"][helper.HASH_ENV] == helper.payload_identity(payload, VERSION)
    assert options["env"]["COPILOT_EXTENSIONS_HUMAN_UTF8"] == "1"
    output = capsys.readouterr()
    assert output.out == "result\n"
    assert output.err == "diagnostic\n"


def test_watchdog_refuses_an_inherited_changed_payload(helper, payload, owned_runner, monkeypatch):
    monkeypatch.setenv(helper.HASH_ENV, "a" * 64)
    with pytest.raises(RuntimeError, match="Payload changed"):
        helper.watchdog(_request(payload))
    assert owned_runner[0] == []


def test_watchdog_timeout_is_failure_after_owned_retirement(helper, payload, owned_runner, capsys, monkeypatch):
    monkeypatch.delenv(helper.HASH_ENV, raising=False)

    def expired(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    owned_runner[1].run_caller_owned = expired
    assert helper.watchdog(_request(payload)) == 124
    assert "WATCHDOG" in capsys.readouterr().err


@pytest.mark.parametrize("timeout", (0, -1, float("nan"), float("inf"), True))
def test_watchdog_refuses_unbounded_deadlines(helper, payload, timeout, owned_runner):
    request = {**_request(payload), "timeout": timeout}
    with pytest.raises(ValueError, match="finite and positive"):
        helper.watchdog(request)
    assert owned_runner[0] == []


def test_worker_binds_original_caller_before_payload_identity(helper, payload, monkeypatch, owned_runner):
    admitted = False
    events = []
    package = sys.modules["agent_procutil"]
    transport = ModuleType("agent_procutil._shell_caller_owned")

    def serve(callback, **lifetime):
        nonlocal admitted
        assert lifetime == {"owner_pid": 123, "owner_birth": "birth", "nonce": "nonce"}
        events.append("admitted")
        admitted = True
        return callback()

    real_identity = helper.payload_identity

    def identity(root, version):
        assert admitted, "payload I/O ran before original-caller custody"
        events.append("hashed")
        return real_identity(root, version)

    transport.serve = serve
    monkeypatch.setattr(package, "_shell_caller_owned", transport, raising=False)
    monkeypatch.setitem(sys.modules, transport.__name__, transport)
    monkeypatch.setattr(helper, "payload_identity", identity)
    assert helper.main({
        **_request(payload),
        "lifetime": {"owner_pid": 123, "owner_birth": "birth", "nonce": "nonce"},
    }) == 7
    assert events == ["admitted", "hashed"]


def test_snapshot_control_launches_disable_bytecode_writes(monkeypatch):
    sh = (SCRIPTS / "init.sh").read_text(encoding="utf-8")
    ps = (SCRIPTS / "init.ps1").read_text(encoding="utf-8")
    for source in (sh, ps):
        dependency_commands = [
            line for line in source.splitlines() if "installer_dependency_policy.py" in line
        ]
        assert len(dependency_commands) == 6
        assert all(" -I -B " in line for line in dependency_commands)
    assert '"$py" -I -B "$vr"' in sh
    assert "@('-I', '-B', $vr," in ps
    assert '"$lifecycle_python" -I -B "$SCRIPT_DIR/cell_lifecycle.py"' in sh
    assert "$python.Source -I -B (Join-Path $PSScriptRoot 'cell_lifecycle.py')" in ps
    assert sh.count('"$VENV_PYTHON" -B -c \'import agent_machines\'') == 2
    assert ps.count("$VenvPython -B -c 'import agent_machines'") == 2
    spec = importlib.util.spec_from_file_location(
        "cell_snapshot_constructor_guards",
        SCRIPTS.parents[2] / "libs/versioned-runtime/versioned_runtime_construction.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    assert 'set -- "$base" -I -B "$helper"' in module.construction_shell_helpers("sh")
    assert "$argv = @('-I', '-B', $Helper," in module.construction_shell_helpers("ps1")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux exact-birth process-group qualification")
def test_watchdog_retires_actual_build_descendant(helper, payload, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(helper.HASH_ENV, raising=False)
    witness = tmp_path / "descendant.json"
    child = (
        "import pathlib,time,json,os;"
        f"p=pathlib.Path({str(witness)!r});"
        "s=pathlib.Path('/proc/self/stat').read_text().rsplit(')',1)[1].split();"
        "p.write_text(json.dumps({'pid':os.getpid(),'birth':s[19]}));time.sleep(30)"
    )
    command = [
        sys.executable, "-c",
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(30)",
    ]
    started = time.monotonic()
    assert helper.watchdog({**_request(payload), "argv": command, "timeout": 1}) == 124
    assert time.monotonic() - started < 10
    assert "WATCHDOG" in capsys.readouterr().err
    identity = json.loads(witness.read_text())
    try:
        fields = Path(f"/proc/{identity['pid']}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return
    assert fields[19] != identity["birth"] or fields[0] == "Z"
