from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager.repository_suite_roster import main, plan


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    target, source = tmp_path / "target", tmp_path / "source"
    scopes = {
        "agent-service": "machine-gated", "agent-utility": "universal",
        "agent-index": "machine-gated", "agent-vault": "machine-gated", "docs": "none",
    }
    write(source / ".github/plugin/marketplace.json", {
        "name": "copilot-extensions",
        "plugins": [{"name": n, "source": f"./plugins/{n}"} for n in scopes],
    })
    for name, scope in scopes.items():
        write(source / "plugins" / name / "plugin.json", {"name": name, "runtimeScope": scope})
    write(target / ".github/copilot/settings.json", {
        "enabledPlugins": {n + "@copilot-extensions": True for n in scopes},
    })
    return target, source


def test_local_override_foreign_market_and_gated_role(roots: tuple[Path, Path]) -> None:
    target, source = roots
    write(target / ".github/copilot/settings.local.json", {
        "enabledPlugins": {"docs@copilot-extensions": False, "agent-other@another-market": True},
    })
    gate = target / "services.yaml"
    gate.write_text("plugins:\n  - name: agent-service\n    deploy_machines: [physical]\n", encoding="utf-8")
    result = plan(target, source, gate_paths=(gate,), machines=("guest", "physical"))
    assert result["complete"] is True
    assert result["required"] == ["worktree-manager", "agent-service", "agent-utility"]
    plugins = result["plugins"]
    assert isinstance(plugins, dict)
    assert set(plugins) == {"agent-service", "agent-utility", "agent-vault", "agent-index"}


def test_absent_gate_does_not_infer_app_participation(roots: tuple[Path, Path]) -> None:
    target, source = roots
    pending = plan(target, source, gate_paths=(target / "missing.yaml",), machines=())
    assert pending["complete"] is False
    assert pending["unresolved"] == ["agent-index", "agent-vault"]
    ready = plan(target, source, gate_paths=(), machines=(), index_role="client", vault_state="configuration-empty")
    assert ready["complete"] is True
    assert ready["required"] == ["worktree-manager", "agent-index", "agent-service", "agent-utility"]


def test_bad_input_never_uses_installed_inventory(roots: tuple[Path, Path]) -> None:
    target, source = roots
    settings = target / ".github/copilot/settings.local.json"
    settings.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="indeterminate"):
        plan(target, source, gate_paths=(), machines=())
    write(settings, {"enabledPlugins": {"agent-missing@copilot-extensions": True}})
    with pytest.raises(ValueError, match="agent-missing"):
        plan(target, source, gate_paths=(), machines=())


def test_empty_allowlist_is_decisive_but_malformed_selector_is_not(roots: tuple[Path, Path]) -> None:
    target, source = roots
    gate = target / "gate.json"
    write(gate, {"plugins": [{"name": "agent-service", "deploy_machines": []}]})
    result = plan(target, source, gate_paths=(gate,), machines=())
    assert result["complete"] is True
    assert result["required"] == ["worktree-manager", "agent-utility"]
    write(gate, {"repos": {"h": {"services": [{"name": "agent-service", "deploy_machines": "host"}]}}})
    with pytest.raises(ValueError, match="machine selector"):
        plan(target, source, gate_paths=(gate,), machines=("host",))


def test_cli_separates_complete_incomplete_and_invalid_plans(
    roots: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    target, source = roots
    args = ["--worktree", str(target), "--source", str(source), "--no-gate"]
    assert main(args + ["--index-role", "client", "--vault-state", "configured"]) == 0
    complete = capsys.readouterr()
    assert json.loads(complete.out)["complete"] is True and not complete.err
    assert main(args) == 2
    incomplete = capsys.readouterr()
    assert json.loads(incomplete.out)["unresolved"] == ["agent-index", "agent-vault"] and not incomplete.err
    (target / ".github/copilot/settings.json").write_text("{", encoding="utf-8")
    assert main(args) == 1
    invalid = capsys.readouterr()
    assert not invalid.out and "indeterminate" in invalid.err


def test_linked_overlay_must_resolve(roots: tuple[Path, Path]) -> None:
    target, source = roots
    overlay = target / ".github/copilot/settings.local.json"
    backing = target / "overlay.json"
    try:
        overlay.symlink_to(backing)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symlink creation privilege")
        raise
    with pytest.raises(ValueError, match="dangling link"):
        plan(target, source, gate_paths=(), machines=())
    write(backing, {"enabledPlugins": {"agent-utility@copilot-extensions": False}})
    result = plan(target, source, gate_paths=(), machines=(), index_role="client", vault_state="configured")
    assert result["complete"] is True
    assert result["required"] == ["worktree-manager", "agent-index", "agent-service", "agent-vault"]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction")
def test_dangling_settings_parent_is_not_absent(roots: tuple[Path, Path]) -> None:
    target, source = roots
    subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(target / ".claude"), str(target / "missing")],
        capture_output=True, timeout=20, check=True, **no_window_kwargs(),
    )
    with pytest.raises(ValueError, match="dangling link"):
        plan(target, source, gate_paths=(), machines=())
