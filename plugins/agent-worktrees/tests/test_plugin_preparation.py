from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import pytest

from plugin_activation import activation_preservation_pending, preserve_activation_durably, state
from agent_worktrees import plugin_preparation as preparation, update_acquisition

pytestmark = pytest.mark.guard


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def selected(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    settings = repo / ".github" / "copilot" / "settings.json"
    write(settings, {
        "enabledPlugins": {"sample@first": True, "sample@second": True, "disabled@first": False},
        "extraKnownMarketplaces": {
            "first": {"source": {"source": "github", "repo": "example/first"}},
            "second": {"source": {"source": "git", "url": "https://example.invalid/second.git", "ref": "stable"}},
        },
    })
    monkeypatch.setattr(preparation.reconcile, "resolve_copilot", lambda: sys.executable)
    return home, repo, settings


def install(home: Path, name: str, marketplace: str) -> None:
    write(home / ".copilot" / "installed-plugins" / marketplace / name / "plugin.json", {
        "name": name, "version": "1.0.0",
    })


def test_observation_is_read_only_and_preserves_qualified_names(selected, monkeypatch):
    home, repo, _ = selected
    install(home, "sample", "first")
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: pytest.fail("read installed"))
    before = sorted(path.relative_to(home) for path in home.rglob("*"))
    result = preparation.observe(repo, home=home)
    assert not result["payloadsReady"]
    assert [(row["source"], row["state"]) for row in result["entries"]] == [
        ("sample@first", "ready"), ("sample@second", "needed"),
    ]
    assert Path(result["entries"][0]["payloadRoot"]).is_dir()
    assert result["entries"][1]["payloadRoot"] is None
    assert before == sorted(path.relative_to(home) for path in home.rglob("*"))


def test_apply_acquires_only_missing_and_never_uses_core_name_lookup(selected, monkeypatch):
    home, repo, settings = selected
    original = settings.read_bytes()
    install(home, "sample", "first")
    calls = []

    def acquire(name, marketplace, copilot, *, cwd, revalidate, copilot_home):
        revalidate()
        calls.append((name, marketplace, cwd))
        install(copilot_home.parent, name, marketplace)
        return "OK (installed)"

    monkeypatch.setattr(update_acquisition, "acquire_missing", acquire)
    monkeypatch.setattr(
        preparation.reconcile, "core_installed_payload_dir",
        lambda *args: pytest.fail("foreign marketplace resolved through core"),
    )
    plan = preparation.observe(repo, home=home)
    result = preparation.prepare(repo, plan["planDigest"], home=home)
    assert result["payloadsReady"] is True
    assert calls == [("sample", "second", repo)]
    assert settings.read_bytes() == original


def test_stale_plan_refuses_before_acquisition(selected, monkeypatch):
    home, repo, settings = selected
    plan = preparation.observe(repo, home=home)
    document = json.loads(settings.read_text())
    document["enabledPlugins"]["sample@second"] = False
    write(settings, document)
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: pytest.fail("stale plan ran"))
    with pytest.raises(preparation.PreparationError, match="plan changed"):
        preparation.prepare(repo, plan["planDigest"], home=home)


@pytest.mark.parametrize("changed", ["project", "home"])
def test_plan_cannot_be_applied_to_another_target(selected, monkeypatch, changed):
    home, repo, settings = selected
    target_home, target_repo = home, repo
    if changed == "project":
        write(home / ".copilot" / "settings.json", json.loads(settings.read_text()))
        settings.unlink()
        target_repo = repo.parent / "other-project"
        target_repo.mkdir()
    else:
        target_home = home.parent / "other-home"
        target_home.mkdir()
    plan = preparation.observe(repo, home=home)
    monkeypatch.setenv("COPILOT_HOME", str(target_home / ".copilot"))
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: pytest.fail("another target acquired"))
    with pytest.raises(preparation.PreparationError, match="plan changed"):
        preparation.prepare(target_repo, plan["planDigest"], home=target_home)


