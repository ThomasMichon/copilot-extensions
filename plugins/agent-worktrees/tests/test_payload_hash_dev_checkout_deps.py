"""Guard: install.sh's `_payload_hash()` must fingerprint a dev-checkout
[tool.uv.sources] `path = "../../libs/<lib>"` dependency's actual source
content, not just the local `libs/<lib>` (promoted/materialized) form it
already covered (#2609). A prior audit on this effort (#5472) found exactly
this narrower scope on another adopter: an EXTERNAL dev-checkout path
dependency living outside the plugin directory was invisible to its payload
hash entirely, so changing that shared library's own source, without a
version bump here, would let `update`/`update --force` wrongly report
"already at latest" and reuse a slot built from stale dependency content.
Exercises the real extracted `_payload_hash` function via brace-counting
(never an exact `\\n}\\n` sequence, which is brittle against CRLF line
endings or incidental reformatting), not a restatement of its logic, so a
regression here would still pass a hash-string-only test while silently
narrowing scope again.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"

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


def _extract_payload_hash() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    return _extract_bash_function(text, "_payload_hash")


def _make_plugin_tree(root: Path) -> tuple[Path, Path]:
    """Return (plugin_dir, external_lib_src_dir) for a minimal fake plugin
    layout with ONE dev-checkout external path dependency declared via
    [tool.uv.sources], matching the real repo's
    `plugins/<name>` + `../../libs/<lib>` sibling layout."""
    plugin_dir = root / "repo" / "plugins" / "agent-worktrees"
    pkg_src_dir = plugin_dir / "src" / "agent_worktrees"
    pkg_src_dir.mkdir(parents=True)
    (pkg_src_dir / "__init__.py").write_text("x = 1\n", encoding="utf-8")

    ext_lib_dir = root / "repo" / "libs" / "some-shared-lib"
    ext_lib_src_dir = ext_lib_dir / "src" / "some_shared_lib"
    ext_lib_src_dir.mkdir(parents=True)
    (ext_lib_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-some-shared-lib"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (ext_lib_src_dir / "__init__.py").write_text("y = 1\n", encoding="utf-8")

    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-worktrees"\nversion = "1.0.0"\n'
        '[tool.uv.sources]\n'
        'agent-some-shared-lib = { path = "../../libs/some-shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    return plugin_dir, ext_lib_src_dir


def _run_payload_hash(plugin_dir: Path) -> str:
    bash = _bash()
    fn = _extract_payload_hash()
    script = f"""
set -euo pipefail
PLUGIN_DIR={plugin_dir.as_posix()!r}
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


def test_payload_hash_changes_when_dev_checkout_dependency_src_changes(tmp_path: Path) -> None:
    plugin_dir, ext_lib_src_dir = _make_plugin_tree(tmp_path)

    before = _run_payload_hash(plugin_dir)
    (ext_lib_src_dir / "__init__.py").write_text("y = 2\n", encoding="utf-8")
    after = _run_payload_hash(plugin_dir)

    assert before != after, (
        "_payload_hash() did not change when the dev-checkout external "
        "[tool.uv.sources] path dependency's src/ content changed -- it is "
        "still invisible to the libs/ scan, which only sees the LOCAL "
        "(promoted/materialized) form"
    )


def test_payload_hash_still_changes_when_src_content_changes(tmp_path: Path) -> None:
    plugin_dir, _ext_lib_src_dir = _make_plugin_tree(tmp_path)
    pkg_src_dir = plugin_dir / "src" / "agent_worktrees"

    before = _run_payload_hash(plugin_dir)
    (pkg_src_dir / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    after = _run_payload_hash(plugin_dir)

    assert before != after


def test_payload_hash_is_stable_for_identical_content(tmp_path: Path) -> None:
    plugin_dir, _ext_lib_src_dir = _make_plugin_tree(tmp_path)

    first = _run_payload_hash(plugin_dir)
    second = _run_payload_hash(plugin_dir)
    assert first == second


def test_payload_hash_ignores_generated_packaging_metadata(tmp_path: Path) -> None:
    plugin_dir, external_src = _make_plugin_tree(tmp_path / "ancestor.egg-info")
    local_src = plugin_dir / "libs" / "local-lib" / "src" / "local_lib"
    local_src.mkdir(parents=True)
    (local_src / "__init__.py").write_text("z = 1\n", encoding="utf-8")
    roots = (plugin_dir / "src", local_src.parent, external_src.parent)
    before = _run_payload_hash(plugin_dir)
    for root in roots:
        for suffix in ("egg-info", "dist-info"):
            metadata = root / f"generated.{suffix}"
            metadata.mkdir()
            (metadata / "PKG-INFO").write_text("generated build metadata\n", encoding="utf-8")
    assert _run_payload_hash(plugin_dir) == before
    (external_src / "runtime.json").write_text('{"enabled": true}\n', encoding="utf-8")
    assert _run_payload_hash(plugin_dir) != before


def test_payload_hash_does_not_double_hash_a_local_materialized_dependency(tmp_path: Path) -> None:
    """A LOCAL (already-promoted/materialized) `libs/<lib>` dependency is
    already fully covered by the pre-existing libs/ scan -- the new
    [tool.uv.sources] resolution must detect it resolves back under
    `$PLUGIN_DIR/libs` and skip it, not hash it a second time (which would
    still happen to produce a stable, changing-on-edit hash, but wastefully
    and in a way that could silently diverge from the libs/ scan's own
    content on a future refactor)."""
    plugin_dir = tmp_path / "repo" / "plugins" / "agent-worktrees"
    pkg_src_dir = plugin_dir / "src" / "agent_worktrees"
    pkg_src_dir.mkdir(parents=True)
    (pkg_src_dir / "__init__.py").write_text("x = 1\n", encoding="utf-8")

    local_lib_dir = plugin_dir / "libs" / "some-shared-lib"
    local_lib_src_dir = local_lib_dir / "src" / "some_shared_lib"
    local_lib_src_dir.mkdir(parents=True)
    (local_lib_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-some-shared-lib"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (local_lib_src_dir / "__init__.py").write_text("y = 1\n", encoding="utf-8")

    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-worktrees"\nversion = "1.0.0"\n'
        '[tool.uv.sources]\n'
        'agent-some-shared-lib = { path = "libs/some-shared-lib", editable = true }\n',
        encoding="utf-8",
    )

    before = _run_payload_hash(plugin_dir)
    (local_lib_src_dir / "__init__.py").write_text("y = 2\n", encoding="utf-8")
    after = _run_payload_hash(plugin_dir)

    assert before != after
