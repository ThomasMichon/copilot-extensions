"""Real legacy invocations preserve both existing records and repair history."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_worktrees import config as cfg
from agent_worktrees import machine_record_repair as repair


@pytest.mark.parametrize("record_present", [False, True])
def test_legacy_cli_does_not_inspect_or_rewrite_record_history(tmp_path, record_present):
    home, anchor = tmp_path / "home", tmp_path / "anchor"
    anchor.mkdir()
    state, registry = home / ".example", home / ".agent-worktrees"
    records = state / "worktrees"
    records.mkdir(parents=True)
    registry.mkdir()
    (state / "config.yaml").write_text(yaml.safe_dump({
        "machine": "configured-name", "platform": cfg.detect_platform(), "repo_name": "example",
        "repos": {"example": {"anchor": str(anchor), "worktree_root": str(anchor / "trees")}},
    }), encoding="utf-8")
    (registry / "projects.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "projects": {"example": {"config_dir": str(state)}},
    }), encoding="utf-8")
    (registry / "repos.yaml").write_text(yaml.safe_dump({"repos": {
        "example": {"class": "worktree", "windows": str(anchor), "linux": str(anchor)},
    }}), encoding="utf-8")
    if record_present:
        (records / "former-host-record.yaml").write_bytes(
            b"# no parse or normalization\r\nmachine: former-host\r\n"
            b"machine: duplicate-old-value\r\nunknown_future_field: preserved\r\n"
        )
    (records / "former-host-record.yaml.machine-label-history.json").write_bytes(
        b'{"state":"prepared","from_machine":"former-host","to_machine":"old-target"}'
    )
    before = {
        str(path.relative_to(home)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in home.rglob("*") if path.is_file()
    }
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_WORKTREES_", "AGENT_HOME"))}
    env.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        AGENT_WORKTREES_STATUS_MONITOR="0",
        PYTHONPATH=str(Path(repair.__file__).resolve().parents[1]),
    )
    command = [
        sys.executable, "-m", "agent_worktrees", "--project", "example", "doctor",
        "--repair-machine-record", "former-host-record", "--from-machine", "former-host",
        "--to-machine", "another-target", "--json",
    ]
    for flags in ([], ["--fix"]):
        result = subprocess.run(
            [*command, *flags], cwd=anchor, env=env, capture_output=True, text=True,
            timeout=20, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["retired"] and not report["repairable"] and not report["repaired"]
        assert {
            str(path.relative_to(home)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in home.rglob("*") if path.is_file()
        } == before