def test_changed_selection_retains_completed_outcomes_without_next_action(selected, monkeypatch):
    home, repo, settings = selected
    calls = []

    def acquire(name, marketplace, copilot, *, cwd, revalidate, copilot_home):
        revalidate()
        calls.append(marketplace)
        install(copilot_home.parent, name, marketplace)
        document = json.loads(settings.read_text())
        document["extraKnownMarketplaces"]["second"]["source"]["ref"] = "changed"
        write(settings, document)
        return "OK (installed)"

    monkeypatch.setattr(update_acquisition, "acquire_missing", acquire)
    plan = preparation.observe(repo, home=home)
    result = preparation.prepare(repo, plan["planDigest"], home=home)
    assert calls == ["first"]
    assert not result["payloadsReady"]
    assert "selection changed" in result["refusal"]
    assert result["outcomes"] == [{"source": "sample@first", "status": "OK (installed)"}]
    assert (home / ".copilot" / "installed-plugins" / "first" / "sample").is_dir()


def test_plan_refuses_retargeted_copilot_home_symlink(selected, monkeypatch):
    home, repo, _ = selected
    first, second = home / "first-state", home / "second-state"
    first.mkdir()
    second.mkdir()
    link = home / ".copilot"
    try:
        link.symlink_to(first, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Directory symlinks are unavailable: {error}")
    plan = preparation.observe(repo, home=home)
    link.unlink()
    link.symlink_to(second, target_is_directory=True)
    monkeypatch.setattr(
        update_acquisition, "acquire_missing",
        lambda *a, **k: pytest.fail("retargeted home acquired"),
    )
    with pytest.raises(preparation.PreparationError, match="plan changed"):
        preparation.prepare(repo, plan["planDigest"], home=home)


def test_success_without_payload_does_not_establish_availability(selected, monkeypatch):
    home, repo, _ = selected
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: "OK (installed)")
    plan = preparation.observe(repo, home=home)
    result = preparation.prepare(repo, plan["planDigest"], home=home)
    assert not result["payloadsReady"]
    assert all(row["state"] == "needed" for row in result["entries"])


def test_invalid_declarations_refuse_before_native_acquisition(selected, monkeypatch):
    home, repo, settings = selected
    settings.write_text('{"enabledPlugins":{"x@first":true,"x@first":false}}', encoding="utf-8")
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: pytest.fail("invalid inputs ran"))
    with pytest.raises(preparation.PreparationError, match="incomplete or invalid"):
        preparation.observe(repo, home=home)


@pytest.mark.parametrize("error", [ValueError, FileNotFoundError])
def test_native_revalidation_refusal_never_launches_or_replays(selected, monkeypatch, error):
    from agent_procutil import owned_process

    home, repo, _ = selected
    calls = []

    def refuse():
        raise error("changed selection")

    monkeypatch.setattr(update_acquisition, "_WINDOWS", False)
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    monkeypatch.setattr(
        update_acquisition, "_run_update",
        lambda *args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    result = update_acquisition.acquire_missing(
        "sample", "first", sys.executable, cwd=repo, revalidate=refuse,
        copilot_home=home / ".copilot",
    )
    assert not result.startswith("OK")
    assert calls == []
    assert not activation_preservation_pending(home / ".copilot")


def authored_local(selected, *, user_declaration=False, stable_id=False, missing=False):
    home, repo, settings = selected
    base = home / ".copilot" if user_declaration else repo
    market = base / ".ai"
    payload = market / "sample"
    source = {"source": "directory", "path": "./.ai", "manifestName": "fixture"}
    if stable_id:
        source["stableId"] = "fixture-local"
    write(market / ".claude-plugin" / "marketplace.json", {
        "name": "fixture", "plugins": [{"name": "sample", "source": "./sample"}],
    })
    if not missing:
        write(payload / "plugin.json", {"name": "sample", "version": "1.0.0"})
    declaration = {"local": {"source": source}}
    write(settings, {
        "enabledPlugins": {"sample@local": True},
        **({} if user_declaration else {"extraKnownMarketplaces": declaration}),
    })
    if user_declaration:
        write(home / ".copilot" / "settings.json", {"extraKnownMarketplaces": declaration})
    return payload


def test_split_layer_marketplace_uses_the_declaration_base(selected):
    home, repo, _ = selected
    authored_local(selected, user_declaration=True)
    assert preparation.observe(repo, home=home)["payloadsReady"]


def test_stale_installed_copy_cannot_replace_missing_authored_local_payload(selected):
    home, repo, _ = selected
    authored_local(selected, missing=True)
    install(home, "sample", "local")
    with pytest.raises(preparation.PreparationError, match="local payload"):
        preparation.observe(repo, home=home)


def test_stable_identity_accepts_the_verified_authored_directory(selected):
    home, repo, _ = selected
    authored_local(selected, stable_id=True)
    assert preparation.observe(repo, home=home)["payloadsReady"]


def test_native_home_mismatch_refuses_before_mutation(selected, monkeypatch):
    home, repo, _ = selected
    before = sorted(home.rglob("*"))
    monkeypatch.setenv("COPILOT_HOME", str(home / "different"))
    with pytest.raises(preparation.PreparationError, match="inventory owner"):
        preparation.observe(repo, home=home)
    assert sorted(home.rglob("*")) == before


def test_file_target_cannot_be_a_ready_empty_project(selected):
    home, _, settings = selected
    with pytest.raises(preparation.PreparationError, match="project directory"):
        preparation.observe(settings, home=home)


@pytest.mark.parametrize("witnessed", [False, True])
def test_available_payload_cannot_hide_unsettled_activation(selected, monkeypatch, witnessed):
    home, repo, _ = selected
    install(home, "sample", "first")
    install(home, "sample", "second")

    def interrupted_restore(*args, **kwargs):
        raise OSError("fixture interruption")

    with monkeypatch.context() as patch:
        if witnessed:
            patch.setattr(state, "restore", interrupted_restore)
        with pytest.raises((RuntimeError, OSError)):
            with preserve_activation_durably("sample@first", home / ".copilot") as guard:
                if witnessed:
                    guard.confirm_not_launched()
                else:
                    raise RuntimeError("fixture unknown settlement")
    plan = preparation.observe(repo, home=home)
    assert plan["activationPending"] and not plan["payloadsReady"]
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: pytest.fail("available payload reinstalled"))
    result = preparation.prepare(repo, plan["planDigest"], home=home)
    assert result["payloadsReady"] is witnessed
    if not witnessed:
        assert "settlement is unproven" in result["refusal"]


