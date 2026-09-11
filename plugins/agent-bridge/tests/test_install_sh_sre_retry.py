"""POSIX regression coverage for the transient SRE-module-mismatch retry
wrapper (#6785) in ``install.sh`` -- the Linux/WSL counterpart to
``test_install_sre_retry.py``'s PowerShell coverage. Keeps
``_uv_pip_install_resilient``'s behavior from silently drifting between the
two installers: retry with backoff on the SRE-mismatch signature, surface any
other failure immediately, and give up after the bounded number of attempts.
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


def _run_harness(tmp_path: Path, uv_stub_body: str, extra_script: str) -> subprocess.CompletedProcess:
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        + _extract_sh_functions("_is_sre_module_mismatch", "_uv_pip_install_resilient")
        + f"""
_warn() {{ echo "WARN: $*"; }}

{uv_stub_body}

{extra_script}
""",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    # Stub `sleep` as a no-op so the test doesn't actually wait through the
    # 3s/6s/10s backoff schedule.
    _executable(fake_bin / "sleep", "#!/bin/sh\nexit 0\n")
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run(
        [_BASH, str(harness)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=True,
    )


def test_sre_mismatch_retries_then_succeeds(tmp_path: Path) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    if [ "$n" -lt 3 ]; then
        echo 'AssertionError: SRE module mismatch'
        return 1
    fi
    echo 'Installed 1 package'
    return 0
}}
"""
    extra = """
if out=$(_uv_pip_install_resilient --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
echo "OUT:$out"
"""
    result = _run_harness(tmp_path, uv_stub, extra)
    # Two retries needed (three total attempts) -- both backoff warnings fire.
    assert result.stdout.count("uv build hit a transient SRE module mismatch") == 2
    assert "EXIT:0" in result.stdout
    assert "OUT:Installed 1 package" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "3"


def test_unrelated_failure_is_not_retried(tmp_path: Path) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    echo 'error: network unreachable'
    return 1
}}
"""
    extra = """
if out=$(_uv_pip_install_resilient --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
echo "OUT:$out"
"""
    result = _run_harness(tmp_path, uv_stub, extra)
    assert "uv build hit a transient SRE module mismatch" not in result.stdout
    assert "EXIT:1" in result.stdout
    assert "OUT:error: network unreachable" in result.stdout
    # Only one attempt -- an unrelated failure must not trigger the retry.
    assert counter_file.read_text(encoding="utf-8").strip() == "1"


def test_persisting_sre_mismatch_still_fails_after_all_retries(tmp_path: Path) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    echo 'AssertionError: SRE module mismatch'
    return 1
}}
"""
    extra = """
if out=$(_uv_pip_install_resilient --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_harness(tmp_path, uv_stub, extra)
    assert "uv build hit a transient SRE module mismatch" in result.stdout
    assert "EXIT:1" in result.stdout
    # One initial attempt plus three backoff retries -- four total, never more.
    assert counter_file.read_text(encoding="utf-8").strip() == "4"
