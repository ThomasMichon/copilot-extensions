"""Native Bash reservation and composed installer-exit contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from test_installer_diagnostics import SCRIPTS, load_diagnostics, native_bash, native_environment

pytestmark = pytest.mark.skipif(native_bash() is None, reason="native Bash unavailable")


@pytest.mark.skipif(os.name == "nt", reason="POSIX reservation parser; Windows delegates to PowerShell")
@pytest.mark.parametrize("formatting", ["canonical", "multiline", "wrong-plugin"])
def test_python_reserved_attempt_enters_native_bash_without_bootstrap_python(tmp_path, formatting):
    diagnostics = load_diagnostics()
    environment = diagnostics.reserve_launch(native_environment(
        HOME=str(tmp_path), USERPROFILE=str(tmp_path),
    ))
    directory = Path(environment["COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY"])
    receipt = directory / "attempt.json"
    if formatting != "canonical":
        record = json.loads(receipt.read_text())
        if formatting == "wrong-plugin":
            record["plugin"] = "another-plugin"
        receipt.write_text(json.dumps(record, indent=2).replace('": ', '"\n:\t') + "\n")
    result = subprocess.run(
        [native_bash(), "-c", """
set -euo pipefail
source "$1"
installer_diagnostics_init agent-worktrees
installer_diagnostics_write 'native reservation entered'
installer_diagnostics_complete 37
printf '%s' "$COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"
exit 37
""", "bash", str(SCRIPTS / "installer-diagnostics.sh")],
        env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 37, result.stderr
    assert result.stdout == ("1" if formatting == "wrong-plugin" else "0")
    writers = list(directory.glob("*.start.json"))
    if formatting == "wrong-plugin":
        assert not writers and "missing diagnostics" in result.stderr
    else:
        assert len(writers) == 1
        writer = writers[0].name.removesuffix(".start.json")
        assert "native reservation entered" in (directory / f"{writer}.log").read_text()
        assert json.loads((directory / f"{writer}.completed.json").read_text())["exit"] == 37
        assert not (directory / "completed.json").exists(), "reservation caller owns attempt completion"


@pytest.mark.parametrize("native_exit,release_failure", [(0, False), (37, False), (0, True), (37, True)])
def test_actual_installer_exit_trap_composes_release_and_native_writer_completion(
    tmp_path, native_exit, release_failure,
):
    source = (SCRIPTS / "install.sh").read_text()
    function = re.search(r"^_installer_exit\(\) \{\n.*?^\}", source, re.MULTILINE | re.DOTALL)
    assert function, "the actual installer must own one composed exit function"
    traps = re.findall(r"^trap .* EXIT$", source, re.MULTILINE)
    assert traps == ["trap '_installer_exit' EXIT"], "a later EXIT trap must not overwrite completion"
    release = tmp_path / "released"
    environment = native_environment(
        host="powershell.exe" if os.name == "nt" else None,
        HOME=tmp_path.as_posix(), USERPROFILE=str(tmp_path),
    )
    for key in ("ATTEMPT", "WRITER", "DIRECTORY", "MISSING"):
        environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_" + key, None)
    program = """
set -euo pipefail
source "$1"
installer_diagnostics_init agent-worktrees
release_path="$2"
release_rc="$3"
_release_versioned_slot_lease() {
    printf 'released\\n' > "$release_path"
    return "$release_rc"
}
""" + function.group() + "\n" + "\n".join(traps) + f"\nprintf '{{\"native\":{native_exit}}}'\nexit {native_exit}\n"
    result = subprocess.run(
        [native_bash(), "-c", program, "bash", (SCRIPTS / "installer-diagnostics.sh").as_posix(),
         release.as_posix(), "23" if release_failure else "0"],
        env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == native_exit, result.stderr
    assert json.loads(result.stdout) == {"native": native_exit}
    assert ("WARNING: installer lease cleanup failed" in result.stderr) is release_failure
    assert release.read_text() == "released\n"
    attempts = list((tmp_path / ".copilot-extensions" / "installer-diagnostics").iterdir())
    assert len(attempts) == 1
    writers = list(attempts[0].glob("*.completed.json"))
    assert len(writers) == 1 and json.loads(writers[0].read_text())["exit"] == native_exit
    assert json.loads((attempts[0] / "completed.json").read_text())["exit"] == native_exit
    archive = "\n".join(path.read_text() for path in attempts[0].glob("*.log"))
    assert ("installer lease cleanup failed" in archive) is release_failure