def test_conflicting_native_marketplace_sources_refuse_before_acquisition(selected, monkeypatch):
    home, repo, _ = selected
    write(home / ".copilot" / "settings.json", {
        "extraKnownMarketplaces": {"first": {
            "source": {"source": "github", "repo": "other/source"},
        }},
    })
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: pytest.fail("conflicting source ran"))
    with pytest.raises(ValueError, match="Conflicting declarations"):
        preparation.observe(repo, home=home)


@pytest.fixture
def tool_profile(selected, monkeypatch):
    home, repo, settings = selected
    write(settings, {})
    config = repo / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text("copilot_plugin_profile: tool-plugins.json\n", encoding="utf-8")
    path = repo / "tool-plugins.json"
    write(path, {
        "enabledPlugins": {"sample@first": True},
        "extraKnownMarketplaces": {
            "first": {"source": {"source": "github", "repo": "example/first"}},
        },
    })

    def git(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, str(repo) if "--show-toplevel" in args else "a" * 40, "")

    monkeypatch.setattr(preparation.profile.git_ops, "git", git)
    return home, repo, settings, path


def test_profile_is_explicit_and_never_activates_or_registers(tool_profile):
    home, repo, settings, path = tool_profile
    before = {item: item.read_bytes() for item in repo.rglob("*") if item.is_file()}
    ordinary = preparation.observe(repo, home=home)
    assert ordinary["entries"] == [] and ordinary["payloadsReady"]
    assert preparation.PROFILE_CAPABILITY in ordinary["capabilities"]
    result = preparation.observe(repo, home=home, include_tool_profile=True)
    assert result["selectionMode"] == preparation.PROFILE_CAPABILITY
    assert not result["payloadsReady"]
    row, = result["entries"]
    assert row["source"] == "sample@first" and row["selection"] == "profile-dependency"
    assert Path(row["payloadContext"]["copilotHome"]).is_relative_to(home)
    assert before == {item: item.read_bytes() for item in repo.rglob("*") if item.is_file()}
    assert not list(home.iterdir())


def test_absent_profile_keeps_active_census(selected):
    home, repo, _ = selected
    ordinary = preparation.observe(repo, home=home)
    result = preparation.observe(repo, home=home, include_tool_profile=True)
    assert [row["source"] for row in ordinary["entries"]] == [row["source"] for row in result["entries"]]
    assert all(row["selection"] == "active" for row in result["entries"])


