from __future__ import annotations

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
    assert before == sorted(path.relative_to(home) for path in home.rglob("*"))


def test_apply_acquires_only_missing_and_never_uses_core_name_lookup(selected, monkeypatch):
    home, repo, settings = selected
    original = settings.read_bytes()
    install(home, "sample", "first")
    calls = []

    def acquire(name, marketplace, copilot, *, cwd, revalidate, home):
        revalidate()
        calls.append((name, marketplace, cwd))
        install(home.parent, name, marketplace)
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

    def acquire(name, marketplace, copilot, *, cwd, revalidate, home):
        revalidate()
        calls.append(marketplace)
        install(home.parent, name, marketplace)
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
        home=home / ".copilot",
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
