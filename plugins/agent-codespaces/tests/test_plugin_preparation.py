"""Provider selection to real target preparation, over a synthetic transport."""

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_codespaces.plugin_preparation import prepare_plugins


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("installed", [False, True])
def test_manifest_enablement_and_worktree_payload_precedence_are_independent(
    monkeypatch, tmp_path, enabled, installed,
):
    import io
    import tarfile
    from agent_codespaces.plugin_preparation import plugin_plan

    anchor, controller, home = [tmp_path / name for name in ("anchor", "controller", "home")]
    for root, decision, text in (
        (anchor, not enabled, "anchor-v1"), (controller, enabled, "controller-v2"),
    ):
        catalog = root / ".ai"
        for name in ("control-harness", "sample", "stale"):
            payload = catalog / name
            payload.mkdir(parents=True)
            manifest = {"name": name, "version": "1.0.0"}
            if name == "control-harness":
                manifest["codespacePlugins"] = [{
                    "source": f"{'sample' if root == controller else 'stale'}@synthetic",
                }]
            (payload / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
            (payload / "payload.txt").write_text(text, encoding="utf-8")
        (catalog / "marketplace.json").write_text(json.dumps({
            "name": "synthetic", "owner": {"name": "Synthetic Publisher"},
            "plugins": [{"name": name, "source": name, "version": "1.0.0"}
                        for name in ("control-harness", "sample", "stale")],
        }), encoding="utf-8")
        settings = root / ".github" / "copilot"
        settings.mkdir(parents=True)
        (settings / "settings.json").write_text(json.dumps({
            "enabledPlugins": {"control-harness@synthetic": decision},
            "extraKnownMarketplaces": {
                "synthetic": {"source": {"source": "directory", "path": "./.ai"}},
            },
        }), encoding="utf-8")
    if installed:
        old = home / "installed-plugins" / "synthetic" / "control-harness"
        old.mkdir(parents=True)
        (old / "plugin.json").write_text(json.dumps({
            "name": "control-harness", "version": "0.1.0",
            "codespacePlugins": [{"source": "stale@synthetic"}],
        }), encoding="utf-8")
    monkeypatch.setattr("agent_codespaces.codespace_plugins.default_copilot_home", lambda: home)
    plan = plugin_plan(None, SimpleNamespace(source_paths=[anchor, controller], codespace_plugins=[]))
    if not enabled:
        assert plan["plugins"] == []
        return
    assert [item["source"] for item in plan["plugins"]] == ["sample@synthetic"]
    item = plan["plugins"][0]
    assert item["enable"] is True
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(item["archive"]))) as archive:
        assert archive.extractfile(item["relative"] + "/payload.txt").read() == b"controller-v2"


