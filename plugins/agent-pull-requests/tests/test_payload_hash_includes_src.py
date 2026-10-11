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


def _extract_bash_function(text: str, func_name: str) -> str:
    """Extract a bash function's full definition via brace counting,
    rather than assuming any particular formatting around its closing
    brace (e.g. an exact `\\n}\\n` sequence, which is brittle against CRLF
    line endings or incidental reformatting unrelated to behavior)."""
    text = text.replace("\r\n", "\n")
    start_marker = f"{func_name}() {{"
    assert text.count(start_marker) == 1, f"expected exactly one {func_name}() definition"
    start = text.index(start_marker)
    brace_start = start + len(start_marker) - 1  # index of the opening '{'
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces while extracting {func_name}()")


def _extract_bootstrap_python() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    return _extract_bash_function(text, "_bootstrap_python")


def _extract_payload_hash() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    return _extract_bash_function(text, "_payload_hash")


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
_fail() {{ printf '  [FAIL] %s\\n' "$1" >&2; }}
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


def test_payload_hash_fails_closed_on_a_genuine_fingerprint_error(tmp_path: Path) -> None:
    """A real failure computing the fingerprint (simulated here via a
    missing/broken versioned_runtime.py) must propagate as a non-zero
    return and the installer's real diagnostic on stderr, never be
    silently swallowed into an empty hash: an empty hash is
    indistinguishable from "nothing changed" to every caller, which would
    let a genuinely-changed (or simply unverifiable) payload be admitted
    as if it matched a previously completed slot. Defines a real `_fail`
    helper (as the installer itself does) rather than leaving it
    undefined, so this proves the function's OWN intended diagnostic
    fires -- not merely that bash reports an unrelated "command not
    found" for a missing `_fail`."""
    plugin_dir, _pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"
    bash = _bash()
    fn = _extract_payload_hash()
    broken_scripts_dir = tmp_path / "broken-scripts"
    broken_scripts_dir.mkdir()
    real_python = Path(sys.executable).as_posix()
    script = f"""
set -euo pipefail
_fail() {{ printf '  [FAIL] %s\\n' "$1" >&2; }}
PLUGIN_DIR={plugin_dir.as_posix()!r}
PKG_SRC_DIR={(plugin_dir / "src" / "agent_pull_requests").as_posix()!r}
INSTALL_DIR={install_dir.as_posix()!r}
SCRIPT_DIR={broken_scripts_dir.as_posix()!r}
_bootstrap_python() {{ printf '%s' {real_python!r}; }}
{fn}
if _payload_hash; then
    echo UNEXPECTED_SUCCESS
else
    echo "FAILED_AS_EXPECTED rc=$?"
fi
"""
    result = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "FAILED_AS_EXPECTED" in result.stdout
    assert "UNEXPECTED_SUCCESS" not in result.stdout
    assert "_fail: command not found" not in result.stderr, (
        "the _fail helper itself was undefined -- this test did not "
        "actually exercise _payload_hash's own diagnostic"
    )
    assert "Failed to compute payload fingerprint" in result.stderr


def test_payload_hash_changes_when_a_uv_sources_path_dependency_changes(tmp_path: Path) -> None:
    """This plugin's dependencies[] on shared libraries (agent-procutil,
    agent-work-coalescing-singleton, agent-single-instance-lease) are
    resolved live via [tool.uv.sources] `path = "..."` entries, never
    vendored. Those libraries' own content is just as much a part of the
    actual runtime payload as src/agent_pull_requests -- a change there
    must be detected too, not just a change under the plugin's own tree."""
    plugin_dir, _pkg_src_dir = _make_plugin_tree(tmp_path)
    shared_lib_dir = tmp_path / "libs" / "a-shared-lib"
    (shared_lib_dir / "src" / "a_shared_lib").mkdir(parents=True)
    (shared_lib_dir / "src" / "a_shared_lib" / "__init__.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-pull-requests"\nversion = "1.0.0"\n'
        "dependencies = [\"a-shared-lib\"]\n\n"
        "[tool.uv.sources]\n"
        'a-shared-lib = { path = "../libs/a-shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    install_dir = tmp_path / "install"

    before = _run_payload_hash(plugin_dir, install_dir)
    (shared_lib_dir / "src" / "a_shared_lib" / "__init__.py").write_text(
        "x = 2\n", encoding="utf-8"
    )
    after = _run_payload_hash(plugin_dir, install_dir)
    assert before != after, (
        "_payload_hash() did not change when a [tool.uv.sources] path "
        "dependency's content changed -- shared library source is still "
        "excluded from the fingerprint"
    )


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


def test_bootstrap_python_falls_back_to_uv_when_no_system_python_is_found(
    tmp_path: Path,
) -> None:
    """The exact regression this round's audit found: without a uv-backed
    fallback, a genuinely python-less first install would compute NO
    payload hash at all (the two earlier lookups both fail), write an
    UNHASHED completion marker, then find a REAL hash available on the
    very next run once the venv's own python exists -- wrongly reporting
    unchanged content as a payload conflict. Shadows the `command` builtin
    (never touches PATH, which is fragile to manipulate portably) so only
    `command -v python3`/`command -v python` report "not found", while
    every other use of `command` still delegates to the real builtin."""
    bash = _bash()
    fn = _extract_bootstrap_python()
    real_python = Path(sys.executable).as_posix()

    fake_uv = tmp_path / "fake-uv.sh"
    state_file = tmp_path / "uv-python-installed"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "python" && "$2" == "find" ]]; then\n'
        f"    if [[ -f {state_file.as_posix()!r} ]]; then\n"
        f"        printf '%s' {real_python!r}\n"
        "    fi\n"
        "    exit 0\n"
        'elif [[ "$1" == "python" && "$2" == "install" ]]; then\n'
        f"    : > {state_file.as_posix()!r}\n"
        "    exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    script = f"""
set -euo pipefail
VENV_DIR={(tmp_path / "no-such-venv").as_posix()!r}
LINK_DIR={(tmp_path / "no-such-link").as_posix()!r}
UV_CMD={fake_uv.as_posix()!r}
command() {{
    if [[ "$1" == "-v" && ( "$2" == "python3" || "$2" == "python" ) ]]; then
        return 1
    fi
    builtin command "$@"
}}
{fn}
_bootstrap_python
"""
    result = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == real_python
