"""Check the real package-to-witness and witness-to-image input contracts."""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_gateway_witness_keeps_committed_package_and_image_inputs_complete() -> None:
    source = runpy.run_path(
        str(ROOT / "tools" / "clean-room" / "gateway-role-tunnels" / "source.py"),
    )
    required = source["REQUIRED"]
    assert isinstance(required, frozenset)
    declared = {name for name in required if isinstance(name, str)}
    assert len(declared) == len(required)
    environment = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "WINDIR")
        if name in os.environ
    }
    environment.update(
        HOME=str(ROOT), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
        GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="",
    )
    prefixes = ("fleet-manager/src/", "libs/agent-procutil/src/")
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(ROOT), "ls-tree", "-r",
         "--name-only", "HEAD", "--", *prefixes],
        capture_output=True, text=True, check=True, timeout=15, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tracked = set(result.stdout.splitlines())
    for prefix in prefixes:
        actual = {name for name in tracked if name.startswith(prefix)}
        selected = {name for name in declared if name.startswith(prefix)}
        assert actual <= selected, sorted(actual - selected)
        assert selected <= actual, sorted(selected - actual)
    exceptions = {
        line[1:]
        for line in (ROOT / "fleet-manager" / "containers" / "gateway"
                     / "Dockerfile.dockerignore").read_text().splitlines()
        if line.startswith("!")
    }
    procutil = {name for name in tracked if name.startswith(prefixes[1])}
    assert procutil <= exceptions, sorted(procutil - exceptions)