@pytest.mark.asyncio
async def test_provider_selection_registers_before_consumer(tmp_path):
    control = tmp_path / "control"
    payload = control / ".ai" / "sample"
    payload.mkdir(parents=True)
    (payload / "plugin.json").write_text(json.dumps({"name": "sample", "version": "1.2.3"}))
    (control / ".ai" / "marketplace.json").write_text(json.dumps({
        "name": "synthetic", "owner": {"name": "Synthetic Publisher"},
        "plugins": [{"name": "sample", "source": "sample", "version": "1.2.3"}],
    }))
    config_dir = control / ".github" / "copilot"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "synthetic": {"source": {"source": "directory", "path": "./.ai"}},
        },
    }))
    target = tmp_path / "target"
    subprocess.run(
        ["git", "init", "--quiet", str(target)], check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    config = SimpleNamespace(
        source_paths=[control], codespace_plugins=[{"source": "sample@synthetic"}],
    )
    shared = Path(__file__).resolve().parents[3] / "libs" / "plugin-resolve" / "src"

    class Transport:
        async def exec_command(self, _name, command, *, input_bytes, timeout):
            assert command == "bash -ls"
            match = re.search(r"request=json.loads\(base64.b64decode\('([^']+)'\)\)", input_bytes.decode())
            request = base64.b64decode(match[1]).decode()
            child = subprocess.run(
                [sys.executable, "-c",
                 "import json,sys;from plugin_resolve.preparation import prepare;"
                 "print('__VENUE_PLUGINS__'+json.dumps(prepare(json.loads(sys.argv[1]))))",
                 request],
                env={**os.environ, "PYTHONPATH": str(shared)},
                capture_output=True, text=True, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return SimpleNamespace(exit_code=child.returncode, stdout=child.stdout, stderr=child.stderr)

    directories = await prepare_plugins(Transport(), "synthetic", "owner/project", config, str(target))
    assert len(directories) == 1
    assert (Path(directories[0]) / "plugin.json").is_file()
    settings = json.loads((target / ".github" / "copilot" / "settings.local.json").read_text())
    assert settings["enabledPlugins"]["sample@synthetic"] is True


@pytest.mark.asyncio
async def test_explicit_worktree_uses_the_native_resolve_contract_before_prepare(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        "agent_codespaces.plugin_preparation.plugin_plan",
        lambda *a, **kw: {"plugins": [], "marketplaces": {}},
    )
    manager = SimpleNamespace(exec_command=AsyncMock(side_effect=[
        SimpleNamespace(exit_code=0, stdout=json.dumps({
            "launch": {"action": "exec", "worktree_id": "selected", "work_dir": "/workspaces/selected"},
        }), stderr=""),
        SimpleNamespace(exit_code=0, stdout="__VENUE_PLUGINS__{}", stderr=""),
    ]))
    context = {}
    await prepare_plugins(
        manager, "synthetic", "example/project", object(), "/workspaces/anchor",
        worktree_id="selected", prepared_context=context,
    )
    first, second = manager.exec_command.await_args_list
    assert 'WORKTREES=$(command -v agent-worktrees)' in first.args[1]
    assert 'resolve --json --dry-run' in first.args[1]
    assert '--worktree-id selected' in first.args[1]
    encoded = re.search(
        r"request=json.loads\(base64.b64decode\('([^']+)'\)\)",
        second.kwargs["input_bytes"].decode(),
    )[1]
    assert json.loads(base64.b64decode(encoded))["repo"] == "/workspaces/selected"
    assert "worktree_id" not in json.loads(base64.b64decode(encoded))
    assert context == {"workspace": "/workspaces/selected"}


@pytest.mark.parametrize("login_project", ["neutral", "wrong-project"])
@pytest.mark.parametrize("worktree", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_attached_child_login_uses_prepared_checkout_with_real_bash(
    tmp_path, login_project, worktree, missing,
):
    import shutil
    import shlex
    from venue_copilot import build_copilot_remote_command
    from agent_codespaces.plugin_preparation import attached_launch_command

    shell = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parents[1] / "bin" / "bash.exe" if git else None
        shell = str(candidate) if candidate and candidate.is_file() else None
    if shell is None:
        pytest.skip("requires native Bash")

    def remote(path):
        value = path.resolve().as_posix()
        return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" else value

    prepared = tmp_path / "prepared 'quoted; $ checkout"
    settings = prepared / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.local.json").write_text('{"prepared":true}', encoding="utf-8")
    home, wrong = tmp_path / "home", tmp_path / login_project
    home.mkdir()
    wrong.mkdir()
    if login_project == "wrong-project":
        wrong_settings = wrong / ".github" / "copilot"
        wrong_settings.mkdir(parents=True)
        (wrong_settings / "settings.local.json").write_text('{"wrong":true}')
    (home / ".bash_profile").write_text('cd -- "$LOGIN_CWD"\nexport FIXTURE_LOGIN=1\n')
    consumer, result = tmp_path / "consumer.sh", tmp_path / "result"
    consumer.write_text(
        'test "$FIXTURE_LOGIN" = 1 || exit 21\n'
        'test -f .github/copilot/settings.local.json || exit 22\n'
        '{ pwd; cat .github/copilot/settings.local.json; printf "\\n%s\\n" "$@"; } > "$RESULT"\n',
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": remote(home), "LOGIN_CWD": remote(wrong),
           "CONSUMER": remote(consumer), "RESULT": remote(result), "CHERE_INVOKING": "1"}

    def run(command):
        return subprocess.run(
            [shell, "-c", 'agent-worktrees() { bash "$CONSUMER" "$@"; }; '
             'export -f agent-worktrees; eval "$COMMAND"'],
            env={**env, "COMMAND": command}, cwd=prepared, capture_output=True, text=True,
            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    producer = build_copilot_remote_command("selected ' worktree", anchor=not worktree, ensure_mux=False)
    for legacy in (producer, f"cd -- {shlex.quote(remote(prepared))} && {producer}"):
        prior = run(legacy)
        if login_project == "wrong-project":
            assert prior.returncode == 0
            assert '{"wrong":true}' in result.read_text()
            result.unlink()
        else:
            assert prior.returncode != 0
    workspace = remote(prepared / "missing") if missing else remote(prepared)
    launched = run(attached_launch_command(producer, workspace))
    if missing:
        assert launched.returncode != 0 and not result.exists()
    else:
        assert launched.returncode == 0, launched.stderr
        text = result.read_text(encoding="utf-8")
        assert text.splitlines()[0] == remote(prepared)
        assert '{"prepared":true}' in text
        assert ("selected ' worktree" in text) is worktree


@pytest.mark.parametrize("workspace", [None, "", "relative", "/bad\0path"])
def test_attached_command_refuses_missing_or_invalid_prepared_root(workspace):
    from agent_codespaces.plugin_preparation import attached_launch_command
    with pytest.raises(RuntimeError, match="prepared execution checkout"):
        attached_launch_command("agent-worktrees copilot --anchor", workspace)


@pytest.mark.asyncio
async def test_failed_preparation_never_publishes_attached_execution_context(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        "agent_codespaces.plugin_preparation.plugin_plan",
        lambda *a, **kw: {"plugins": [], "marketplaces": {}},
    )
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=SimpleNamespace(
        exit_code=1, stdout="", stderr="installation failed",
    )))
    context = {}
    with pytest.raises(RuntimeError, match="installation failed"):
        await prepare_plugins(
            manager, "synthetic", "example/project", object(), "/prepared/execution",
            prepared_context=context,
        )
    assert context == {}


def test_explicit_sources_retain_the_harness_only_propagation_filter(monkeypatch):
    from agent_codespaces.plugin_preparation import plugin_plan
    monkeypatch.setattr(
        "agent_codespaces.plugin_preparation.resolve_codespace_plugins", lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "agent_codespaces.plugin_preparation.repo_copilot_settings", lambda roots: {},
    )
    plan = plugin_plan("example/project", SimpleNamespace(), ["control-harness@synthetic"])
    assert plan["plugins"] == []


@pytest.mark.asyncio
async def test_relay_is_proven_and_prelude_exported_before_installer(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
        "agent_codespaces.plugin_preparation.plugin_plan",
        lambda *a, **kw: {"plugins": [], "marketplaces": {}},
    )
    manager = SimpleNamespace(exec_command=AsyncMock(side_effect=[
        SimpleNamespace(exit_code=0, stdout="", stderr=""),
        SimpleNamespace(exit_code=0, stdout="__VENUE_PLUGINS__{}", stderr=""),
    ]))
    await prepare_plugins(
        manager, "synthetic", "example/project", object(), "/workspaces/project",
        prelude="export SYNTHETIC_CONTEXT=ready;", relay_port=12001,
    )
    probe, install = manager.exec_command.await_args_list
    assert "ping" in probe.args[1] and "pong" in probe.args[1]
    assert install.kwargs["input_bytes"].startswith(b"export SYNTHETIC_CONTEXT=ready;")


@pytest.mark.asyncio
async def test_attached_preparation_waits_for_owner_and_deploys_helpers(monkeypatch):
    from unittest.mock import AsyncMock
    from agent_codespaces.plugin_preparation import prepare_attached
    events = []
    manager = SimpleNamespace(ensure_connected=AsyncMock(), disconnect=AsyncMock())
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    monkeypatch.setattr("venue_copilot.live_session_for", lambda *a: {})
    monkeypatch.setattr("agent_codespaces.__main__._lookup_codespace_repo", lambda *a: "example/project")

    async def owner(*args):
        events.append("owner-ready")
        return True

    async def helpers(*args):
        events.append("helpers")

    async def install(*args, **kwargs):
        assert kwargs["prelude"] == "SYNTHETIC_PRELUDE;"
        assert kwargs["relay_port"] == 12001
        events.append("install")

    monkeypatch.setattr("agent_codespaces.connection_owner.await_owner_relay", owner)
    monkeypatch.setattr("agent_codespaces.__main__._provision_relay_helpers", helpers)
    monkeypatch.setattr("agent_codespaces.__main__._provision_repo_hooks", AsyncMock())
    monkeypatch.setattr("agent_codespaces.plugin_preparation.require_relay_ready", AsyncMock())
    monkeypatch.setattr("agent_codespaces.relay_launch.build_relay_launch_env",
                        lambda *a: ("SYNTHETIC_PRELUDE;", 12001))
    monkeypatch.setattr("agent_codespaces.plugin_preparation.prepare_plugins", install)
    await prepare_attached(
        SimpleNamespace(name="synthetic", worktree_id=None, no_relay=False),
        SimpleNamespace(resolved_workspace_folder_for=lambda repo: "/workspaces/project"),
    )
    assert events == ["owner-ready", "helpers", "install"]
    manager.disconnect.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "ready", "install"])
