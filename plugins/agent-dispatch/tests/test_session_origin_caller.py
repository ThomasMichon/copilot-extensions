"""CLI acquisition consumes the reservation's origin, never workspace history."""

from __future__ import annotations

from dataclasses import asdict
import argparse
import contextlib
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

from agent_dispatch import bridge, bridge_owner_launch, session_acquisition, session_origin
from agent_dispatch.client import DispatchError
from agent_dispatch.ordinary_bridge import reserve_for_backend
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import pool, publish, task

q = controls.q
api = controls.api


@pytest.fixture
def peer(monkeypatch):
    calls = []
    supported = {"cli": True, "daemon": True}
    prefix = ("selected-bridge", "immutable-build")
    monkeypatch.setattr(session_origin, "retained_selection", lambda: prefix)

    def observe(argv, **kwargs):
        assert argv[:2] == list(prefix)
        assert kwargs["env"]["AGENT_BRIDGE_CLIENT_NO_ENSURE"] == "1"
        assert kwargs["timeout"] == 5 and kwargs["max_output"] == 64 * 1024
        calls.append(argv)
        if argv[3:] == ["version", "--contracts-json"]:
            value = {
                "schema": "agent-bridge.cli-contracts", "version": 1,
                "contracts": {"agent-bridge.acquire": {
                    "version": 1,
                    "capabilities": ["dispatch-cli-session-origin"] if supported["cli"] else [],
                }, "agent-bridge.create": {
                    "version": 1,
                    "capabilities": ["keyed-dispatch-origin"] if supported["cli"] else [],
                }},
            }
        else:
            assert argv[3:] == ["status"]
            value = {
                "service": "agent-bridge", "status": "ok", "ready": True, "draining": False,
                "capabilities": {
                    "dispatch_cli_session_origin": supported["daemon"],
                    "dispatch_acp_session_origin": supported["daemon"],
                },
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(session_origin, "run_background_capture", observe)
    supported["daemon"] = 1
    return calls, supported, prefix


def test_real_placement_seals_current_origin_before_workspace_and_sends_same_snapshot(q, api, monkeypatch, peer):
    calls, supported, prefix = peer
    publish(q, pool())
    requested = task(q)
    requests = []
    with controls.connected(api, requests) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
            instance=("worker", "host-a", "linux"),
        )
        reservation = placed["reservation"]
        assert reservation["session_origin_transport"] == "origin-v1"
        assert reservation["worktree"] is None
        origin = copy.deepcopy(reservation["session_origin"])
        assert origin["cohort"]["pool"]["reservation_key"] == reservation["key"]
        assert calls == [
            [*prefix, "--json", "version", "--contracts-json"], [*prefix, "--json", "status"],
        ]
        sent = []

        def acquire(argv, **kwargs):
            payload = json.loads(kwargs["input"])
            sent.append(payload)
            assert argv == [*prefix, "acquire", "--request-file", "-"]
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                **{name: payload[name] for name in (
                    "mode", "task_id", "task_generation", "worktree_id", "delivery_id",
                )},
                "session_id": "native", "native_session_id": "native", "acquired": "created",
            }), "")

        monkeypatch.setattr(session_acquisition.subprocess, "run", acquire)
        prepared = {"path": "/workspace", "project": "example"}
        sample = session_acquisition.spawn_snapshot(
            {**asdict(q.get(requested.id)), "session_origin": {"mutable": "wrong"}},
            reservation, "reused-worktree", prepared, "reused", False,
        )
        reservation["session_origin"]["cohort"]["pool"]["registration_id"] = "mutated"
        session_acquisition.acquire(sample, mode="cli", prompt="work")
        session_acquisition.acquire(sample, mode="cli", prompt="work")
        assert sent[0] == sent[1]
        assert sent[0]["dispatch_origin"] == origin
        assert len(calls) == 6
        supported["daemon"] = 0
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="cannot be replayed"):
            session_acquisition.acquire(sample, mode="cli", prompt="work")
        assert len(sent) == 2
    assert sum(request.url.path == "/pool-placements" and request.method == "POST" for request in requests) == 1


