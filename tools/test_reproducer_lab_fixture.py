"""Synthetic guest fixture controls; never launches Git, services or peers."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_reproducer_lab_monitoring import LAB

sys.path.insert(0, str(LAB))
import guest_fixture

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux guest fixture contract")
REAL_PRIVATE_DIRECTORY = guest_fixture._private_directory


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    state, workspace, evidence = [tmp_path / name for name in ("state", "workspace", "evidence")]
    for path in (state, workspace, evidence):
        path.mkdir(mode=0o700)
    (state / guest_fixture.LOCK.name).touch()
    (state / "runtime").mkdir(mode=0o700)
    for name, value in {
        "STATE": state, "WORKSPACE": workspace, "EVIDENCE": evidence,
        "HOME": state / "home", "SOURCE": tmp_path / "source",
    }.items():
        monkeypatch.setattr(guest_fixture, name, value)
    # Does not qualify actual mounted guest ownership or runtime origins.
    monkeypatch.setattr(guest_fixture, "_private_directory", lambda *_a, **_kw: None)
    calls: list[tuple[list[str], dict[str, object]]] = []
    custody = SimpleNamespace(assert_owned=lambda: None)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(guest_fixture, "run", run)
    return SimpleNamespace(
        state=state, workspace=workspace, evidence=evidence, home=state / "home",
        calls=calls, custody=custody,
    )


def test_minimal_environment_never_inherits_a_parent(
    paths: SimpleNamespace, monkeypatch: Any,
) -> None:
    for key in ("GH_TOKEN", "SSH_AUTH_SOCK", "HTTPS_PROXY", "COPILOT_EXTENSIONS_CONTEXT",
                "PYTHONPATH", "GIT_CONFIG_COUNT", "AGENT_BRIDGE_ENDPOINT"):
        monkeypatch.setenv(key, "synthetic-parent-value")
    environment = guest_fixture.environment()
    assert "synthetic-parent-value" not in environment.values()
    assert environment["HOME"] == str(paths.home)
    assert environment["AGENT_HOME"] == str(paths.home)
    assert environment["AGENT_DISPATCH_NO_AUTOSTART"] == "1"
    assert environment["AGENT_WORKTREES_STATUS_MONITOR"] == "0"
    assert environment["AGENT_BRIDGE_PROVIDERS_DIR"] == str(paths.home / ".agent-bridge/providers.d")
    environment["HOME"] = "changed"
    assert guest_fixture.environment()["HOME"] == str(paths.home)


def test_first_preparation_writes_supported_inputs_and_only_three_git_calls(
    paths: SimpleNamespace,
) -> None:
    assert guest_fixture.prepare_fixture(paths.custody) is None
    expected = guest_fixture.documents()
    for path, document in expected.items():
        assert json.loads(path.read_text(encoding="utf-8")) == document
        assert path.stat().st_mode & 0o777 == 0o600
    assert len(paths.calls) == 3
    assert [argv[-1] for argv, _options in paths.calls] == [
        "--template=" + str(paths.home / "git-template"),
        ".github", "--message=Create synthetic offline harness",
    ]
    for argv, options in paths.calls:
        assert argv[:3] == ["/usr/bin/git", "-c", "core.hooksPath=/dev/null"]
        assert options == {
            "timeout": 15, "max_stdout_bytes": 16 * 1024, "env": guest_fixture.environment(),
        }
    assert list((paths.home / ".agent-bridge/providers.d").iterdir()) == []
    assert not (paths.state / "bridge/bridge.db").exists()
    assert not (paths.state / "dispatch/queue.db").exists()
    assert not (paths.evidence / "acp-peer.jsonl").exists()
    assert not (paths.workspace / "anchor/.git").exists()  # Git is simulated.


def test_subject_is_separate_from_source_and_explicitly_hosts(
    paths: SimpleNamespace,
) -> None:
    docs = guest_fixture.documents()
    subject = paths.workspace / "anchor"
    assert docs[subject / ".copilot-extensions/agent-worktrees/config.yaml"]["expose_agent"] is True
    assert all(guest_fixture.SOURCE not in path.parents for path in docs)
    agents = docs[paths.home / ".agent-bridge/acp-agents.json"]
    agent = agents[guest_fixture.PROJECT]
    assert agent["project"] == guest_fixture.PROJECT
    assert agent["copilot_path"] == str(paths.home / "bin/mock-acp")
    assert agent["copilot_args"] == []
    bridge = docs[paths.home / ".agent-bridge/config.yaml"]
    assert bridge["enable_credential_relay"] is False
    assert bridge["idle_reap_ttl_seconds"] == 0
    assert bridge["bind"] == "127.0.0.1" and bridge["port"] == 0


@pytest.mark.parametrize("retained", ["home", "bridge", "dispatch", "unknown"])
def test_retained_state_refuses_before_writes(paths: SimpleNamespace, retained: str) -> None:
    (paths.state / retained).mkdir()
    with pytest.raises(ValueError, match="original owner"):
        guest_fixture.prepare_fixture(paths.custody)
    assert not paths.calls
    if retained != "home":
        assert not paths.home.exists()


def test_retained_workspace_refuses(paths: SimpleNamespace) -> None:
    (paths.workspace / "original").write_text("retained", encoding="utf-8")
    with pytest.raises(ValueError, match="retained workspace"):
        guest_fixture.prepare_fixture(paths.custody)
    assert not paths.home.exists() and not paths.calls


@pytest.mark.parametrize("failure", ["exit", "exception"])
def test_failed_git_is_retained_and_never_automatically_retried(
    paths: SimpleNamespace, monkeypatch: Any, failure: str,
) -> None:
    def fail(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure == "exception":
            raise TimeoutError("synthetic capture timeout")
        return subprocess.CompletedProcess(argv, 17, "", "synthetic literal cause")

    monkeypatch.setattr(guest_fixture, "run", fail)
    with pytest.raises((RuntimeError, TimeoutError), match="synthetic"):
        guest_fixture.prepare_fixture(paths.custody)
    assert paths.home.is_dir()
    assert (paths.home / ".agent-bridge/config.yaml").is_file()
    with pytest.raises(ValueError, match="original owner"):
        guest_fixture.prepare_fixture(paths.custody)


@pytest.mark.parametrize("after", [0, 3, 10])
def test_lost_custody_is_not_success(
    paths: SimpleNamespace, after: int,
) -> None:
    calls = 0

    def owned() -> None:
        nonlocal calls
        calls += 1
        if calls > after:
            raise ValueError("synthetic lost operation")

    paths.custody.assert_owned = owned
    with pytest.raises(ValueError, match="lost operation"):
        guest_fixture.prepare_fixture(paths.custody)
    assert not paths.calls


def test_event_loop_refuses_before_writes(paths: SimpleNamespace) -> None:
    async def invoke() -> None:
        guest_fixture.prepare_fixture(paths.custody)

    with pytest.raises(RuntimeError, match="outside an event loop"):
        asyncio.run(invoke())
    assert not paths.home.exists() and not paths.calls


def test_exclusive_document_write_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("retained", encoding="utf-8")
    with pytest.raises(FileExistsError):
        guest_fixture._write_document(path, {})
    assert path.read_text() == "retained"


@pytest.mark.parametrize("kind", ["file", "link", "wrong-device", "wrong-mode"])
def test_private_directory_refuses_unsafe_posture(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "root"
    if kind == "file":
        path.touch()
    elif kind == "link":
        path.symlink_to(tmp_path, target_is_directory=True)
    else:
        path.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        REAL_PRIVATE_DIRECTORY(path, device=-1 if kind == "wrong-device" else None)
