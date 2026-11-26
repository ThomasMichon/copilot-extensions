from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config, update_runtime
from agent_worktrees.update_runtime_plan import RuntimeUpdateStep


@pytest.mark.parametrize("bundled", [False, True])
@pytest.mark.parametrize("scope", ["selected", "incomplete", "disabled"])
def test_targeted_ssh_update_preserves_repository_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundled: bool, scope: str,
) -> None:
    from agent_worktrees import installation_updates, reconcile, update_options, update_runtime_plan

    payload, engine = tmp_path / "ssh", tmp_path / "engine"
    payload.mkdir()
    (payload / "plugin.json").write_text(json.dumps({"runtimeScope": "universal"}))
    monkeypatch.setattr(reconcile, "core_installed_payload_dir",
                        lambda name: payload if name == "agent-ssh" else engine)
    monkeypatch.setattr(update_options, "_module_dirs", lambda _: {"agent-ssh"} if bundled else set())
    monkeypatch.setattr(config, "detect_platform", lambda: "linux")
    contexts = (tmp_path / "first", tmp_path / "second") if scope != "disabled" else ()
    target = update_runtime._RegisteredPluginTarget(
        tmp_path, update_runtime._PluginActivation.ACTIVE, contexts,
        discovery_complete=scope != "incomplete",
    )
    expected = contexts if scope != "incomplete" else None
    calls: list[object] = []

    def plan(name: str, *args: object, **kwargs: object) -> RuntimeUpdateStep:
        calls.append(kwargs["repository_contexts"])
        return RuntimeUpdateStep(name=name, phase="runtime_reconcile", payload=payload)

    monkeypatch.setattr(update_runtime_plan, "plan_module", plan)
    monkeypatch.setattr(update_runtime_plan, "plan_registered", plan)
    installation_updates.target_plan("agent-ssh", target)
    assert calls == [expected]


@pytest.mark.parametrize("state,expected", [("applied", "SKIPPED (current)"), ("failed", "failed")])
def test_standard_current_ssh_update_still_invokes_dependency_maintenance(
    tmp_path: Path, state: str, expected: str,
) -> None:
    payload = tmp_path / "payload"
    script = payload / "scripts" / "maintain-driver-dependencies.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json\nfrom pathlib import Path\n"
        "Path('maintained').write_text('explicit-update', encoding='utf-8')\n"
        f"print({json.dumps(json.dumps({'schema': 'agent-ssh.driver-result', 'version': 1, 'state': state, 'detail': 'owner-refused'}))})\n",
        encoding="utf-8",
    )
    step = RuntimeUpdateStep(
        name="agent-ssh", phase="runtime_reconcile", payload=payload,
        status="SKIPPED (current)", project_root=tmp_path, environment=dict(os.environ),
    )
    status = update_runtime._execute_runtime_step(step)
    assert (tmp_path / "maintained").read_text(encoding="utf-8") == "explicit-update"
    assert status == expected if expected != "failed" else "maintenance failed" in status


def test_inactive_ssh_inventory_does_not_run_dependency_maintenance(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    script = payload / "scripts" / "maintain-driver-dependencies.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise RuntimeError('inactive dependency executed')\n", encoding="utf-8")
    step = RuntimeUpdateStep(
        name="agent-ssh", phase="runtime_reconcile", payload=payload,
        status="SKIPPED (current)", required=False,
    )
    assert update_runtime._execute_runtime_step(step) == "SKIPPED (current)"


def test_standard_ssh_update_maintains_every_active_repository(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    script = payload / "scripts" / "maintain-driver-dependencies.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "roots=[Path(sys.argv[i+1]) for i,v in enumerate(sys.argv) if v=='--project-root']\n"
        "for root in roots: (root/'maintained').write_text('explicit-update', encoding='utf-8')\n"
        "print(json.dumps({'schema':'agent-ssh.driver-result','version':1,'state':'applied'}))\n",
        encoding="utf-8",
    )
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    target = update_runtime._RegisteredPluginTarget(
        first, update_runtime._PluginActivation.ACTIVE, (first, second),
    )
    contexts = target.planning_contexts("agent-ssh")
    assert contexts == (first, second)
    step = RuntimeUpdateStep(
        name="agent-ssh", phase="runtime_reconcile", payload=payload,
        status="SKIPPED (current)", project_root=first,
        repository_contexts=contexts, environment=dict(os.environ),
    )
    assert update_runtime._execute_runtime_step(step) == "SKIPPED (current)"
    assert (first / "maintained").exists()
    assert (second / "maintained").exists()