async def test_attached_owner_failure_keeps_foreground_relay_until_release(monkeypatch, failure):
    from unittest.mock import AsyncMock
    from agent_codespaces.plugin_preparation import prepare_attached
    events = []
    manager = SimpleNamespace(
        ensure_connected=AsyncMock(return_value=SimpleNamespace(config=object())),
        disconnect=AsyncMock(side_effect=lambda *a: events.append("disconnect")),
    )

    async def start(*a, **kw):
        events.append("forward")
        return SimpleNamespace(stop=AsyncMock(side_effect=lambda: events.append("stop")))

    async def ready(*a):
        events.append("ready")
        if failure == "ready":
            raise RuntimeError("not ready")

    async def install(*a, **kw):
        events.append("install")
        if failure == "install":
            raise RuntimeError("installation failed")

    async def release():
        events.append("interactive")
        assert "stop" not in events

    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    monkeypatch.setattr("venue_copilot.live_session_for", lambda *a: {})
    monkeypatch.setattr("agent_codespaces.__main__._lookup_codespace_repo", lambda *a: "example/project")
    monkeypatch.setattr("agent_codespaces.connection_owner.await_owner_relay",
                        AsyncMock(return_value=False))
    monkeypatch.setattr("agent_codespaces.__main__._start_supervised_relay", start)
    monkeypatch.setattr("agent_codespaces.__main__._provision_relay_helpers", AsyncMock())
    monkeypatch.setattr("agent_codespaces.__main__._provision_repo_hooks", AsyncMock())
    monkeypatch.setattr("agent_codespaces.relay_launch.build_relay_launch_env",
                        lambda *a: ("PRELUDE;", 12001))
    monkeypatch.setattr("agent_codespaces.plugin_preparation.require_relay_ready", ready)
    monkeypatch.setattr("agent_codespaces.plugin_preparation.prepare_plugins", install)
    async def launch():
        await prepare_attached(
            SimpleNamespace(name="synthetic", worktree_id=None, no_relay=False),
            SimpleNamespace(resolved_workspace_folder_for=lambda repo: "/workspaces/project"),
            on_ready=lambda ready: events.append(("prepared", ready)), release=release,
        )

    if failure:
        with pytest.raises(RuntimeError):
            await launch()
        assert "interactive" not in events
        assert ("install" in events) == (failure == "install")
        assert events[-2:] == ["stop", "disconnect"]
    else:
        await launch()
        assert events == ["forward", "ready", "install", ("prepared", True),
                          "interactive", "stop", "disconnect"]


