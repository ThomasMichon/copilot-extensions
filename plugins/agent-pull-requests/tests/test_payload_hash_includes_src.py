"""Guard: install.sh's `_payload_hash()` must fingerprint the FULL
attributable runtime install input (pyproject.toml AND
src/agent_pull_requests), not just pyproject.toml. A prior audit on this
effort (#5472) found exactly this narrower scope on this adopter:
fingerprinting only pyproject.toml missed a changed src/ tree entirely,
which could let `check_admission()` wrongly report `reuse` for a slot
built from genuinely different source content. Exercises the real
extracted `_payload_hash` function (via libs/versioned-runtime's canonical
`fingerprint_source()`), not a restatement of its logic, so a regression
here would still pass a hash-string-only test while silently narrowing
scope again.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_VERSIONED_RUNTIME = _PLUGIN_ROOT / "scripts" / "versioned_runtime.py"

pytestmark = pytest.mark.guard


def _bash() -> str:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("a POSIX bash is unavailable")
    return bash


def _extract_payload_hash() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = "_payload_hash() {"
    assert text.count(start) == 1, "expected exactly one _payload_hash() definition"
    body = start + text.split(start, 1)[1].split("\n}\n", 1)[0] + "\n}"
    return body


def _make_plugin_tree(root: Path) -> tuple[Path, Path]:
    """Return (plugin_dir, pkg_src_dir) for a minimal fake plugin layout."""
    plugin_dir = root / "plugin"
    pkg_src_dir = plugin_dir / "src" / "agent_pull_requests"
    pkg_src_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-pull-requests"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (pkg_src_dir / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    return plugin_dir, pkg_src_dir


def _run_payload_hash(plugin_dir: Path, install_dir: Path) -> str:
    bash = _bash()
    fn = _extract_payload_hash()
    # Resolve a REAL python interpreter directly (sys.executable), not via
    # PATH lookup inside the test's bash -- `command -v python3` can resolve
    # to Windows' broken WindowsApps "App execution alias" stub, which
    # `command -v` happily finds but which fails when actually invoked.
    real_python = Path(sys.executable).as_posix()
    script = f"""
set -euo pipefail
PLUGIN_DIR={plugin_dir.as_posix()!r}
PKG_SRC_DIR={(plugin_dir / "src" / "agent_pull_requests").as_posix()!r}
INSTALL_DIR={install_dir.as_posix()!r}
SCRIPT_DIR={_VERSIONED_RUNTIME.parent.as_posix()!r}
_bootstrap_python() {{ printf '%s' {real_python!r}; }}
{fn}
_payload_hash
"""
    result = subprocess.run(
        [bash, "-c", script], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    assert out, f"expected a non-empty hash; stderr: {result.stderr}"
    return out


def test_payload_hash_changes_when_src_content_changes(tmp_path: Path) -> None:
    plugin_dir, pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"

    before = _run_payload_hash(plugin_dir, install_dir)
    (pkg_src_dir / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    after = _run_payload_hash(plugin_dir, install_dir)

    assert before != after, (
        "_payload_hash() did not change when src/agent_pull_requests content "
        "changed -- it is still only hashing pyproject.toml"
    )


def test_payload_hash_still_changes_when_pyproject_changes(tmp_path: Path) -> None:
    plugin_dir, _pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"

    before = _run_payload_hash(plugin_dir, install_dir)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-pull-requests"\nversion = "1.0.1"\n',
        encoding="utf-8",
    )
    after = _run_payload_hash(plugin_dir, install_dir)
    assert before != after


def test_payload_hash_is_stable_for_identical_content(tmp_path: Path) -> None:
    plugin_dir, _pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"

    first = _run_payload_hash(plugin_dir, install_dir)
    second = _run_payload_hash(plugin_dir, install_dir)
    assert first == second


def test_payload_hash_matches_canonical_fingerprint_source(tmp_path: Path) -> None:
    """The shell function's output must agree with calling
    fingerprint_source() directly on the same (pyproject.toml, src) roots,
    proving it genuinely delegates to the canonical seam rather than
    reimplementing a parallel hash."""
    plugin_dir, pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"

    shell_hash = _run_payload_hash(plugin_dir, install_dir)

    py = sys.executable
    result = subprocess.run(
        [py, str(_VERSIONED_RUNTIME), "--root", str(install_dir), "fingerprint",
         str(plugin_dir / "pyproject.toml"), str(pkg_src_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert shell_hash == result.stdout.strip()
