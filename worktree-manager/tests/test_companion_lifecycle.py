"""Observer lifecycle evidence cannot mutate or adopt a replacement CLI."""

from __future__ import annotations

from copy import deepcopy
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import companion_lifecycle as lifecycle
from worktree_manager.mux_mapping_registry import register_mapping


@pytest.fixture
def owner(monkeypatch, tmp_path):
    lifecycle._bindings.clear()
    lifecycle._ended.clear()
    mapping = {
        "project": "example", "worktree_id": "one", "live": True,
        "mux_bin": "owned-mux", "mux_session": "managed", "session_incarnation": "$7:100",
        "worktree_path": str(tmp_path), "mapping_revision": 1,
        "panes": [{"pane_id": "%42", "role": "cli", "live": True}],
    }
    state = SimpleNamespace(
        mapping=mapping, raw=f"%42\t0\tmanaged\t$7:100\t{tmp_path}",
        calls=[],
    )
    monkeypatch.setattr(lifecycle, "get_mapping", lambda *args: state.mapping)

    def native(mapping, *arguments):
        state.calls.append((deepcopy(mapping), arguments))
        return state.raw

    monkeypatch.setattr(lifecycle, "_call", native)
    yield state
    lifecycle._bindings.clear()
    lifecycle._ended.clear()


def test_exact_live_cli_is_read_only_and_uses_only_native_inventory(owner):
    original = deepcopy(owner.mapping)
    assert lifecycle.cli_alive("example", "one", "%42") is True
    assert owner.mapping == original
    assert len(owner.calls) == 1
    assert owner.calls[0][1][:5] == ("list-panes", "-s", "-t", "=managed", "-F")
    assert "session_path" not in owner.calls[0][1][-1]


@pytest.mark.parametrize("flags,expected", [
    ("0\t1\t1\t1\t1", True),
    ("0\t0\t1\t0\t1", True),
    ("0\t0\t1\t1\t1", False),
    ("0\t1\t0\t1\t1", False),
    ("1\t1\t1\t1\t1", False),
    ("0\t1\t1\t1\t", None),
    ("0\tmaybe\t1\t1\t1", None),
])
def test_visibility_requires_native_owner_and_visible_attached_pane(owner, monkeypatch, flags, expected):
    calls = []

    def native(mapping, *args, **kw):
        calls.append((args, kw))
        assert args[0] == "display-message"
        return "%99\t$7:100\t" + flags

    monkeypatch.setattr(lifecycle, "_call", native)
    assert lifecycle.observer_visible("example", "one", "%99", "$7:100") is expected
    assert all(call[1] == {"timeout": 2} for call in calls)
    assert lifecycle.observer_visible("example", "one", None, "$7:100") is None
    assert lifecycle.observer_visible("example", "one", "%99", "$8:200") is None
    assert len(calls) == 1


def test_detached_visibility_ignores_synthetic_client_listings(owner, monkeypatch):
    def native(mapping, *args, **kw):
        assert args[0] == "display-message"
        return "%99\t$7:100\t0\t1\t1\t1\t0"

    monkeypatch.setattr(lifecycle, "_call", native)
    assert lifecycle.observer_visible("example", "one", "%99", "$7:100") is False


@pytest.mark.parametrize("case", [
    "missing-cli", "missing-cli-observer-without-path", "dead-cli",
    "dead-cli-without-path", "empty-native", "native-successor",
    "registry-successor", "registry-dead-cli", "registry-tombstone",
])
def test_positive_owner_end_is_terminal_even_if_pane_id_is_recycled(owner, case):
    assert lifecycle.cli_alive("example", "one", "%42") is True
    original_mapping, original_raw = deepcopy(owner.mapping), owner.raw
    if case == "missing-cli":
        owner.raw = owner.raw.replace("%42", "%99")
    elif case == "missing-cli-observer-without-path":
        owner.raw = owner.raw.replace("%42", "%99").rsplit("\t", 1)[0] + "\t"
    elif case == "dead-cli":
        owner.raw = owner.raw.replace("\t0\t", "\t1\t")
    elif case == "dead-cli-without-path":
        owner.raw = owner.raw.replace("\t0\t", "\t1\t").rsplit("\t", 1)[0] + "\t"
    elif case == "empty-native":
        owner.raw = ""
    elif case == "native-successor":
        owner.raw = owner.raw.replace("$7:100", "$8:200")
    elif case == "registry-successor":
        owner.mapping["session_incarnation"] = "$8:200"
    elif case == "registry-dead-cli":
        owner.mapping["panes"][0]["live"] = False
    elif case == "registry-tombstone":
        owner.mapping["live"] = False
    assert lifecycle.cli_alive("example", "one", "%42") is False
    owner.mapping, owner.raw = original_mapping, original_raw
    assert lifecycle.cli_alive("example", "one", "%42") is False


