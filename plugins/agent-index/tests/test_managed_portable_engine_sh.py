"""Foreground engine ingress without Linux-specific selector supervision."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

import pytest

from test_install_engine_adoption import PLUGIN, function, stage_index_installer
from test_managed_bootstrap_reader_sh import prepared_helper as prepared_helper
from test_native_runtime_uninstall import (
    test_nested_engine_capture_helper_removal_plan as _native_uninstall,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native POSIX engine launcher")


def _wait_for_absence(probe: Callable[[], object], deadline: float) -> None:
    while True:
        assert time.monotonic() < deadline, "Owned capture exceeded its cleanup deadline"
        try:
            probe()
        except ProcessLookupError:
            return
        time.sleep(.01)


@pytest.mark.parametrize("materialized", [False, True])
@pytest.mark.parametrize("selection", [
    "current", "lkg", "newest", "legacy", "incomplete",
    "missing-uv", "denied", "wrong", "missing-support",
    "cancel-term", "cancel-kill", "timeout",
    "background", "errexit-denied", "errexit-timeout",
    "cancel-release",
])
def test_portable_engine_uses_prepared_selection_and_foreground_exec(
    tmp_path: Path, materialized: bool, selection: str,
    prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    actual_uv, _helper, store = prepared_helper
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PYTHON")) and key not in {"VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    environment.update(
        UV_PYTHON_INSTALL_DIR=str(store), UV_OFFLINE="1", UV_NO_CONFIG="1",
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
    )
    found = subprocess.run(
        [str(actual_uv), "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.10.20"],
        env=environment, capture_output=True, text=True, timeout=15,
    )
    if found.returncode and "No interpreter found" in found.stderr:
        pytest.skip("Prepare CPython 3.10.20 before this acquisition-free control")
    assert found.returncode == 0, found.stdout + found.stderr
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=materialized,
    )
    scripts = payload / "scripts"
    shutil.copy2(PLUGIN / "scripts/resolve-runtime.sh", scripts / "resolve-runtime.sh")
    support = scripts if materialized else payload.parents[1] / "libs/installer-engine"
    root = tmp_path / "engine's \u00e9 $literal \\path"
    slot = root / (".venv" if selection == "legacy" else "versions/1.0.0")
    built = subprocess.run(
        [found.stdout.strip(), "-I", "-B", "-m", "venv", "--copies", "--without-pip", str(slot)],
        env=environment, capture_output=True, text=True, timeout=20,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / "bin/python"
    site = next(slot.glob("lib/python*/site-packages"))
    package = site / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json,os,sys\n"
        "print(json.dumps({'pid':os.getpid(),'prefix':sys.prefix,'args':sys.argv[1:],"
        "'version':list(sys.version_info[:2]),'isolated':sys.flags.isolated,"
        "'bytecode':sys.dont_write_bytecode,'cwd':os.getcwd(),"
        "'uv':os.environ.get('AGENT_INDEX_UV'),'port':os.environ.get('AGENT_INDEX_ENGINE_PORT'),"
        "'home':os.environ.get('AGENT_INDEX_ENGINE_HOME'),'stdin':sys.stdin.read()}))\n"
        "print('application stderr',file=sys.stderr)\nraise SystemExit(23)\n", encoding="utf-8",
    )
    legacy = python
    if selection != "legacy":
        legacy = root / "stale engine/bin/python"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 97\n', encoding="utf-8",
        )
        legacy.chmod(0o700)
        (root / "current-version").write_text(
            "1.0.0" if selection != "newest" else "absent", encoding="utf-8",
        )
        if selection != "incomplete":
            (slot / ".install-complete.json").write_text(json.dumps({
                "version": "1.0.0", "completed_at": "fixture", "pid": 0,
            }), encoding="utf-8")
        if selection == "lkg":
            (root / "current-version").unlink()
            (root / "current-version").symlink_to(root / "absent-marker")
            (root / "last-known-good").write_text("1.0.0", encoding="utf-8")
        elif selection == "newest":
            (root / "last-known-good").write_text("also-absent", encoding="utf-8")
    env_file = root / "engine's.env"
    env_file.write_text(
        "AGENT_INDEX_ENGINE_PORT=19421\nAGENT_INDEX_UV=/foreign/env-file/uv\n",
        encoding="utf-8",
    )
    tools = tmp_path / "private tools"
    tools.mkdir()
    for name in ("bash", "cat", "cp", "mkdir", "chmod", "mv", "dirname", "sort",
                 "awk", "tr", "sed", "date", "grep", "cut", "mktemp", "stat", "rmdir", "rm",
                 "mkfifo", "sleep"):
        binary = shutil.which(name)
        assert binary, name
        (tools / name).symlink_to(binary)
    for name in ("sha256sum", "shasum"):
        binary = shutil.which(name)
        if binary:
            (tools / name).symlink_to(binary)
    for name in ("uv", "python", "python3", "ps"):
        poison = tools / name
        poison.write_text(
            '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 97\n', encoding="utf-8",
        )
        poison.chmod(0o700)
    uv = tmp_path / "bound uv's"
    uv.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$UV_PROBE_LOG"\n'
        'case "$SELECTOR_MODE" in denied|errexit-denied) '
        'printf "selector denied\\n" >&2; exit 93;;\n'
        'wrong) printf "/foreign/python\\n"; exit 0;;\n'
        'cancel-term|cancel-kill|*timeout)\n'
        'trap "" TERM\n'
        '/bin/bash -c \'trap "" TERM; printf "%s\\n" "$$" > "$UV_CHILD"; '
        'exec sleep 60\' &\n'
        'printf "%s\\n" "$$" > "$UV_PARENT"\n'
        'wait; exit 94;; esac\n'
        f'exec {shlex.quote(str(actual_uv))} "$@"\n', encoding="utf-8",
    )
    uv.chmod(0o700)
    environment.update(
        PATH=str(tools), AMBIENT_MARKER=str(tmp_path / "ambient-ran"),
        UV_PROBE_LOG=str(tmp_path / "uv-calls"), SELECTOR_MODE=selection,
        UV_PYTHON=str(tools / "python"), UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        PYTHONHOME="/invalid", PYTHONPATH="/invalid", VIRTUAL_ENV="/invalid",
        CONDA_PREFIX="/invalid", AGENT_RT_ROOT="/foreign/root",
        UV_PARENT=str(tmp_path / "uv-parent"), UV_CHILD=str(tmp_path / "uv-child"),
        TMPDIR=str(tmp_path),
        CAPTURE_RELEASE_GATE=str(tmp_path / "capture-release"),
    )
    publish = tmp_path / "publish.sh"
    publish.write_text(
        "set -euo pipefail\n"
        f". {shlex.quote(str(support / 'installer-engine.sh'))}\n"
        f"SCRIPT_DIR={shlex.quote(str(scripts))}\n"
        f"ENGINE_HOME={shlex.quote(str(root))}\n"
        f"ENGINE_VENV_PYTHON={shlex.quote(str(legacy))}\n"
        f"ENGINE_ENV_FILE={shlex.quote(str(env_file))}\n"
        'ENGINE_LAUNCHER="$ENGINE_HOME/engine.sh"\n'
        f"INSTALL_DIR={shlex.quote(str(tmp_path / 'independent service'))}\n"
        f"ensure_uv() {{ printf '%s\\n' {shlex.quote(str(uv))}; }}\n"
        "_ok() { :; }; _skip() { :; }; _fail() { echo \"$*\" >&2; }\n"
        "uname() { printf 'Darwin\\n'; }\n"
        + function("_write_engine_files", "sh") + "\n_write_engine_files\n",
        encoding="utf-8",
    )
    generated = subprocess.run(
        ["/bin/bash", str(publish)], env=environment, cwd=tmp_path,
        capture_output=True, text=True, timeout=20,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    assert not (root / "bin/native_owned.sh").exists()
    if selection == "missing-uv":
        uv.rename(uv.with_name("unavailable-uv"))
    elif selection == "missing-support":
        (root / "bin/bootstrap-uv.sh").rename(root / "bin/unavailable-support")
    elif selection == "cancel-release":
        capture = root / "bin/native_capture.sh"
        source = capture.read_text(encoding="utf-8")
        gate = "    exec 8>&-\n"
        assert source.count(gate) == 1
        capture.write_text(source.replace(
            gate, gate + '    printf "%s\\n" "$__native_capture_pid" > "$CAPTURE_RELEASE_GATE"\n'
            '    IFS= read -r -t 15 _capture_release_pause\n',
        ), encoding="utf-8")
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    successful = selection in {"current", "lkg", "newest", "legacy", "background"}
    samples = []
    for _cycle in range(2 if successful else 1):
        started = time.monotonic()
        launch = 'printf "%s\\n" "$$"; exec /bin/bash "$@"'
        if selection == "background":
            launch = '/bin/bash "$@" <&0 & child=$!; printf "%s\\n" "$child"; wait "$child"'
        elif selection.startswith("errexit-"):
            launch = "set -e; export SHELLOPTS; " + launch
        process = subprocess.Popen(
            ["/bin/bash", "-c", launch, "fixture",
             str(root / "engine.sh"), "two words", "", "\u00e9"],
            env=environment, cwd=tmp_path, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            if selection.startswith("cancel-"):
                gate_file = tmp_path / (
                    "capture-release" if selection == "cancel-release" else "uv-child"
                )
                deadline = time.monotonic() + 5
                while (
                    (not gate_file.is_file() or not gate_file.stat().st_size)
                    and time.monotonic() < deadline
                ):
                    time.sleep(.01)
                assert gate_file.is_file() and gate_file.stat().st_size, (
                    "Capture never reached its cancellation gate"
                )
                if selection == "cancel-term":
                    process.send_signal(signal.SIGTERM)
                else:
                    process.kill()
            stdout, stderr = process.communicate("small input\n", timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=15)
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        samples.append(time.monotonic() - started)
        lines = result.stdout.splitlines()
        if successful:
            assert result.returncode == 23, (result.returncode, result.stdout, result.stderr)
            assert len(lines) == 2, result.stdout + result.stderr
            observed = json.loads(lines[1])
            assert observed == {
                "pid": int(lines[0]), "prefix": str(slot),
                "args": ["engine", "run", "two words", "", "\u00e9"],
                "version": [3, 10], "isolated": 1, "bytecode": True,
                "cwd": str(root), "uv": str(uv), "port": "19421", "home": str(root),
                "stdin": "small input\n",
            }, observed
            assert result.stderr == "application stderr\n", repr(result.stderr)
        else:
            assert result.returncode == {
                "incomplete": 1, "missing-uv": 127, "denied": 93,
                "wrong": 1, "missing-support": 126,
                "cancel-term": 143, "cancel-kill": -9, "timeout": 124,
                "errexit-denied": 93, "errexit-timeout": 124,
                "cancel-release": -9,
            }[selection], result.stdout + result.stderr
            assert len(lines) == 1, result.stdout + result.stderr
        cleanup_deadline = min(started + 20, time.monotonic() + 5)
        if selection == "cancel-release":
            group = int((tmp_path / "capture-release").read_text())
            _wait_for_absence(lambda: getattr(os, "killpg")(group, 0), cleanup_deadline)
        elif selection.startswith("cancel-") or selection.endswith("timeout"):
            for name in ("uv-parent", "uv-child"):
                pid = int((tmp_path / name).read_text())
                _wait_for_absence(lambda: os.kill(pid, 0), cleanup_deadline)
        assert not list(tmp_path.glob("native-capture.*"))
        assert not Path(environment["AMBIENT_MARKER"]).exists()
        assert {path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()} == before
    calls = Path(environment["UV_PROBE_LOG"])
    assert (calls.read_text().splitlines() if calls.exists() else []) == (
        [f"python find --no-project --managed-python --no-python-downloads {python}"] * len(samples)
        if successful or selection in {
            "denied", "wrong", "cancel-term", "cancel-kill", "timeout",
            "errexit-denied", "errexit-timeout",
            "cancel-release",
        }
        else []
    )
    record_property("index_portable_engine", json.dumps({
        "layout": "materialized" if materialized else "raw", "selection": selection,
        "platform": sys.platform, "status": "measured", "completed": len(samples),
        "samples_seconds": samples, "runtime_python": "3.10.20", "network": "disabled",
        "unavailable": ["installed supervision", "production engine", "child CPU", "tails"],
    }))


def test_portable_engine_nested_helper_uninstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _native_uninstall(tmp_path, monkeypatch)
