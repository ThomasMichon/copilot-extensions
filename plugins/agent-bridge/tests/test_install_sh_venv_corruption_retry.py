"""POSIX regression coverage for the transient venv-corruption retry wrapper
(#6852) in ``install.sh`` -- the Linux/WSL counterpart to
``test_install_venv_corruption_retry.py``'s PowerShell coverage. Keeps
``_uv_venv_resilient``'s behavior from silently drifting between the two
installers: retry with backoff on the pyvenv.cfg-corruption signature (or
the original #6785 SRE-mismatch signature), retry a "successful" run that
didn't actually leave a pyvenv.cfg behind, surface any other failure
immediately, and give up after the bounded number of attempts.

Mirrors production's stdout/stderr split exactly (the real ``_warn`` writes
to stderr, and the wrapper's success payload goes to stdout while its
failure payload goes to stderr) so the harness can't accidentally pass by
conflating the two streams.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)


def _extract_sh_functions(*names: str) -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        start = text.index(f"{name}()")
        # Each helper is closed by a `}` at column 0 (this file's top-level
        # function-closing convention).
        end = text.index("\n}\n", start)
        chunks.append(text[start : end + 2])
    return "\n\n".join(chunks)


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _run_harness(
    tmp_path: Path, uv_stub_body: str, extra_script: str, delays_file: Path
) -> subprocess.CompletedProcess:
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        + _extract_sh_functions(
            "_is_sre_module_mismatch", "_is_venv_corruption", "_uv_venv_resilient"
        )
        # Matches the real `_warn() { echo "  [WARN] $*" >&2; }` -- routed to
        # stderr so it never pollutes the wrapper's captured stdout payload.
        + """
_warn() { echo "WARN: $*" >&2; }

"""
        + uv_stub_body
        + "\n\n"
        + extra_script
        + "\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    # Stub `sleep` so the test doesn't actually wait through the 3s/6s/10s
    # backoff schedule, while recording each requested delay so the caller
    # can assert the actual schedule, not just the retry count.
    _executable(fake_bin / "sleep", f'#!/bin/sh\necho "$1" >> "{delays_file}"\nexit 0\n')
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run(
        [_BASH, str(harness)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=True,
    )


def _delays(delays_file: Path) -> list[int]:
    if not delays_file.exists():
        return []
    return [int(line) for line in delays_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_pyvenv_cfg_corruption_retries_then_succeeds(tmp_path: Path) -> None:
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    if [ "$n" -lt 3 ]; then
        echo 'error: Failed to inspect Python interpreter'
        echo 'failed to locate pyvenv.cfg: The system cannot find the file specified.'
        return 106
    fi
    echo 'home = fake' > "{venv_dir}/pyvenv.cfg"
    echo 'Created venv'
    return 0
}}
"""
    extra = f"""
if out=$(_uv_venv_resilient '{venv_dir}' --python 3.10 --allow-existing); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(tmp_path, uv_stub, extra, delays_file)
    # Two retries needed (three total attempts) -- both backoff warnings fire
    # on stderr (matches production's stderr-only `_warn`), never on stdout.
    assert result.stderr.count("uv venv hit a transient pyvenv.cfg corruption") == 2
    assert "uv venv hit a transient pyvenv.cfg corruption" not in result.stdout
    assert "EXIT:0" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "3"
    assert (venv_dir / "pyvenv.cfg").exists()
    # The first two entries of the 3s/6s/10s backoff schedule, in order.
    assert _delays(delays_file) == [3, 6]


def test_success_without_pyvenv_cfg_is_retried(tmp_path: Path) -> None:
    """uv can exit 0 while a concurrent writer is still touching the same
    slot -- the wrapper must not trust a zero exit code alone."""
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    if [ "$n" -ge 2 ]; then
        echo 'home = fake' > "{venv_dir}/pyvenv.cfg"
    fi
    echo 'Created venv'
    return 0
}}
"""
    extra = f"""
if out=$(_uv_venv_resilient '{venv_dir}' --allow-existing); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(tmp_path, uv_stub, extra, delays_file)
    assert result.stderr.count("uv venv reported success but pyvenv.cfg is missing") == 1
    assert "EXIT:0" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "2"
    assert (venv_dir / "pyvenv.cfg").exists()
    assert _delays(delays_file) == [3]


def test_unrelated_failure_is_not_retried(tmp_path: Path) -> None:
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    echo 'error: network unreachable'
    return 1
}}
"""
    extra = f"""
if out=$(_uv_venv_resilient '{venv_dir}' --allow-existing); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(tmp_path, uv_stub, extra, delays_file)
    assert "uv venv hit a transient" not in result.stderr
    assert "pyvenv.cfg is missing" not in result.stderr
    assert "EXIT:1" in result.stdout
    # The wrapper's final (unretried) failure payload goes to stderr.
    assert "error: network unreachable" in result.stderr
    # Only one attempt -- an unrelated failure must not trigger the retry.
    assert counter_file.read_text(encoding="utf-8").strip() == "1"
    assert _delays(delays_file) == []


def test_persisting_corruption_still_fails_after_all_retries(tmp_path: Path) -> None:
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    echo 'failed to locate pyvenv.cfg: The system cannot find the file specified.'
    return 106
}}
"""
    extra = f"""
if out=$(_uv_venv_resilient '{venv_dir}' --allow-existing); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(tmp_path, uv_stub, extra, delays_file)
    # One initial attempt plus three backoff retries -- three warnings fire
    # (one before each retry), all on stderr.
    assert result.stderr.count("uv venv hit a transient pyvenv.cfg corruption") == 3
    assert "EXIT:1" in result.stdout
    # One initial attempt plus three backoff retries -- four total, never more.
    assert counter_file.read_text(encoding="utf-8").strip() == "4"
    assert not (venv_dir / "pyvenv.cfg").exists()
    # The complete 3s/6s/10s backoff schedule, in order.
    assert _delays(delays_file) == [3, 6, 10]
