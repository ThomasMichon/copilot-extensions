"""Trusted container CLI consumes the shared preparation result before launch."""

from types import SimpleNamespace

import pytest

from agent_containers.plugin_preparation import prepare_plugins


def test_real_producer_packet_returns_locked_metadata_with_unchanged_directory_contract(tmp_path):
    import os
    import subprocess
    import sys
    from plugin_resolve.registration import atomic_json

    target, controller, home = [tmp_path / name for name in ("target", "controller", "home")]
    subprocess.run(["git", "init", "--quiet", str(target)], check=True, timeout=10,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    payload = controller / ".ai" / "helper"
    payload.mkdir(parents=True)
    atomic_json(payload / "plugin.json", {"name": "helper", "version": "2.0.0"})
    atomic_json(payload.parent / "marketplace.json", {
        "name": "local", "owner": {"name": "Synthetic"}, "plugins": [{"name": "helper", "source": "helper"}],
    })
    settings = controller / ".github" / "copilot"
    settings.mkdir(parents=True)
    atomic_json(settings / "settings.json", {
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "./.ai"}}},
    })
    def run(command, stdin, **kwargs):
        assert command == "bash -ls"
        code = stdin.decode().split("<<'VENUE_PLUGIN_PREPARE'\n", 1)[1].rsplit("VENUE_PLUGIN_PREPARE", 1)[0]
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-"], input=code,
            env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
            cwd=target, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode, result.stdout, result.stderr
    context = {}
    directories = prepare_plugins(
        SimpleNamespace(stage_plugins=["helper@local"], plugin_roots=[str(controller)],
                        hosting_live_attachment=True),
        SimpleNamespace(actual_profile="trusted", workspace_folder=str(target)),
        run_input=run, prepared_context=context,
    )
    assert context["plugin_context"]["cwd"] == str(target)
    assert context["plugin_context"]["logical_payloads"] == {"helper@local": directories[0]}
    assert context["plugin_context"]["plugins"][0]["version"] == "2.0.0"


@pytest.mark.parametrize("bare_source", [False, True])
def test_registered_target_source_rebinds_through_container_script(monkeypatch, tmp_path, bare_source):
    import base64
    import json
    import re
    import subprocess
    from pathlib import Path
    from agent_containers.plugin_preparation import _script
    from plugin_resolve.preparation import prepare

    anchor, target, controller = [tmp_path / name for name in ("anchor", "target", "controller")]
    for root in (anchor, target):
        subprocess.run(
            ["git", "init", "--quiet", *(["--bare"] if root == anchor and bare_source else []), str(root)],
            check=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        payload = root / ".ai" / "sample"
        payload.mkdir(parents=True)
        (payload / "plugin.json").write_text(json.dumps({"name": "sample", "version": "1.0.0"}))
        (payload / "marker").write_text(root.name)
        (payload.parent / "marketplace.json").write_text(json.dumps({
            "name": "synthetic", "owner": {"name": "Synthetic"},
            "plugins": [{"name": "sample", "source": "./sample"}],
        }))
    settings = controller / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            "synthetic": {"source": {"source": "directory", "path": str(anchor / ".ai")}},
        },
    }))
    observed = []
    monkeypatch.setattr(
        "agent_containers.plugin_preparation._registered_source_roots",
        lambda repository: observed.append(repository) or [anchor],
    )
    script, _run = _script(
        SimpleNamespace(stage_plugins=["sample@synthetic"], plugin_roots=[str(controller)],
                        hosting_live_attachment=True),
        SimpleNamespace(actual_profile="trusted", workspace_folder=str(target),
                        info=SimpleNamespace(repo="registered-alias"),
                        fleet=SimpleNamespace(repo="wrong-fallback", devcontainer_path=str(controller))),
        run_input=lambda *a, **kw: (0, "", ""),
    )
    encoded = re.search(r"request=json.loads\(base64.b64decode\('([^']+)'\)\)", script).group(1)
    request = json.loads(base64.b64decode(encoded))
    assert observed == ["registered-alias"]
    assert request["plugins"][0]["target_relative"]["marketplace_relative"] == ".ai"
    assert "archive" not in request["plugins"][0]
    anchor.rename(tmp_path / "unavailable-host-source")
    prepared = prepare(request)
    assert prepared == {"sample@synthetic": str(target / ".ai" / "sample")}
    assert Path(prepared["sample@synthetic"], "marker").read_text() == "target"


