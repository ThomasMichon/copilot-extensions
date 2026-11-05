"""Opt-in native acquisition/CLI controls; no real plugin installation."""

import json
import os
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import plugin_preparation as preparation
import test_plugin_preparation as fixtures

pytestmark = [pytest.mark.real_installer_processes, pytest.mark.timeout(60)]
selected = fixtures.selected
write = fixtures.write


def test_native_acquisition_preserves_activation_and_checks_actual_payload(selected):
    home, repo, settings = selected
    document = json.loads(settings.read_text())
    document["enabledPlugins"]["sample@second"] = False
    write(settings, document)
    write(home / ".copilot" / "settings.json", {"enabledPlugins": {"unrelated@first": False}})
    # Python stands in only for the finite native acquisition process.
    (repo / "plugin").write_text(
        "import json, os, pathlib, sys\n"
        "assert sys.argv[1:] == ['install', 'sample@first']\n"
        "root = pathlib.Path(os.environ['COPILOT_HOME'])\n"
        "settings = root / 'settings.json'\n"
        "data = json.loads(settings.read_text()); data['enabledPlugins']['sample@first'] = True\n"
        "settings.write_text(json.dumps(data))\n"
        "(root / 'config.json').write_text(json.dumps({'installedPlugins': ["
        "{'name': 'sample', 'marketplace': 'first', 'enabled': True}]}))\n"
        "payload = root / 'installed-plugins' / 'first' / 'sample'\n"
        "payload.mkdir(parents=True)\n"
        "(payload / 'plugin.json').write_text(json.dumps({'name': 'sample', 'version': '1.0.0'}))\n",
        encoding="utf-8",
    )
    plan = preparation.observe(repo, home=home)
    result = preparation.prepare(repo, plan["planDigest"], home=home)
    assert result["payloadsReady"], result
    assert json.loads((home / ".copilot" / "settings.json").read_text()) == {
        "enabledPlugins": {"unrelated@first": False},
    }
    assert json.loads((home / ".copilot" / "config.json").read_text())["installedPlugins"][0]["enabled"] is False


def test_real_cli_observation_and_unpinned_apply_never_acquire(selected):
    home, repo, _ = selected
    command = [
        sys.executable, "-m", "agent_worktrees", "prepare-plugin-payloads",
        "--repo", str(repo),
    ]
    for arguments, expected in (([], 0), (["--apply"], 1)):
        result = subprocess.run(
            [*command, *arguments], cwd=repo, env=dict(os.environ),
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode == expected, result.stderr
        document = json.loads(result.stdout)
        assert document["schema"] == preparation.SCHEMA
        assert document["payloadsReady"] is False
    assert not (home / ".copilot" / "installed-plugins").exists()