def test_different_registered_cli_is_positive_displacement(owner):
    owner.mapping["panes"][0]["pane_id"] = "%99"
    assert lifecycle.cli_alive("example", "one", "%42") is False
    assert owner.calls == []


@pytest.mark.parametrize("case", [
    "missing-registry", "malformed-registry", "unknown-live", "missing-panes",
    "ambiguous-cli", "unknown-cli-live", "malformed-native", "unknown-dead",
    "duplicate-native", "wrong-native-session", "missing-native-path", "wrong-native-path",
    "mixed-incarnations",
])
def test_uncertainty_never_infers_death_and_can_recover(owner, case):
    original_mapping, original_raw = deepcopy(owner.mapping), owner.raw
    if case == "missing-registry":
        owner.mapping = None
    elif case == "malformed-registry":
        owner.mapping = {"live": False}
    elif case == "unknown-live":
        owner.mapping["live"] = None
    elif case == "missing-panes":
        owner.mapping["panes"] = []
    elif case == "ambiguous-cli":
        owner.mapping["panes"] *= 2
    elif case == "unknown-cli-live":
        owner.mapping["panes"][0]["live"] = None
    elif case == "malformed-native":
        owner.raw = "unexpected output"
    elif case == "unknown-dead":
        owner.raw = owner.raw.replace("\t0\t", "\t?\t")
    elif case == "duplicate-native":
        owner.raw += "\n" + owner.raw
    elif case == "wrong-native-session":
        owner.raw = owner.raw.replace("managed", "unrelated")
    elif case == "missing-native-path":
        owner.raw = owner.raw.rsplit("\t", 1)[0] + "\t"
    elif case == "wrong-native-path":
        owner.raw += "-different"
    elif case == "mixed-incarnations":
        owner.raw += "\n" + owner.raw.replace("%42", "%99").replace("$7:100", "$8:200")
    assert lifecycle.cli_alive("example", "one", "%42") is None
    owner.mapping, owner.raw = original_mapping, original_raw
    assert lifecycle.cli_alive("example", "one", "%42") is True


@pytest.mark.parametrize("source", ["registry", "native"])
@pytest.mark.parametrize("error", [OSError("unavailable"), RuntimeError("job unavailable"),
                                  subprocess.TimeoutExpired("owned-mux", 5), ValueError("invalid")])
def test_probe_or_registry_failure_is_unknown(owner, monkeypatch, source, error):
    def unavailable(*args):
        raise error

    monkeypatch.setattr(lifecycle, "get_mapping" if source == "registry" else "_call", unavailable)
    assert lifecycle.cli_alive("example", "one", "%42") is None


def test_registry_refresh_same_identity_is_not_a_successor(owner):
    assert lifecycle.cli_alive("example", "one", "%42") is True
    owner.mapping["mapping_revision"] += 1
    owner.mapping["observed_at"] = "refreshed"
    assert lifecycle.cli_alive("example", "one", "%42") is True


def test_pinned_companions_resolve_their_own_panes_during_handoff(owner):
    predecessor = owner.mapping["panes"][0]
    successor = {"pane_id": "%99", "role": "cli", "live": True}
    owner.mapping["panes"].append(successor)
    owner.raw += "\n" + owner.raw.replace("%42", "%99")
    assert lifecycle.cli_alive("example", "one", "%42") is True
    assert lifecycle.cli_alive("example", "one", "%99") is True
    predecessor["live"] = False
    owner.mapping["observed_end"] = {
        "pane_id": "%42", "session_incarnation": "$7:100", "exit_code": 0,
        "observed_at": "2026-01-01T00:00:00+00:00",
    }
    assert lifecycle.cli_alive("example", "one", "%42") is False
    assert lifecycle.cli_alive("example", "one", "%99") is True


def test_equivalent_mux_path_does_not_displace_a_pinned_companion(owner, tmp_path, monkeypatch):
    from worktree_manager import mux_mapping_registry

    executable = tmp_path / "owned-mux"
    executable.touch()
    monkeypatch.setattr(mux_mapping_registry.shutil, "which", lambda _name: str(executable))
    assert lifecycle.cli_alive("example", "one", "%42") is True
    owner.mapping["mux_bin"] = str(executable)
    assert lifecycle.cli_alive("example", "one", "%42") is True


def test_initial_incarnation_mismatch_refuses_recycled_pane_before_binding_or_probe(owner):
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$6:90") is False
    assert lifecycle._bindings == {}
    assert owner.calls == []


def test_initial_expected_incarnation_accepts_exact_owner(owner):
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is True


@pytest.mark.parametrize("expected", ["", "7:100", "$7:invalid", "$7:100\n", 42])
def test_malformed_expected_incarnation_is_unknown_before_binding_or_probe(owner, expected):
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation=expected) is None
    assert lifecycle._bindings == {}
    assert owner.calls == []