@pytest.mark.parametrize("repository", ["registered-alias", "example/app", "Example/App"])
def test_container_binding_uses_registered_alias_or_full_repository(monkeypatch, tmp_path, repository):
    import sys
    from unittest.mock import Mock
    from agent_containers.plugin_preparation import _registered_source_roots

    query = Mock(side_effect=[
        {"inventory_authority": "complete", "repos": [
            {"name": "registered-alias", "remote": "git@github.com:example/app.git"},
            {"name": "app", "remote": "git@github.com:another/app.git"},
        ]},
        {"registration": "ready", "name": "registered-alias",
         "remote": "git@github.com:example/app.git", "path": str(tmp_path)},
    ])
    monkeypatch.setattr("agent_containers.plugin_preparation._worktrees_registry", query)
    monkeypatch.setitem(sys.modules, "agent_worktrees", None)
    assert _registered_source_roots(repository) == [tmp_path]
    assert query.call_args_list[0].args == (["repos", "list", "--strict", "--json"],)
    assert query.call_args_list[1].args == (
        ["repos", "find", "registered-alias", "--registered-only", "--json"],
    )


@pytest.mark.parametrize("peer_present", [False, True])
def test_container_registry_lookup_preserves_same_cell_peer_boundary(monkeypatch, tmp_path, peer_present):
    import json
    from unittest.mock import Mock
    from agent_containers import _peer_launch
    from agent_containers.plugin_preparation import _worktrees_registry

    peer = tmp_path / "plugins" / "agent-worktrees"
    if peer_present:
        peer.mkdir(parents=True)
    own = {"cellRoot": str(tmp_path), "pluginRoot": str(tmp_path / "plugins" / "agent-containers")}
    monkeypatch.setattr("agent_containers.config._installation_owner", lambda: own)
    monkeypatch.setenv(_peer_launch.CONTEXT_ENV, "synthetic-explicit-context")
    prefix = Mock(return_value=["same-cell-peer"])
    monkeypatch.setattr(_peer_launch, "launch_prefix", prefix)
    monkeypatch.setattr(
        "agent_containers.plugin_preparation.shutil.which",
        lambda *a: pytest.fail("explicit context cannot use a global peer"),
    )
    run = Mock(return_value=SimpleNamespace(
        returncode=0, stdout=json.dumps({"inventory_authority": "complete", "repos": []}), stderr="",
    ))
    monkeypatch.setattr("agent_containers.plugin_preparation.subprocess.run", run)
    result = _worktrees_registry(["repos", "list", "--strict", "--json"])
    if peer_present:
        assert result["inventory_authority"] == "complete"
        assert run.call_args.args[0] == ["same-cell-peer", "repos", "list", "--strict", "--json"]
        assert prefix.call_args.args[-1] == "agent-worktrees"
    else:
        assert result is None
        run.assert_not_called()
        prefix.assert_not_called()


def test_preparation_uses_selected_checkout_and_worktree(monkeypatch):
    observed = []
    monkeypatch.setattr(
        "agent_containers.plugin_preparation.build_prepare_script",
        lambda plan, workspace, **kw: observed.append((plan, workspace, kw)) or "PREPARE",
    )
    args = SimpleNamespace(stage_plugins=[], plugin_roots=[], worktree_id="selected",
                           hosting_live_attachment=True)
    target = SimpleNamespace(actual_profile="trusted", workspace_folder="/workspaces/project")

    def run(command, stdin, *, timeout):
        if "resolve --json --dry-run" in command:
            assert "--worktree-id selected" in command
            return 0, '{"action":"exec","worktree_id":"selected","work_dir":"/workspaces/selected"}', ""
        assert command == "bash -ls" and stdin.endswith(b"PREPARE")
        return 0, '__VENUE_PLUGINS__{"sample@synthetic":"/prepared/sample"}', ""

    assert prepare_plugins(args, target, run_input=run) == ["/prepared/sample"]
    assert observed == [({"plugins": [], "marketplaces": {}}, "/workspaces/selected", {})]


def test_restricted_container_is_not_projected():
    with pytest.raises(RuntimeError, match="Restricted"):
        prepare_plugins(SimpleNamespace(), SimpleNamespace(actual_profile="restricted"))


def test_install_or_copy_failure_blocks_the_cli():
    target = SimpleNamespace(actual_profile="trusted", workspace_folder="/workspaces/project")
    with pytest.raises(RuntimeError, match="install failed"):
        prepare_plugins(
            SimpleNamespace(), target,
            run_input=lambda *a, **kw: (1, "", "install failed"),
        )


def test_explicit_sources_retain_the_harness_only_propagation_filter(monkeypatch):
    observed = []
    monkeypatch.setattr(
        "agent_containers.plugin_preparation.build_plan",
        lambda specs, roots: observed.append(specs) or {"plugins": [], "marketplaces": {}},
    )
    target = SimpleNamespace(actual_profile="trusted", workspace_folder="/workspaces/project")
    assert prepare_plugins(
        SimpleNamespace(stage_plugins=["control-harness@synthetic"], plugin_roots=[]),
        target, run_input=lambda *a, **kw: (0, "__VENUE_PLUGINS__{}", ""),
    ) == []
    assert observed == [[]]


