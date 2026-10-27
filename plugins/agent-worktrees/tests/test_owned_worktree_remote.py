"""Exact execution-entry routing and bounded actionable failure diagnostics."""

from __future__ import annotations

import json
import base64
from types import SimpleNamespace

import pytest
from machine_transport import MachineEntry, SSHEnvironment, resolve_machine_selection

from agent_worktrees import owned_worktree as allocation, owned_worktree_remote as remote


@pytest.fixture
def transport(monkeypatch, tmp_path):
    entry = MachineEntry(
        key="native-custom", display_name="Native execution", physical_host="desktop",
        execution_platform="windows", ssh_ready=True,
        ssh_environments=[SSHEnvironment(name="windows", alias="native-ssh", shell="powershell")],
    )
    entries = {entry.key: entry}
    selected = resolve_machine_selection(entries, entry.key)
    assert selected.entry is entry and selected.target.logical_machine != entry.key
    config = SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))
    monkeypatch.setattr(remote.cfg, "load_project_config", lambda project: config)
    monkeypatch.setattr(remote.cfg, "load_machines_yaml", lambda anchor, **kwargs: entries)
    metadata = {
        "version": allocation.METADATA_VERSION, "lifetime": allocation.LIFETIME,
        "namespace": allocation.NAMESPACE, "creator": "origin/project/parent",
        "request_id": "original", "token": "a" * 32, "phase": "pending",
        "recovery": {"machine": entry.key, "project": "destination"},
    }
    return metadata


@pytest.mark.parametrize("operation", ["inspect", "confirm", "cancel", "create"])
def test_real_transport_resolver_uses_exact_custom_execution_key(transport, monkeypatch, operation):
    def run(argv, **kwargs):
        assert argv[-2] == "native-ssh"
        assert argv[-1].startswith("powershell -NoProfile -EncodedCommand ")
        command = base64.b64decode(argv[-1].rsplit(" ", 1)[1]).decode("utf-16-le")
        assert "worktree-allocation-receive" in command and operation in command
        assert json.loads(kwargs["input_text"])["target"]["machine"] == "native-custom"
        return SimpleNamespace(returncode=0, stdout='{"status":"pending"}', stderr="")

    monkeypatch.setattr(remote, "run_caller_owned", run)
    assert remote.request(operation, transport) == {"status": "pending"}


@pytest.mark.parametrize("stdout,stderr,expected", [
    ('{"error":"rejected","message":"unsupported receiving schema/version"}', "", "unsupported receiving schema/version"),
    ('{"message":"original request conflicts with target facts"}', "irrelevant", "original request conflicts"),
    ("", "worktree-allocation-receive: command not found", "command not found"),
    ('{"message":null}', "permission denied", "permission denied"),
    ('{"message":"first","message":"untrusted"}', "invalid receiver output", "invalid receiver output"),
])
def test_nonzero_receiver_retains_structured_failure_or_stderr(transport, monkeypatch, stdout, stderr, expected):
    monkeypatch.setattr(remote, "run_caller_owned", lambda *a, **k: SimpleNamespace(
        returncode=127, stdout=stdout, stderr=stderr,
    ))
    with pytest.raises(RuntimeError, match=expected) as failure:
        remote.request("inspect", transport)
    assert "exit 127" in str(failure.value) and "responsibility retained" in str(failure.value)


@pytest.mark.parametrize("structured", [False, True])
def test_failure_redacts_before_truncating_and_removes_control_characters(transport, monkeypatch, structured):
    credential = "sensitive-value"
    message = (
        "conflict " + transport["token"] + " password=" + credential
        + " https://user:other-value@example.invalid/path"
        + "\x1b[31m\r\n\x00\u202e" + "x" * 1000
    )
    monkeypatch.setattr(remote, "run_caller_owned", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=json.dumps({"message": message}) if structured else "",
        stderr="" if structured else message,
    ))
    with pytest.raises(RuntimeError) as failure:
        remote.request("inspect", transport)
    text = str(failure.value)
    detail = text.split("responsibility retained: ", 1)[1]
    assert len(detail) == 512 and "[REDACTED]" in detail
    assert transport["token"] not in text and credential not in text and "other-value" not in text
    assert not any(char in text for char in "\x1b\r\n\x00\u202e")


def test_failure_redacts_credential_that_crosses_output_cut(transport):
    prefix = "x" * 500
    detail = remote._failure_detail("", prefix + " password=" + "sensitive" * 100, transport["token"])
    assert "sensitive" not in detail and len(detail) <= 512
