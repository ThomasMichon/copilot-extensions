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


def test_filesystem_repository_keys_cannot_alias_github_or_fold_path_case():
    key = copilot_plugins.repository_key("example/project")
    github = copilot_plugins.repository_key("https://github.com/example/project.git")
    assert key == "filesystem:example/project"
    assert not copilot_plugins._repository_equal(key, github)
    assert not copilot_plugins._repository_equal(
        copilot_plugins.repository_key("Mirror/Repo"),
        copilot_plugins.repository_key("mirror/repo"),
    )
    assert copilot_plugins.repository_key("") == ""


@pytest.mark.parametrize("remote,expected", [
    ("code.example:Org/Repo.git", "network:code.example/Org/Repo"),
    ("git@code.example:Org/Repo.git", "network:code.example/Org/Repo"),
    ("ssh://git@code.example/Org/Repo.git", "network:code.example/Org/Repo"),
    ("https://code.example/Org/Some%20Repo.git", "network:code.example/Org/Some Repo"),
    ("github.com:Example/Project.git", "Example/Project"),
    ("ssh://operator@github.com/Example/Project.git", "Example/Project"),
    ("file://localhost/Org/Repo.git", "filesystem:file://localhost/Org/Repo.git"),
    ("./mirror:repo", "filesystem:./mirror:repo"),
])
def test_repository_keys_share_network_normalization_with_bridge(remote, expected):
    from plugin_resolve.remote_identity import normalize_remote

    assert copilot_plugins.repository_key(remote) == expected
    if expected.startswith("network:"):
        assert normalize_remote(remote) == expected
    elif not expected.startswith("filesystem:"):
        assert normalize_remote(remote) == "network:github.com/" + expected


def test_namespace_discovery_has_one_receiver_within_existing_bridge_deadline(monkeypatch, capsys):
    monkeypatch.setattr(copilot_detach, "_ssh_config", lambda target: object())
    monkeypatch.setattr(copilot_detach, "_ensure_posix", lambda *a: pytest.fail("extra SSH discovery"))
    monkeypatch.setattr(copilot_detach, "plan_for", lambda args: {"workspace": args.workspace})
    monkeypatch.setattr(copilot_plugins, "resolve_launch", lambda *a: pytest.fail("discovery is not launch"))
    calls = []
    def receive(_config, _command, stdin, *, timeout):
        calls.append(timeout)
        assert b",60.0)\n" in stdin
        return 0, '__SSH_CHECKOUT__' + json.dumps({
            "requested": "/child", "cwd": "/child", "root": "/child",
            "origin": "https://github.com/example/project.git",
        }), ""
    monkeypatch.setattr(copilot_detach, "_remote_input", receive)
    assert copilot_plugins.target_repo(SimpleNamespace(target="host", workspace="/child")) == 0
    assert capsys.readouterr().out.strip() == "example/project"
    assert calls == [80.0]
    assert calls[0] < 90.0


