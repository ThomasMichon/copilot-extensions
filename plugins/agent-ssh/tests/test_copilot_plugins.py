"""Source-qualified SSH CLI registration and retained seed admission."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_flags
from venue_copilot.refs import owned_delivery_journal, owned_result

from agent_ssh import copilot_delivery, copilot_detach, copilot_plugins


def _checkout(root, *, plugin=False, enabled=True, absolute=False):
    subprocess.run(
        ["git", "init", "--quiet", str(root)], check=True, capture_output=True,
        timeout=20, creationflags=no_window_flags(),
    )
    if not plugin:
        return
    payload = root / ".ai" / "sample"
    payload.mkdir(parents=True, exist_ok=True)
    (payload / "plugin.json").write_text(json.dumps({"name": "sample", "version": "1.0.0"}))
    (payload / "marker").write_text(root.name)
    (payload.parent / "marketplace.json").write_text(json.dumps({
        "name": "local", "owner": {"name": "Synthetic"},
        "plugins": [{"name": "sample", "source": "./sample"}],
    }))
    settings = root / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {"local": {"source": {
            "source": "directory", "path": str(payload.parent) if absolute else "./.ai",
        }}},
        "enabledPlugins": {"sample@local": enabled},
    }))


class _Receiver:
    def __init__(self, cwd, environment):
        self.cwd, self.environment, self.calls = cwd, environment, []

    def run_input(self, command, stdin, *, timeout):
        self.calls.append((command, stdin, timeout))
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-X", "utf8", "-u", "-"],
            input=stdin, cwd=self.cwd, env=self.environment, capture_output=True,
            timeout=timeout, creationflags=no_window_flags(),
        )
        return result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8")


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    controller, child, home, daemon = [tmp_path / name for name in ("controller", "child", "home", "daemon")]
    _checkout(controller, plugin=True)
    _checkout(child)
    home.mkdir()
    daemon.mkdir()
    (child / "nested").mkdir()
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_HOME": str(home / ".copilot"),
    }
    adapter = _Receiver(daemon, environment)
    proof = {
        "requested": str(child / "nested"), "cwd": str(child / "nested"),
        "root": str(child), "repository": "example/project",
    }
    monkeypatch.setattr(copilot_plugins, "probe_checkout", lambda *a: dict(proof))
    args = argparse.Namespace(
        stage_plugins=["sample@local"], plugin_roots=[str(controller)],
        expected_repository="example/project",
    )
    return args, adapter, controller, child, home


@pytest.mark.parametrize("native", ["absent", "enabled", "disabled", "missing"])
def test_real_streamed_preparation_retains_child_authority_and_globals(receiver, native):
    args, adapter, controller, child, home = receiver
    globals_path = home / ".copilot" / "settings.json"
    globals_path.parent.mkdir()
    globals_path.write_text('{"enabledPlugins":{"unrelated@elsewhere":true}}')
    before = globals_path.read_bytes()
    if native != "absent":
        _checkout(child, plugin=True, enabled=native != "disabled")
    if native == "missing":
        (child / ".ai" / "sample" / "plugin.json").unlink()
        with pytest.raises(RuntimeError, match="Required SSH CLI plugin preparation failed"):
            copilot_plugins.prepare(args, adapter, {"workspace": str(child / "nested")})
    else:
        context = copilot_plugins.prepare(args, adapter, {"workspace": str(child / "nested")})
        assert context["cwd"] == str(child / "nested")
        assert context["repository_root"] == str(child)
        if native == "disabled":
            assert context["plugins"] == [] and context["logical_payloads"] == {}
        else:
            payload = Path(context["logical_payloads"]["sample@local"])
            assert (payload / "marker").read_text() == ("child" if native == "enabled" else "controller")
            assert payload != controller / ".ai" / "sample"
        assert (child / ".github" / "copilot" / "settings.local.json").is_file()
    assert globals_path.read_bytes() == before
    assert len(adapter.calls) == 1
    assert adapter.calls[0][0] == "python3 -I -S -X utf8 -u -"
    assert adapter.calls[0][2] > 540
    assert len(adapter.calls[0][1]) > 32_768


def test_absolute_target_owned_marketplace_rebinds_via_registered_source(receiver, monkeypatch):
    args, adapter, controller, child, _home = receiver
    _checkout(controller, plugin=True, absolute=True)
    _checkout(child, plugin=True)
    # No target override masks the producer's absolute-source ownership.
    (child / ".github" / "copilot" / "settings.json").unlink()
    answers = iter([
        {"inventory_authority": "complete", "repos": [{
            "name": "project", "remote": "https://github.com/example/project.git",
        }]},
        {"registration": "ready", "name": "project",
         "remote": "https://github.com/example/project.git", "path": str(controller)},
    ])
    monkeypatch.setattr(copilot_plugins, "_registry", lambda _args: next(answers))
    context = copilot_plugins.prepare(args, adapter, {"workspace": str(child / "nested")})
    payload = Path(context["logical_payloads"]["sample@local"])
    assert payload == child / ".ai" / "sample"
    assert (payload / "marker").read_text() == "child"


def test_changed_repository_refuses_before_registration(receiver):
    args, adapter, _controller, child, _home = receiver
    args.expected_repository = "different/project"
    with pytest.raises(RuntimeError, match="changed after controller"):
        copilot_plugins.prepare(args, adapter, {"workspace": str(child / "nested")})
    assert adapter.calls == []
    assert not (child / ".github" / "copilot" / "settings.local.json").exists()


def test_external_absolute_controller_source_is_reflected_not_rebound(receiver, tmp_path, monkeypatch):
    args, adapter, controller, child, _home = receiver
    external = tmp_path / "external"
    _checkout(external, plugin=True)
    settings_path = controller / ".github" / "copilot" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["extraKnownMarketplaces"]["local"]["source"]["path"] = str(external / ".ai")
    settings_path.write_text(json.dumps(settings))
    monkeypatch.setattr(copilot_plugins, "_registry", lambda _args: {
        "inventory_authority": "complete", "repos": [],
    })
    context = copilot_plugins.prepare(args, adapter, {"workspace": str(child / "nested")})
    payload = Path(context["logical_payloads"]["sample@local"])
    assert payload not in {external / ".ai" / "sample", child / ".ai" / "sample"}
    assert (payload / "marker").read_text() == "external"


def test_repository_keys_preserve_non_github_path_case_and_port():
    key = copilot_plugins.repository_key("https://user@code.example:8443/Org/Repo.git")
    assert key == "network:code.example:8443/Org/Repo"
    assert not copilot_plugins._repository_equal(key, "network:code.example:8443/org/repo")
    assert not copilot_plugins._repository_equal(key, "network:code.example/Org/Repo")
    assert copilot_plugins._repository_equal("Example/Project", "example/project")


def test_interrupted_preparation_waits_for_receiver_quiescence(monkeypatch):
    finished = threading.Event()
    class InterruptedWait:
        def __init__(self):
            self.event, self.interrupted = threading.Event(), False
        def is_set(self):
            return self.event.is_set()
        def set(self):
            self.event.set()
        def wait(self):
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt
            return self.event.wait()
    monkeypatch.setattr(copilot_plugins, "threading", SimpleNamespace(
        Event=InterruptedWait, Thread=threading.Thread,
    ))
    def remote(*a, **kw):
        time.sleep(0.05)
        finished.set()
        return 0, "", ""
    with pytest.raises(KeyboardInterrupt):
        copilot_plugins.run_python(SimpleNamespace(run_input=remote), "pass", timeout=1)
    assert finished.is_set()


@pytest.mark.parametrize("inventory", [None, {}, {"inventory_authority": "partial", "repos": []}])
def test_absolute_source_requires_complete_registry(monkeypatch, inventory):
    monkeypatch.setattr(copilot_plugins, "_registry", lambda _args: inventory)
    with pytest.raises(RuntimeError, match="complete repository registry"):
        copilot_plugins._binding("example/project")


def test_installed_only_context_is_not_native_cli_origin(receiver, monkeypatch):
    args, adapter, _controller, child, _home = receiver
    monkeypatch.setattr(copilot_plugins, "run_python", lambda *a, **kw: (0, "", ""))
    monkeypatch.setattr(copilot_plugins, "parse_prepared_context", lambda *a, **kw: {
        "version": 1, "cwd": str(child), "plugins": [], "acp_only_sources": ["sample@local"],
    })
    with pytest.raises(ValueError, match="no marketplace origin"):
        copilot_plugins.prepare(args, adapter, {"workspace": str(child / "nested")})


@pytest.mark.parametrize("output", [
    "", "__SSH_CHECKOUT__{}",
    '__SSH_CHECKOUT__{"requested":"/wrong","cwd":"/child","root":"/child","origin":""}',
    '__SSH_CHECKOUT__{"requested":"/child","cwd":"relative","root":"/child","origin":""}',
])
def test_checkout_probe_requires_matching_absolute_proof(monkeypatch, output):
    monkeypatch.setattr(copilot_plugins, "run_python", lambda *a, **kw: (0, output, ""))
    with pytest.raises((RuntimeError, ValueError)):
        copilot_plugins.probe_checkout(object(), "/child")


@pytest.mark.skipif(os.name == "nt", reason="This actual SSH CLI owner accepts POSIX targets only")
def test_actual_posix_probe_uses_requested_git_checkout(tmp_path):
    child, home, daemon = [tmp_path / name for name in ("child", "home", "daemon")]
    _checkout(child)
    home.mkdir()
    daemon.mkdir()
    environment = {**os.environ, "HOME": str(home), "GIT_DIR": str(daemon)}
    adapter = _Receiver(daemon, environment)
    proof = copilot_plugins.probe_checkout(adapter, str(child))
    assert proof["cwd"] == proof["root"] == str(child)
    assert proof["origin"] == proof["repository"] == ""


def _pending():
    context = {"version": 1, "cwd": "/canonical/child", "plugins": []}
    plan = {"scope_id": "anchor-child@host", "mux_session": "wt-child"}
    return {
        **owned_delivery_journal(
            "native-session", "original seed", context, operation="original-operation", plan=plan,
            reservation_id="original-reservation", creation_receipt=None, daemon_port=41234,
        ), "requested_workspace": "/configured/child",
    }


def test_delivery_journal_survives_keeper_state_replacement(tmp_path, monkeypatch):
    store = copilot_detach.KeeperStore(tmp_path)
    monkeypatch.setattr(copilot_detach, "_STORE", store)
    pending = _pending()
    copilot_delivery.record("host", "anchor-child@host", pending)
    store.write("host", {"pid": 42})
    store.remove("host")
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    assert copilot_delivery.read("host", "anchor-child@host") == pending


def test_unknown_delivery_recovers_original_inputs_without_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    pending = _pending()
    copilot_delivery.record("host", "anchor-child@host", pending)
    calls = []
    adapter = SimpleNamespace(
        target="host", pending_delivery=True,
        plugin_args=SimpleNamespace(workspace="/configured/child"),
        ensure_keeper=lambda **kw: calls.append(("keeper", kw)),
        run_input=lambda *a, **kw: (0, "", ""),
    )
    plan = {
        "identity": "anchor-child", "scope_id": "anchor-child@host",
        "workspace": "/canonical/child", "venue": {"kind": "ssh", "target": "host"},
    }
    monkeypatch.setattr("venue_copilot.detached._bridge_path_ok", lambda *a: True)
    monkeypatch.setattr("venue_copilot.release_cli_mode", lambda *a, **kw: calls.append(("release", kw)))
    def reconcile(original):
        assert original == pending
        envelope = original["envelope"]
        return owned_result(
            True, session_id=original["session_id"], operation=original["operation"],
            note=envelope["prompt"], context=envelope["plugin_context"],
        )
    monkeypatch.setattr(copilot_delivery, "reconcile_owned_delivery", reconcile)
    rc, payload = copilot_delivery.recover(adapter, plan, pending)
    assert rc == 0 and payload["session_id"] == "native-session"
    assert plan["workspace"] == "/canonical/child" and plan["mux_session"] == "wt-child"
    assert not adapter.pending_delivery
    assert copilot_delivery.read("host", "anchor-child@host") is None
    assert calls[-1] == ("release", {"reservation_id": "original-reservation"})


def test_changed_native_identity_does_not_fall_back_to_fresh_session(tmp_path, monkeypatch):
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    copilot_delivery.record("host", "anchor-child@host", _pending())
    with pytest.raises(RuntimeError, match="native identity changed"):
        copilot_delivery.read(
            "host", "anchor-renamed@host", requested_workspace="/configured/child",
            execution_workspace="/canonical/child",
        )
    assert copilot_delivery.read("host", "anchor-child@host") is not None


def test_corrupt_delivery_journal_refuses_fresh_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    store = copilot_delivery._store()
    store.state_dir.mkdir(parents=True)
    store.state_path("host").write_text("not JSON")
    with pytest.raises(RuntimeError, match="unreadable"):
        copilot_delivery.read("host", "scope")


@pytest.mark.parametrize("matching", [False, True])
def test_native_anchor_resolution_uses_owner_identity_not_directory_basename(monkeypatch, matching):
    monkeypatch.setattr(copilot_plugins, "probe_checkout", lambda *a: {
        "cwd": "/selected/child/nested", "root": "/selected/child",
    })
    adapter = SimpleNamespace(target="host", hold_id="anchor-nested@host")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return 0, json.dumps({
            "ok": True, "dry_run": True, "anchor": True,
            "work_dir": "/selected/child" if matching else "/different/anchor",
            "worktree_id": "anchor-registered-name", "session": "wt-anchor-registered-name",
        }), ""
    adapter.run = run
    plan = {
        "workspace": "/selected/child/nested", "scope_id": "anchor-nested@host",
        "venue": {"kind": "ssh", "target": "host"},
    }
    if not matching:
        with pytest.raises(RuntimeError, match="configured checkout"):
            copilot_plugins.resolve_launch(adapter, plan)
        assert plan["workspace"] == "/selected/child/nested"
    else:
        copilot_plugins.resolve_launch(adapter, plan)
        assert plan["identity"] == "anchor-registered-name"
        assert plan["workspace"] == "/selected/child"
        assert plan["scope_id"] == adapter.hold_id == "anchor-registered-name@host"
        assert plan["mux_session"] == "wt-anchor-registered-name"
    assert "--dry-run --json" in calls[0]
