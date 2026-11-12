"""Session overlay controls and a real Linux exec identity boundary."""

from __future__ import annotations

import copy
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from agent_procutil import no_window_kwargs
from agent_mcp import owner_exec
from agent_mcp.__main__ import main


def _entry():
    return {
        "command": sys.executable, "args": ["-I", "-m", "agent_mcp", "forward", "--config", "x"],
        "env": {"FIXTURE_CONTEXT": "literal"}, "cwd": "original-cwd", "tools": ["*"],
    }


def test_overlay_preserves_selected_entry_and_never_activates_unselected_servers(monkeypatch):
    config = {"mcpServers": {"chosen": _entry(), "other": _entry()}}
    before = copy.deepcopy(config)
    query = Mock(side_effect=AssertionError("parent-time query is not physical MCP start"))
    monkeypatch.setattr(subprocess, "run", query)
    result = owner_exec.overlay(
        config, ["chosen"], pid=42, birth="native:1", environment={"BASE": "unchanged"},
    )
    assert set(result["mcpServers"]) == {"chosen"}
    entry = result["mcpServers"]["chosen"]
    assert entry["command"] == str(Path(sys._base_executable).absolute())
    assert entry["args"][:3] == ["-I", "-S", "-c"]
    assert {key: value for key, value in entry.items() if key not in {"command", "args"}} == {
        key: value for key, value in before["mcpServers"]["chosen"].items()
        if key not in {"command", "args"}
    }
    assert config == before
    request = json.loads(entry["args"][-1])
    assert request["query"] == [
        sys.executable, "-I", "-m", "agent_mcp", "session-owner-capabilities",
    ]
    assert request["original"] == [sys.executable, *_entry()["args"]]
    assert request["arguments"] == ["forward", "--config", "x"]
    assert request["pid"] == 42 and request["birth"] == "native:1"
    query.assert_not_called()


@pytest.mark.parametrize("change", [
    {"command": "agent-mcp"},
    {"args": ["bridge", "echo"]},
    {"args": ["-I", "-m", "agent_mcp", "forward", "echo", "--session-owner-pid=99"]},
    {"args": ["-I", "-m", "agent_mcp", "bridge", "--", "echo"]},
    {"env": {"KEY": "${UNEXPANDED}"}},
    {"command": str(Path(sys.executable).parent / "${SLOT}" / "agent-mcp")},
    {"args": ["-I", "-m", "agent_mcp", "bridge", "--config", "${CONFIG_PATH}"]},
    {"type": "http"},
])
def test_unknown_or_already_owned_entry_is_not_probed_or_overridden(monkeypatch, change):
    entry = {**_entry(), **change}
    query = Mock(side_effect=AssertionError("not eligible"))
    monkeypatch.setattr(subprocess, "run", query)
    assert owner_exec.overlay(
        {"mcpServers": {"chosen": entry}}, ["chosen"],
        pid=42, birth="native:1", environment={},
    ) == {"mcpServers": {}}
    query.assert_not_called()


@pytest.mark.parametrize("names", [[], ["missing"], ["chosen", "chosen"]])
def test_selection_refusal_precedes_provider_execution(monkeypatch, names):
    query = Mock(side_effect=AssertionError("not admitted"))
    monkeypatch.setattr(subprocess, "run", query)
    with pytest.raises(ValueError):
        owner_exec.overlay(
            {"mcpServers": {"chosen": _entry()}}, names, pid=42, birth="native:1", environment={},
        )
    query.assert_not_called()


@pytest.mark.parametrize("extra", [
    ["--", "literal"], ["--additional-mcp-config", "{}"], ["--additional-mcp-config={}"],
])
def test_unqualified_client_arguments_refuse_without_provider_execution(monkeypatch, extra):
    resolve = Mock(side_effect=AssertionError("not admitted"))
    monkeypatch.setattr(owner_exec.shutil, "which", resolve)
    with pytest.raises(ValueError):
        owner_exec._client(["copilot", *extra], {})
    resolve.assert_not_called()


def test_forwarding_script_is_not_claimed_as_the_native_client(tmp_path, monkeypatch):
    script = tmp_path / "copilot"
    script.write_bytes(b"#!/bin/sh\nexec other-client\n")
    script.chmod(0o700)
    monkeypatch.setattr(owner_exec.shutil, "which", lambda *args, **kwargs: str(script))
    with pytest.raises(ValueError, match="script or forwarding"):
        owner_exec._client([str(script)], {})


def test_unqualified_platform_refuses_without_client_or_provider_effects(monkeypatch, tmp_path):
    config = tmp_path / "entries.json"
    config.write_text('{"mcpServers":{}}', encoding="utf-8")
    monkeypatch.setattr(owner_exec.sys, "platform", "win32")
    query, execute = Mock(), Mock()
    monkeypatch.setattr(subprocess, "run", query)
    monkeypatch.setattr(owner_exec.os, "execve", execute)
    assert main([
        "exec-client", "--mcp-config", str(config), "--server", "chosen", "--", "copilot",
    ]) == 2
    query.assert_not_called()
    execute.assert_not_called()