@pytest.mark.parametrize("old", ["cli", "daemon"])
def test_unsupported_peer_keeps_legacy_reservation_before_effects(q, api, peer, old):
    calls, supported, _ = peer
    supported[old] = False if old == "cli" else True
    requested = task(q)
    requests = []
    with controls.connected(api, requests) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
        )
    assert placed["reservation"]["session_origin_transport"] == "baseline"
    posted = next(request for request in requests if request.method == "POST")
    assert "session_origin_transport" not in json.loads(posted.content)
    assert len(calls) == (1 if old == "cli" else 2)


@pytest.mark.parametrize("versions", [None, [], [True], [2]])
def test_old_coordinator_omits_option_without_probing_bridge(q, api, peer, versions):
    calls, _, _ = peer
    requested = task(q)
    requests = []

    def rewrite(request, response):
        if request.url.path == "/pool-placements/contract":
            value = response.json()
            value["session_origin_reservation_versions"] = versions
            return httpx.Response(200, json=value)
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
        )
    assert placed["reservation"]["session_origin_transport"] == "baseline"
    assert not calls
    assert "session_origin_transport" not in json.loads(next(
        request.content for request in requests if request.method == "POST"
    ))


def test_new_cli_attempt_in_old_workspace_does_not_inherit_its_origin(q, api, peer):
    requested = task(q)
    first, _ = q.reserve_spawn(requested.id, driver="cli")
    q.record_spawn_worktree(first.key, "old-worktree", ownership="created", driver="cli")
    q.fail_spawn(first.key, detail="before launch")
    with controls.connected(api, []) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
        )
    reservation = placed["reservation"]
    assert reservation["worktree"] == "old-worktree"
    assert reservation["allocation_reservation_key"] == first.key
    assert reservation["session_origin"]["reservation_key"] == reservation["key"] != first.key
    assert reservation["session_origin_transport"] == "origin-v1"


@pytest.mark.parametrize("change", ["task", "reservation", "cohort", "transport", "mode"])
def test_bad_sealed_origin_never_reaches_acquisition(q, monkeypatch, peer, change):
    requested = task(q)
    reservation, _ = q.reserve_spawn(requested.id, session_origin_transport="origin-v1")
    sample = session_acquisition.spawn_snapshot(
        asdict(requested), asdict(reservation), "workspace", {"path": "/work", "project": "example"},
        "created", False,
    )
    mode = "cli"
    if change == "task":
        sample["spawn_session_origin"]["task_id"] = "another"
    elif change == "reservation":
        sample["spawn_session_origin_reservation_key"] = "another"
    elif change == "cohort":
        sample["spawn_session_origin"]["cohort"]["version"] = True
    elif change == "transport":
        sample["spawn_session_origin_transport"] = "unknown"
    else:
        mode = "acp"
    monkeypatch.setattr(session_acquisition.subprocess, "run", lambda *a, **k: pytest.fail("native effect"))
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="unconfirmed"):
        session_acquisition.acquire(sample, mode=mode, prompt="work")
    assert not peer[0]


def test_baseline_never_forwards_unsealed_mutable_task_metadata(monkeypatch, peer):
    from tests.test_session_acquisition import task as sample_task

    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["legacy"])
    sent = []

    def run(argv, **kwargs):
        sent.append(json.loads(kwargs["input"]))
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="outcome requires reconciliation"):
        session_acquisition.acquire(sample_task(dispatch_origin={"mutable": "wrong"}), mode="cli", prompt="work")
    assert "dispatch_origin" not in sent[0]
    assert not peer[0]


def test_ordinary_and_restricted_paths_do_not_enroll(q, api, peer):
    requested = task(q)
    with controls.connected(api, []) as client:
        placed = reserve_for_backend(client, asdict(requested), "worker", ordinary=True)
    assert placed["reservation"]["session_origin_transport"] == "baseline"
    assert not peer[0]


