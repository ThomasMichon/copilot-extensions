"""Tests for the restricted provider-exec SSH-compatible transport."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_containers import __main__ as containers_cli
from agent_containers import lease as lease_mod
from agent_containers import provider_launcher, provider_ssh
from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers.resolver import LiveExecTarget


def _target(*, profile: str = "restricted", user: str = "agent") -> LiveExecTarget:
    config = ContainersConfig()
    fleet = FleetConfig(
        security_profile=profile,
        exec_user=user,
        workspace_folder="/workspace/repo",
    )
    return LiveExecTarget(
        name="sandbox-1",
        container_id="instance-123",
        config=config,
        fleet=fleet,
        info=SimpleNamespace(name="sandbox-1"),
        actual_profile=profile,
        user=user,
        workspace_folder="/workspace/repo",
        acp_command="minimal-agent --stdio",
    )


def _venue(**overrides):
    venue = {
        "schema_version": 1,
        "provider": "agent-containers",
        "kind": "container",
        "target_id": "container:sandbox-1",
        "scope": "provider-instance",
        "instance_id": "instance-123",
        "fleet": "sandbox",
        "workspace_folder": "/workspace/repo",
        "security_profile": "restricted",
        "configured_security_profile": "restricted",
        "observed_security_profile": "restricted",
        "effective_security_profile": "restricted",
        "state": "running",
        "ready": True,
        "posture_verified": False,
        "transport": "provider-exec",
        "capabilities": {
            "container_local_workspace": True,
            "host_credentials": False,
            "credential_relay": False,
            "session_host": False,
            "ssh_profile": True,
        },
        "lifecycle_hold": {
            "state": "none",
            "operation": None,
            "reason": None,
        },
    }
    venue.update(overrides)
    return venue


def _lease():
    return SimpleNamespace(
        container="sandbox-1",
        effort="example-effort",
        acquired_at=1_700_000_000.0,
    )


@contextmanager
def _lease_guard(*leases):
    yield leases


def test_command_request_uses_fleet_user_and_no_projection():
    command = provider_ssh._command_for_request(
        _target(user="sandbox-agent"),
        provider_ssh.SessionRequest(
            command="printf '%s' hello",
            term=None,
            width=80,
            height=24,
        ),
    )

    assert command[:3] == ["docker", "exec", "-i"]
    assert command[3:6] == ["-u", "sandbox-agent", "instance-123"]
    assert "-e" not in command
    assert "--mount" not in command
    assert "--network" not in command
    assert "GH_TOKEN" not in " ".join(command)
    assert "cd /workspace/repo" in command[-1]
    assert "printf" in command[-1]


def test_pty_request_uses_target_side_helper_with_initial_dimensions():
    command = provider_ssh._command_for_request(
        _target(),
        provider_ssh.SessionRequest(
            command=None,
            term="xterm-256color",
            width=132,
            height=43,
        ),
        session_nonce="0123456789abcdef",
    )

    payload = command[-1]
    assert "script -qefc true" in payload
    assert "script -qefc" in payload
    assert "stty rows 43 cols 132" in payload
    assert "TERM=xterm-256color" in payload
    assert "exec bash -l" in payload
    assert "AGENT_CONTAINERS_SESSION_NONCE=0123456789abcdef" in payload
    assert "setsid --wait" in payload
    assert "-e" not in command


def test_pty_request_uses_safe_defaults_when_client_reports_zero_dimensions():
    command = provider_ssh._command_for_request(
        _target(),
        provider_ssh.SessionRequest(
            command="true",
            term="xterm",
            width=0,
            height=0,
        ),
        session_nonce="0123456789abcdef",
    )

    assert "stty rows 24 cols 80" in command[-1]


def test_cleanup_targets_only_the_nonce_marked_process_group(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(provider_ssh.subprocess, "run", run)

    provider_ssh._cleanup_target_session(_target(), "0123456789abcdef")

    command = calls[0][0]
    assert command[:6] == [
        "docker",
        "exec",
        "-i",
        "-u",
        "agent",
        "instance-123",
    ]
    payload = command[-1]
    assert "AGENT_CONTAINERS_SESSION_NONCE=0123456789abcdef" in payload
    assert "/proc/[0-9]*/environ" in payload
    assert "kill_marked TERM" in payload
    assert "kill_marked KILL" in payload
    assert '[ "$3" = "$pid" ]' in payload


def test_inactive_transport_triggers_target_cleanup(monkeypatch):
    class Process:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    class Channel:
        closed = False

        def recv(self, _size):
            return b""

        def get_transport(self):
            return SimpleNamespace(is_active=lambda: False)

        def send_exit_status(self, _status):
            return None

    process = Process()
    cleaned = []
    monkeypatch.setattr(provider_ssh.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(
        provider_ssh,
        "_cleanup_target_session",
        lambda target, nonce: cleaned.append((target.name, nonce)),
    )
    monkeypatch.setattr(provider_ssh, "_FORCED_EXIT_SECONDS", 0)
    monkeypatch.setattr(provider_ssh, "_CHANNEL_POLL_SECONDS", 0)

    rc = provider_ssh._run_channel(
        _target(),
        provider_ssh.SessionRequest(command="sleep 19", term=None, width=80, height=24),
        Channel(),
    )

    assert rc == -15
    assert len(cleaned) == 1
    assert cleaned[0][0] == "sandbox-1"
    assert len(cleaned[0][1]) == 32


def test_profile_spec_is_named_hardened_and_provider_owned(monkeypatch, tmp_path):
    module = tmp_path / "module.yaml"
    module.write_text("module: provider-exec\n", encoding="utf-8")
    monkeypatch.setattr(provider_ssh, "provider_module_path", lambda: module)
    monkeypatch.setattr(
        provider_ssh,
        "resolve_live_exec_target",
        lambda name: _target(),
    )

    async def resolve_spec(_self, _name):
        return {"venue": _venue()}

    monkeypatch.setattr(
        provider_ssh.ContainerResolver,
        "resolve_spec",
        resolve_spec,
    )

    result = provider_ssh.ssh_profile_spec("sandbox-1", "restricted-worker")

    assert result["module"] == str(module)
    assert result["venue"]["posture_verified"] is True
    machine = result["registry"]["machines"][0]
    assert machine["name"] == "restricted-worker"
    assert machine["hostname"] == "sandbox-1"
    assert machine["user"] == "agent"
    assert machine["options"]["ControlMaster"] == "no"
    assert machine["options"]["PubkeyAuthentication"] == "no"
    assert machine["options"]["PasswordAuthentication"] == "no"
    assert machine["options"]["UserKnownHostsFile"] in {"/dev/null", "NUL"}


def test_profile_spec_can_describe_project_scoped_picker_source(monkeypatch, tmp_path):
    module = tmp_path / "module.yaml"
    runtime_root = tmp_path / "runtime"
    state_root = tmp_path / "state"
    module.write_text("module: provider-exec\n", encoding="utf-8")
    monkeypatch.setattr(provider_ssh, "provider_module_path", lambda: module)
    monkeypatch.setattr(provider_ssh, "RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(provider_ssh, "STATE_DIR", state_root)
    monkeypatch.setattr(
        provider_ssh,
        "resolve_live_exec_target",
        lambda name: _target(),
    )

    async def resolve_spec(_self, _name):
        return {"venue": _venue()}

    monkeypatch.setattr(provider_ssh.ContainerResolver, "resolve_spec", resolve_spec)
    monkeypatch.setattr(provider_ssh, "get_lease", lambda _name: _lease())
    monkeypatch.setattr(
        provider_ssh.shutil,
        "which",
        lambda name: f"/bin/{name}",
    )

    result = provider_ssh.ssh_profile_spec(
        "sandbox-1",
        "restricted-worker",
        project="example-project",
        label="Restricted target",
    )

    source = result["worktree_source"]
    assert source["project"] == "example-project"
    assert source["target_id"] == "container:sandbox-1"
    assert source["instance_id"] == "instance-123"
    assert source["label"] == "Restricted target"
    assert source["alias"] == "restricted-worker"
    assert source["resolve"] == [
        str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
        "-I",
        str(runtime_root / "provider-launcher.py"),
        "ssh-profile",
        "sandbox-1",
        "--alias",
        "restricted-worker",
        "--project",
        "example-project",
        "--label",
        "Restricted target",
        "--json",
    ]
    assert source["connect"] == [
        str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
        "-I",
        str(runtime_root / "provider-launcher.py"),
        "ssh-stdio",
        "sandbox-1",
    ]
    assert (runtime_root / "provider-launcher.py").read_bytes() == (
        Path(provider_launcher.__file__).read_bytes()
    )
    assert source["venue"]["posture_verified"] is True
    assert source["venue"]["assignment"] == {
        "kind": "lease",
        "effort": "example-effort",
        "acquired_at": 1_700_000_000.0,
    }
    assert source["capabilities"]["messages"] is True
    assert source["capabilities"]["resume"] is False


def test_provider_launcher_executes_active_isolated_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    python = runtime / "versions" / "1.2.3" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(
        provider_launcher,
        "__file__",
        str(runtime / "provider-launcher.py"),
    )
    monkeypatch.setattr(
        provider_launcher.sys,
        "argv",
        ["provider-launcher.py", "leases"],
    )
    calls = []

    def execv(executable, argv):
        calls.append((executable, argv))
        raise SystemExit(0)

    monkeypatch.setattr(provider_launcher.os, "execv", execv)

    with pytest.raises(SystemExit):
        provider_launcher.main()

    assert calls == [(
        str(python),
        [
            str(python),
            "-I",
            str(runtime / "provider-launcher.py"),
            "--agent-containers-active-runtime",
            "leases",
        ],
    )]


def test_profile_spec_refuses_unleased_picker_source(monkeypatch, tmp_path):
    module = tmp_path / "module.yaml"
    module.write_text("module: provider-exec\n", encoding="utf-8")
    monkeypatch.setattr(provider_ssh, "provider_module_path", lambda: module)
    monkeypatch.setattr(
        provider_ssh,
        "resolve_live_exec_target",
        lambda _name: _target(),
    )

    async def resolve_spec(_self, _name):
        return {"venue": _venue()}

    monkeypatch.setattr(provider_ssh.ContainerResolver, "resolve_spec", resolve_spec)
    monkeypatch.setattr(provider_ssh, "get_lease", lambda _name: None)

    with pytest.raises(RuntimeError, match="active lease"):
        provider_ssh.ssh_profile_spec(
            "sandbox-1",
            project="example-project",
        )


def test_emit_profile_persists_registry_and_delegates_to_agent_ssh(
    monkeypatch,
    tmp_path,
):
    spec = {
        "module": str(tmp_path / "module.yaml"),
        "registry": {
            "transport": "provider-exec",
            "proxy_command_binary": "/bin/agent-containers",
            "machines": [{"name": "restricted-worker"}],
        },
    }
    registry_path = tmp_path / "profile.json"
    registry_path.write_text(
        json.dumps(
            {
                "transport": "provider-exec",
                "proxy_command_binary": "/old/agent-containers",
                "machines": [{"name": "existing-worker", "hostname": "sandbox-0"}],
            }
        ),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(provider_ssh, "ssh_profile_spec", lambda *_args: spec)
    monkeypatch.setattr(
        provider_ssh,
        "_profile_registry_path",
        lambda: registry_path,
    )
    monkeypatch.setattr(
        provider_ssh.shutil,
        "which",
        lambda name: f"/bin/{name}",
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(provider_ssh.subprocess, "run", run)

    assert (
        provider_ssh.emit_ssh_profile(
            "sandbox-1",
            "restricted-worker",
            print_only=False,
        )
        == 0
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["transport"] == "provider-exec"
    assert registry["proxy_command_binary"] == "/bin/agent-containers"
    assert [machine["name"] for machine in registry["machines"]] == [
        "existing-worker",
        "restricted-worker",
    ]
    assert calls[0][0] == [
        "/bin/agent-ssh",
        "emit-profile",
        str(registry_path),
        "--module",
        spec["module"],
    ]


def test_emit_profile_publishes_picker_source_after_ssh_profile(
    monkeypatch,
    tmp_path,
):
    registry_path = tmp_path / "profiles" / "provider-exec.json"
    source_path = tmp_path / "sources" / "agent-containers.json"
    spec = {
        "module": str(tmp_path / "module.yaml"),
        "registry": {
            "transport": "provider-exec",
            "proxy_command_binary": "/bin/agent-containers",
            "machines": [{"name": "restricted-worker"}],
        },
        "worktree_source": {
            "kind": "provider-exec",
            "project": "example-project",
            "target_id": "container:sandbox-1",
            "instance_id": "container-id",
            "label": "Restricted target",
            "alias": "restricted-worker",
            "shell": "bash",
            "resolve": [
                "/bin/agent-containers",
                "ssh-profile",
                "sandbox-1",
                "--alias",
                "restricted-worker",
                "--project",
                "example-project",
                "--label",
                "Restricted target",
                "--json",
            ],
            "venue": {
                "provider": "agent-containers",
                "target_id": "container:sandbox-1",
                "posture_verified": True,
                "assignment": {
                    "kind": "lease",
                    "effort": "example-effort",
                    "acquired_at": 1_700_000_000.0,
                },
            },
            "capabilities": {"list": True, "resume": False},
        },
    }
    monkeypatch.setattr(provider_ssh, "ssh_profile_spec", lambda *_args, **_kwargs: spec)
    monkeypatch.setattr(provider_ssh, "_profile_registry_path", lambda: registry_path)
    monkeypatch.setattr(provider_ssh, "_source_registry_path", lambda: source_path)
    monkeypatch.setattr(
        provider_ssh.shutil,
        "which",
        lambda name: f"/bin/{name}",
    )
    monkeypatch.setattr(
        provider_ssh.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        provider_ssh,
        "provider_lease_guard",
        lambda: _lease_guard(_lease()),
    )

    assert provider_ssh.emit_ssh_profile(
        "sandbox-1",
        "restricted-worker",
        project="example-project",
        label="Restricted target",
    ) == 0

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["provider"] == "agent-containers"
    assert payload["sources"] == [spec["worktree_source"]]

    monkeypatch.setattr(
        provider_ssh,
        "provider_lease_guard",
        lambda: _lease_guard(
            SimpleNamespace(
                container="sandbox-1",
                effort="replacement-effort",
                acquired_at=2_000_000_000.0,
            )
        ),
    )
    with pytest.raises(RuntimeError, match="lease assignment changed"):
        provider_ssh.emit_ssh_profile(
            "sandbox-1",
            "restricted-worker",
            project="example-project",
            label="Restricted target",
        )


def test_publish_picker_source_replaces_project_case_insensitively(
    monkeypatch,
    tmp_path,
):
    source_path = tmp_path / "sources" / "agent-containers.json"
    monkeypatch.setattr(provider_ssh, "_source_registry_path", lambda: source_path)
    original = {
        "kind": "provider-exec",
        "project": "Example-Project",
        "target_id": "container:sandbox-1",
    }
    provider_ssh._publish_worktree_source(original)

    replacement = {
        **original,
        "project": "example-project",
        "instance_id": "replacement",
    }
    provider_ssh._publish_worktree_source(replacement)

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    assert payload["sources"] == [replacement]


def test_remove_picker_source_is_case_insensitive_and_targeted(
    monkeypatch,
    tmp_path,
):
    profile_path = tmp_path / "profiles" / "provider-exec.json"
    source_path = tmp_path / "sources" / "agent-containers.json"
    monkeypatch.setattr(provider_ssh, "_profile_registry_path", lambda: profile_path)
    monkeypatch.setattr(provider_ssh, "_source_registry_path", lambda: source_path)
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        json.dumps({
            "schema_version": 1,
            "provider": "agent-containers",
            "sources": [
                {
                    "project": "Example-Project",
                    "target_id": "container:sandbox-1",
                },
                {
                    "project": "other-project",
                    "target_id": "container:sandbox-2",
                },
            ],
        }),
        encoding="utf-8",
    )

    assert provider_ssh.remove_worktree_source(
        "sandbox-1", "example-project"
    ) is True
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    assert payload["sources"] == [
        {
            "project": "other-project",
            "target_id": "container:sandbox-2",
        }
    ]
    assert provider_ssh.remove_worktree_source(
        "sandbox-1", "example-project"
    ) is False


def test_remove_stale_picker_sources_by_container_or_effort(monkeypatch, tmp_path):
    source_path = tmp_path / "sources" / "agent-containers.json"
    monkeypatch.setattr(provider_ssh, "_source_registry_path", lambda: source_path)
    monkeypatch.setattr(
        provider_ssh,
        "provider_lease_guard",
        lambda: _lease_guard(
            SimpleNamespace(
                container="sandbox-2",
                effort="two",
                acquired_at=200.0,
            )
        ),
    )
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        json.dumps({
            "schema_version": 1,
            "provider": "agent-containers",
            "sources": [
                {
                    "project": "one",
                    "target_id": "container:sandbox-1",
                    "venue": {
                        "assignment": {
                            "kind": "lease",
                            "effort": "released-effort",
                            "acquired_at": 100.0,
                        }
                    },
                },
                {
                    "project": "two",
                    "target_id": "container:sandbox-2",
                    "venue": {
                        "assignment": {
                            "kind": "lease",
                            "effort": "two",
                            "acquired_at": 200.0,
                        }
                    },
                },
                {
                    "project": "three",
                    "target_id": "container:sandbox-3",
                    "venue": {
                        "assignment": {
                            "kind": "lease",
                            "effort": "released-effort",
                            "acquired_at": 300.0,
                        }
                    },
                },
            ],
        }),
        encoding="utf-8",
    )

    assert provider_ssh.remove_stale_worktree_sources("released-effort") == 2
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    assert payload["sources"] == [
        {
            "project": "two",
            "target_id": "container:sandbox-2",
            "venue": {
                "assignment": {
                    "kind": "lease",
                    "effort": "two",
                    "acquired_at": 200.0,
                }
            },
        }
    ]


def test_release_reports_busy_provider_admission(monkeypatch, capsys):
    def blocked(_target):
        raise lease_mod.ProviderAdmissionError("active provider session")

    monkeypatch.setattr(lease_mod, "release", blocked)
    monkeypatch.setattr(
        provider_ssh,
        "remove_stale_worktree_sources",
        lambda _target: pytest.fail("blocked release must not clean registrations"),
    )

    result = containers_cli._cmd_release(
        SimpleNamespace(target="sandbox-1")
    )

    assert result == 75
    assert "Release blocked: active provider session" in capsys.readouterr().err


def test_emit_profile_fails_when_agent_ssh_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        provider_ssh,
        "ssh_profile_spec",
        lambda *_args: {
            "module": "/module.yaml",
            "registry": {
                "transport": "provider-exec",
                "proxy_command_binary": "/bin/agent-containers",
                "machines": [{"name": "sandbox-1"}],
            },
        },
    )
    monkeypatch.setattr(
        provider_ssh,
        "_profile_registry_path",
        lambda: Path("provider-profile.json"),
    )
    monkeypatch.setattr(provider_ssh, "atomic_write_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(provider_ssh.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="agent-ssh is required"):
        provider_ssh.emit_ssh_profile("sandbox-1")


def test_print_profile_does_not_invalidate_published_registry(monkeypatch, tmp_path):
    registry_path = tmp_path / "provider-exec.json"
    published = {
        "transport": "provider-exec",
        "proxy_command_binary": "/bin/agent-containers",
        "machines": [{"name": "existing-worker", "hostname": "sandbox-0"}],
    }
    registry_path.write_text(json.dumps(published), encoding="utf-8")
    monkeypatch.setattr(
        provider_ssh,
        "ssh_profile_spec",
        lambda *_args: {
            "module": str(tmp_path / "module.yaml"),
            "registry": {
                "transport": "provider-exec",
                "proxy_command_binary": "/bin/agent-containers",
                "machines": [{"name": "preview-worker", "hostname": "sandbox-1"}],
            },
        },
    )
    monkeypatch.setattr(provider_ssh, "_profile_registry_path", lambda: registry_path)
    monkeypatch.setattr(
        provider_ssh.shutil,
        "which",
        lambda name: f"/bin/{name}",
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(provider_ssh.subprocess, "run", run)

    assert provider_ssh.emit_ssh_profile("sandbox-1", print_only=True) == 0
    assert json.loads(registry_path.read_text(encoding="utf-8")) == published
    assert calls[0][-1] == "--print"
    assert Path(calls[0][2]) != registry_path
    assert not Path(calls[0][2]).exists()


def test_provider_server_rejects_forwarding_and_agent_projection():
    paramiko = pytest.importorskip("paramiko")
    server = provider_ssh._ProviderServer(paramiko)

    assert (
        server.check_channel_direct_tcpip_request(
            1,
            ("127.0.0.1", 1234),
            ("example.invalid", 443),
        )
        == paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    )
    assert server.check_port_forward_request("127.0.0.1", 0) is False
    assert server.check_channel_forward_agent_request(SimpleNamespace()) is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ready": False}, "ready restricted"),
        ({"state": "exited"}, "ready restricted"),
        ({"effective_security_profile": "trusted"}, "ready restricted"),
        ({"transport": "ssh"}, "ready restricted"),
        ({"lifecycle_hold": {"state": "active"}}, "ready restricted"),
        ({"instance_id": "replacement-456"}, "ready restricted"),
    ],
)
def test_profile_spec_rejects_unready_or_nonrestricted_metadata(
    monkeypatch,
    tmp_path,
    overrides,
    message,
):
    module = tmp_path / "module.yaml"
    module.write_text("module: provider-exec\n", encoding="utf-8")
    monkeypatch.setattr(provider_ssh, "provider_module_path", lambda: module)
    monkeypatch.setattr(
        provider_ssh,
        "resolve_live_exec_target",
        lambda name: _target(),
    )

    async def resolve_spec(_self, _name):
        return {"venue": _venue(**overrides)}

    monkeypatch.setattr(
        provider_ssh.ContainerResolver,
        "resolve_spec",
        resolve_spec,
    )

    with pytest.raises(RuntimeError, match=message):
        provider_ssh.ssh_profile_spec("sandbox-1")


def test_run_ssh_stdio_holds_session_admission_for_transport(monkeypatch):
    events = []

    @contextmanager
    def admission(name, *, expected_assignment=None):
        events.append(("admit", name, expected_assignment))
        yield
        events.append(("release", name))

    monkeypatch.setattr(provider_ssh, "session_admission", admission)
    monkeypatch.setattr(
        provider_ssh,
        "resolve_live_exec_target",
        lambda name: events.append(("resolve", name)) or _target(),
    )
    monkeypatch.setattr(
        provider_ssh,
        "_serve_ssh",
        lambda target, **kwargs: events.append(("serve", target.name)) or 0,
    )
    monkeypatch.setattr(
        provider_ssh.sys,
        "stdin",
        SimpleNamespace(buffer=SimpleNamespace()),
    )
    monkeypatch.setattr(
        provider_ssh.sys,
        "stdout",
        SimpleNamespace(buffer=SimpleNamespace()),
    )

    assert provider_ssh.run_ssh_stdio("sandbox-1") == 0
    assert events == [
        ("admit", "sandbox-1", None),
        ("resolve", "sandbox-1"),
        ("serve", "sandbox-1"),
        ("release", "sandbox-1"),
    ]


def test_run_ssh_stdio_reports_provider_hold_as_busy(monkeypatch, capsys):
    @contextmanager
    def admission(_name, *, expected_assignment=None):
        raise provider_ssh.ProviderAdmissionError("replacement in progress")
        yield

    monkeypatch.setattr(provider_ssh, "session_admission", admission)

    assert provider_ssh.run_ssh_stdio("sandbox-1") == 75
    assert "replacement in progress" in capsys.readouterr().err


def test_run_ssh_stdio_binds_expected_target_instance_and_assignment(
    monkeypatch,
    capsys,
):
    assignment = {
        "kind": "lease",
        "effort": "example-effort",
        "acquired_at": 1_700_000_000.0,
    }
    captured = {}

    @contextmanager
    def admission(name, *, expected_assignment=None):
        captured["admission"] = (name, expected_assignment)
        yield

    monkeypatch.setattr(provider_ssh, "session_admission", admission)
    monkeypatch.setattr(provider_ssh, "resolve_live_exec_target", lambda _name: _target())
    monkeypatch.setattr(provider_ssh, "_serve_ssh", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        provider_ssh.sys,
        "stdin",
        SimpleNamespace(buffer=SimpleNamespace()),
    )
    monkeypatch.setattr(
        provider_ssh.sys,
        "stdout",
        SimpleNamespace(buffer=SimpleNamespace()),
    )

    assert provider_ssh.run_ssh_stdio(
        "sandbox-1",
        expected_target_id="container:sandbox-1",
        expected_instance_id="instance-123",
        expected_assignment=assignment,
    ) == 0
    assert captured["admission"] == ("sandbox-1", assignment)

    assert provider_ssh.run_ssh_stdio(
        "sandbox-1",
        expected_target_id="container:other",
        expected_instance_id="instance-123",
        expected_assignment=assignment,
    ) == 75
    assert "target identity changed" in capsys.readouterr().err

    assert provider_ssh.run_ssh_stdio(
        "sandbox-1",
        expected_target_id="container:sandbox-1",
        expected_instance_id="instance-other",
        expected_assignment=assignment,
    ) == 75
    assert "instance identity changed" in capsys.readouterr().err


@pytest.mark.skipif(
    shutil.which("ssh") is None or sys.platform == "win32",
    reason="POSIX OpenSSH client unavailable",
)
def test_openssh_exec_round_trip_over_stdio_proxy(tmp_path):
    pytest.importorskip("paramiko")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        '[ "$1" = exec ]\n'
        '[ "$2" = -i ]\n'
        '[ "$3" = -u ]\n'
        '[ "$4" = sandbox-agent ]\n'
        '[ "$5" = instance-123 ]\n'
        '[ "$6" = bash ]\n'
        '[ "$7" = -c ]\n'
        'exec bash -lc "$8"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    helper = tmp_path / "proxy.py"
    helper.write_text(
        "import sys\n"
        "from types import SimpleNamespace\n"
        "from agent_containers import provider_ssh\n"
        "target = SimpleNamespace(\n"
        "    name='sandbox-1', container_id='instance-123', user='sandbox-agent',\n"
        f"    workspace_folder={str(workspace)!r})\n"
        "raise SystemExit(provider_ssh._serve_ssh(\n"
        "    target, stdin=sys.stdin.buffer, stdout=sys.stdout.buffer))\n",
        encoding="utf-8",
    )
    proxy = f"{shlex_quote(sys.executable)} {shlex_quote(str(helper))}"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            f"ProxyCommand={proxy}",
            "-o",
            "ControlMaster=no",
            "-o",
            "ControlPath=none",
            "-o",
            "GSSAPIAuthentication=no",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "ignored-user@provider-target",
            "printf out; printf err >&2; exit 7",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )

    assert proc.returncode == 7, proc.stderr
    assert proc.stdout == "out"
    assert proc.stderr == "err"


def shlex_quote(value: str) -> str:
    """Quote a ProxyCommand token for the POSIX OpenSSH test environment."""
    import shlex

    return shlex.quote(value)