def test_legacy_exec_keeps_argv_environment_and_does_not_create_an_overlay(monkeypatch):
    class Executed(BaseException):
        pass

    native = [sys.executable, "literal", '"quoted"']
    environment = os.environ.copy()
    monkeypatch.setattr(owner_exec.sys, "platform", "linux")
    monkeypatch.setattr(owner_exec, "_client", lambda *args: list(native))
    monkeypatch.setattr(owner_exec, "overlay", lambda *args, **kwargs: {"mcpServers": {}})
    create = Mock(side_effect=AssertionError("no qualified entries"))
    monkeypatch.delattr(owner_exec.os, "memfd_create", raising=False)
    monkeypatch.setattr(owner_exec, "_sealed_overlay", create)
    execute = Mock(side_effect=Executed)
    monkeypatch.setattr(owner_exec.os, "execve", execute)
    with pytest.raises(Executed):
        owner_exec.execute({}, ["old"], native)
    execute.assert_called_once_with(native[0], native, environment)
    create.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "kernel"])
def test_missing_python_binding_retains_native_creation_refusal(monkeypatch, failure):
    monkeypatch.delattr(owner_exec.os, "memfd_create", raising=False)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    library = SimpleNamespace()
    if failure == "kernel":
        library.memfd_create = Mock(return_value=-1)
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: library)
    monkeypatch.setattr(ctypes, "get_errno", lambda: errno.ENOSYS)
    if failure == "missing":
        with pytest.raises(ValueError, match="anonymous-file creation is unavailable"):
            owner_exec._sealed_overlay({})
    else:
        with pytest.raises(OSError) as raised:
            owner_exec._sealed_overlay({})
        assert raised.value.errno == errno.ENOSYS