@pytest.mark.parametrize("original", [None, "baseline", "origin-v1"])
def test_ordinary_preference_preserves_original_keyed_wire_choice(q, api, original):
    requested = task(q)
    first, _ = q.reserve_spawn(
        requested.id, driver="ordinary-bridge", session_origin_transport=original,
    )
    q.defer_spawn(first.key, detail="original keyed result unknown")
    with controls.connected(api, []) as client:
        retry = client.reserve_spawn(
            requested.id, driver="ordinary-bridge", session_origin_transport="prefer-origin-v1",
        )["reservation"]
        replay = client.reserve_spawn(
            requested.id, driver="ordinary-bridge", session_origin_transport="prefer-origin-v1",
        )["reservation"]
    assert replay["key"] == retry["key"]
    assert retry["session_origin_transport"] == (original or "baseline")
    assert retry["session_origin"] == first.session_origin
    assert retry["session_origin_reservation_key"] == first.key
    assert retry["creation_key"] == first.creation_key


def test_managed_ordinary_selects_preference_before_claim(q, api, peer):
    publish(q, pool())
    requested = task(q)
    requests = []
    with controls.connected(api, requests) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=True,
            instance=("worker", "host-a", "linux"),
        )
    reservation = placed["reservation"]
    assert reservation["session_origin_transport"] == "origin-v1"
    assert reservation["session_origin"]["cohort"]["pool"]["reservation_key"] == reservation["key"]
    assert not reservation["worktree"]
    posted = next(request for request in requests if request.url.path == "/pool-placements")
    assert json.loads(posted.content)["session_origin_transport"] == "prefer-origin-v1"
    assert len(peer[0]) == 2


def test_preference_refuses_version_one_provider_before_post(q, api, peer):
    requested = task(q)
    requests = []

    def rewrite(request, response):
        if request.url.path == "/pool-placements/contract":
            value = response.json()
            value["session_origin_reservation_versions"] = [1]
            return httpx.Response(200, json=value)
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        assert session_origin.reservation_options(client, ordinary=True) == {}
        with pytest.raises(DispatchError, match="version 2"):
            client.reserve_spawn(requested.id, session_origin_transport="prefer-origin-v1")
    assert all(request.method == "GET" for request in requests)
    assert not peer[0]
    assert q.list_reservations(task_id=requested.id) == []


def test_ordinary_lost_reply_and_rollback_preserve_origin_form_and_cleanup(q, peer):
    requested = task(q)
    original, _ = q.reserve_spawn(
        requested.id, driver="ordinary-bridge", session_origin_transport="prefer-origin-v1",
    )
    reservation = {**asdict(original), "worktree": "workspace"}
    calls = []

    def run(arguments, **kwargs):
        path = Path(arguments[arguments.index("--dispatch-origin-file") + 1])
        assert json.loads(path.read_text(encoding="utf-8")) == original.session_origin
        assert kwargs["execution_prefix"] == list(peer[2])
        assert kwargs["prompt_input"] == "writer-input"
        calls.append(path)
        raise subprocess.TimeoutExpired(arguments, 1)

    with pytest.raises(subprocess.TimeoutExpired):
        session_origin.create(run, reservation, ["--json", "create", "local"], prompt_input="writer-input")
    assert len(calls) == 1 and not calls[0].exists()
    peer[1]["daemon"] = 0
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="cannot be replayed"):
        session_origin.create(run, reservation, ["--json", "create", "local"], prompt_input="writer-input")
    assert len(calls) == 1