@pytest.mark.asyncio
@pytest.mark.parametrize("child_alive", [False, True])
@pytest.mark.parametrize("failure", [None, "copy", "install"])
async def test_attached_live_ledger_requires_preparation_before_remote_decision(
    monkeypatch, child_alive, failure,
):
    from unittest.mock import AsyncMock
    from agent_codespaces.plugin_preparation import prepare_attached
    events = []
    monkeypatch.setattr("agent_codespaces.__main__._lookup_codespace_repo", lambda *a: "example/project")
    monkeypatch.setattr("venue_copilot.live_session_for", lambda *a: {"status": "live"})
    manager = SimpleNamespace(ensure_connected=AsyncMock(), disconnect=AsyncMock())
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    hooks = AsyncMock()
    monkeypatch.setattr("agent_codespaces.__main__._provision_repo_hooks", hooks)

    async def prepare(*args, **kwargs):
        events.append("prepare")
        assert args[5] == [{"source": "sample@synthetic", "enable": False}]
        if failure:
            raise RuntimeError(f"plugin {failure} failed")

    async def release():
        events.append("reuse" if child_alive else "create")

    monkeypatch.setattr("agent_codespaces.plugin_preparation.prepare_plugins", prepare)
    args = SimpleNamespace(name="synthetic", worktree_id=None, no_relay=True,
                           stage_plugins=[{"source": "sample@synthetic", "enable": False}])
    config = SimpleNamespace(resolved_workspace_folder_for=lambda repo: "/workspaces/project")
    if failure:
        with pytest.raises(RuntimeError, match="failed"):
            await prepare_attached(args, config, on_ready=lambda ready: None, release=release)
        assert events == ["prepare"]
    else:
        await prepare_attached(args, config, on_ready=lambda ready: None, release=release)
        assert events == ["prepare", "reuse" if child_alive else "create"]
    manager.disconnect.assert_awaited_once()
    hooks.assert_not_awaited()