@pytest.mark.parametrize("change", [
    "false-choice", "marketplace", "escape", "invalid", "duplicate", "invalid-sha", "escaping-remote-path",
])
def test_invalid_profile_refuses_without_acquisition(tool_profile, monkeypatch, change):
    home, repo, settings, path = tool_profile
    if change == "false-choice":
        write(settings, {"enabledPlugins": {"sample@first": False}})
    elif change == "marketplace":
        write(settings, {"extraKnownMarketplaces": {
            "first": {"source": {"source": "github", "repo": "other/first"}},
        }})
    elif change == "escape":
        (repo / ".agent-worktrees" / "config.yaml").write_text(
            "copilot_plugin_profile: ../outside.json\n", encoding="utf-8",
        )
    elif change == "invalid":
        write(path, {"unsupported": True})
    elif change in {"invalid-sha", "escaping-remote-path"}:
        data = json.loads(path.read_text())
        data["extraKnownMarketplaces"]["first"]["source"].update(
            {"sha": False} if change == "invalid-sha" else {"path": "../outside"},
        )
        write(path, data)
    else:
        path.write_text('{"enabledPlugins":{"sample@first":true,"sample@first":false}}', encoding="utf-8")
    monkeypatch.setattr(update_acquisition, "acquire_prepared", lambda *a, **k: pytest.fail("invalid inputs ran"))
    with pytest.raises(ValueError):
        preparation.observe(repo, home=home, include_tool_profile=True)
    assert not list(home.iterdir())


@pytest.mark.parametrize("change", ["profile", "config", "revision"])
def test_profile_plan_binds_all_selected_checkout_inputs(tool_profile, monkeypatch, change):
    home, repo, _, path = tool_profile
    plan = preparation.observe(repo, home=home, include_tool_profile=True)
    if change == "profile":
        data = json.loads(path.read_text())
        data["extraKnownMarketplaces"]["first"]["source"]["repo"] = "other/first"
        write(path, data)
    elif change == "config":
        (repo / ".agent-worktrees" / "config.yaml").write_text(
            "copilot_plugin_profile: tool-plugins.json\nexpose_agent: false\n", encoding="utf-8",
        )
    else:
        monkeypatch.setattr(preparation.profile.git_ops, "git", lambda *a, **k: subprocess.CompletedProcess(
            a, 0, str(repo) if "--show-toplevel" in a else "b" * 40, "",
        ))
    monkeypatch.setattr(update_acquisition, "acquire_prepared", lambda *a, **k: pytest.fail("stale plan ran"))
    with pytest.raises(ValueError, match="plan changed"):
        preparation.prepare(repo, plan["planDigest"], home=home, include_tool_profile=True)


def test_profile_plan_cannot_be_applied_as_active_only(tool_profile, monkeypatch):
    home, repo, _, _ = tool_profile
    plan = preparation.observe(repo, home=home, include_tool_profile=True)
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: pytest.fail("downgraded plan ran"))
    with pytest.raises(ValueError, match="plan changed"):
        preparation.prepare(repo, plan["planDigest"], home=home)


def test_profile_local_payload_is_qualified_without_global_copy(tool_profile):
    home, repo, _, path = tool_profile
    payload = repo / ".ai" / "sample"
    write(payload / "plugin.json", {"name": "sample", "version": "1.0.0"})
    write(payload.parent / ".github" / "plugin" / "marketplace.json", {
        "name": "first", "plugins": [{"name": "sample", "source": "./sample"}],
    })
    write(path, {
        "enabledPlugins": {"sample@first": True},
        "extraKnownMarketplaces": {"first": {"source": {"source": "directory", "path": "./.ai"}}},
    })
    result = preparation.observe(repo, home=home, include_tool_profile=True)
    assert result["payloadsReady"]
    assert Path(result["entries"][0]["payloadRoot"]) == payload
    assert not list(home.iterdir())


