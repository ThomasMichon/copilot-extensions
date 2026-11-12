"""Session overlay controls and a real Linux exec identity boundary."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest

from agent_procutil import no_window_kwargs
from agent_mcp import owner_exec
from agent_mcp.__main__ import main
from agent_mcp.owner_capabilities import CONTRACT


def _entry():
    return {
        "command": sys.executable, "args": ["-I", "-m", "agent_mcp", "forward", "--config", "x"],
        "env": {"FIXTURE_CONTEXT": "literal"}, "cwd": "original-cwd", "tools": ["*"],
    }


def _reply(**changes):
    return subprocess.CompletedProcess(
        [], 0, json.dumps({
            "contract": CONTRACT, "supported": True, "invocation_allowed": True, **changes,
        }), "",
    )


def test_overlay_preserves_selected_entry_and_never_activates_unselected_servers(monkeypatch):
    config = {"mcpServers": {"chosen": _entry(), "other": _entry()}}
    before = copy.deepcopy(config)
    query = Mock(return_value=_reply())
    monkeypatch.setattr(owner_exec.subprocess, "run", query)
    result = owner_exec.overlay(
        config, ["chosen"], pid=42, birth="native:1", environment={"BASE": "unchanged"},
    )
    expected = copy.deepcopy(config["mcpServers"]["chosen"])
    expected["args"].extend(("--session-owner-pid", "42", "--session-owner-birth", "native:1"))
    assert result == {"mcpServers": {"chosen": expected}}
    assert config == before
    assert query.call_args.args[0] == [
        sys.executable, "-I", "-m", "agent_mcp", "session-owner-capabilities",
    ]
    assert query.call_args.kwargs["env"] == {"BASE": "unchanged", "FIXTURE_CONTEXT": "literal"}
    assert query.call_args.kwargs["cwd"] == "original-cwd"


@pytest.mark.parametrize("reply", [
    _reply(supported=False), _reply(invocation_allowed=False),
    _reply(contract={**CONTRACT, "minimum": True}),
    _reply(contract={**CONTRACT, "maximum": 0}),
    _reply(contract={**CONTRACT, "capabilities": "explicit-native-owner-pair"}),
    subprocess.CompletedProcess([], 2, "not-json", ""),
    subprocess.TimeoutExpired("provider", 5),
])
def test_unsupported_provider_keeps_existing_server_selection(monkeypatch, reply):
    query = Mock(**({"side_effect": reply} if isinstance(reply, Exception) else {"return_value": reply}))
    monkeypatch.setattr(owner_exec.subprocess, "run", query)
    assert owner_exec.overlay(
        {"mcpServers": {"chosen": _entry()}}, ["chosen"],
        pid=42, birth="native:1", environment={},
    ) == {"mcpServers": {}}


@pytest.mark.parametrize("change", [
    {"command": "agent-mcp"},
    {"args": ["bridge", "echo"]},
    {"args": ["-I", "-m", "agent_mcp", "forward", "echo", "--session-owner-pid=99"]},
    {"args": ["-I", "-m", "agent_mcp", "bridge", "--", "echo"]},
    {"env": {"KEY": "${UNEXPANDED}"}},
    {"type": "http"},
])
def test_unknown_or_already_owned_entry_is_not_probed_or_overridden(monkeypatch, change):
    entry = {**_entry(), **change}
    query = Mock(side_effect=AssertionError("not eligible"))
    monkeypatch.setattr(owner_exec.subprocess, "run", query)
    assert owner_exec.overlay(
        {"mcpServers": {"chosen": entry}}, ["chosen"],
        pid=42, birth="native:1", environment={},
    ) == {"mcpServers": {}}
    query.assert_not_called()


@pytest.mark.parametrize("names", [[], ["missing"], ["chosen", "chosen"]])
def test_selection_refusal_precedes_provider_execution(monkeypatch, names):
    query = Mock(side_effect=AssertionError("not admitted"))
    monkeypatch.setattr(owner_exec.subprocess, "run", query)
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
    monkeypatch.setattr(owner_exec.subprocess, "run", query)
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
    monkeypatch.setattr(owner_exec.os, "memfd_create", create, raising=False)
    execute = Mock(side_effect=Executed)
    monkeypatch.setattr(owner_exec.os, "execve", execute)
    with pytest.raises(Executed):
        owner_exec.execute({}, ["old"], native)
    execute.assert_called_once_with(native[0], native, environment)
    create.assert_not_called()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux exec and sealed memfd")
def test_real_exec_retains_owner_birth_and_sealed_private_overlay(tmp_path):
    from agent_procutil._daemon_instance_identity import birth_identity

    native = tmp_path / "copilot"
    native.symlink_to(shutil.which("python3") or sys.executable)
    fixture = tmp_path / "client.py"
    fixture.write_text(
        "import fcntl,json,os,sys\n"
        "path=sys.argv[-1][1:]\n"
        "with open(path) as stream: config=json.load(stream)\n"
        "entry=config['mcpServers']['chosen']\n"
        "assert entry['env']=={'FIXTURE_CONTEXT':'literal'}\n"
        "args=entry['args']\n"
        "assert int(args[-3])==os.getpid()\n"
        "fd=int(path.rsplit('/',1)[1])\n"
        "assert fcntl.fcntl(fd,fcntl.F_GET_SEALS)&fcntl.F_SEAL_WRITE\n"
        "print(json.dumps({'pid':os.getpid(),'birth':args[-1],'cwd':os.getcwd(),"
        "'literal':sys.argv[1],'environment':os.environ['OWNER_EXEC_FIXTURE']}),flush=True)\n",
        encoding="utf-8",
    )
    root = Path(owner_exec.__file__).resolve().parents[1]
    script = (
        "import json,sys\n"
        f"sys.path.insert(0,{str(root)!r})\n"
        "from agent_mcp import owner_exec\n"
        f"entry={_entry()!r}\n"
        "owner_exec._supports=lambda *args:True\n"
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