def test_payload_roots_reverse_config_priority_without_reversing_settings(monkeypatch, tmp_path):
    from agent_codespaces import plugin_preparation as module
    anchor, controller, explicit = [tmp_path / name for name in ("anchor", "controller", "explicit")]
    seen = {}
    monkeypatch.setattr(module, "repo_copilot_settings",
                        lambda roots: seen.setdefault("settings", list(roots)) and {})
    monkeypatch.setattr(module, "resolve_codespace_plugins", lambda *a, **kw: [])
    monkeypatch.setattr(module, "build_plan",
                        lambda selections, roots, **kw: {"roots": list(roots), "plugins": selections})
    config = SimpleNamespace(source_paths=[anchor, controller], codespace_plugins=[])
    plan = module.plugin_plan("example/project", config, [
        {"source": "sample@synthetic", "enable": False}, "other@synthetic",
    ], repo_roots=[explicit])
    assert seen["settings"] == [anchor, controller, explicit]
    assert plan["roots"] == [explicit, controller, anchor]
    assert plan["plugins"] == [
        {"source": "sample@synthetic", "enable": False},
        {"source": "other@synthetic", "enable": True},
    ]


def test_provider_selection_bool_wins_related_fallback(monkeypatch):
    from agent_codespaces import plugin_preparation as module
    monkeypatch.setattr(module, "repo_copilot_settings", lambda roots: {})
    monkeypatch.setattr(module, "resolve_codespace_plugins", lambda *a, **kw: [
        SimpleNamespace(source="sample@synthetic", enable=False),
    ])
    monkeypatch.setattr(module, "build_plan", lambda selections, roots, **kw: selections)
    assert module.plugin_plan(None, SimpleNamespace(), ["sample@synthetic"]) == [
        {"source": "sample@synthetic", "enable": False},
    ]


def test_interrupt_before_attached_readiness_cancels_install_and_awaits_cleanup(monkeypatch):
    import asyncio
    import threading
    import time
    from unittest.mock import AsyncMock
    from agent_codespaces import copilot_venue

    events = []
    original_event = threading.Event
    installing = original_event()
    worker_ids = []
    manager = SimpleNamespace(
        ensure_connected=AsyncMock(return_value=SimpleNamespace(config=object())),
        disconnect=AsyncMock(side_effect=lambda *a: events.append("disconnect")),
    )
    relay = SimpleNamespace(stop=AsyncMock(side_effect=lambda: events.append("relay-stop")))

    async def install(*a, **kw):
        worker_ids.append(threading.get_ident())
        installing.set()
        try:
            await asyncio.sleep(0.75)
            events.append("late-write")
        finally:
            events.append("install-cleanup")

    class InterruptedReady:
        def set(self):
            pass

        def wait(self):
            assert installing.wait(5)
            raise KeyboardInterrupt

    created = []

    def event_factory():
        event = InterruptedReady() if not created else original_event()
        created.append(event)
        return event

    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    monkeypatch.setattr("venue_copilot.live_session_for", lambda *a: {})
    monkeypatch.setattr("agent_codespaces.__main__._lookup_codespace_repo", lambda *a: "example/project")
    monkeypatch.setattr("agent_codespaces.connection_owner.await_owner_relay",
                        AsyncMock(return_value=False))
    monkeypatch.setattr("agent_codespaces.__main__._start_supervised_relay",
                        AsyncMock(return_value=relay))
    monkeypatch.setattr("agent_codespaces.__main__._provision_relay_helpers", AsyncMock())
    monkeypatch.setattr("agent_codespaces.__main__._provision_repo_hooks", AsyncMock())
    monkeypatch.setattr("agent_codespaces.relay_launch.build_relay_launch_env",
                        lambda *a: ("PRELUDE;", 12001))
    monkeypatch.setattr("agent_codespaces.plugin_preparation.require_relay_ready", AsyncMock())
    monkeypatch.setattr("agent_codespaces.plugin_preparation.prepare_plugins", install)
    monkeypatch.setattr(copilot_venue.threading, "Event", event_factory)
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        with copilot_venue._attached_preparation(
            SimpleNamespace(name="synthetic", worktree_id=None, no_relay=False),
            SimpleNamespace(resolved_workspace_folder_for=lambda repo: "/workspaces/project"),
        ):
            pytest.fail("must not start the interactive child")
    assert time.monotonic() - started < 0.5
    assert events == ["install-cleanup", "relay-stop", "disconnect"]
    assert not any(thread.ident in worker_ids for thread in threading.enumerate())