@pytest.mark.parametrize("source", [
    "sample@synthetic", {"source": "sample@synthetic", "enable": False},
])
def test_preparation_preserves_plain_and_typed_selections(monkeypatch, source):
    observed = []
    monkeypatch.setattr(
        "agent_containers.plugin_preparation.build_plan",
        lambda specs, roots: observed.extend(specs) or {"plugins": [], "marketplaces": {}},
    )
    prepare_plugins(
        SimpleNamespace(stage_plugins=[source], plugin_roots=[]),
        SimpleNamespace(actual_profile="trusted", workspace_folder="/workspaces/project"),
        run_input=lambda *a, **kw: (0, "__VENUE_PLUGINS__{}", ""),
    )
    assert observed == [{
        "source": "sample@synthetic", "enable": isinstance(source, str),
    }]


def test_disabled_harness_selection_is_filtered_before_preparation(monkeypatch):
    observed = []
    monkeypatch.setattr(
        "agent_containers.plugin_preparation.build_plan",
        lambda specs, roots: observed.append(specs) or {"plugins": [], "marketplaces": {}},
    )
    prepare_plugins(
        SimpleNamespace(stage_plugins=[{"source": "control-harness@synthetic", "enable": False}],
                        plugin_roots=[]),
        SimpleNamespace(actual_profile="trusted", workspace_folder="/workspaces/project"),
        run_input=lambda *a, **kw: (0, "__VENUE_PLUGINS__{}", ""),
    )
    assert observed == [[]]


def test_container_installer_sources_launch_env_and_waits_for_relay(monkeypatch):
    from agent_containers.plugin_preparation import _script
    monkeypatch.setattr("agent_containers.plugin_preparation.build_prepare_script",
                        lambda *a, **kw: "INSTALL_COPY_REGISTER")
    script, _runner = _script(
        SimpleNamespace(), SimpleNamespace(actual_profile="trusted", workspace_folder="/workspaces/project"),
        run_input=lambda *a, **kw: (0, "", ""), remote_env="/launch/general.env", relay_port=12001,
    )
    assert script.index(". /launch/general.env") < script.index("ping") < script.index("INSTALL_COPY_REGISTER")
    assert "rm -f /launch/general.env" not in script


def test_attached_stage_only_transfers_script_then_defers_execution_to_forwarded_channel(monkeypatch):
    from agent_containers.plugin_preparation import stage_for_attached_launch
    calls = []

    def run(command, stdin, **kwargs):
        calls.append((command, stdin))
        return 0, "", ""

    monkeypatch.setattr("agent_containers.plugin_preparation._script",
                        lambda *a, **kw: ("INSTALL_COPY_REGISTER", run))
    prefix = stage_for_attached_launch(object(), object())
    assert calls[0][1] == b"INSTALL_COPY_REGISTER"
    assert "cat >" in calls[0][0]
    assert prefix.startswith('bash "$HOME/.acp-staged-plugins/preparation/')
    assert prefix.endswith(" && ")


def test_attached_stage_retains_exact_resolved_worktree(monkeypatch):
    import json
    from agent_containers.plugin_preparation import _script
    seen = []
    monkeypatch.setattr(
        "agent_containers.plugin_preparation.build_prepare_script",
        lambda plan, workspace: seen.append(workspace) or "PREPARE",
    )

    def run(command, stdin, **kwargs):
        return 0, json.dumps({
            "action": "exec", "worktree_id": "selected", "work_dir": "/actual/selected ' checkout",
        }), ""

    context = {}
    _script(
        SimpleNamespace(worktree_id="selected"),
        SimpleNamespace(actual_profile="trusted", workspace_folder="/conventional/wrong"),
        run_input=run, prepared_context=context,
    )
    assert context == {"workspace": "/actual/selected ' checkout"}
    assert seen == [context["workspace"]]


@pytest.mark.parametrize("login_project", ["neutral", "wrong-project"])
@pytest.mark.parametrize("worktree", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_attached_child_login_uses_prepared_checkout_with_real_bash(
    tmp_path, login_project, worktree, missing,
):
    import os
    import shutil
    import shlex
    import subprocess
    from pathlib import Path
    from venue_copilot import build_copilot_remote_command
    from agent_containers.plugin_preparation import attached_launch_command

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
    from agent_containers.plugin_preparation import attached_launch_command
    with pytest.raises(RuntimeError, match="prepared execution checkout"):
        attached_launch_command("agent-worktrees copilot --anchor", workspace)


def test_failed_staging_never_publishes_attached_execution_context(monkeypatch):
    from agent_containers.plugin_preparation import stage_for_attached_launch

    def script(*a, prepared_context, **kwargs):
        prepared_context["workspace"] = "/prepared/execution"
        return "PREPARE", lambda *a, **kw: (1, "", "copy failed")

    monkeypatch.setattr("agent_containers.plugin_preparation._script", script)
    context = {}
    with pytest.raises(RuntimeError, match="copy failed"):
        stage_for_attached_launch(object(), object(), prepared_context=context)
    assert context == {}
