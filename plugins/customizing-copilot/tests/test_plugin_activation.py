from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "installing-plugins"
    / "scripts"
    / "plugin-activation.py"
)
IDENTITY = "optional-plugin@example-marketplace"


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            *args,
            "--copilot-home",
            str(home),
            "--json",
        ],
        capture_output=True,
        text=True,
    )


def _state(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / ".copilot"
    repo = tmp_path / "repo"
    (repo / ".github" / "copilot").mkdir(parents=True)
    home.mkdir()
    (home / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {IDENTITY: True, "other@m": True},
                "unrelated": {"keep": True},
            }
        ),
        encoding="utf-8",
    )
    (home / "config.json").write_text(
        "// managed\n"
        + json.dumps(
            {
                "installedPlugins": [
                    {
                        "name": "optional-plugin",
                        "marketplace": "example-marketplace",
                        "enabled": True,
                        "version": "1.0.0",
                    }
                ],
                "trustedFolders": [str(repo.resolve())],
                "other": 1,
            }
        ),
        encoding="utf-8",
    )
    (repo / ".github" / "copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {IDENTITY: True}}),
        encoding="utf-8",
    )
    return home, repo


def test_inspect_distinguishes_inventory_user_repo_and_trust(tmp_path):
    home, repo = _state(tmp_path)
    result = _run(home, "inspect", IDENTITY, "--repo", str(repo))
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state == {
        "identity": IDENTITY,
        "installed": True,
        "inventoryEnabled": True,
        "userActivation": "true",
        "repositoryActivation": "true",
        "repositoryTrusted": True,
        "installedButNotUserEnabled": False,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows path comparison contract")
def test_inspect_matches_windows_trust_case_insensitively(tmp_path):
    home, repo = _state(tmp_path)
    raw = (home / "config.json").read_text(encoding="utf-8")
    config = json.loads("\n".join(raw.splitlines()[1:]))
    config["trustedFolders"] = [str(repo.resolve()).swapcase()]
    (home / "config.json").write_text(
        "// managed\n" + json.dumps(config), encoding="utf-8"
    )
    result = _run(home, "inspect", IDENTITY, "--repo", str(repo))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["repositoryTrusted"] is True


def test_remove_defaults_to_dry_run_then_applies_idempotently(tmp_path):
    home, _ = _state(tmp_path)
    before_settings = (home / "settings.json").read_text(encoding="utf-8")
    before_config = (home / "config.json").read_text(encoding="utf-8")

    preview = _run(home, "remove-user-activation", IDENTITY)
    assert preview.returncode == 0
    assert json.loads(preview.stdout)["changed"] is True
    assert (home / "settings.json").read_text(encoding="utf-8") == before_settings
    assert (home / "config.json").read_text(encoding="utf-8") == before_config

    applied = _run(home, "remove-user-activation", IDENTITY, "--apply")
    assert applied.returncode == 0
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert settings == {
        "enabledPlugins": {"other@m": True},
        "unrelated": {"keep": True},
    }
    raw_config = (home / "config.json").read_text(encoding="utf-8")
    assert raw_config.startswith("// managed\n")
    config = json.loads("\n".join(raw_config.splitlines()[1:]))
    assert config["installedPlugins"][0]["enabled"] is False
    assert config["other"] == 1

    second = _run(home, "remove-user-activation", IDENTITY, "--apply")
    assert second.returncode == 0
    assert json.loads(second.stdout)["changed"] is False


@pytest.mark.parametrize(
    ("file_name", "content", "message"),
    [
        ("settings.json", "{bad", "cannot read"),
        ("settings.json", '{"enabledPlugins":[]}', "enabledPlugins must be an object"),
        ("config.json", '{"installedPlugins":{}}', "installedPlugins must be an array"),
    ],
)
def test_malformed_state_is_an_explicit_error(
    tmp_path, file_name, content, message
):
    home, _ = _state(tmp_path)
    (home / file_name).write_text(content, encoding="utf-8")
    result = _run(home, "inspect", IDENTITY)
    assert result.returncode == 2
    assert message in result.stderr


def test_remove_requires_installed_inventory(tmp_path):
    home, _ = _state(tmp_path)
    (home / "config.json").write_text("{}", encoding="utf-8")
    result = _run(home, "remove-user-activation", IDENTITY, "--apply")
    assert result.returncode == 2
    assert "not present in installed inventory" in result.stderr


def test_inspect_treats_missing_optional_state_as_absent(tmp_path):
    home = tmp_path / ".copilot"
    result = _run(home, "inspect", IDENTITY)
    assert result.returncode == 0
    state = json.loads(result.stdout)
    assert state["installed"] is False
    assert state["userActivation"] == "absent"