@pytest.mark.asyncio
async def test_cold_attached_fallback_hook_receives_launch_environment(monkeypatch, tmp_path):
    import shlex
    from unittest.mock import AsyncMock
    from agent_procutil import no_window_flags
    from agent_codespaces import __main__ as cli
    from agent_codespaces.plugin_preparation import prepare_attached

    checkout = tmp_path / "initially-absent"
    events = []
    prelude = "export SYNTHETIC_RELAY_TOKEN=launch-only; export SYNTHETIC_RELAY_ACCOUNT=selected; "
    hook = "python3 -c " + shlex.quote(
        "import os,pathlib;"
        "assert os.environ['SYNTHETIC_RELAY_TOKEN']=='launch-only';"
        "assert os.environ['SYNTHETIC_RELAY_ACCOUNT']=='selected';"
        f"pathlib.Path({str(checkout / '.git')!r}).mkdir(parents=True)"
    )
    manager = SimpleNamespace(
        ensure_connected=AsyncMock(return_value=SimpleNamespace(config=object())),
        disconnect=AsyncMock(),
    )

    async def execute(manager, name, command, **kwargs):
        argv = shlex.split(command)
        if argv[:3] != ["bash", "-l", "-c"]:
            return SimpleNamespace(exit_code=1, stderr="missing hook login environment")
        token, account, reader = argv[3].split("; ", 2)
        environment = {}
        for export in (token, account):
            word, assignment = shlex.split(export)
            assert word == "export"
            key, value = assignment.split("=", 1)
            environment[key] = value
        reader = shlex.split(reader)
        assert reader[0] == "python3"
        child = subprocess.run(
            [sys.executable, *reader[1:]], env=environment, capture_output=True,
            text=True, timeout=10, creationflags=no_window_flags(),
        )
        assert child.returncode == 0, child.stderr
        events.append("authenticated-hook")
        return SimpleNamespace(exit_code=child.returncode, stderr=child.stderr)

    async def install(*args, **kwargs):
        assert (checkout / ".git").is_dir(), "cold hook had no relay authentication context"
        assert kwargs["prelude"] == prelude
        events.append("prepare")
        return []

    async def record(event, *args, **kwargs):
        events.append(event)

    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    monkeypatch.setattr("venue_copilot.live_session_for", lambda *a: {})
    monkeypatch.setattr(cli, "_lookup_codespace_repo", lambda *a: "example/project")
    monkeypatch.setattr(cli, "validate_context", lambda: None)
    monkeypatch.setattr(cli, "exec_with_retry", execute)
    monkeypatch.setattr("agent_codespaces.provision.build_provision_command", lambda *a, **kw: hook)
    monkeypatch.setattr("agent_codespaces.connection_owner.await_owner_relay",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(cli, "_start_supervised_relay", AsyncMock(return_value=SimpleNamespace(
        stop=AsyncMock(),
    )))
    monkeypatch.setattr(cli, "_provision_relay_helpers", lambda *a: record("helpers"))
    monkeypatch.setattr("agent_codespaces.relay_launch.build_relay_launch_env", lambda *a: (prelude, 12001))
    monkeypatch.setattr("agent_codespaces.plugin_preparation.require_relay_ready",
                        lambda *a: record("ready"))
    monkeypatch.setattr("agent_codespaces.plugin_preparation.prepare_plugins", install)
    await prepare_attached(
        SimpleNamespace(name="synthetic", worktree_id=None, no_relay=False),
        SimpleNamespace(resolved_workspace_folder_for=lambda repo: str(checkout),
                        provision_for_repo=lambda repo: object()),
    )
    assert events == ["ready", "helpers", "authenticated-hook", "prepare"]
    manager.disconnect.assert_awaited_once()
