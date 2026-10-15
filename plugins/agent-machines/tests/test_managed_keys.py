from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs

from agent_machines import __main__ as cli
from agent_machines.manifest import RequirementPackage
from agent_machines.reconcile import plan, plan_to_dict, restore, restore_result_to_dict


def _package(name="example/settings", **kwargs):
    return RequirementPackage(name=name, schema_version=4, gate=["*"], **kwargs)


def test_managed_keys_follow_active_settings_contributions(tmp_path):
    package = _package(manage={
        "copilot.settings": {
            "disposition": "enforce",
            "values": {"model": "private-value", "effortLevel": "high"},
        },
        "copilot.settings.floor": {
            "disposition": "ensure-present",
            "value": {"model": "floor", "footer": {"hidden": True}},
        },
        "copilot.settings.plugin-activation": {
            "disposition": "ensure-absent",
            "keys": {"enabledPlugins": ["optional@example"]},
        },
        "copilot.settingsish": {
            "disposition": "enforce", "values": {"wrongPrefix": True},
        },
        **{
            f"copilot.settings.{disposition}": {
                "disposition": disposition,
                "values": {f"unused-{disposition}": "private-value"},
            }
            for disposition in (
                "ignore", "exclude", "capture-only", "prune", "prerequisite-check",
            )
        },
    })
    original = copy.deepcopy(package)
    payload = plan_to_dict(plan([package], "box"))
    assert payload["managed_keys"] == {
        "copilot.settings": ["effortLevel", "enabledPlugins", "footer", "model"],
    }
    assert "private-value" not in json.dumps(payload["managed_keys"])
    assert package == original
    assert list(tmp_path.iterdir()) == []


def test_empty_settings_payload_does_not_declare_a_settings_operation():
    package = _package(manage={"copilot.settings.preferences": {
        "disposition": "enforce", "values": {},
    }})
    assert plan([package], "box").managed_keys == {"copilot.settings": []}


def test_managed_keys_respect_gating_and_machine_layering():
    package = _package(
        manage={"copilot.settings": {
            "disposition": "enforce",
            "values": {"model": "base", "footer": "remove"},
        }},
        per_machine={"box": {"manage": {"copilot.settings": {
            "values": {"footer": None, "effortLevel": "high"},
        }}}},
    )
    unrelated = _package(
        name="example/other",
        manage={"copilot.settings": {
            "disposition": "enforce", "values": {"unrelated": True},
        }},
    )
    unrelated.gate = ["other-box"]
    assert plan([unrelated, package], "box").managed_keys == {
        "copilot.settings": ["effortLevel", "model"],
    }
    assert plan([], "box").managed_keys == {"copilot.settings": []}
    assert plan([unrelated], "box").managed_keys == {"copilot.settings": []}


def test_restore_serializes_same_plan_and_covers_declared_keys(tmp_path):
    package = _package(manage={
        "copilot.settings.preferences": {
            "disposition": "enforce", "values": {"model": "example"},
        },
        "copilot.settings.floor": {
            "disposition": "ensure-present", "values": {"footer": {"hidden": True}},
        },
    })
    expected = plan_to_dict(plan([package], "box"))
    result = restore_result_to_dict(restore(
        [package], "box", home=tmp_path, only=["copilot.settings"],
    ))
    assert result["plan"] == expected
    assert result["surfaces"][0]["applied_keys"] == expected["managed_keys"]["copilot.settings"]
    assert not (tmp_path / "settings.json").exists()


@pytest.mark.parametrize("verb", ["plan", "restore"])
def test_cli_json_managed_keys_use_exact_repo_scope(monkeypatch, tmp_path, capsys, verb):
    package_root = tmp_path / ".copilot-extensions" / "agent-machines" / "all"
    package_root.mkdir(parents=True)
    (package_root / "settings.yaml").write_text(
        "schema_version: 4\npackage: example/settings\ngate: ['*']\nmanage:\n"
        "  copilot.settings.preferences:\n"
        "    disposition: enforce\n"
        "    values:\n      model: example\n      effortLevel: high\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli._discover, "resolve_registered_repo", lambda name: ("example", tmp_path))
    monkeypatch.setattr(
        cli, "_collect_all_packages",
        lambda *args, **kwargs: pytest.fail("exact repo scope must not collect the machine union"),
    )
    args = [verb, "--repo", "example", "--machine", "box", "--json"]
    if verb == "restore":
        args += ["--only", "copilot.settings"]
        monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "copilot"))
    assert cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "repo:example"
    selected_plan = payload["plan"] if verb == "restore" else payload
    assert selected_plan["managed_keys"] == {"copilot.settings": ["effortLevel", "model"]}
    if verb == "restore":
        assert payload["surfaces"][0]["applied_keys"] == ["effortLevel", "model"]


@pytest.mark.parametrize("verb", ["plan", "restore"])
def test_native_cli_managed_keys_in_fresh_repo(tmp_path, verb):
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True, capture_output=True, timeout=10, **no_window_kwargs(),
    )
    package_root = tmp_path / ".copilot-extensions" / "agent-machines" / "all"
    package_root.mkdir(parents=True)
    (package_root / "settings.yaml").write_text(
        "schema_version: 4\npackage: example/settings\ngate: ['*']\nmanage:\n"
        "  copilot.settings.preferences:\n"
        "    disposition: enforce\n"
        "    values:\n      model: example\n      effortLevel: high\n",
        encoding="utf-8",
    )
    args = [
        sys.executable, "-m", "agent_machines", verb,
        "--repo", str(tmp_path), "--machine", "box", "--json",
    ]
    if verb == "restore":
        args += ["--only", "copilot.settings"]
    outcome = subprocess.run(
        args, cwd=tmp_path, capture_output=True, text=True,
        timeout=15, **no_window_kwargs(),
    )
    assert outcome.returncode == 0, outcome.stderr
    payload = json.loads(outcome.stdout)
    assert payload["scope"].startswith("repo:")
    selected_plan = payload["plan"] if verb == "restore" else payload
    assert selected_plan["managed_keys"] == {"copilot.settings": ["effortLevel", "model"]}
    if verb == "restore":
        assert payload["surfaces"][0]["applied_keys"] == ["effortLevel", "model"]