@pytest.mark.parametrize("remote", ["origin", "upstream"])
def test_probe_queries_native_owner_selected_remote(tmp_path, monkeypatch, capsys, remote):
    child = tmp_path / "child"
    _checkout(child)
    url = "https://github.com/example/project.git"
    subprocess.run(
        ["git", "-C", str(child), "remote", "add", remote, url],
        check=True, capture_output=True, timeout=20, creationflags=no_window_flags(),
    )
    run = subprocess.run
    which = copilot_plugins.shutil.which
    calls = []
    monkeypatch.setattr(copilot_plugins.shutil, "which", lambda name, **kw: (
        "native-owner" if name == "agent-worktrees" else which(name, **kw)
    ))
    def configured(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "native-owner":
            assert argv == ["native-owner", "get", "git-remote"]
            assert Path(kwargs["cwd"]) == child
            return subprocess.CompletedProcess(argv, 0, stdout=remote + "\n", stderr="")
        return run(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", configured)
    def execute(_adapter, program, **_kwargs):
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(program, {})
        proof = json.loads(output.getvalue().removeprefix("__SSH_CHECKOUT__"))
        assert proof["origin"] == url
        # The native receiver is POSIX; this test also exercises Git on Windows.
        proof.update(cwd="/child", root="/child")
        return 0, "__SSH_CHECKOUT__" + json.dumps(proof), ""
    monkeypatch.setattr(copilot_plugins, "run_python", execute)
    before = Path.cwd()
    try:
        proof = copilot_plugins.probe_checkout(object(), str(child))
        monkeypatch.setattr(copilot_detach, "_prepare_ssh_and_workspace", lambda args, **kw: object())
        monkeypatch.setattr(copilot_detach, "plan_for", lambda args: {"workspace": args.workspace})
        monkeypatch.setattr(copilot_plugins, "resolve_launch", lambda *a: None)
        assert copilot_plugins.target_repo(SimpleNamespace(target="host", workspace=str(child))) == 0
        assert capsys.readouterr().out.strip() == "example/project"
    finally:
        os.chdir(before)
    assert proof["repository"] == "example/project"
    assert any(argv[1:] == ["config", "--get", f"remote.{remote}.url"] for argv in calls)
    if remote != "origin":
        assert not any("remote.origin.url" in argv for argv in calls)


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
        current_thread=threading.current_thread, main_thread=threading.main_thread,
    ))
    def remote(*a, **kw):
        time.sleep(0.05)
        finished.set()
        return 0, "", ""
    with pytest.raises(KeyboardInterrupt):
        copilot_plugins.run_python(SimpleNamespace(run_input=remote), "pass", timeout=1)
    assert finished.is_set()


def test_terminal_interrupt_at_worker_start_waits_for_receiver_and_restores_handler(monkeypatch):
    import signal

    finished = threading.Event()
    previous = signal.getsignal(signal.SIGINT)
    class InterruptedStart(threading.Thread):
        def start(self):
            super().start()
            signal.raise_signal(signal.SIGINT)
    monkeypatch.setattr(copilot_plugins, "threading", SimpleNamespace(
        Event=threading.Event, Thread=InterruptedStart,
        current_thread=threading.current_thread, main_thread=threading.main_thread,
    ))
    def remote(*a, **kw):
        time.sleep(0.1)
        finished.set()
        return 0, "", ""
    try:
        with pytest.raises(KeyboardInterrupt):
            copilot_plugins.run_python(SimpleNamespace(run_input=remote), "pass", timeout=1)
        assert finished.is_set()
        assert signal.getsignal(signal.SIGINT) == previous
    finally:
        assert finished.wait(2)


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
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    owner = bin_dir / "agent-worktrees"
    owner.write_text('#!/bin/sh\n[ "$*" = "get git-remote" ] || exit 1\nprintf "origin\\n"\n')
    owner.chmod(0o700)
    environment = {
        **os.environ, "HOME": str(home), "GIT_DIR": str(daemon),
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
    }
    adapter = _Receiver(daemon, environment)
    proof = copilot_plugins.probe_checkout(adapter, str(child))
    assert proof["cwd"] == proof["root"] == str(child)
    assert proof["origin"] == proof["repository"] == ""


