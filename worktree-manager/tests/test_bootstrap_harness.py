from pathlib import Path
from unittest.mock import patch

import pytest

from worktree_manager import bootstrap_harness as boot
from worktree_manager import registered_suite, runtime_source_repository, source_config


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
