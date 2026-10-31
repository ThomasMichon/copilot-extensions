"""The rendered POSIX payload shim's no-flock provisioning lock never unlinks
a live lock that it did not judge stale itself.

The acquisition loop is driven deterministically: ``ln``/``readlink``/``rm``/
``kill``/``sleep`` are replaced by shell functions over plain files (a "link"
is a file holding its target), so another process's action can be injected at
an exact point of the loop. This runs under any bash, including Git Bash.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "generate.py"
_spec = importlib.util.spec_from_file_location("payload_invocation_generate_lock", SCRIPT)
generator = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(generator)

LOCK_START = '_lock_link=""\n'

# ``rm`` records a VIOLATION whenever the lock link is removed while it names
# a live owner -- i.e. whenever a contender deletes a lock it does not own.
# Scenarios hook in through before_read/after_read, called with the 1-based
# count of reads of the lock link.
_SHIM = r"""
LINK="$_runtime_root/.provision.lock.pid"
_count() {
    local c; c=$(( $(cat "$STATE/$1" 2>/dev/null || echo 0) + 1 ))
    echo "$c" > "$STATE/$1"; echo "$c"
}
sleep() { :; }
kill() { [[ "$1" == -0 ]] && grep -qx -- "$2" "$STATE/live"; }
ln() { [[ "$1" == -s && ! -e "$3" ]] || return 1; printf '%s' "$2" > "$3"; }
rm() {
    local p
    for p in "$@"; do
        if [[ "$p" == "$LINK" && -e "$p" ]] && kill -0 "$(cat "$p")"; then
            echo VIOLATION >> "$STATE/violations"
        fi
    done
    command rm "$@"
}
readlink() {
    local n="" rc=1
    if [[ "$1" == "$LINK" ]]; then n="$(_count reads)"; before_read "$n"; fi
    if [[ -e "$1" ]]; then cat "$1"; rc=0; fi
    if [[ -n "$n" ]]; then after_read "$n"; fi
    return "$rc"
}
"""


def _bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    root = Path(git).resolve().parents[1]
    for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


def _rendered_lock_section(tmp_path: Path, entry: str) -> str:
    if entry in {"bridge", "containers"}:
        dispatcher = SCRIPT.parents[2] / f"plugins/agent-{entry}/scripts/runtime-gate.sh"
        text = dispatcher.read_text(encoding="utf-8")
        start = text.index(LOCK_START)
        return 'RUNTIME_ROOT="$_runtime_root"\n' + text[start:text.index("\nresolve_runtime\n", start)]
    if entry == "dispatch":
        dispatcher = SCRIPT.parents[2] / "plugins/agent-dispatch/scripts/runtime-gate.sh"
        text = dispatcher.read_text(encoding="utf-8")
        start = text.index('LOCK_LINK=""\n')
        return 'RUNTIME_ROOT="$_runtime_root"\n' + text[start:text.index("\nresolve_runtime\n", start)]
    plugin = tmp_path / "plugin"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    for name, text in (
        ("install.sh", "#!/bin/sh\n"),
        ("install.ps1", "# fixture\n"),
        ("resolve-runtime.sh", 'AGENT_RT_PY=""\n'),
        ("resolve-runtime.ps1", "$AgentRtPy = $null\n"),
    ):
        (scripts / name).write_text(text, encoding="utf-8")
    manifest = plugin / "payload-invocation.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.payload-invocation",
                "version": 1,
                "command": "agent-example",
                "module": "agent_example",
                "runtimeRoot": ".agent-example",
                "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
                "purpose": "Exercise an example runtime",
            }
        ),
        encoding="utf-8",
    )
    rendered = {
        path.name: text for path, text in generator.expected_files(manifest).items()
    }
    shim = rendered["agent-example"]
    start = shim.index(LOCK_START)
    return shim[start : shim.index("\n_resolve_runtime\n", start)]


def _lock_harness(tmp_path: Path, entry: str, hooks: str) -> tuple[Path, Path, Path]:
    section = _rendered_lock_section(tmp_path, entry)
    root = tmp_path / "rt"
    root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (root / ".provision.lock.pid").write_bytes(b"6161")
    (state / "live").write_bytes(b"")
    script = (
        "set -eu\n_command=agent-example\n"
        f"_runtime_root='{_posix(root)}'\nSTATE='{_posix(state)}'\n"
        + _SHIM + hooks + section
        + '\necho "acquired $$ $(cat "$LINK")"\n'
    )
    harness = tmp_path / "harness.sh"
    harness.write_bytes(script.encode())
    return harness, root, state


def _run_harness(harness: Path) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash not available")
    return subprocess.run(
        [bash, _posix(harness)], capture_output=True, text=True, timeout=10,
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _assert_lock_scenario(tmp_path: Path, entry: str, hooks: str) -> None:
    harness, _, state = _lock_harness(tmp_path, entry, hooks)
    result = _run_harness(harness)
    assert result.returncode == 0, result.stderr
    violations = state / "violations"
    assert not violations.exists(), violations.read_text()
    status, pid, holder = result.stdout.split()
    assert status == "acquired" and holder == pid


@pytest.mark.parametrize("entry", ["shim", "dispatch", "bridge", "containers"])
def test_posix_shim_reaper_never_unlinks_a_newer_holder_released_mid_check(
    tmp_path: Path, entry: str,
) -> None:
    """Only remove the owner originally judged stale, even if a live holder
    releases and another holder recreates the link during revalidation."""
    hooks = (
        'before_read() {\n'
        '    case "$1" in\n'
        '        2) printf 7171 > "$LINK"; echo 7171 > "$STATE/live" ;;\n'
        '        3) command rm -f "$LINK" ;;\n'
        '    esac\n'
        '}\n'
        'after_read() {\n'
        '    if [[ "$1" == 2 ]]; then echo 8181 > "$STATE/live"; printf 8181 > "$LINK"; fi\n'
        '}\n'
    )
    _assert_lock_scenario(tmp_path, entry, hooks)


@pytest.mark.parametrize("entry", ["shim", "dispatch", "bridge", "containers"])
@pytest.mark.parametrize(("signal", "exit_code"), [("TERM", 143), ("INT", 130)])
def test_interrupted_reaper_releases_only_its_mutex(
    tmp_path: Path, entry: str, signal: str, exit_code: int,
) -> None:
    interrupt = f'command kill -s {signal} "$$"'
    hooks = (
        'before_read() { :; }\n'
        'after_read() {\n'
        f'    if [[ "$1" == 2 ]]; then {interrupt}; fi\n'
        '}\n'
    )
    harness, root, state = _lock_harness(tmp_path, entry, hooks)
    result = _run_harness(harness)
    assert result.returncode == exit_code, result.stderr
    assert not (root / ".provision.reap.mutex").exists()
    assert (root / ".provision.lock.pid").read_bytes() == b"6161"
    assert "acquired" not in result.stdout
    assert not (state / "violations").exists()
    harness.write_text(harness.read_text().replace(interrupt, ":"), encoding="utf-8")
    retry = _run_harness(harness)
    assert retry.returncode == 0, retry.stderr
    assert retry.stdout.startswith("acquired ")
    assert not (root / ".provision.reap.mutex").exists()


@pytest.mark.parametrize("entry", ["shim", "dispatch", "bridge", "containers"])
@pytest.mark.parametrize("peer", [False, True], ids=["abandoned", "live-peer"])
def test_unowned_reaper_mutex_refuses_within_bounded_attempts(
    tmp_path: Path, entry: str, peer: bool,
) -> None:
    harness, root, state = _lock_harness(
        tmp_path, entry,
        'before_read() { :; }\nafter_read() { :; }\n'
        'sleep() {\n'
        '    if [[ "$(_count waits)" -gt 40 ]]; then\n'
        '        echo "test watchdog: unbounded mutex wait" >&2; exit 97\n'
        '    fi\n'
        '}\n',
    )
    mutex = root / ".provision.reap.mutex"
    mutex.mkdir()
    if peer:
        (mutex / "owner").write_bytes(b"live-peer")
    result = _run_harness(harness)
    assert result.returncode == 126, result.stderr
    assert "reaper mutex" in result.stderr
    assert 1 <= int((state / "waits").read_text()) <= 30
    assert (root / ".provision.lock.pid").read_bytes() == b"6161"
    assert mutex.is_dir()
    assert sorted(p.name for p in mutex.iterdir()) == (["owner"] if peer else [])
    if peer:
        assert (mutex / "owner").read_bytes() == b"live-peer"
    assert not (state / "violations").exists()
    assert "acquired" not in result.stdout


@pytest.mark.parametrize("entry", ["shim", "dispatch", "bridge", "containers"])
@pytest.mark.parametrize(("signal", "exit_code"), [("TERM", 143), ("INT", 130)])
def test_reaper_release_signal_preserves_reacquired_peer_mutex(
    tmp_path: Path, entry: str, signal: str, exit_code: int,
) -> None:
    hooks = (
        'before_read() { :; }\nafter_read() { :; }\n'
        'rmdir() {\n'
        '    _count releases >/dev/null\n'
        '    command rmdir "$@" || return\n'
        '    if [[ ! -e "$STATE/peer" ]]; then\n'
        '        : > "$STATE/peer"\n'
        '        command mkdir "$1"\n'
        '        printf 7171 > "$LINK"; echo 7171 > "$STATE/live"\n'
        f'        command kill -s {signal} "$$"\n'
        '    fi\n'
        '}\n'
    )
    harness, root, state = _lock_harness(tmp_path, entry, hooks)
    result = _run_harness(harness)
    assert result.returncode == exit_code, result.stderr
    assert (root / ".provision.reap.mutex").is_dir()
    assert (root / ".provision.lock.pid").read_bytes() == b"7171"
    assert (state / "releases").read_text().strip() == "1"
    assert not (state / "violations").exists()
    assert "acquired" not in result.stdout


@pytest.mark.parametrize("entry", ["shim", "dispatch", "bridge", "containers"])
def test_live_provision_holder_wait_is_not_reaper_timeout(
    tmp_path: Path, entry: str,
) -> None:
    hooks = (
        'before_read() { :; }\nafter_read() { :; }\n'
        'sleep() {\n'
        '    if [[ "$(_count waits)" == 35 ]]; then\n'
        '        command rm -f "$LINK"; : > "$STATE/live"\n'
        '    fi\n'
        '}\n'
        'echo 6161 > "$STATE/live"\n'
    )
    harness, root, state = _lock_harness(tmp_path, entry, hooks)
    result = _run_harness(harness)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("acquired ")
    assert int((state / "waits").read_text()) == 35
    assert not (root / ".provision.reap.mutex").exists()
    assert not (state / "violations").exists()


@pytest.mark.parametrize("entry", ["shim", "dispatch", "bridge", "containers"])
def test_posix_reaper_serializes_another_reaper_after_final_read(
    tmp_path: Path, entry: str,
) -> None:
    hooks = (
        'before_read() { :; }\n'
        'after_read() {\n'
        '    if [[ "$1" == 2 && ! -d "$_runtime_root/.provision.reap.mutex" ]]; then\n'
        '        echo 8181 > "$STATE/live"; printf 8181 > "$LINK"\n'
        '    fi\n'
        '}\n'
    )
    _assert_lock_scenario(tmp_path, entry, hooks)
