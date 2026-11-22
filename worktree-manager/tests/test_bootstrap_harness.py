from pathlib import Path
from unittest.mock import patch

import pytest

from worktree_manager import bootstrap_harness as boot
from worktree_manager import registered_suite, runtime_source_repository, source_config
from worktree_manager.harness_onboarding import target_machines


def test_refresh_accepts_only_untracked_generated_overlay(tmp_path: Path) -> None:
    boot._git(tmp_path, "init", "--quiet")
    output = tmp_path / ".github/copilot/settings.local.json"
    output.parent.mkdir(parents=True)
    output.write_text("{}", encoding="utf-8")
    assert not boot._refresh_clean(tmp_path)
    output.write_text('{"_agentWorktreesKnowledgePluginOverlay":{"version":1,"marketplaces":{},'
                      '"enabledPlugins":{}}}', encoding="utf-8")
    assert boot._refresh_clean(tmp_path)
    other = tmp_path / "operator-change"
    other.touch()
    assert not boot._refresh_clean(tmp_path)
    other.unlink()
    boot._git(tmp_path, "add", "--", ".github/copilot/settings.local.json")
    assert not boot._refresh_clean(tmp_path)


def test_target_gate_includes_alias_and_physical_host(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  fixture-host:\n    hostname: fixture-host\n    alias: local-alias\n",
        encoding="utf-8",
    )
    with patch("platform.node", return_value="fixture-host"):
        machines = target_machines(tmp_path, config)
    assert machines[0] == "local-alias"
    assert "fixture-host" in machines and "foreign-host" not in machines


def test_inherited_git_selectors_do_not_redirect_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, foreign = tmp_path / "target", tmp_path / "foreign"
    for path in (target, foreign):
        path.mkdir()
        boot._git(path, "init", "--quiet")
    for key, path in (("GIT_DIR", foreign / ".git"), ("GIT_WORK_TREE", foreign),
                      ("GIT_INDEX_FILE", foreign / ".git/index"),
                      ("GIT_OBJECT_DIRECTORY", foreign / ".git/objects")):
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'credential.helper=fixture-helper'")
    assert Path(boot._git(target, "rev-parse", "--show-toplevel")).samefile(target)
    assert boot._git(target, "config", "--get", "credential.helper") == "fixture-helper"
    (target / "selected").write_text("fixture", encoding="utf-8")
    boot._git(target, "add", "--", "selected")
    assert boot._git(target, "ls-files") == "selected"
    assert not (foreign / ".git/index").exists()


def test_register_reopen_and_select_pin(tmp_path: Path) -> None:
    root = tmp_path / "footprint"
    source = tmp_path / "src"
    harness = source / "example"
    harness.mkdir(parents=True)
    boot._git(harness, "init", "--quiet")
    config = harness / ".copilot-extensions" / "config.yaml"
    config.parent.mkdir()
    pin = "a" * 40
    config.write_text(f"current_version: {pin}\n", encoding="utf-8")
    for _ in range(2):
        selected = boot.first_registration(root, source, str(harness))
        assert selected.samefile(harness)
        assert runtime_source_repository.read(selected, default_repository=source_config.DEFAULT_REPO)[
            "reference"
        ] == pin
    other = tmp_path / "other" / harness.name
    other.mkdir(parents=True)
    boot._git(other, "init", "--quiet")
    with pytest.raises(ValueError, match="another checkout"):
        boot.first_registration(root, source, str(other))
    assert registered_suite.registrations(root)[1]["example"].samefile(harness)


@pytest.mark.parametrize("requested", [None, "example/default"])
def test_refreshes_every_remote_not_already_selected(tmp_path: Path, requested: str | None) -> None:
    anchors = {name: tmp_path / name for name in ("default", "local", "other")}
    rows = {"repos": {"default": {"remote": "example/default"}, "local": {},
                     "other": {"remote": "example/other"}}}
    with (
        patch.object(registered_suite, "registrations", return_value=("default", anchors)),
        patch("worktree_manager.harness_state.repos_registry", return_value=rows),
        patch.object(boot, "acquire_checkout") as refresh,
    ):
        assert boot.refresh_registrations(tmp_path, tmp_path, requested) == anchors["default"]
    expected = ["other"] if requested else ["default", "other"]
    assert [call.args for call in refresh.call_args_list] == [
        (tmp_path, anchors[name], "https://github.com/example/" + name) for name in expected
    ]
