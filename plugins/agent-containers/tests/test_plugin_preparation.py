"""Trusted container CLI consumes the shared preparation result before launch."""

from types import SimpleNamespace

import pytest

from agent_containers.plugin_preparation import prepare_plugins


def test_preparation_uses_selected_checkout_and_worktree(monkeypatch):
    observed = []
    monkeypatch.setattr(
        "agent_containers.plugin_preparation.build_prepare_script",
        lambda plan, workspace, **kw: observed.append((plan, workspace, kw)) or "PREPARE",
    )
    args = SimpleNamespace(stage_plugins=[], plugin_roots=[], worktree_id="selected")
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
