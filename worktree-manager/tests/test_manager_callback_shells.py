"""Execute source/legacy fallback resolution with an owned uv stand-in."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

BIN = Path(__file__).resolve().parents[1] / "bin"


@pytest.mark.parametrize("layout", ["source", "retained", "flat", "nested", "old-plan"])
@pytest.mark.parametrize("shell", ["ps1", "sh"] if os.name == "nt" else ["sh"])
def test_pane_preparation_preserves_the_versioned_contract_for_wheel_assets(tmp_path, layout, shell):
    root = tmp_path / "Manager with spaces"
    slot = root / "versions" / "1.2.3"
    prefix = slot / ".venv" if layout in {"nested", "old-plan"} else slot
    payload = (
        tmp_path / "source" if layout == "source" else slot / "payload" if layout == "retained"
        else prefix / "lib" / "site-packages" / "worktree_manager" / "_runtime_assets"
    )
    bin_path = payload / "bin"
    bin_path.mkdir(parents=True)
    if layout not in {"source", "retained"}:
        (prefix / "pyvenv.cfg").touch()
        if prefix != slot:
            (slot / "application.json").write_text("{}")
    for name in (f"manager-runtime.{shell}",):
        shutil.copyfile(BIN / name, bin_path / name)
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    copied = prepared / f"pane-wrapper.{shell}"
    copied.touch()
    (prepared / "pane-launch.ps1").touch()
    (bin_path / f"pane-wrapper.{shell}").touch()
    (bin_path / "pane-launch.ps1").touch()
    admitted = layout not in {"source", "old-plan"}
    version = 0 if layout == "old-plan" else 1
    record = tmp_path / "launch.json"
    if shell == "ps1":
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        source = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
        block = source.split("    $paneCmd = $cmd\n", 1)[1].split("    $ahpTokenFile = $null", 1)[0]
        script = tmp_path / "prepare.ps1"
        script.write_text(
            f"$PSScriptRoot={quote(bin_path)};$RuntimeDir={quote(tmp_path / 'engine')}\n"
            f"$plan=@{{pane_shell_version={version};record_path={quote(record)}}}\n"
            "function Resolve-RuntimePython { 'Invoke-FixturePython' }\n"
            "function Invoke-FixturePython { $script:ObservedArgs=@($args);$global:LASTEXITCODE=0;"
            f"@{{wrapper={quote(copied)};directory={quote(prepared)}}}|ConvertTo-Json -Compress }}\n"
            + block +
            "\n@{root=$paneManagerRoot;wrapper=$paneWrapper;arguments=$script:ObservedArgs}"
            "|ConvertTo-Json -Compress",
            encoding="utf-8",
        )
        command = [shutil.which("pwsh"), "-NoProfile", "-File", str(script)]
    else:
        source = (BIN / "launch-session.sh").read_text(encoding="utf-8")
        block = 'source "$SCRIPT_DIR/manager-runtime.sh"' + source.split(
            'source "$SCRIPT_DIR/manager-runtime.sh"', 1,
        )[1].split('        AHP_TOKEN_FILE=""', 1)[0]
        response = json.dumps({"wrapper": copied.as_posix(), "directory": prepared.as_posix()})
        observed = tmp_path / "arguments.json"
        # Exercise only launcher dispatch here; native POSIX lifecycle is a separate suite.
        code = (
            f"SCRIPT_DIR={shlex.quote(bin_path.as_posix())};"
            f"RECORD_PATH={shlex.quote(record.as_posix())};PANE_SHELL_VERSION={version};"
            f"RUNTIME_DIR={shlex.quote((tmp_path / 'engine').as_posix())};OSTYPE=linux-gnu\n"
            f"PANE_WRAPPER=\"$SCRIPT_DIR/pane-wrapper.sh\";PANE_MANAGER_ROOT='';PYTHON=fixture_python\n"
            "fixture_python() { if [[ \"$1\" == -I ]]; then "
            f"printf '%s\\0' \"$@\" > {shlex.quote(observed.as_posix())};"
            f"printf '%s' {shlex.quote(response)};"
            f"else {shlex.quote(Path(sys.executable).as_posix())} \"$@\";fi; }}\n"
            + block + '\nprintf "%s\\0%s" "$PANE_MANAGER_ROOT" "$PANE_WRAPPER"'
        )
        bash = Path(shutil.which("git")).parent.parent / "bin" / "bash.exe" if os.name == "nt" else shutil.which("bash")
        command = [str(bash), "-c", code]
    result = subprocess.run(command, capture_output=True, timeout=15, **no_window_kwargs())
    assert result.returncode == 0, result.stderr
    if shell == "ps1":
        reply = json.loads(result.stdout)
        expected_root = str(root)
        expected_wrapper = str(copied if admitted else bin_path / "pane-wrapper.ps1")
    else:
        manager_root, wrapper = result.stdout.decode().split("\0")
        reply = {"root": manager_root, "wrapper": wrapper, "arguments": (
            observed.read_bytes().decode().rstrip("\0").split("\0") if observed.exists() else None
        )}
        expected_root = root.as_posix()
        expected_wrapper = (copied if admitted else bin_path / "pane-wrapper.sh").as_posix()
    assert reply["root"] == (expected_root if admitted else None if shell == "ps1" else "")
    assert reply["wrapper"] == expected_wrapper
    if admitted:
        assert reply["arguments"][:3] == ["-I", "-m", "agent_worktrees.session_shell"]
        assert reply["arguments"][-1] == expected_root
    else:
        assert reply["arguments"] is None


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("shell", ["ps1", "sh"] if os.name == "nt" else ["sh"])
def test_source_and_legacy_callbacks_keep_project_entry(tmp_path, legacy, shell):
    payload = tmp_path / "versions" / "1.2.3" if legacy else tmp_path / "source with spaces"
    payload.mkdir(parents=True)
    if shell == "sh":
        bash = Path(shutil.which("git")).parent.parent / "bin" / "bash.exe" if os.name == "nt" else shutil.which("bash")
        code = (
            'source "$1"; uv() { printf "%s\\0" "$@"; }; '
            'aw_manager_callback "$2" register "--project=two words"'
        )
        process = subprocess.run([
            str(bash), "-c", code, "_", (BIN / "manager-runtime.sh").as_posix(), payload.as_posix(),
        ], capture_output=True, timeout=15, **no_window_kwargs())
        assert process.returncode == 0, process.stderr
        arguments = process.stdout.decode().rstrip("\0").split("\0")
        assert arguments[:4] == ["run", "--quiet", "--project", payload.as_posix()]
    else:
        pwsh = shutil.which("pwsh")
        assert pwsh
        (tmp_path / "run").write_text(
            "import json,sys; from pathlib import Path;"
            "Path('arguments.json').write_text(json.dumps(sys.argv[1:]))",
        )
        script = tmp_path / "invoke.ps1"
        script.write_text(
            f". '{BIN / 'manager-runtime.ps1'}'\n"
            "function Get-Command { param($Name,$CommandType,$ErrorAction);"
            f"[pscustomobject]@{{Source='{sys.executable}'}} }}\n"
            f"Invoke-ManagerMuxCallback -PayloadRoot '{payload}' "
            "-CallbackArguments @('register','--project=two words')",
        )
        process = subprocess.run([
            pwsh, "-NoProfile", "-NonInteractive", "-File", str(script),
        ], cwd=tmp_path, capture_output=True, timeout=15, **no_window_kwargs())
        assert process.returncode == 0, process.stderr
        arguments = ["run", *json.loads((tmp_path / "arguments.json").read_text())]
        assert arguments[:4] == ["run", "--quiet", "--project", str(payload)]
    assert arguments[4:] == ["-m", "worktree_manager.mux_callback", "register", "--project=two words"]
