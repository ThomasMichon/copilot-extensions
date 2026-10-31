"""Actual fixed-shell handoff controls, not official-image deployment proof."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

import pytest

from fleet_manager.komodo_profile import MONGO_COMMAND, render_profile
from fleet_manager.planning import decode
from test_komodo_native import native_rig as native_rig

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux fixed image profile")


@pytest.mark.parametrize("missing", [False, True])
def test_fixed_mongo_shell_reads_private_mounts_before_vendor_handoff(
    tmp_path: Path, missing: bool,
) -> None:
    from agent_procutil.owned_process import run_caller_owned

    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    expected = {"database_username": "example-owner",
                "database_password": "literal $(exit 91); ' \" * secret-value"}
    for name, value in expected.items():
        if not (missing and name == "database_password"):
            path = secrets / name
            path.write_text(value)
            path.chmod(0o600)
    observation = tmp_path / "vendor-observation.json"
    entrypoint = tmp_path / "docker-entrypoint.sh"
    entrypoint.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "assert 'MONGO_INITDB_ROOT_USERNAME_FILE' not in os.environ\n"
        "assert 'MONGO_INITDB_ROOT_PASSWORD_FILE' not in os.environ\n"
        "Path(os.environ['OBSERVATION']).write_text(json.dumps({\n"
        " 'argv': sys.argv[1:],\n"
        " 'database_username': os.environ['MONGO_INITDB_ROOT_USERNAME'],\n"
        " 'database_password': os.environ['MONGO_INITDB_ROOT_PASSWORD']}))\n"
    )
    entrypoint.chmod(0o700)
    script = MONGO_COMMAND[2].replace("/run/secrets/", str(secrets) + "/")
    result = run_caller_owned(
        [*MONGO_COMMAND[:2], script], cwd=str(tmp_path), timeout=10,
        env={"PATH": str(tmp_path) + os.pathsep + os.defpath,
             "OBSERVATION": str(observation),
             "MONGO_INITDB_ROOT_USERNAME_FILE": "unused-before-drop",
             "MONGO_INITDB_ROOT_PASSWORD_FILE": "unused-before-drop"},
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.stdout == ""
    assert expected["database_password"] not in result.stderr
    for path in secrets.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    if missing:
        assert result.returncode != 0 and not observation.exists()
    else:
        assert result.returncode == 0
        assert json.loads(observation.read_text()) == {
            **expected, "argv": ["mongod", "--quiet", "--wiredTigerCacheSizeGB", "0.25"],
        }


def test_compose_escaping_and_retained_periphery_key_are_explicit(native_rig: Any) -> None:
    selected, material = native_rig["selected"], native_rig["material"]
    profile = decode(render_profile(selected, material, owner="a" * 64))
    command = profile["services"]["mongo"]["command"]
    assert tuple(argument.replace("$$", "$") for argument in command) == MONGO_COMMAND
    assert "$(" not in "".join(command).replace("$$", "")
    assert profile["services"]["periphery"]["environment"]["PERIPHERY_PRIVATE_KEY"] == (
        "file:/config/keys/periphery.key"
    )
    encoded = json.dumps(profile)
    for value in material.credential_bytes().values():
        assert value.decode() not in encoded