def test_global_ssh_activation_is_resolved_in_each_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import reconcile

    roots = [tmp_path / name for name in ("first", "disabled", "inherited")]
    for root in roots:
        (root / ".github" / "copilot").mkdir(parents=True)
    (roots[0] / ".git").mkdir()
    nested = roots[0] / "nested"
    nested.mkdir()
    (roots[1] / ".github" / "copilot" / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"agent-ssh@copilot-extensions": False},
    }), encoding="utf-8")
    (roots[2] / ".github" / "copilot" / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"agent-vault@copilot-extensions": False},
    }), encoding="utf-8")
    monkeypatch.setattr(update_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setenv("AGENT_WORKTREES_UPDATE_CONTEXT", str(nested))
    monkeypatch.setattr(
        config, "load_config",
        lambda: SimpleNamespace(repos={str(i): SimpleNamespace(anchor=str(root)) for i, root in enumerate(roots[1:])}),
    )
    monkeypatch.setattr(reconcile, "read_user_enabled_plugins", lambda: ["agent-ssh", "agent-vault"])
    monkeypatch.setattr(reconcile, "read_installed_plugins", lambda: [])
    targets = update_runtime._registered_plugin_targets()
    assert targets["agent-ssh"].planning_contexts("agent-ssh") == (roots[0], roots[2])
    assert targets["agent-vault"].planning_contexts("agent-vault") == (roots[0], roots[1])


def test_unreadable_repository_settings_refuse_ssh_dependency_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import reconcile

    first, broken = tmp_path / "first", tmp_path / "broken"
    first.mkdir()
    (broken / ".github" / "copilot").mkdir(parents=True)
    (broken / ".github" / "copilot" / "settings.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(update_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(update_runtime, "_invocation_update_context", lambda: first)
    monkeypatch.setattr(
        config, "load_config",
        lambda: SimpleNamespace(repos={"broken": SimpleNamespace(anchor=str(broken))}),
    )
    monkeypatch.setattr(reconcile, "read_user_enabled_plugins", lambda: ["agent-ssh"])
    monkeypatch.setattr(reconcile, "read_installed_plugins", lambda: [])
    target = update_runtime._registered_plugin_targets()["agent-ssh"]
    assert target.discovery_complete is False
    assert target.planning_contexts("agent-ssh") is None


def test_all_repository_ssh_opt_outs_do_not_fall_back_to_the_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import reconcile

    root = tmp_path / "disabled"
    (root / ".github" / "copilot").mkdir(parents=True)
    (root / ".github" / "copilot" / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"agent-ssh@copilot-extensions": False},
    }), encoding="utf-8")
    monkeypatch.setattr(update_runtime, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(update_runtime, "_invocation_update_context", lambda: root)
    monkeypatch.setattr(
        config, "load_config",
        lambda: SimpleNamespace(repos={"disabled": SimpleNamespace(anchor=str(root))}),
    )
    monkeypatch.setattr(reconcile, "read_user_enabled_plugins", lambda: ["agent-ssh"])
    monkeypatch.setattr(reconcile, "read_installed_plugins", lambda: [])
    target = update_runtime._registered_plugin_targets()["agent-ssh"]
    contexts = target.planning_contexts("agent-ssh")
    assert contexts == ()
    payload = tmp_path / "payload"
    script = payload / "scripts" / "maintain-driver-dependencies.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise RuntimeError('disabled repository maintenance ran')\n", encoding="utf-8")
    step = RuntimeUpdateStep(
        name="agent-ssh", phase="runtime_reconcile", payload=payload,
        status="SKIPPED (current)", project_root=root, repository_contexts=contexts,
    )
    assert update_runtime._execute_runtime_step(step) == "SKIPPED (current)"


def test_update_preserves_unconfirmed_native_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import owned_process

    script = tmp_path / "scripts" / "maintain-driver-dependencies.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    failure = owned_process.OwnedCleanupError(None)

    def run(*args: object, **kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(owned_process, "run_caller_owned", run)
    step = RuntimeUpdateStep(
        name="agent-ssh", phase="runtime_reconcile", payload=tmp_path,
        status="SKIPPED (current)", project_root=tmp_path,
    )
    with pytest.raises(owned_process.OwnedCleanupError) as caught:
        update_runtime._execute_runtime_step(step)
    assert caught.value is failure