def test_prepared_payload_requires_actual_source_and_complete_receipt(tool_profile):
    home, repo, _, _ = tool_profile
    row, = preparation.observe(repo, home=home, include_tool_profile=True)["entries"]
    native_home = Path(row["payloadContext"]["copilotHome"])
    root = native_home / "installed-plugins" / "first" / "sample"
    write(root / "plugin.json", {"name": "sample", "version": "1.0.0"})
    assert not preparation.observe(repo, home=home, include_tool_profile=True)["payloadsReady"]
    write(native_home / ".installation-complete.json", {"source": "different@first"})
    with pytest.raises(ValueError, match="different source"):
        preparation.observe(repo, home=home, include_tool_profile=True)


@pytest.mark.parametrize("change", ["repo", "sha", "path", "manifest"])
def test_prepared_payload_cannot_replace_the_declared_source(tool_profile, change):
    home, repo, _, _ = tool_profile
    row, = preparation.observe(repo, home=home, include_tool_profile=True)["entries"]
    native_home = Path(row["payloadContext"]["copilotHome"])
    write(native_home / "installed-plugins" / "first" / "sample" / "plugin.json", {
        "name": "different" if change == "manifest" else "sample", "version": "1.0.0",
    })
    write(native_home / ".installation-complete.json", {
        "source": "sample@first",
        "catalog": {"name": "first", "owner": {"name": "Fixture"}},
        "entry": {"name": "sample", "version": "1.0.0"},
    })
    descriptor = {"source": "github", "repo": "example/first"}
    if change != "manifest":
        descriptor[change] = {"repo": "other/source", "sha": "a" * 40, "path": "other-catalog"}[change]
    write(native_home / "settings.json", {"extraKnownMarketplaces": {"first": {"source": descriptor}}})
    with pytest.raises(ValueError, match="does not match|different plugin"):
        preparation.observe(repo, home=home, include_tool_profile=True)


def test_plan_binds_non_json_yaml_configuration_without_serializing_it(tool_profile):
    home, repo, _, _ = tool_profile
    config = repo / ".agent-worktrees" / "config.yaml"
    config.write_text(
        "copilot_plugin_profile: tool-plugins.json\nannotation: 2020-01-01\n", encoding="utf-8",
    )
    before = preparation.observe(repo, home=home, include_tool_profile=True)
    config.write_text(config.read_text() + "# revised\n", encoding="utf-8")
    after = preparation.observe(repo, home=home, include_tool_profile=True)
    assert before["selectionDigest"] != after["selectionDigest"]


@pytest.mark.parametrize("selector", ["sha", "path"])
def test_active_payload_cannot_hide_a_conflicting_profile_acquisition_selector(tool_profile, monkeypatch, selector):
    home, repo, settings, path = tool_profile
    data = json.loads(path.read_text())
    write(settings, data)
    install(home, "sample", "first")
    data["extraKnownMarketplaces"]["first"]["source"][selector] = "a" * 40 if selector == "sha" else "catalog"
    write(path, data)
    monkeypatch.setattr(update_acquisition, "acquire_missing", lambda *a, **k: pytest.fail("conflict acquired"))
    monkeypatch.setattr(update_acquisition, "acquire_prepared", lambda *a, **k: pytest.fail("conflict acquired"))
    with pytest.raises(ValueError, match="conflicts with a declared marketplace"):
        preparation.observe(repo, home=home, include_tool_profile=True)


def test_equivalent_github_source_spelling_retains_active_payload(tool_profile):
    home, repo, settings, path = tool_profile
    data = json.loads(path.read_text())
    data["extraKnownMarketplaces"]["first"]["source"]["repo"] = "https://github.com/EXAMPLE/FIRST.git"
    write(settings, data)
    install(home, "sample", "first")
    result = preparation.observe(repo, home=home, include_tool_profile=True)
    assert result["payloadsReady"]
    assert Path(result["entries"][0]["payloadRoot"]) == home / ".copilot" / "installed-plugins" / "first" / "sample"


@pytest.mark.parametrize("layer", ["active", "profile"])
@pytest.mark.parametrize("override", [
    {"repo": "other/second", "canonical": "github:example/first"},
    {"source": "git", "kind": "github", "url": "https://example.invalid/second.git"},
])
def test_portable_identity_cannot_hide_a_different_native_source(tool_profile, layer, override):
    home, repo, settings, path = tool_profile
    data = json.loads(path.read_text())
    write(settings, data)
    target = settings if layer == "active" else path
    data["extraKnownMarketplaces"]["first"]["source"].update(override)
    write(target, data)
    with pytest.raises(ValueError, match="contradicts its native acquisition"):
        preparation.observe(repo, home=home, include_tool_profile=True)
    assert not list(home.iterdir())


