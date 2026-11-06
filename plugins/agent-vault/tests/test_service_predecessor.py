"""Central-role ownership is separate from native identity-pinned retirement."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_procutil._daemon_instance_identity import path_identity
from agent_vault import service_predecessor as predecessor
from agent_vault.rendezvous import Endpoint


@pytest.fixture
def owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "versions" / "previous" / "agent_vault" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text("# fixture\n")
    device, inode = path_identity(tmp_path)
    record = dict(
        instance_id="a" * 32, pid="123", birth="native-birth", service="agent-vault",
        purpose="production", lifecycle="resident", owner="native-owner",
        owner_pid="456", owner_birth="owner-birth", run_id="", state="running",
        runtime_root=str(tmp_path), runtime_device=device, runtime_inode=inode,
        source=str(source), source_member="", protected="0",
    )
    monkeypatch.setattr(predecessor, "installation_id", lambda: "fixture-installation")
    monkeypatch.setattr(predecessor, "inspect_instances", lambda _: [dict(record)])
    monkeypatch.setattr(predecessor, "identity_state", lambda *args: "matching")
    monkeypatch.setattr(predecessor, "peer_identity", lambda _: SimpleNamespace())
    monkeypatch.setattr(predecessor, "_has_children", lambda _: False)
    endpoint = Endpoint("tcp", "127.0.0.1:12345", pid=123, installation_id="fixture-installation")
    return endpoint, tmp_path, record


@pytest.mark.parametrize("field,value", (
    ("service", "agent-bridge"), ("lifecycle", "finite-worker"),
    ("protected", "1"), ("state", "finished"), ("source_member", "foreign/archive.py"),
    ("purpose", "unattended-test"), ("runtime_inode", "changed"),
))
def test_foreign_or_protected_owner_is_never_a_retirement_target(
    owned, field: str, value: str,
) -> None:
    endpoint, root, record = owned
    record[field] = value
    assert predecessor.Predecessor.capture(endpoint, root) is None


@pytest.mark.parametrize("change", ("self", "installation", "client-source", "foreign-slot"))
def test_endpoint_identity_does_not_grant_central_role_custody(owned, change: str) -> None:
    endpoint, root, record = owned
    if change == "self":
        endpoint = Endpoint("tcp", endpoint.address, pid=os.getpid(), installation_id=endpoint.installation_id)
    elif change == "installation":
        endpoint = Endpoint("tcp", endpoint.address, pid=123, installation_id="foreign-installation")
    elif change == "client-source":
        path = Path(record["source"]).with_name("cli.py")
        path.touch()
        record["source"] = str(path)
    else:
        path = root / "foreign" / "agent_vault" / "service.py"
        path.parent.mkdir(parents=True)
        path.touch()
        record["source"] = str(path)
    assert predecessor.Predecessor.capture(endpoint, root) is None


@pytest.mark.parametrize("change", ("selection", "owner", "duplicate", "birth", "principal", "exit"))
def test_mutation_rechecks_owner_selection_and_native_birth(
    owned, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    endpoint, root, record = owned
    captured = predecessor.Predecessor.capture(endpoint, root)
    assert captured is not None
    events = []

    class Process:
        def __init__(self, pid: int, birth: str):
            assert (pid, birth) == (123, "native-birth")
            events.append("pin")
            if change == "birth":
                raise predecessor.RetirementRefused("fixture birth mismatch")

        def __enter__(self):
            if change == "owner":
                record["owner"] = "changed-owner"
            if change == "duplicate":
                monkeypatch.setattr(predecessor, "inspect_instances", lambda _: [dict(record), dict(record)])
            return self

        def __exit__(self, *_):
            events.append("close")

        def exited(self):
            return False

        def pause(self):
            pass

        def terminate(self, *, timeout: float):
            if change == "exit":
                raise predecessor.RetirementRefused("fixture unconfirmed exit")
            pytest.fail("An unqualified target reached native termination")

    monkeypatch.setattr(predecessor, "PinnedProcess", Process)
    selections = iter((True, False)) if change == "selection" else iter((True, True))
    if change == "principal":
        def refuse(_: int):
            raise RuntimeError("fixture principal mismatch")
        monkeypatch.setattr(predecessor, "peer_identity", refuse)
    result = captured.retire(lambda: next(selections))
    assert result.startswith("refused-")
    assert "pin" in events if change != "principal" else not events


def test_one_owned_predecessor_uses_its_native_pin_and_confirms_exit(
    owned, monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, root, _ = owned
    captured = predecessor.Predecessor.capture(endpoint, root)
    assert captured is not None
    events = []

    class Process:
        def __init__(self, pid: int, birth: str):
            events.append((pid, birth))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            events.append("closed")

        def exited(self):
            return False

        def pause(self):
            events.append("paused")

        def terminate(self, *, timeout: float):
            events.append(("terminate-and-confirm", timeout))

    monkeypatch.setattr(predecessor, "PinnedProcess", Process)
    assert captured.retire(lambda: True) == "cold-retired"
    assert events == [(123, "native-birth"), "paused", ("terminate-and-confirm", 5), "closed"]


def test_unavailable_native_identity_is_not_mistaken_for_exit(
    owned, monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, root, _ = owned
    captured = predecessor.Predecessor.capture(endpoint, root)
    assert captured is not None

    def unavailable(*args):
        raise OSError("fixture identity inaccessible")

    monkeypatch.setattr(predecessor, "identity_state", unavailable)
    assert not captured.exited()
    assert captured.retire(lambda: True) == "refused-native-retirement"


def test_reused_pid_is_positive_original_exit_not_a_signal_target(
    owned, monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, root, _ = owned
    captured = predecessor.Predecessor.capture(endpoint, root)
    assert captured is not None
    monkeypatch.setattr(predecessor, "identity_state", lambda *args: "reused")
    assert captured.exited()
    assert captured.retire(lambda: True) == "exited"


def test_live_credential_worker_refuses_parent_retirement_and_resumes_it(
    owned, monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, root, _ = owned
    captured = predecessor.Predecessor.capture(endpoint, root)
    assert captured is not None
    events = []

    class Process:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            events.append("resume-and-close")

        def exited(self):
            return False

        def pause(self):
            events.append("paused")

        def terminate(self, **kwargs):
            pytest.fail("A parent with live credential children was terminated")

    monkeypatch.setattr(predecessor, "PinnedProcess", Process)
    monkeypatch.setattr(predecessor, "_has_children", lambda _: True)
    assert captured.retire(lambda: True) == "refused-live-workers"
    assert events == ["paused", "resume-and-close"]


@pytest.mark.parametrize("image", ("system-console", "foreign-console", "backend"))
def test_worker_census_exempts_only_the_actual_system_console_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, image: str,
) -> None:
    import psutil

    system = tmp_path / "system" / "conhost.exe"
    system.parent.mkdir()
    system.touch()
    foreign = tmp_path / ("conhost.exe" if image == "foreign-console" else "backend.exe")
    foreign.touch()
    child = SimpleNamespace(exe=lambda: str(system if image == "system-console" else foreign))
    monkeypatch.setattr(psutil, "Process", lambda _: SimpleNamespace(children=lambda: [child]))
    monkeypatch.setattr(predecessor, "_console_host_path", lambda: system)
    samefile = Path.samefile

    def local_identity(path: Path, other) -> bool:
        assert path.parent == system.parent
        return samefile(path, other)

    monkeypatch.setattr(Path, "samefile", local_identity)
    assert predecessor._has_children(123) is (image != "system-console")
