"""Restricted session-evidence rescue tests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_containers import private_state, rescue, rescue_protocol, restricted_exec
from agent_containers.config import ContainersConfig, FleetConfig, RescueConfig

SESSION_ID = "11111111-1111-4111-8111-111111111111"


def _deep_projection() -> bytes:
    nested: object = 0
    for _ in range(70):
        nested = [nested]
    return json.dumps(
        {
            "version": 1,
            "session_id": SESSION_ID,
            "relations": [{"nested": nested}],
            "overflow": False,
            "omitted_relations": 0,
        }
    ).encode()


class _FakeProcess:
    def __init__(self, payload: bytes, control: bytes, returncode: int = 0):
        self.stdout = io.BytesIO(payload)
        self.stderr = io.BytesIO(control)
        self.returncode = returncode
        self._terminated = False

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode if self._terminated else None

    def terminate(self):
        self._terminated = True

    def kill(self):
        self._terminated = True


class _BlockingStream:
    def read(self, _size):
        time.sleep(0.2)
        return b"x"


def _configured() -> tuple[ContainersConfig, FleetConfig]:
    fleet = FleetConfig(
        repo="example/repository",
        image="example/agent",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
    )
    config = ContainersConfig(
        fleets={"sandbox": fleet},
        rescue=RescueConfig(
            max_member_bytes=1024,
            max_capture_bytes=4096,
            max_total_bytes=8192,
            retain_per_container=2,
        ),
    )
    return config, fleet


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for embedded rescue-script tests")
    return node


def _run_node(script: str, home, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [_node(), "-e", script, *args],
        capture_output=True,
        env=env,
        check=False,
    )


def _patch_capture_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rescue, "RESCUE_ROOT", tmp_path / "rescues")
    monkeypatch.setattr(rescue, "ensure_state_dir", lambda: None)
    monkeypatch.setattr(
        rescue,
        "inspect_container",
        lambda name: {
            "Id": name,
            "State": {"StartedAt": "2026-01-01T00:00:00Z"},
            "Config": {
                "Labels": {
                    "agent-containers.security-home": "/home/agent",
                }
            },
            "HostConfig": {"Tmpfs": {"/home/agent": "", "/workspace": ""}},
            "Mounts": [],
        },
    )
    monkeypatch.setattr(
        rescue,
        "resolve_executable",
        lambda *_args, **_kwargs: ("/usr/local/bin/node", "/home/agent"),
    )


def _patch_inventory(monkeypatch) -> None:
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory(
            "present",
            [(SESSION_ID, "d"), ("not-a-session", "d")],
        ),
    )
    monkeypatch.setattr(
        rescue,
        "_inventory_session",
        lambda *_args, **_kwargs: [
            ("events.jsonl", "f"),
            ("workspace.yaml", "f"),
            ("agent-worktrees.json", "f"),
            ("files", "d"),
            ("files/large.bin", "f"),
            ("rewind-file-snapshots", "d"),
            ("research", "d"),
            ("unknown.bin", "f"),
            ("session-store.db", "f"),
            ("settings.json", "f"),
            ("credentials.json", "f"),
            ("checkpoints", "d"),
        ],
    )


@pytest.mark.parametrize(
    "script",
    [
        rescue_protocol._ROOT_INVENTORY_SCRIPT,
        rescue_protocol._SESSION_INVENTORY_SCRIPT,
        rescue_protocol._MEMBER_STREAM_SCRIPT,
    ],
)
def test_embedded_rescue_javascript_passes_node_check(script):
    proc = subprocess.run(
        [_node(), "--check"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_embedded_member_script_normal_missing_symlink_fifo_and_oversize(tmp_path):
    session = tmp_path / ".copilot" / "session-state" / SESSION_ID
    session.mkdir(parents=True)
    normal = session / "events.jsonl"
    normal.write_bytes(b"event\n")

    proc = _run_node(
        rescue_protocol._MEMBER_STREAM_SCRIPT,
        tmp_path,
        SESSION_ID,
        "events.jsonl",
        "64",
    )
    assert proc.returncode == 0
    assert proc.stdout == b"event\n"
    assert proc.stderr == b"OK\t6\n"

    missing = _run_node(
        rescue_protocol._MEMBER_STREAM_SCRIPT,
        tmp_path,
        SESSION_ID,
        "origin.json",
        "64",
    )
    assert missing.returncode == 0
    assert missing.stderr == b"MISSING\n"

    symlink = session / "context.json"
    try:
        symlink.symlink_to(normal.name)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    linked = _run_node(
        rescue_protocol._MEMBER_STREAM_SCRIPT,
        tmp_path,
        SESSION_ID,
        "context.json",
        "64",
    )
    assert linked.returncode == 0
    assert linked.stderr == b"EXCLUDED\tsymlink\n"

    if hasattr(os, "mkfifo"):
        fifo = session / "workspace.yaml"
        os.mkfifo(fifo)
        piped = _run_node(
            rescue_protocol._MEMBER_STREAM_SCRIPT,
            tmp_path,
            SESSION_ID,
            "workspace.yaml",
            "64",
        )
        assert piped.returncode == 0
        assert piped.stderr == b"EXCLUDED\tirregular\n"

    oversize = _run_node(
        rescue_protocol._MEMBER_STREAM_SCRIPT,
        tmp_path,
        SESSION_ID,
        "events.jsonl",
        "2",
    )
    assert oversize.returncode == 0
    assert oversize.stderr == b"EXCLUDED\toversize\t6\n"


def test_embedded_inventory_distinguishes_missing_and_skips_high_growth(tmp_path):
    missing = _run_node(rescue_protocol._ROOT_INVENTORY_SCRIPT, tmp_path)
    records = rescue_protocol._decode_nul_records(missing.stdout, 2)
    assert records == [("@root", "missing")]

    session = tmp_path / ".copilot" / "session-state" / SESSION_ID
    (session / "files").mkdir(parents=True)
    (session / "files" / "large.bin").write_bytes(b"x" * 100)
    (session / "research").mkdir()
    (session / "research" / "notes.txt").write_text("notes", encoding="utf-8")
    root = _run_node(rescue_protocol._ROOT_INVENTORY_SCRIPT, tmp_path)
    root_records = rescue_protocol._decode_nul_records(root.stdout, 2)
    assert root_records[0] == ("@root", "present")

    inventory = _run_node(
        rescue_protocol._SESSION_INVENTORY_SCRIPT,
        tmp_path,
        SESSION_ID,
    )
    paths = {
        path
        for path, _kind in rescue_protocol._decode_nul_records(inventory.stdout, 2)
    }
    assert "files" in paths
    assert "research" in paths
    assert "files/large.bin" not in paths
    assert "research/notes.txt" not in paths


def test_node_interpreter_must_resolve_from_immutable_rootfs(monkeypatch):
    inspected = {
        "Config": {
            "Labels": {
                "agent-containers.security-home": "/home/agent",
            }
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Tmpfs": {
                "/home/agent": "",
                "/workspace": "",
                "/tmp": "",  # noqa: S108
                "/run": "",
            }
        },
        "Mounts": [],
    }
    monkeypatch.setattr(
        restricted_exec,
        "_NODE_CANDIDATES",
        ("/workspace/node",),
    )
    monkeypatch.setattr(
        restricted_exec.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("writable candidate must not execute")
        ),
    )
    with pytest.raises(restricted_exec.RestrictedExecError, match="no safe absolute"):
        restricted_exec.resolve_executable(
            "instance",
            "agent",
            inspected,
            kind="node",
            deadline=None,
        )

    calls = []
    monkeypatch.setattr(
        restricted_exec,
        "_NODE_CANDIDATES",
        ("/usr/local/bin/node",),
    )
    monkeypatch.setattr(
        restricted_exec.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(args)
        or (
            subprocess.CompletedProcess(
                args,
                0,
                "/usr/local/bin/node\n",
                "",
            )
            if "-f" in args
            else subprocess.CompletedProcess(args, 0)
        ),
    )
    assert restricted_exec.resolve_executable(
        "instance",
        "agent",
        inspected,
        kind="node",
        deadline=None,
    ) == ("/usr/local/bin/node", "/home/agent")
    argv = calls[0]
    assert "/usr/bin/readlink" in argv
    assert "LD_PRELOAD=" in argv
    assert "NODE_OPTIONS=" in argv
    assert "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" in argv
    assert "/usr/local/bin/node" in calls[1]


def test_executable_resolution_requires_readonly_root_and_rejects_writable_target(
    monkeypatch,
):
    inspected = {
        "Config": {
            "Labels": {
                "agent-containers.security-home": "/home/agent",
            }
        },
        "HostConfig": {
            "ReadonlyRootfs": False,
            "Tmpfs": {"/home/agent": "", "/workspace": ""},
        },
        "Mounts": [],
    }
    with pytest.raises(restricted_exec.RestrictedExecError, match="read-only"):
        restricted_exec.resolve_executable(
            "instance",
            "agent",
            inspected,
            kind="node",
            deadline=None,
        )

    inspected["HostConfig"]["ReadonlyRootfs"] = True
    monkeypatch.setattr(
        restricted_exec,
        "_NODE_CANDIDATES",
        ("/usr/bin/node",),
    )
    monkeypatch.setattr(
        restricted_exec.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args,
            0,
            "/workspace/node\n",
            "",
        ),
    )
    with pytest.raises(restricted_exec.RestrictedExecError, match="no safe absolute"):
        restricted_exec.resolve_executable(
            "instance",
            "agent",
            inspected,
            kind="node",
            deadline=None,
        )


def test_allowlisted_manifest_hashes_and_publishes_atomically(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    payloads = {
        "events.jsonl": b'{"type":"user.message"}\n',
        "workspace.yaml": b"cwd: /workspace\n",
        "agent-worktrees.json": json.dumps(
            {
                "version": 1,
                "session_id": SESSION_ID,
                "relations": [],
                "overflow": False,
                "omitted_relations": 0,
            }
        ).encode(),
    }
    _patch_capture_root(monkeypatch, tmp_path)
    _patch_inventory(monkeypatch)
    calls = []

    def fake_popen(args, **_kwargs):
        calls.append(args)
        relative = args[-2]
        payload = payloads.get(relative)
        if payload is None:
            return _FakeProcess(b"", b"MISSING\n")
        return _FakeProcess(payload, f"OK\t{len(payload)}\n".encode())

    monkeypatch.setattr(rescue_protocol.subprocess, "Popen", fake_popen)

    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="sha256:instance",
        user="agent",
    )

    assert metadata["status"] == "verified"
    assert metadata["completeness"] == "complete"
    assert metadata["restorable"] is False
    members = metadata["sessions"][SESSION_ID]["members"]
    assert members["events.jsonl"]["sha256"] == hashlib.sha256(
        payloads["events.jsonl"]
    ).hexdigest()
    assert members["workspace.yaml"]["sha256"] == hashlib.sha256(
        payloads["workspace.yaml"]
    ).hexdigest()
    assert members["agent-worktrees.json"]["sha256"] == hashlib.sha256(
        payloads["agent-worktrees.json"]
    ).hexdigest()
    assert metadata["excluded"] == {
        "unknown_session_entries": 1,
        "unknown_members": 4,
        "high_growth_roots": [
            "files",
            "research",
            "rewind-file-snapshots",
        ],
        "allowlisted": [],
        "missing_events": [],
    }
    node_index = calls[0].index("/usr/local/bin/node")
    stream_script = calls[0][node_index + 2]
    assert "O_NOFOLLOW" in stream_script
    assert "/proc/self/fd/" in stream_script
    assert "fstatSync(fd)" in stream_script
    assert "BASH_ENV=" in calls[0]
    assert "LD_PRELOAD=" in calls[0]
    assert "NODE_OPTIONS=" in calls[0]

    container_root = rescue.RESCUE_ROOT / "sandbox-1"
    captures = [
        path
        for path in container_root.iterdir()
        if path.is_dir() and not path.name.startswith(".staging-")
    ]
    assert len(captures) == 1
    capture = captures[0]
    assert not any(
        path.name.startswith(".staging-") for path in container_root.iterdir()
    )
    published = json.loads((capture / "metadata.json").read_text(encoding="utf-8"))
    assert published == metadata
    evidence_root = capture / "sessions" / SESSION_ID
    assert (evidence_root / "events.jsonl").read_bytes() == payloads["events.jsonl"]
    assert (evidence_root / "agent-worktrees.json").read_bytes() == payloads[
        "agent-worktrees.json"
    ]
    for forbidden in (
        "files",
        "research",
        "unknown.bin",
        "session-store.db",
        "settings.json",
        "credentials.json",
    ):
        assert not (evidence_root / forbidden).exists()


def test_stream_is_host_bounded_even_if_descriptor_grows(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rescue_protocol.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(
            b"0123456789",
            b"OK\t10\n",
        ),
    )
    destination = tmp_path / "member"

    with pytest.raises(rescue.RescueError, match="exceeded 5 bytes"):
        rescue._stream_member(
            "instance",
            "agent",
            "/usr/bin/node",
            "/home/agent",
            SESSION_ID,
            "events.jsonl",
            destination,
            max_bytes=5,
            deadline=None,
        )

    assert not destination.exists()


def test_member_destination_is_0600_at_initial_open_under_open_umask(
    monkeypatch,
    tmp_path,
):
    payload = b"event\n"
    monkeypatch.setattr(
        rescue_protocol.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(payload, b"OK\t6\n"),
    )
    destination = tmp_path / "member"
    real_open = os.open
    observed = {}

    def tracking_open(path, flags, mode=0o777):
        fd = real_open(path, flags, mode)
        if str(path) == str(destination):
            observed["requested"] = mode
            observed["created"] = stat.S_IMODE(os.fstat(fd).st_mode)
        return fd

    monkeypatch.setattr(rescue_protocol.os, "open", tracking_open)
    previous = os.umask(0)
    try:
        result = rescue_protocol._stream_member(
            "instance",
            "agent",
            "/usr/local/bin/node",
            "/home/agent",
            SESSION_ID,
            "events.jsonl",
            destination,
            max_bytes=64,
            deadline=None,
        )
    finally:
        os.umask(previous)

    assert result.status == "captured"
    assert observed == {"requested": 0o600, "created": 0o600}


def test_member_stream_wall_clock_deadline_terminates_child(monkeypatch, tmp_path):
    proc = _FakeProcess(b"", b"OK\t1\n")
    proc.stdout = _BlockingStream()
    monkeypatch.setattr(
        rescue_protocol.subprocess,
        "Popen",
        lambda *_args, **_kwargs: proc,
    )
    destination = tmp_path / "member"
    started = time.monotonic()

    with pytest.raises(rescue.RescueError, match="deadline"):
        rescue_protocol._stream_member(
            "instance",
            "agent",
            "/usr/bin/node",
            "/home/agent",
            SESSION_ID,
            "events.jsonl",
            destination,
            max_bytes=1024,
            deadline=time.monotonic() + 0.02,
        )

    assert time.monotonic() - started < 0.5
    assert proc._terminated is True
    assert not destination.exists()


def test_inventory_limit_terminates_child_during_stream(monkeypatch):
    proc = _FakeProcess(
        b"x" * (rescue_protocol._INVENTORY_LIMIT + 1),
        b"",
    )
    monkeypatch.setattr(
        rescue_protocol.subprocess,
        "Popen",
        lambda *_args, **_kwargs: proc,
    )

    with pytest.raises(rescue.RescueError, match="byte limit"):
        rescue_protocol._docker_bytes(
            "instance",
            "agent",
            "/usr/local/bin/node",
            "/home/agent",
            "script",
            deadline=None,
        )

    assert proc._terminated is True


def test_inventory_drains_pipe_filling_stderr_without_hanging(monkeypatch):
    real_popen = subprocess.Popen

    def helper(_args, **kwargs):
        return real_popen(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stderr.buffer.write(b'x' * 200000); "
                    "sys.stderr.flush(); "
                    "sys.stdout.buffer.write(b'@root\\0present\\0')"
                ),
            ],
            stdin=kwargs.get("stdin"),
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
        )

    monkeypatch.setattr(rescue_protocol.subprocess, "Popen", helper)
    started = time.monotonic()

    with pytest.raises(rescue.RescueError, match="diagnostics exceeded"):
        rescue_protocol._docker_bytes(
            "instance",
            "agent",
            "/usr/local/bin/node",
            "/home/agent",
            "script",
            deadline=time.monotonic() + 2,
        )

    assert time.monotonic() - started < 2


@pytest.mark.parametrize("reason", ["symlink", "irregular", "oversize"])
def test_descriptor_protocol_records_unsafe_member_without_open_artifact(
    monkeypatch,
    tmp_path,
    reason,
):
    control = (
        b"EXCLUDED\toversize\t2048\n"
        if reason == "oversize"
        else f"EXCLUDED\t{reason}\n".encode()
    )
    monkeypatch.setattr(
        rescue_protocol.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(b"", control),
    )
    destination = tmp_path / "member"

    result = rescue_protocol._stream_member(
        "instance",
        "agent",
        "/usr/bin/node",
        "/home/agent",
        SESSION_ID,
        "events.jsonl",
        destination,
        max_bytes=1024,
        deadline=None,
    )

    assert result.status == "excluded"
    assert result.reason == reason
    assert not destination.exists()


def test_irregular_and_oversize_members_are_partial_not_fatal(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory(
            "present",
            [(SESSION_ID, "d")],
        ),
    )
    monkeypatch.setattr(
        rescue,
        "_inventory_session",
        lambda *_args, **_kwargs: [],
    )

    def stream(
        _container,
        _user,
        _node_path,
        _home,
        _session,
        relative,
        destination,
        **_kwargs,
    ):
        if relative == "events.jsonl":
            return rescue_protocol.StreamResult("excluded", 2048, reason="oversize")
        if relative == "workspace.yaml":
            destination.write_bytes(b"cwd: /workspace\n")
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            return rescue_protocol.StreamResult(
                "captured",
                destination.stat().st_size,
                digest,
            )
        if relative == "agent-worktrees.json":
            return rescue_protocol.StreamResult("excluded", reason="symlink")
        return rescue_protocol.StreamResult("missing", reason="missing")

    monkeypatch.setattr(rescue, "_stream_member", stream)

    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    assert metadata["status"] == "verified"
    assert metadata["completeness"] == "partial"
    assert "workspace.yaml" in metadata["sessions"][SESSION_ID]["members"]
    assert {
        (item["member"], item["reason"])
        for item in metadata["excluded"]["allowlisted"]
    } == {
        ("events.jsonl", "oversize"),
        ("agent-worktrees.json", "symlink"),
    }


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"{", "invalid_projection_json"),
        (_deep_projection(), "invalid_projection_schema"),
        (b" " * (128 * 1024 + 1), "oversize"),
        (
            json.dumps(
                {
                    "version": 1,
                    "session_id": "22222222-2222-4222-8222-222222222222",
                    "relations": [],
                    "overflow": False,
                    "omitted_relations": 0,
                }
            ).encode(),
            "projection_session_id_mismatch",
        ),
    ],
    ids=[
        "malformed",
        "nested-schema",
        "oversize",
        "session-mismatch",
    ],
)
def test_invalid_agent_worktrees_projection_is_excluded(
    monkeypatch,
    tmp_path,
    payload,
    reason,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    _patch_inventory(monkeypatch)

    def stream(
        _container,
        _user,
        _node_path,
        _home,
        _session,
        relative,
        destination,
        **_kwargs,
    ):
        if relative == "events.jsonl":
            content = b'{"type":"user.message"}\n'
        elif relative == "agent-worktrees.json":
            content = payload
        else:
            return rescue_protocol.StreamResult("missing", reason="missing")
        destination.write_bytes(content)
        return rescue_protocol.StreamResult(
            "captured",
            len(content),
            hashlib.sha256(content).hexdigest(),
        )

    monkeypatch.setattr(rescue, "_stream_member", stream)

    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    assert metadata["completeness"] == "partial"
    assert metadata["sessions"][SESSION_ID]["members"].keys() == {"events.jsonl"}
    assert metadata["excluded"]["allowlisted"] == [
        {
            "session_id": SESSION_ID,
            "member": "agent-worktrees.json",
            "reason": reason,
            "bytes": len(payload),
        }
    ]


def test_future_agent_worktrees_projection_is_preserved(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    _patch_inventory(monkeypatch)
    projection = json.dumps(
        {
            "version": 2,
            "session_id": SESSION_ID,
            "future": {"shape": "opaque"},
        }
    ).encode()

    def stream(
        _container,
        _user,
        _node_path,
        _home,
        _session,
        relative,
        destination,
        **_kwargs,
    ):
        if relative == "events.jsonl":
            content = b'{"type":"user.message"}\n'
        elif relative == "agent-worktrees.json":
            content = projection
        else:
            return rescue_protocol.StreamResult("missing", reason="missing")
        destination.write_bytes(content)
        return rescue_protocol.StreamResult(
            "captured",
            len(content),
            hashlib.sha256(content).hexdigest(),
        )

    monkeypatch.setattr(rescue, "_stream_member", stream)

    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    assert metadata["completeness"] == "complete"
    capture = rescue.RESCUE_ROOT / "sandbox-1" / metadata["capture_id"]
    assert (
        capture / "sessions" / SESSION_ID / "agent-worktrees.json"
    ).read_bytes() == projection


def test_canonical_tolerant_v1_projection_is_preserved(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    _patch_inventory(monkeypatch)
    projection = json.dumps(
        {
            "version": 1,
            "session_id": SESSION_ID,
            "relations": [{"worktree_id": str(index)} for index in range(129)],
        }
    ).encode()

    def stream(
        _container,
        _user,
        _node_path,
        _home,
        _session,
        relative,
        destination,
        **_kwargs,
    ):
        if relative == "events.jsonl":
            content = b'{"type":"user.message"}\n'
        elif relative == "agent-worktrees.json":
            content = projection
        else:
            return rescue_protocol.StreamResult("missing", reason="missing")
        destination.write_bytes(content)
        return rescue_protocol.StreamResult(
            "captured",
            len(content),
            hashlib.sha256(content).hexdigest(),
        )

    monkeypatch.setattr(rescue, "_stream_member", stream)
    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    assert "agent-worktrees.json" in metadata["sessions"][SESSION_ID]["members"]


def test_failed_latest_status_retains_verified_fallback(monkeypatch, tmp_path):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    verified = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(rescue.RescueError("probe failed")),
    )

    with pytest.raises(rescue.RescueError, match="probe failed"):
        rescue.capture_restricted_sessions(
            config,
            fleet,
            container="sandbox-1",
            container_instance="instance-b",
            user="agent",
        )

    status = rescue.latest_rescue_status("sandbox-1")
    assert status["status"] == "failed"
    assert status["latest_verified"]["capture_id"] == verified["capture_id"]


def test_missing_session_root_is_verified_partial_not_complete_empty(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("missing", []),
    )

    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    assert metadata["session_state"] == "missing"
    assert metadata["completeness"] == "partial"


def test_verified_capture_requires_exact_id_and_run_generation(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="same-container-id",
        user="agent",
    )

    assert metadata["container_generation"] == "2026-01-01T00:00:00Z"
    assert rescue.verified_capture_for_instance(
        "sandbox-1",
        "same-container-id",
        "2026-01-01T00:00:00Z",
    ) is not None
    assert rescue.verified_capture_for_instance(
        "sandbox-1",
        "same-container-id",
        "2026-01-02T00:00:00Z",
    ) is None


def test_explicit_loss_status_retains_verified_fallback(monkeypatch, tmp_path):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    verified = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    rescue.record_telemetry_loss(
        container="sandbox-1",
        container_instance="instance-b",
        container_generation="2026-01-02T00:00:00Z",
        reason="container_not_running",
    )

    status = rescue.latest_rescue_status("sandbox-1")
    assert status["status"] == "abandoned"
    assert status["latest_verified"]["capture_id"] == verified["capture_id"]


def test_retention_failure_does_not_invalidate_verified_capture(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    monkeypatch.setattr(
        rescue,
        "_enforce_retention",
        lambda *_args: (_ for _ in ()).throw(OSError("busy")),
    )

    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    assert metadata["status"] == "verified"
    assert rescue.latest_rescue_status("sandbox-1")["status"] == "verified"


def test_retention_keeps_newest_verified_capture(monkeypatch, tmp_path):
    config, fleet = _configured()
    config.rescue.retain_per_container = 1
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    ticks = iter([100, 200])
    monkeypatch.setattr(rescue.time, "time_ns", lambda: next(ticks))

    first = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )
    second = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-b",
        user="agent",
    )

    root = rescue.RESCUE_ROOT / "sandbox-1"
    captures = sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".staging-")
    )
    assert captures == [second["capture_id"]]
    assert first["capture_id"] not in captures


def test_retention_repairs_failed_status_fallback_after_deletion(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    config.rescue.retain_per_container = 1
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    ticks = iter([100, 200])
    monkeypatch.setattr(rescue.time, "time_ns", lambda: next(ticks))
    first = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )
    second = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-b",
        user="agent",
    )
    base = rescue.RESCUE_ROOT / "sandbox-1"
    rescue._publish_status(
        base,
        {
            "status": "failed",
            "reason": "capture_failed",
            "latest_verified": {
                "capture_id": first["capture_id"],
                "container_instance": "instance-a",
            },
        },
    )

    rescue._enforce_retention(config, base / second["capture_id"])

    status = rescue.latest_rescue_status("sandbox-1")
    assert not (base / first["capture_id"]).exists()
    assert status["status"] == "failed"
    assert status["latest_verified"]["capture_id"] == second["capture_id"]


def test_active_lifecycle_pin_survives_concurrent_quota_retention(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    config.rescue.retain_per_container = 1
    config.rescue.max_total_bytes = config.rescue.max_capture_bytes
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    ticks = iter([100, 200])
    monkeypatch.setattr(rescue.time, "time_ns", lambda: next(ticks))
    first = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )
    base = rescue.RESCUE_ROOT / "sandbox-1"
    first_path = base / first["capture_id"]
    first_metadata = json.loads(
        (first_path / "metadata.json").read_text(encoding="utf-8")
    )
    first_metadata["total_bytes"] = config.rescue.max_total_bytes
    (first_path / "metadata.json").write_text(
        json.dumps(first_metadata),
        encoding="utf-8",
    )

    with rescue.pin_verified_capture(
        "sandbox-1",
        "instance-a",
        "2026-01-01T00:00:00Z",
        first["capture_id"],
        expires_at=time.time() + 60,
    ) as pin:
        second = rescue.capture_restricted_sessions(
            config,
            fleet,
            container="sandbox-1",
            container_instance="instance-b",
            user="agent",
        )
        assert first_path.exists()
        rescue.verify_pinned_capture(pin)

    second_path = base / second["capture_id"]
    rescue._enforce_retention(config, second_path)
    assert not first_path.exists()
    assert second_path.exists()


def test_malformed_pin_is_fail_closed_then_expires_for_retention(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    config.rescue.retain_per_container = 2
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    ticks = iter([100, 200])
    monkeypatch.setattr(rescue.time, "time_ns", lambda: next(ticks))
    first = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )
    second = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-b",
        user="agent",
    )
    base = rescue.RESCUE_ROOT / "sandbox-1"
    first_path = base / first["capture_id"]
    second_path = base / second["capture_id"]
    malformed = first_path / ".pin-crashed.json"
    malformed.write_text("{truncated", encoding="utf-8")
    config.rescue.retain_per_container = 1

    rescue._enforce_retention(config, second_path)
    assert first_path.exists()

    old = time.time() - rescue._MALFORMED_PIN_MAX_AGE - 1
    os.utime(malformed, (old, old))
    rescue._enforce_retention(config, second_path)

    assert not first_path.exists()
    assert second_path.exists()


def test_nul_inventory_framing_preserves_newlines_and_rejects_partial():
    assert rescue_protocol._decode_nul_records(b"name\nwith-newline\0f\0", 2) == [
        ("name\nwith-newline", "f")
    ]
    with pytest.raises(rescue.RescueError, match="invalid framing"):
        rescue_protocol._decode_nul_records(b"name\0f", 2)


def test_rescue_lock_prevents_concurrent_mutation(tmp_path):
    lock = tmp_path / "capture.lock"
    with rescue._exclusive_lock(lock):
        with pytest.raises(rescue.RescueError, match="lock is busy"):
            with rescue._exclusive_lock(lock, timeout=0):
                pass


def test_rescue_lock_cleanup_never_unlinks_new_owner(tmp_path):
    lock = tmp_path / "capture.lock"
    with rescue._exclusive_lock(lock):
        lock.write_text("replacement-owner", encoding="ascii")

    assert lock.read_text(encoding="ascii") == "replacement-owner"


def test_rescue_metadata_write_is_owner_only_under_open_umask(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "status.json"
    fsync_calls = []
    monkeypatch.setattr(
        private_state,
        "fsync_directory",
        lambda directory: fsync_calls.append(directory),
    )
    previous = os.umask(0)
    try:
        rescue._write_json_fsynced(path, {"status": "verified"})
    finally:
        os.umask(previous)

    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "verified"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert fsync_calls == [path.parent]


def test_rescue_paths_tolerate_acl_backed_chmod_limitations(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    monkeypatch.setattr(
        private_state,
        "filesystem_type",
        lambda _path: "9p",
    )
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("DrvFS ACL")
        ),
    )

    metadata = rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    assert metadata["status"] == "verified"


def test_member_stream_tolerates_acl_backed_chmod_limitations(
    monkeypatch,
    tmp_path,
):
    payload = b"event\n"
    monkeypatch.setattr(
        rescue_protocol.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _FakeProcess(payload, b"OK\t6\n"),
    )
    monkeypatch.setattr(
        private_state,
        "filesystem_type",
        lambda _path: "cifs",
    )
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("ACL-backed")
        ),
    )
    destination = tmp_path / "member"

    result = rescue_protocol._stream_member(
        "instance",
        "agent",
        "/usr/local/bin/node",
        "/home/agent",
        SESSION_ID,
        "events.jsonl",
        destination,
        max_bytes=64,
        deadline=None,
    )

    assert result.status == "captured"
    assert destination.read_bytes() == payload


def test_publication_and_cross_container_retention_use_distinct_locks(
    monkeypatch,
    tmp_path,
):
    config, fleet = _configured()
    _patch_capture_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rescue,
        "_inventory_root",
        lambda *_args, **_kwargs: rescue_protocol.RootInventory("present", []),
    )
    real_lock = rescue._exclusive_lock
    observed = []

    @contextmanager
    def tracked(path, **kwargs):
        observed.append(path.name)
        with real_lock(path, **kwargs):
            yield

    monkeypatch.setattr(rescue, "_exclusive_lock", tracked)

    rescue.capture_restricted_sessions(
        config,
        fleet,
        container="sandbox-1",
        container_instance="instance-a",
        user="agent",
    )

    assert ".capture-sandbox-1.lock" in observed
    assert ".retention.lock" in observed
