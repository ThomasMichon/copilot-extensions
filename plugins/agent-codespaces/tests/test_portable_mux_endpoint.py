"""Portable endpoints retain exact native custody across Owner persistence/probes."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_codespaces import connection_owner as owner
from agent_codespaces import session_forwards as sf
from agent_codespaces import owner_inventory as inv, owner_mux_probe
from agent_codespaces.portable_mux_control import retained_endpoint
from venue_copilot.portable_mux import MuxEndpoint


ATTEMPT = "00000000-0000-4000-8000-000000000001"
SESSION = "00000000-0000-4000-8000-000000000002"


def endpoint(*, pending=False):
    return MuxEndpoint.parse({
        "version": 1, "attempt": ATTEMPT, "session_id": SESSION,
        "mux_session": "wt-bridge-" + ATTEMPT, "mux_socket": f"/tmp/p/{ATTEMPT}/tmux.sock",
        "native": None if pending else {"incarnation": "$1:123", "pane": "%2"},
        "socket_identity": None if pending else {"device": 1, "inode": 2},
    })


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "owner.lock")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "live.json")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


def test_pending_endpoint_is_retained_and_confirmed_without_changing_attempt(store):
    pending, confirmed = endpoint(pending=True), endpoint()
    initial = owner.hold(
        "cs", "t", mux_session=pending.mux_session, mux_endpoint=pending.record(), fresh=True,
    )
    generation = initial.sessions["t"]["generation"]
    again = owner.hold(
        "cs", "t", mux_session=confirmed.mux_session, mux_endpoint=confirmed.record(), confirmed=True,
    )
    assert again.sessions["t"]["generation"] == generation
    assert owner.get_hold("cs").sessions["t"]["mux_endpoint"] == confirmed.record()
    assert owner.hold("cs", "t", mux_session=confirmed.mux_session).sessions["t"]["mux_endpoint"] == confirmed.record()
    before = owner.OWNER_FILE.read_bytes()
    with pytest.raises(ValueError, match="replace"):
        owner.hold(
            "cs", "t", mux_session=confirmed.mux_session,
            mux_endpoint=replace(confirmed, inode=44).record(),
        )
    assert owner.OWNER_FILE.read_bytes() == before


def test_retained_provider_metadata_cannot_override_or_downgrade_owner_proof(store):
    value = endpoint()
    held = owner.hold(
        "cs", "t", mux_session=value.mux_session, mux_endpoint=value.record(), confirmed=True,
    )
    plan = {"codespace": "cs", "tenant": "t"}
    row = {"venue": {
        "kind": "codespace", "target": "cs", "mux_session_name": value.mux_session,
        "mux_endpoint": endpoint(pending=True).record(),
    }}
    assert retained_endpoint(plan, held, row) == value
    assert retained_endpoint(plan, None, row) == endpoint(pending=True)
    row["venue"]["mux_endpoint"] = replace(value, inode=44).record()
    with pytest.raises(ValueError, match="disagree"):
        retained_endpoint(plan, held, row)


@pytest.mark.parametrize("version", [None, 0, 1, True, "1"])
def test_portable_owner_contract_is_running_process_metadata(store, version):
    owner._write_liveness(10)
    raw = json.loads(owner.LIVE_FILE.read_text())
    assert raw["portable_mux_version"] == 1
    if version is None:
        raw.pop("portable_mux_version")
    else:
        raw["portable_mux_version"] = version
    owner.LIVE_FILE.write_text(json.dumps(raw))
    assert owner.read_liveness().portable_mux_version == (1 if type(version) is int and version == 1 else 0)


@pytest.mark.parametrize("bad", [
    {"version": 2}, None, "malformed",
])
async def test_malformed_persisted_endpoint_never_downgrades_to_default_socket(store, bad):
    value = endpoint()
    owner.hold("cs", "t", mux_session=value.mux_session, confirmed=True)
    raw = json.loads(owner.OWNER_FILE.read_text())
    raw["cs"]["sessions"]["t"]["mux_endpoint"] = bad
    owner.OWNER_FILE.write_text(json.dumps(raw))
    restored = owner.get_hold("cs")
    assert restored.sessions["t"]["mux_endpoint"] == bad
    calls = []

    async def probe(cs, targets):
        calls.extend(targets)
        return {target: False for target in targets}

    await sf.SessionForwards(lambda *a: None, probe).probe([restored])
    assert calls == []
    assert owner.get_hold("cs").sessions["t"]["mux_endpoint"] == bad


def test_fresh_launch_cannot_erase_pending_native_attempt(store):
    value = endpoint(pending=True)
    owner.hold("cs", "t", mux_session=value.mux_session, mux_endpoint=value.record(), fresh=True)
    before = owner.OWNER_FILE.read_bytes()
    with pytest.raises(ValueError, match="settled"):
        owner.hold("cs", "t", mux_session="other", fresh=True)
    assert owner.OWNER_FILE.read_bytes() == before


async def test_probe_keys_private_endpoint_separately_and_cannot_refresh_replacement(store, monkeypatch):
    value = endpoint()
    first = owner.hold(
        "cs", "t", mux_session=value.mux_session, mux_endpoint=value.record(), confirmed=True,
    )
    owner.hold("cs", "legacy", mux_session=value.mux_session, confirmed=True)
    saved = owner.get_hold("cs")

    async def probe(cs, targets):
        assert set(targets) == {value, value.mux_session}
        owner.release("cs", "t")
        replacement = owner.hold("cs", "t", mux_session="other", fresh=True)
        assert replacement.sessions["t"]["generation"] != first.sessions["t"]["generation"]
        return {value: True, value.mux_session: False}

    calls = []
    real = owner.heartbeat

    def heartbeat(*args, **kwargs):
        calls.append(kwargs["generation"])
        return real(*args, **kwargs)

    # The stale positive proof must retain the old generation, just like release.
    monkeypatch.setattr(sf, "heartbeat", heartbeat)
    await sf.SessionForwards(lambda *a: None, probe).probe([saved])
    current = owner.get_hold("cs")
    assert set(current.sessions) == {"t"}
    assert current.sessions["t"]["mux_session"] == "other"
    assert calls == [first.sessions["t"]["generation"]]


@pytest.mark.parametrize("state,output,code,expected", [
    ("Available", "present\n", 0, True), ("Available", "gone\n", 0, False),
    ("Available", "unknown\n", 0, None), ("Available", "gone\n", 1, None),
    ("Available", "gone\nextra\n", 0, None), ("Stopped", "", 0, False),
])
async def test_real_probe_uses_exact_endpoint_and_preserves_no_wake(state, output, code, expected):
    value = endpoint()
    calls = []

    class Manager:
        async def exec_command(self, cs, command, **kwargs):
            calls.append(command)
            assert value.mux_socket in command
            assert "has-session" not in command
            return SimpleNamespace(
                exit_code=code, stdout=f"ACMUX1\t0\t{output.strip()}\nACMUX1\tDONE\t1\n", stderr="",
            )

        async def disconnect(self, cs):
            pass

    async def opener(cs):
        assert state == "Available"
        return Manager()

    scope = inv.InventoryScope("source", "config", ("a",), ("x",))
    admitted = owner_mux_probe.RemoteMuxProbe(
        inventory_reader=lambda wave: inv.OwnerInventory(wave, scope, (
            inv.AccountListing("a", (inv.InventoryRow("cs", state, "a"),), 0.0, True),
        )),
        scope_reader=lambda: scope, open_manager=opener, clock=lambda: 1.0,
    )
    probe = sf.make_remote_mux_probe(
        list_codespaces=lambda: pytest.fail("portable observation cannot use the legacy partial listing"),
        open_manager=opener, portable_probe=admitted,
    )
    assert await probe("cs", [value]) == {value: expected}
    assert bool(calls) == (state == "Available")


@pytest.mark.parametrize("action", ["observe", "tail", "stop", "attach"])
def test_pending_endpoint_refuses_all_native_control(action):
    with pytest.raises(ValueError, match="unconfirmed"):
        endpoint(pending=True).command(action)


@pytest.mark.parametrize("action,changed,listing,expected", [
    ("observe", False, "matching", "present"),
    ("observe", False, "renamed", "present"),
    ("observe", False, "absent", "gone"),
    ("observe", False, "replacement", "unknown"),
    ("observe", True, "matching", "unknown"),
    ("stop", False, "matching", "STOPPED"),
    ("tail", False, "matching", "line2\nline3"),
    ("stop", True, "matching", "refused"),
    ("stop", False, "replacement", "refused"),
])
def test_transported_control_checks_socket_and_native_incarnation(
    monkeypatch, capsys, action, changed, listing, expected,
):
    value = endpoint()
    argv = shlex.split(value.command(action, lines=2))
    monkeypatch.setattr(sys, "argv", ["-c", *argv[5:]])
    calls = []

    def lstat(path):
        if str(path).replace("\\", "/").endswith("tmux.sock"):
            return SimpleNamespace(
                st_mode=stat.S_IFSOCK | 0o700, st_dev=1, st_ino=99 if changed else 2,
                st_ctime_ns=999,
            )
        return SimpleNamespace(st_mode=stat.S_IFDIR)

    def run(command, **kwargs):
        calls.append(command)
        assert command[:3] == ["tmux", "-S", value.mux_socket]
        if command[3] == "list-panes":
            row = {
                "matching": f"{value.mux_session}|$1:123|%2\n",
                "renamed": "human-label|$1:123|%2\n",
                "replacement": f"{value.mux_session}|$9:999|%8\n",
                "absent": "another|$5:111|%4\n",
            }[listing]
            return SimpleNamespace(returncode=0, stdout=row)
        assert command[3:] in (
            ["kill-session", "-t", "$1"], ["capture-pane", "-p", "-t", "%2", "-S", "-2"],
        )
        return SimpleNamespace(returncode=0, stdout="line1\nline2\nline3\n")

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(subprocess, "run", run)
    if expected == "refused":
        with pytest.raises(SystemExit):
            exec(compile(argv[4], "<portable-mux-control>", "exec"), {})
    else:
        exec(compile(argv[4], "<portable-mux-control>", "exec"), {})
        assert capsys.readouterr().out.strip() == expected
    assert len(calls) == (0 if changed else 2 if action in {"stop", "tail"} and listing == "matching" else 1)