@pytest.mark.parametrize("failure", ["error", "unconfirmed"])
def test_failed_sealing_closes_the_unpublished_descriptor(monkeypatch, tmp_path, failure):
    descriptor = os.open(tmp_path / "overlay", os.O_RDWR | os.O_CREAT, 0o600)
    monkeypatch.setattr(owner_exec.os, "memfd_create", lambda *args: descriptor, raising=False)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
        fcntl=Mock(side_effect=OSError(errno.EPERM, "sealing refused"))
        if failure == "error" else Mock(return_value=0),
    ))
    with pytest.raises(OSError if failure == "error" else ValueError):
        owner_exec._sealed_overlay({})
    with pytest.raises(OSError) as closed:
        os.fstat(descriptor)
    assert closed.value.errno == errno.EBADF


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux exec and sealed memfd")
@pytest.mark.parametrize("bindings", ["default", "omitted"])
def test_real_exec_retains_owner_birth_and_sealed_private_overlay(tmp_path, bindings):
    from agent_procutil._daemon_instance_identity import birth_identity

    native = tmp_path / "copilot"
    native.symlink_to(shutil.which("python3") or sys.executable)
    fixture = tmp_path / "client.py"
    fixture.write_text(
        "import errno,fcntl,json,os,sys\n"
        "path=sys.argv[-1][1:]\n"
        "with open(path) as stream: config=json.load(stream)\n"
        "entry=config['mcpServers']['chosen']\n"
        "assert entry['env']=={'FIXTURE_CONTEXT':'literal'}\n"
        "request=json.loads(entry['args'][-1])\n"
        "assert request['pid']==os.getpid()\n"
        "fd=int(path.rsplit('/',1)[1])\n"
        "assert fcntl.fcntl(fd,getattr(fcntl,'F_GET_SEALS',1034))&15==15\n"
        "try: os.write(fd,b'changed')\n"
        "except OSError as error: assert error.errno==errno.EPERM\n"
        "else: raise AssertionError('overlay remained writable')\n"
        "print(json.dumps({'pid':os.getpid(),'birth':request['birth'],'cwd':os.getcwd(),"
        "'literal':sys.argv[1],'environment':os.environ['OWNER_EXEC_FIXTURE']}),flush=True)\n",
        encoding="utf-8",
    )
    root = Path(owner_exec.__file__).resolve().parents[1]
    script = (
        "import fcntl,json,os,sys\n"
        + (
            "for module in (os,fcntl):\n"
            " for name in ('memfd_create','MFD_ALLOW_SEALING','MFD_CLOEXEC',"
            "'F_ADD_SEALS','F_GET_SEALS','F_SEAL_SEAL','F_SEAL_SHRINK','F_SEAL_GROW','F_SEAL_WRITE'):\n"
            "  if hasattr(module,name): delattr(module,name)\n"
            if bindings == "omitted" else ""
        )
        + f"sys.path.insert(0,{str(root)!r})\n"
        "from agent_mcp import owner_exec\n"
        f"entry={_entry()!r}\n"
        "owner_exec.execute({'mcpServers':{'chosen':entry}},['chosen'],sys.argv[1:])\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", script, str(native), str(fixture), 'literal "value"'],
        cwd=tmp_path, env={**os.environ, "OWNER_EXEC_FIXTURE": "preserved"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    try:
        expected_birth = birth_identity(process.pid)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert json.loads(stdout) == {
            "pid": process.pid, "birth": expected_birth, "cwd": str(tmp_path),
            "literal": 'literal "value"', "environment": "preserved",
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux MCP-start bootstrap")
@pytest.mark.parametrize("provider_state", ["rollback", "unprovisioned"])
def test_actual_mcp_start_uses_current_provider_and_probe_never_provisions(tmp_path, provider_state):
    from agent_procutil._daemon_instance_identity import birth_identity

    package = tmp_path / "agent_mcp"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "probe-provisioned"
    entry = {
        "command": sys.executable, "args": ["-m", "agent_mcp", "bridge", 'literal "target"'],
        "env": {"PYTHONPATH": str(tmp_path), "FIXTURE_CONTEXT": "preserved"},
    }
    selected = owner_exec.overlay(
        {"mcpServers": {"chosen": entry}}, ["chosen"], pid=os.getpid(),
        birth=birth_identity(os.getpid()), environment={},
    )["mcpServers"]["chosen"]
    # The selected provider changes only after the client overlay was prepared.
    (package / "__main__.py").write_text(
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:]==['session-owner-capabilities']:\n"
        " if os.environ.get('AGENT_MCP_NO_SELFPROVISION')!='1':\n"
        f"  Path({str(marker)!r}).write_text('unexpected provision')\n"
        f" raise SystemExit({1 if provider_state == 'unprovisioned' else 2})\n"
        "print(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd(),\n"
        " 'context':os.environ['FIXTURE_CONTEXT'],\n"
        " 'probe_only':os.environ.get('AGENT_MCP_NO_SELFPROVISION')}))\n",
        encoding="utf-8",
    )
    environment = {**os.environ, **selected["env"]}
    environment.pop("AGENT_MCP_NO_SELFPROVISION", None)
    completed = subprocess.run(
        [selected["command"], *selected["args"]], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "args": ["bridge", 'literal "target"'], "cwd": str(tmp_path),
        "context": "preserved", "probe_only": None,
    }
    assert not marker.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux pidfd ownership")
@pytest.mark.parametrize("during_query", [False, True], ids=["already-exited", "query-race"])
def test_bootstrap_origin_exit_never_starts_the_ordinary_provider(tmp_path, during_query):
    from agent_procutil._daemon_instance_identity import birth_identity

    package = tmp_path / "agent_mcp"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    queried, release, started = (tmp_path / name for name in ("queried", "release", "started"))
    (package / "__main__.py").write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "if sys.argv[1:]==['session-owner-capabilities']:\n"
        f" Path({str(queried)!r}).write_text('query')\n"
        " deadline=time.monotonic()+5\n"
        f" while not Path({str(release)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        " raise SystemExit(2)\n"
        f"Path({str(started)!r}).write_text('ordinary startup')\n",
        encoding="utf-8",
    )
    origin = subprocess.Popen(
        [sys.executable, "-c", "import sys;print('ready',flush=True);sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    bootstrap = None
    try:
        assert origin.stdout.readline().strip() == b"ready"
        birth = birth_identity(origin.pid)
        entry = {
            "command": sys.executable, "args": ["-m", "agent_mcp", "bridge", "fixture"],
            "env": {"PYTHONPATH": str(tmp_path)},
        }
        selected = owner_exec.overlay(
            {"mcpServers": {"chosen": entry}}, ["chosen"],
            pid=origin.pid, birth=birth, environment={},
        )["mcpServers"]["chosen"]
        if not during_query:
            origin.terminate()
            origin.wait(timeout=5)
        bootstrap = subprocess.Popen(
            [selected["command"], *selected["args"]], cwd=tmp_path,
            env={**os.environ, **selected["env"]},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
        )
        if during_query:
            for _ in range(200):
                if queried.exists():
                    break
                time.sleep(.01)
            assert queried.exists(), "origin was never pinned before the query"
            origin.terminate()
            origin.wait(timeout=5)
            release.write_text("continue", encoding="utf-8")
        _stdout, stderr = bootstrap.communicate(timeout=10)
        assert bootstrap.returncode != 0
        assert b"owner bootstrap refused" in stderr
        assert queried.exists() is during_query
        assert not started.exists()
    finally:
        release.write_text("continue", encoding="utf-8")
        for process in (bootstrap, origin):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
