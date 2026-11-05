"""A real package-manager process boundary with no host package effects."""

from __future__ import annotations

import json
import os
import shlex
import sys

from agent_machines.resources import ResourceContext, apply_resources

from .test_package_compatibility import package


def test_real_manager_lookup_and_upgrade_preserve_compatible_versions(tmp_path, monkeypatch):
    helper = tmp_path / "fake_winget.py"
    helper.write_text(
        """
import json
from pathlib import Path
import sys

state_file = Path(__file__).with_suffix(".json")
state = json.loads(state_file.read_text())
args = sys.argv[1:]
assert args[args.index("--id") + 1] == "marlocarlo.psmux"
if args[0] == "list":
    print("PSMux marlocarlo.psmux " + state["version"])
elif args[0] == "upgrade":
    assert "--version" not in args
    state["version"] = "3.4.0"
    state["upgrades"] += 1
    state_file.write_text(json.dumps(state))
else:
    raise AssertionError(args)
""",
        encoding="utf-8",
    )
    command = tmp_path / ("winget.cmd" if os.name == "nt" else "winget")
    command.write_text(
        f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\nexit /b %errorlevel%\r\n'
        if os.name == "nt" else
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(helper))} "$@"\n',
        encoding="utf-8",
    )
    command.chmod(command.stat().st_mode | 0o111)
    state_file = helper.with_suffix(".json")
    state_file.write_text(json.dumps({"version": "3.4.0", "upgrades": 0}), encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    declaration = package(tmp_path)
    context = ResourceContext(tmp_path, {}, "windows")

    def restore():
        return apply_resources(
            [declaration], "box-1", "windows", context, dry_run=False,
        )[0]

    assert restore().status == "ok"
    assert json.loads(state_file.read_text()) == {"version": "3.4.0", "upgrades": 0}
    state_file.write_text(json.dumps({"version": "3.3.6", "upgrades": 0}), encoding="utf-8")
    assert restore().action == "update"
    assert json.loads(state_file.read_text()) == {"version": "3.4.0", "upgrades": 1}
    assert restore().status == "ok"
    assert json.loads(state_file.read_text())["upgrades"] == 1