@pytest.mark.parametrize("caller", ["create", "interactive"])
def test_direct_callers_select_before_the_first_workspace_effect(q, api, monkeypatch, peer, caller):
    from agent_dispatch import create_cli, embody, interactive_embody

    requested = task(q)
    observed = []

    def prepare(current, reservation, **kwargs):
        observed.append(copy.deepcopy(reservation))
        raise RuntimeError("stop at workspace boundary")

    monkeypatch.setattr(embody, "prepare_reusable_worktree", prepare)
    with controls.connected(api, []) as client:
        if caller == "create":
            monkeypatch.setattr(create_cli._core(), "_client", lambda _: contextlib.nullcontext(client))
            monkeypatch.setattr(create_cli._core(), "_spawn_route", lambda _: "")
            operation = lambda: create_cli._spawn_worker_for(
                argparse.Namespace(spawn_backend="embody"), asdict(requested),
            )
        else:
            operation = lambda: interactive_embody.launch_interactive_embodiment(
                client, requested.id, machine="host-a",
            )
        with pytest.raises(RuntimeError, match="stop at workspace boundary"):
            operation()
    assert len(observed) == 1
    reservation = observed[0]
    assert reservation["session_origin_transport"] == "origin-v1"
    assert reservation["session_origin"]["reservation_key"] == reservation["key"]
    assert q.get_reservation(reservation["key"]).session_origin == reservation["session_origin"]
    assert len(peer[0]) == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_real_selected_bridge_process_keeps_original_build_and_ownership(tmp_path, monkeypatch, legacy):
    from tests.test_procutil import (
        FIRST_MARKETPLACE_ID, _make_live_peer, _make_namespaced_context, _update_receipt,
    )
    from agent_dispatch import peer_launch, procutil

    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    own = _make_namespaced_context(cell)
    first = _make_live_peer(cell, "agent-bridge", "1.0.0", nested=True)
    _make_live_peer(cell, "agent-bridge", "2.0.0", nested=True)
    peer_root = cell / "plugins" / "agent-bridge"
    scripts = Path(__file__).resolve().parents[2] / "agent-bridge" / "scripts"
    for version in ("1.0.0", "2.0.0"):
        slot = peer_root / "versions" / version
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir()
        for source in (scripts / "runtime_launcher.py", *scripts.glob("versioned_runtime*.py")):
            shutil.copyfile(source, bootstrap / source.name)
        (bootstrap / "runtime-generation").write_text("1" * 32, encoding="ascii")
        _update_receipt(slot / ".install-complete.json", lambda value: value.update(payload_hash="a" * 64))
    monkeypatch.setattr(peer_launch, "_peer_runtimes", {})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if legacy:
        relative = first.relative_to(peer_root)
        destination = tmp_path / ".agent-bridge"
        peer_root.rename(destination)
        peer_root, first = destination, destination / relative
        monkeypatch.delenv(peer_launch.CONTEXT_ENV, raising=False)
        monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / ".agent-dispatch"))
    else:
        monkeypatch.setenv(peer_launch.CONTEXT_ENV, str(own))
        monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(own.parent))
    monkeypatch.setattr(procutil.shutil, "which", lambda _: pytest.fail("ambient PATH lookup"))
    (peer_root / "current-version").write_text("1.0.0", encoding="utf-8")
    pinned = bridge_owner_launch.retained_selection()
    (peer_root / "current-version").write_text("2.0.0", encoding="utf-8")
    observed = subprocess.run(
        [*pinned, "probe"], capture_output=True, encoding="utf-8", timeout=20,
        **procutil.no_window_kwargs(),
    )
    assert observed.returncode == 0, observed.stderr
    value = json.loads(observed.stdout)
    assert value["python"] == str(first) and value["isolated"] == 1
    streams = subprocess.run(
        [*pinned, "streams"], input="retained origin input\n", capture_output=True,
        encoding="utf-8", timeout=20, **procutil.no_window_kwargs(),
    )
    assert (streams.returncode, streams.stdout, streams.stderr) == (
        7, "retained origin input\n", "peer-stderr\n",
    )
    if legacy:
        (peer_root / ".installation-ownership.json").write_text("{}", encoding="utf-8")
    else:
        _update_receipt(peer_root / "installation-activation.json", lambda value: value.update(state="inactive"))
    sentinel = tmp_path / "unexpected-native"
    refused = subprocess.run(
        [*pinned, "probe"], env={**os.environ, "PEER_EXECUTION_SENTINEL": str(sentinel)},
        capture_output=True, encoding="utf-8", timeout=20, **procutil.no_window_kwargs(),
    )
    assert refused.returncode != 0 and not sentinel.exists()
