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