@pytest.mark.parametrize("metadata", ["empty-panes", "no-panes", "missing-registry"])
def test_bound_owner_still_polls_native_after_cli_metadata_is_lost(owner, metadata, monkeypatch):
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is True
    if metadata == "empty-panes":
        owner.mapping["panes"] = []
    elif metadata == "no-panes":
        owner.mapping.pop("panes")
    else:
        owner.mapping = None
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is True
    native = lifecycle._call

    def unavailable(*args):
        raise subprocess.TimeoutExpired("owned-mux", 5)

    monkeypatch.setattr(lifecycle, "_call", unavailable)
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is None
    monkeypatch.setattr(lifecycle, "_call", native)
    owner.raw = owner.raw.replace("%42", "%99")
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is False


def test_timeout_empty_reconnect_then_cli_exit_cannot_adopt_a_relaunch(owner, tmp_path, monkeypatch):
    from worktree_manager import mux_daemon, mux_daemon_cli, mux_mapping_registry as registry

    monkeypatch.setattr(registry, "default_root", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "get_mapping", registry.get_mapping)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(mux_daemon, "publish_live_observation", lambda *args, **kwargs: {"applied": True})
    payload = deepcopy(owner.mapping)
    payload.pop("mapping_revision")
    registry.register_next_mapping(payload)
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is True

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("owned-mux", 2)

    monkeypatch.setattr(mux_daemon_cli, "run_mux_child", timeout)
    assert mux_daemon_cli._register_reconnect(payload)["applied"]
    assert registry.get_mapping("example", "one")["panes"] == payload["panes"]
    # An already-issued older callback may have persisted empty CLI metadata.
    registry.register_next_mapping(dict(payload, panes=[]))
    native = lifecycle._call
    monkeypatch.setattr(lifecycle, "_call", timeout)
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is None
    monkeypatch.setattr(lifecycle, "_call", native)
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is True
    owner.raw = owner.raw.replace("%42", "%99")
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is False
    registry.register_next_mapping(dict(payload, session_incarnation="$8:200"))
    owner.raw = owner.raw.replace("%99", "%42").replace("$7:100", "$8:200")
    assert lifecycle.cli_alive("example", "one", "%42", session_incarnation="$7:100") is False


def test_owned_observer_process_exits_after_cli_process_exits(tmp_path):
    mapping = {
        "project": "example", "worktree_id": "one", "live": True,
        "mux_bin": "owned-mux", "mux_session": "managed", "session_incarnation": "$7:100",
        "worktree_path": str(tmp_path), "mapping_revision": 1,
        "panes": [{"pane_id": "%42", "role": "cli", "live": True}],
    }
    assert register_mapping(mapping, root=tmp_path)["applied"]
    inventory, ready = tmp_path / "native-inventory", tmp_path / "observer-ready"
    imported = tmp_path / "observer-imported"
    inventory.write_text(f"%42\t0\tmanaged\t$7:100\t{tmp_path}", encoding="utf-8")
    program = """
import sys, time
from pathlib import Path
from worktree_manager import companion_lifecycle as lifecycle
inventory, ready, imported = map(Path, sys.argv[1:])
lifecycle._call = lambda *args: inventory.read_text(encoding='utf-8')
imported.touch()
for iteration in range(200):
    result = lifecycle.cli_alive('example', 'one', '%42')
    if result is False:
        raise SystemExit(0)
    if result is True:
        ready.touch()
    time.sleep(0.025)
raise SystemExit(9)
"""
    cli = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **no_window_kwargs(),
    )
    observer = None
    try:
        observer = subprocess.Popen(
            [sys.executable, "-c", program, str(inventory), str(ready), str(imported)],
            env=dict(os.environ, WORKTREE_MANAGER_ROOT=str(tmp_path)),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
        )
        # This control measures owner-end handling, not cold package imports.
        import_deadline = time.monotonic() + 10
        while not imported.is_file() and observer.poll() is None and time.monotonic() < import_deadline:
            time.sleep(0.025)
        assert imported.is_file(), "observer dependencies did not initialize"
        deadline = time.monotonic() + 4
        while not ready.is_file() and observer.poll() is None and time.monotonic() < deadline:
            time.sleep(0.025)
        assert ready.is_file()
        assert observer.poll() is None
        assert cli.poll() is None
        cli.stdin.close()
        assert cli.wait(timeout=3) == 0
        # The native adapter fixture reports that only Summary remains.
        inventory.write_text(f"%99\t0\tmanaged\t$7:100\t{tmp_path}", encoding="utf-8")
        stdout, stderr = observer.communicate(timeout=4)
        assert observer.returncode == 0, stdout + stderr
        assert (tmp_path / "mux-mapping.json").is_file()
    finally:
        for process in (observer, cli):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