def _pending(*, operation="original-operation", note="original seed"):
    context = {"version": 1, "cwd": "/canonical/child", "plugins": []}
    plan = {"scope_id": "anchor-child@host", "mux_session": "wt-child"}
    return {
        **owned_delivery_journal(
            "native-session", note, context, operation=operation, plan=plan,
            reservation_id="original-reservation", creation_receipt=None, daemon_port=41234,
        ), "requested_workspace": "/configured/child", "keeper_hold_id": plan["scope_id"],
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


@pytest.mark.parametrize("hold", [None, "", "other-scope", "attached:bad", "attached:1:wrong"])
def test_recovery_refuses_missing_or_unrelated_keeper_hold_identity(tmp_path, monkeypatch, hold):
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    pending = {**_pending(), "keeper_hold_id": hold}
    copilot_delivery.record("host", pending["scope_id"], pending)
    adapter = SimpleNamespace(
        target="host", pending_delivery=True,
        plugin_args=SimpleNamespace(workspace="/configured/child"),
        ensure_keeper=lambda **kw: pytest.fail("must retain original hold ownership"),
    )
    plan = {
        "scope_id": pending["scope_id"], "workspace": "/canonical/child",
        "venue": {"kind": "ssh", "target": "host"},
    }
    with pytest.raises(RuntimeError, match="original keeper hold identity"):
        copilot_delivery.recover(adapter, plan, pending)
    assert copilot_delivery.read("host", pending["scope_id"]) == pending


@pytest.mark.parametrize("phase", ["reconcile", "settle"])
def test_interleaved_recovery_cannot_overwrite_or_remove_newer_delivery(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    pending = _pending()
    scope = pending["scope_id"]
    newer = _pending(operation="new-operation", note="new seed")
    copilot_delivery.record("host", scope, pending)
    adapter = SimpleNamespace(
        target="host", pending_delivery=True,
        plugin_args=SimpleNamespace(workspace="/configured/child"),
        ensure_keeper=lambda **kw: None, run_input=lambda *a, **kw: (0, "", ""),
    )
    plan = {
        "identity": "anchor-child", "scope_id": scope, "workspace": "/canonical/child",
        "venue": {"kind": "ssh", "target": "host"},
    }
    monkeypatch.setattr("venue_copilot.detached._bridge_path_ok", lambda *a: True)
    monkeypatch.setattr("venue_copilot.release_cli_mode", lambda *a, **kw: pytest.fail("must retain ownership"))
    def replace():
        copilot_delivery.record("host", scope, None)
        copilot_delivery.record("host", scope, newer)
    def reconcile(original):
        if phase == "reconcile":
            replace()
        return owned_result(
            True, session_id=original["session_id"], operation=original["operation"],
            note=original["envelope"]["prompt"], context=original["envelope"]["plugin_context"],
        )
    def settle(*a):
        assert phase == "settle"
        replace()
        return True
    monkeypatch.setattr(copilot_delivery, "reconcile_owned_delivery", reconcile)
    monkeypatch.setattr(copilot_delivery, "settle_deferred_seed", settle)
    with pytest.raises(RuntimeError, match="ownership changed"):
        copilot_delivery.recover(adapter, plan, pending)
    assert adapter.pending_delivery
    assert copilot_delivery.read("host", scope) == newer
    assert pending == _pending()


def test_stale_recovery_does_not_resurrect_a_cleared_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    pending = _pending()
    copilot_delivery.record("host", pending["scope_id"], pending)
    copilot_delivery.record("host", pending["scope_id"], None)
    with pytest.raises(RuntimeError, match="ownership changed"):
        copilot_delivery.record("host", pending["scope_id"], pending, expected=pending)
    assert copilot_delivery.read("host", pending["scope_id"]) is None


def test_pending_retry_checks_repository_before_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    pending = _pending()
    copilot_delivery.record("host", pending["scope_id"], pending)
    args = SimpleNamespace(
        target="host", workspace="/configured/child", expected_repository="example/project",
    )
    adapter = SimpleNamespace(
        target="host", hold_id=pending["scope_id"], plugin_args=args,
        run=lambda *a, **kw: pytest.fail("repository mismatch must refuse before native resolution"),
    )
    monkeypatch.setattr(copilot_plugins, "probe_checkout", lambda *a: {
        "cwd": "/canonical/child", "root": "/canonical/child", "repository": "other/project",
    })
    monkeypatch.setattr(copilot_delivery, "recover", lambda *a: pytest.fail("must not retry original seed"))
    plan = {
        "identity": "anchor-child", "scope_id": pending["scope_id"], "workspace": args.workspace,
        "venue": {"kind": "ssh", "target": "host"},
    }
    with pytest.raises(RuntimeError, match="repository changed"):
        copilot_detach._launch(args, adapter, plan, seed="fresh task")
    assert copilot_delivery.read("host", pending["scope_id"]) == pending


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
    store.state_dir.mkdir(parents=True, exist_ok=True)
    store.state_path("host").write_text("not JSON")
    with pytest.raises(RuntimeError, match="unreadable"):
        copilot_delivery.read("host", "scope")


@pytest.mark.skipif(os.name == "nt", reason="Requires real POSIX directory permissions")
@pytest.mark.parametrize("existing", [False, True])
def test_delivery_journal_is_private_before_access_under_permissive_umask(tmp_path, monkeypatch, existing):
    import stat

    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    directory = tmp_path / "pending-deliveries"
    if existing:
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
        (directory / "host.json").write_text(json.dumps({
            "version": 1, "pending_deliveries": {"anchor-child@host": _pending()},
        }))
    original = copilot_detach.KeeperStore.read
    def read(store, key):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        return original(store, key)
    monkeypatch.setattr(copilot_detach.KeeperStore, "read", read)
    before = os.umask(0o022)
    try:
        copilot_delivery.record("host", "anchor-child@host", _pending())
        assert copilot_delivery.read("host", "anchor-child@host") == _pending()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    finally:
        os.umask(before)


def test_retirement_preserves_newer_operation_and_other_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(copilot_detach, "_STORE", copilot_detach.KeeperStore(tmp_path))
    pending = _pending()
    plan = {"scope_id": pending["scope_id"], "mux_session": "wt-child", "workspace": "/canonical/child"}
    newer = _pending(operation="new-operation", note="new seed")
    copilot_delivery.record("host", pending["scope_id"], newer)
    with pytest.raises(RuntimeError, match="ownership changed"):
        copilot_delivery.retire_stopped("host", plan, pending)
    assert copilot_delivery.read("host", pending["scope_id"]) == newer
    copilot_delivery.record("host", "another-scope", {"operation": "unrelated"})
    copilot_delivery.retire_stopped("host", plan, newer)
    assert copilot_delivery.read("host", pending["scope_id"]) is None
    assert copilot_delivery.read("host", "another-scope") == {"operation": "unrelated"}
    archived = list((copilot_delivery._store().state_dir / "retired").glob("*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text())["delivery"] == newer


@pytest.mark.parametrize("matching", [False, True])
def test_native_anchor_resolution_uses_owner_identity_not_directory_basename(monkeypatch, matching):
    monkeypatch.setattr(copilot_plugins, "probe_checkout", lambda _adapter, workspace: {
        "cwd": workspace, "root": "/selected/child",
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


@pytest.mark.parametrize("native_directory", ["/selected/child/", "/alias/child"])
def test_native_anchor_equivalent_directory_spellings_are_resolved_on_target(monkeypatch, native_directory):
    probes = []
    def probe(_adapter, workspace):
        probes.append(workspace)
        return {
            "cwd": "/selected/child/nested" if workspace.endswith("/nested") else "/selected/child",
            "root": "/selected/child", "repository": "example/project",
        }
    monkeypatch.setattr(copilot_plugins, "probe_checkout", probe)
    adapter = SimpleNamespace(
        target="host", hold_id="anchor-nested@host",
        run=lambda *a, **kw: (0, json.dumps({
            "ok": True, "dry_run": True, "anchor": True, "work_dir": native_directory,
            "worktree_id": "anchor-registered-name", "session": "wt-anchor-registered-name",
        }), ""),
    )
    plan = {
        "workspace": "/selected/child/nested", "scope_id": "anchor-nested@host",
        "venue": {"kind": "ssh", "target": "host"},
    }
    copilot_plugins.resolve_launch(adapter, plan)
    assert probes == ["/selected/child/nested", native_directory]
    assert plan["workspace"] == "/selected/child"
    assert plan["scope_id"] == "anchor-registered-name@host"
