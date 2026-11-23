"""The early read guard must not need either Python or prepared uv."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

GUARD = Path(__file__).resolve().parents[1] / "bootstrap-killswitch-guard.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(os.name == "nt" or BASH is None, reason="native POSIX guard")


@pytest.mark.parametrize("selection", [
    "default", "legacy", "relative", "home-relative", "missing-parent", "linked-parent",
    "physical-parent", "named-user", "named-user-macos",
])
@pytest.mark.parametrize("python", ["absent", "poisoned"])
def test_active_guard_needs_no_python_or_uv(
    tmp_path: Path, selection: str, python: str,
) -> None:
    assert BASH is not None
    home = tmp_path / "home"
    home.mkdir()
    state = home / ".copilot-extensions/killSwitches/bootstrap-killswitch.json"
    if selection == "legacy":
        state = home / ".copilot-extensions/bootstrap-killswitch.json"
    elif selection == "physical-parent":
        state = tmp_path / "physical/hold.json"
    state.parent.mkdir(parents=True)
    reason = 'pause "quoted" \\path\nλ'
    state.write_text(json.dumps({"active": True, "reason": reason}), encoding="utf-8")
    tools = tmp_path / "native-tools"
    tools.mkdir()
    for name in ("dirname", "readlink", "awk"):
        source = shutil.which(name)
        assert source is not None
        shutil.copy2(source, tools / name)
    probe = tmp_path / "interpreter-called"
    if python == "poisoned":
        for name in ("python", "python3"):
            executable = tools / name
            executable.write_text(
                f"#!{BASH}\nprintf called >>\"$INTERPRETER_PROBE\"\nexit 97\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
    environment = {
        "PATH": str(tools), "HOME": str(home), "INTERPRETER_PROBE": str(probe),
    }
    if selection == "relative":
        environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(state.relative_to(tmp_path))
    elif selection == "home-relative":
        environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = "~/" + str(state.relative_to(home))
    elif selection == "missing-parent":
        environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(state.parent / "missing/../" / state.name)
    elif selection == "linked-parent":
        link = tmp_path / "linked"
        link.symlink_to(state.parent, target_is_directory=True)
        environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(link / state.name)
    elif selection == "physical-parent":
        physical = state.parent / "child"
        physical.mkdir()
        link = tmp_path / "linked"
        link.symlink_to(physical, target_is_directory=True)
        environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(link / ".." / state.name)
    elif selection.startswith("named-user"):
        lookup = tools / ("getent" if selection == "named-user" else "dscacheutil")
        lookup.write_text(
            f"#!{BASH}\n" + (
                'printf "fixture:x:1:1:fixture:%s:/bin/sh\\n" "$HOME"\n'
                if selection == "named-user" else 'printf "dir: %s\\n" "$HOME"\n'
            ), encoding="utf-8",
        )
        lookup.chmod(0o755)
        environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = "~fixture/" + str(state.relative_to(home))
    result = subprocess.run(
        [BASH, str(GUARD), "check"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0 and result.stdout == "", result.stdout + result.stderr
    assert reason in result.stderr
    assert not probe.exists(), "the early guard executed a PATH interpreter"


@pytest.mark.parametrize("active", [False, "true", 1, None])
def test_native_guard_does_not_promote_nontrue_state(tmp_path: Path, active: object) -> None:
    assert BASH is not None
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"active": active, "reason": "not active"}), encoding="utf-8")
    result = subprocess.run(
        [BASH, str(GUARD), "check"],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(state)},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 1 and result.stdout == "" and result.stderr == ""
