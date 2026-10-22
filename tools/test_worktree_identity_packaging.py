"""The real codec participates in canonical packaging without a sibling runtime."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from build_python_artifacts import resolve_vendored_libs  # noqa: E402
from materialize_main import materialize_uv_editable_ref_into  # noqa: E402

CONSUMERS = ["agent-worktrees", "agent-dispatch", "agent-bridge", "worktree-manager"]


def _consumer(plugin):
    return REPO / plugin if plugin == "worktree-manager" else REPO / "plugins" / plugin


@pytest.mark.parametrize("plugin", CONSUMERS)
def test_consumer_source_graph_and_change_attribution(plugin):
    consumer = _consumer(plugin)
    assert (
        dict(resolve_vendored_libs(consumer))["worktree-identity"]
        == (REPO / "libs" / "worktree-identity").resolve()
    )
    spec = importlib.util.spec_from_file_location(
        "worktree_identity_bump_attribution",
        TOOLS / "check-version-bump.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert plugin in module._vendored_consumers()["worktree-identity"]
    from uv_editable_ref import tomllib

    metadata = tomllib.loads(
        (REPO / "libs/worktree-identity/pyproject.toml").read_text(encoding="utf-8"),
    )
    assert metadata["project"]["name"] == "agent-worktree-identity"
    assert metadata["project"]["dependencies"] == []


@pytest.mark.parametrize("plugin", CONSUMERS)
def test_real_materialization_preserves_codec_and_rewrites_source(tmp_path, plugin):
    source = _consumer(plugin)
    destination = tmp_path / "standalone"
    destination.mkdir()
    shutil.copy2(source / "pyproject.toml", destination / "pyproject.toml")
    if (source / "libs").is_dir():
        shutil.copytree(
            source / "libs",
            destination / "libs",
            ignore=shutil.ignore_patterns(
                "__pycache__",
                ".pytest_cache",
                ".venv",
                "build",
                "*.egg-info",
            ),
        )
    log = materialize_uv_editable_ref_into(
        source_consumer_dir=source,
        dest_consumer_dir=destination,
        canonical_root=REPO,
    )
    assert not any("SKIP" in entry for entry in log), log
    assert 'agent-worktree-identity = { path = "libs/worktree-identity" }' in (
        destination / "pyproject.toml"
    ).read_text(encoding="utf-8")
    canonical = REPO / "libs" / "worktree-identity"
    embedded = destination / "libs" / "worktree-identity"
    for relative in (
        "src/worktree_identity/__init__.py",
        "pyproject.toml",
        "README.md",
    ):
        assert (embedded / relative).read_bytes() == (canonical / relative).read_bytes()
    assert (
        dict(resolve_vendored_libs(destination))["worktree-identity"]
        == embedded.resolve()
    )


def test_stdlib_only_import_and_compatibility_without_sibling_runtime(tmp_path):
    script = """
import importlib.abc
import sys
class RefuseSiblingImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'machine_transport', 'yaml', 'agent_bridge', 'agent_dispatch',
        }:
            raise AssertionError('unexpected dependency: ' + fullname)
sys.meta_path.insert(0, RefuseSiblingImports())
sys.path[:0] = sys.argv[1:]
import worktree_identity
import agent_worktrees.worktree_machine_identity as compat
assert compat.WorktreeMachineIdentity is worktree_identity.WorktreeMachineIdentity
assert (
    compat.WorktreeMachineIdentityError
    is worktree_identity.WorktreeMachineIdentityError
)
assert compat.parse_machine_identity is worktree_identity.parse_machine_identity
identity = compat.WorktreeMachineIdentity('host', 'wsl', 'guest')
assert compat.execution_machine_from_fields(identity.to_record_fields()) == 'guest'
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            script,
            str(REPO / "libs/worktree-identity/src"),
            str(REPO / "plugins/agent-worktrees/src"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