def test_consistent_portable_identity_preserves_native_source_equivalence(tool_profile):
    home, repo, settings, path = tool_profile
    data = json.loads(path.read_text())
    write(settings, data)
    data["extraKnownMarketplaces"]["first"]["source"].update(
        kind="github", canonical="github:EXAMPLE/FIRST",
    )
    write(path, data)
    install(home, "sample", "first")
    assert preparation.observe(repo, home=home, include_tool_profile=True)["payloadsReady"]


@pytest.mark.parametrize("version", [None, "1.0.0"])
def test_deleted_prepared_payload_is_needed_without_trusting_its_old_receipt(tool_profile, version):
    home, repo, _, path = tool_profile
    row, = preparation.observe(repo, home=home, include_tool_profile=True)["entries"]
    native_home = Path(row["payloadContext"]["copilotHome"])
    write(native_home / ".installation-complete.json", {
        "source": "sample@first",
        "catalog": {"name": "first", "owner": {"name": "Fixture"}},
        "entry": {"name": "sample", **({"version": version} if version else {})},
    })
    write(native_home / "settings.json", json.loads(path.read_text()))
    result = preparation.observe(repo, home=home, include_tool_profile=True)
    assert not result["payloadsReady"]
    assert result["entries"][0]["state"] == "needed"
    root = native_home / "installed-plugins" / "first" / "sample"
    root.mkdir(parents=True)
    with pytest.raises(ValueError, match="manifest is missing"):
        preparation.observe(repo, home=home, include_tool_profile=True)


@pytest.mark.parametrize("kind", ["git", "url"])
def test_native_remote_source_kinds_use_the_isolated_acquisition_owner(tool_profile, kind):
    home, repo, _, path = tool_profile
    data = json.loads(path.read_text())
    data["extraKnownMarketplaces"]["first"]["source"] = {
        "source": kind, "url": "https://example.invalid/market.git",
    }
    write(path, data)
    row, = preparation.observe(repo, home=home, include_tool_profile=True)["entries"]
    assert row["state"] == "needed"
    assert "prepared-payloads" in Path(row["payloadContext"]["copilotHome"]).parts
    assert not list(home.iterdir())


def fail_profile_git(monkeypatch, timeout):
    def fail(*args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(["git", "rev-parse"], 15)
        raise preparation.profile.git_ops.GitError(["git", "rev-parse"], 128, "fixture probe failure")

    monkeypatch.setattr(preparation.profile.git_ops, "git", fail)


@pytest.mark.parametrize("timeout", [False, True])
def test_profile_git_probe_failures_return_structured_refusal(tool_profile, monkeypatch, capsys, timeout):
    home, repo, _, _ = tool_profile
    monkeypatch.setattr(preparation.Path, "home", lambda: home)
    fail_profile_git(monkeypatch, timeout)
    code = preparation.command(argparse.Namespace(
        repo=repo, apply=False, expected_plan_digest=None, include_tool_profile=True,
    ))
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and not result["payloadsReady"]
    assert "cannot inspect checkout tool profile Git identity" in result["refusal"]


@pytest.mark.parametrize("timeout", [False, True])
def test_profile_git_prelaunch_failure_confirms_no_native_writer(tool_profile, monkeypatch, timeout):
    from agent_procutil import owned_process
    import plugin_resolve.preparation as native
    from agent_worktrees import installer_run

    home, repo, _, _ = tool_profile
    plan = preparation.observe(repo, home=home, include_tool_profile=True)
    native_home = Path(plan["entries"][0]["payloadContext"]["copilotHome"])
    fail_profile_git(monkeypatch, timeout)
    monkeypatch.setattr(update_acquisition, "_WINDOWS", False)
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    monkeypatch.setattr(update_acquisition, "_run_update", lambda *a, **k: pytest.fail("native writer launched"))

    def drive(source, definition, *, staging, repo, copilot_command, environment, runner, check_cancelled):
        return runner(
            [sys.executable, "-c", "pass"], cwd=repo, timeout=5,
            env={**environment, "COPILOT_HOME": str(native_home)},
        )

    monkeypatch.setattr(native, "prepare_payload", drive)
    with pytest.raises(installer_run.InstallerLaunchError, match="selection changed"):
        update_acquisition.acquire_prepared(
            "sample@first", {"source": {"source": "github", "repo": "example/first"}},
            sys.executable, cwd=repo, staging=home / "prepared",
            revalidate=lambda: preparation.observe(repo, home=home, include_tool_profile=True),
        )
    assert not activation_preservation_pending(native_home)


def test_prepared_auth_context(selected, monkeypatch):
    from agent_procutil import owned_process
    import plugin_resolve.plugin_auth as auth

    home, repo, _ = selected
    monkeypatch.setattr(preparation.Path, "home", lambda: home)
    definition = {"source": {"source": "github", "repo": "example/first"}}
    write(home / ".copilot" / "settings.json", {"extraKnownMarketplaces": {"first": definition}})
    lookups, launches = [], []

    def token(url, environment, cwd, *, timeout):
        lookups.append(cwd)
        return "synthetic-checkout-value" if cwd == repo else "synthetic-global-value"

    def run(argv, **kwargs):
        env = kwargs["env"]
        launches.append(Path(kwargs["cwd"]))
        assert Path(kwargs["cwd"]) != repo
        assert env["GH_TOKEN"] == env["COPILOT_GITHUB_TOKEN"] == "synthetic-checkout-value"
        write(Path(env["COPILOT_HOME"]) / "installed-plugins" / "first" / "sample" / "plugin.json", {
            "name": "sample", "version": "1.0.0",
        })
        write(Path(env["COPILOT_CACHE_HOME"]) / "marketplaces" / "fixture" / "marketplace.json", {
            "name": "first", "owner": {"name": "Fixture"},
            "plugins": [{"name": "sample", "source": "./sample"}],
        })
        kwargs["on_settled"]()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(auth, "_git_token", token)
    monkeypatch.setattr(update_acquisition._auth, "mode", lambda: "on")
    monkeypatch.setattr(update_acquisition, "_WINDOWS", False)
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    monkeypatch.setattr(update_acquisition, "_run_update", run)
    assert update_acquisition.acquire_prepared(
        "sample@first", definition, sys.executable, cwd=repo,
        staging=home / "p", revalidate=lambda: None,
    ) == "OK (prepared)"
    assert len(launches) == 1 and len(lookups) == 2 and all(path == repo for path in lookups)


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("elapsed", [2, 241])
def test_final_revalidation_consumes_the_native_launch_budget(selected, monkeypatch, prepared, elapsed):
    from agent_procutil import owned_process
    import plugin_resolve.preparation as native
    from agent_worktrees import installer_run

    home, repo, _ = selected
    native_home = home / ".copilot"
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(update_acquisition, "monotonic", lambda: clock[0])
    monkeypatch.setattr(update_acquisition, "_WINDOWS", False)
    monkeypatch.setattr(update_acquisition._auth, "mode", lambda: "off")
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)

    def revalidate():
        clock[0] += elapsed

    def run(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        kwargs["on_settled"]()
        return subprocess.CompletedProcess(argv, 0, "", "")

    def drive(source, definition, *, staging, repo, copilot_command, environment, runner, check_cancelled):
        return runner(
            [sys.executable, "-c", "pass"], cwd=repo, timeout=5,
            env={**environment, "COPILOT_HOME": str(native_home)},
        )

    monkeypatch.setattr(update_acquisition, "_run_update", run)
    monkeypatch.setattr(native, "prepare_payload", drive)
    if prepared:
        def acquire():
            return update_acquisition.acquire_prepared(
                "sample@first", {"source": {"source": "github", "repo": "example/first"}},
                sys.executable, cwd=repo, staging=home / "prepared", revalidate=revalidate,
            )
        if elapsed > 240:
            with pytest.raises(installer_run.InstallerLaunchError, match="budget exhausted"):
                acquire()
        else:
            assert acquire().startswith("OK")
    else:
        result = update_acquisition.acquire_missing(
            "sample", "first", sys.executable, cwd=repo, revalidate=revalidate,
            copilot_home=native_home,
        )
        assert result.startswith("OK") is (elapsed < 240)
    assert timeouts == ([3 if prepared else 118] if elapsed == 2 else [])
    assert not activation_preservation_pending(native_home)
