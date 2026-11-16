"""Transport replacement preserves the original writer, not a retired Bridge slot."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge import _peer_launch
from agent_bridge import acquisition_authority as authority
from agent_bridge import acquisition_worktree_writer as writers
from agent_bridge.acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from agent_bridge.creation_worktree_writer import CreationWriterScope
from agent_bridge import session_lifecycle_cli as lifecycle
from agent_bridge import worktree_lineage as lineage
from agent_bridge._peer_launch import ContextRefused
from agent_bridge.retained_writer_transport import execution_prefix
from test_worktree_owner_launch import deploy_installed_layout


@pytest.fixture(params=["legacy", "cell"])
def retained_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> tuple[str, ...]:
    root = (
        tmp_path / "bridge" if request.param == "legacy"
        else tmp_path / "marketplaces" / "sample" / "plugins" / "agent-bridge"
    )
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: root)
    pinned = ["--pinned-runtime", str(tmp_path / "worktrees" / "python"),
              "--runtime-identity", "original-build:fingerprint"]
    if request.param == "legacy":
        prefix = (
            str(root / "retired" / "python"), "-I", "-X", "utf8", "-m",
            "agent_bridge.worktree_owner_launch", str(root), *pinned,
        )
    else:
        prefix = (
            str(root / "retired" / "python"), "-I", "-B", "-X", "utf8",
            str(root / "retired" / "_peer_launch.py"), *pinned,
            "agent-bridge", str(root), str(root / "install.json"), "agent-worktrees",
        )
    return prefix


@pytest.mark.asyncio
async def test_original_journal_survives_both_execution_boundaries(
    retained_prefix: tuple[str, ...], monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = CreationWriterScope("key", "project", "worktree", "/fixture", WorktreeWriterRequirement.model_validate({
        "identity": {"installation_root": "/owner", "runtime_generation": "a" * 32},
        "creation_nonce": "original-incarnation",
    }))
    original = writers.RetainedWriter(scope, retained_prefix)
    saved = original.document()
    expected = execution_prefix(retained_prefix)
    assert expected[0] == sys.executable
    assert not Path(retained_prefix[0]).exists()
    args = ["resource-relationship", "capabilities", "--json"]
    child = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"ok":true}', b"")))
    spawn = AsyncMock(return_value=child)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{"ok":true}', ""))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        lifecycle, "_agent_worktrees_runtime_selection",
        Mock(side_effect=AssertionError("must not select a new Worktrees runtime")),
    )
    token = writers._current.set(original)
    try:
        assert lifecycle._agent_worktrees_launch_prefix() == expected
    finally:
        writers._current.reset(token)
    assert await authority.worktree_command(args, "project", owner_prefix=retained_prefix) == {"ok": True}
    assert spawn.call_args.args == (*expected, "--project", "project", *args)
    result = lineage._run(args, owner_prefix=retained_prefix)
    assert result is not None and result.returncode == 0
    assert run.call_args.args[0] == [*expected, *args]
    assert spawn.call_count == run.call_count == 1
    assert original.document() == saved


@pytest.mark.parametrize("spelling_owner", ["retained", "serving"])
def test_equivalent_bridge_root_keeps_original_writer(
    retained_prefix: tuple[str, ...], monkeypatch: pytest.MonkeyPatch, spelling_owner: str,
) -> None:
    root_index = 6 if retained_prefix[4] == "-m" else 11
    root = Path(retained_prefix[root_index])
    equivalent = root / ".." / root.name
    prefix = list(retained_prefix)
    if spelling_owner == "retained":
        prefix[root_index] = str(equivalent)
    else:
        monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: equivalent)
    saved = tuple(prefix)
    actual = execution_prefix(prefix)
    assert actual[0] == sys.executable
    pinned_index = prefix.index("--pinned-runtime")
    actual_pinned = actual.index("--pinned-runtime")
    assert actual[actual_pinned:actual_pinned + 4] == prefix[pinned_index:pinned_index + 4]
    if root_index == 11:
        monkeypatch.setattr(_peer_launch, "_active_context", Mock(return_value={"pluginRoot": str(root)}))
        monkeypatch.setattr(_peer_launch, "_governance", Mock(return_value={}))
        owner = _peer_launch.validate_owner(actual[10], Path(actual[11]), actual[12])
        assert owner["pluginRoot"] == str(root)
    assert actual[root_index] == str(root)
    assert tuple(prefix) == saved


def test_foreign_bridge_is_not_adopted(
    retained_prefix: tuple[str, ...], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: tmp_path / "another-bridge")
    with pytest.raises(ContextRefused, match="another Bridge installation"):
        execution_prefix(retained_prefix)


def test_partial_pinned_wrapper_does_not_become_dynamic(retained_prefix: tuple[str, ...]) -> None:
    with pytest.raises(ContextRefused, match="incomplete"):
        execution_prefix(retained_prefix[:-1])


def test_direct_original_writer_is_not_reselected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = (str(tmp_path / "original-python"), "-I", "-m", "agent_worktrees")
    monkeypatch.setattr(
        lifecycle, "_agent_bridge_owner_root", Mock(side_effect=AssertionError("direct writer changed")),
    )
    assert execution_prefix(direct) == list(direct)


def test_unpinned_compatibility_prefix_is_not_reinterpreted(
    retained_prefix: tuple[str, ...],
) -> None:
    start = retained_prefix.index("--pinned-runtime")
    unpinned = (*retained_prefix[:start], *retained_prefix[start + 4:])
    assert execution_prefix(unpinned) == list(unpinned)


@pytest.mark.asyncio
async def test_retired_bridge_reaches_actual_original_runtime_and_keeps_build_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    own = tmp_path / ".agent-bridge"
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: own)
    deploy_installed_layout(own, "powershell" if os.name == "nt" else "bash")
    root = tmp_path / ".agent-worktrees"
    slot = root / "versions" / "1.0.0"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(slot)],
        check=True, capture_output=True, timeout=20,
        **no_window_kwargs(),
    )
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    query = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True, capture_output=True, text=True, timeout=10,
        **no_window_kwargs(),
    )
    package = Path(query.stdout.strip()) / "agent_worktrees"
    package.mkdir()
    (package / "__main__.py").write_text(
        "import json, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'python': sys.executable}))\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0", "completed_at": "fixture", "pid": 0, "payload_hash": "a" * 64,
    }), encoding="utf-8")
    (root / "current-version").write_text("1.0.0", encoding="ascii")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for source in (scripts / "runtime_launcher.py", *scripts.glob("versioned_runtime*.py")):
        shutil.copyfile(source, bootstrap / source.name)
    generation = bootstrap / "runtime-generation"
    generation.write_text("1" * 32, encoding="ascii")
    selected, selected_root, selected_python = lifecycle._agent_worktrees_pinned_selection()
    assert selected_root == root and selected_python == python
    prefix = (str(own / "versions" / "retired" / "bin" / "python"), *selected[1:])
    assert not Path(prefix[0]).exists()
    original = writers.RetainedWriter(
        CreationWriterScope("key", "project", "worktree", str(tmp_path), WorktreeWriterRequirement.model_validate({
            "identity": {"installation_root": str(root), "runtime_generation": "1" * 32},
            "creation_nonce": "original-incarnation",
        })), prefix,
    )
    saved = original.document()
    (root / "current-version").write_text("2.0.0", encoding="ascii")
    result = await authority.worktree_command(["record", "--json"], "project", owner_prefix=prefix)
    assert result == {"argv": ["--project", "project", "record", "--json"], "python": str(python)}
    legacy = lineage._run(["session-role", "--json"], owner_prefix=prefix)
    assert legacy is not None and legacy.returncode == 0
    assert json.loads(legacy.stdout)["python"] == str(python)
    assert original.document() == saved
    generation.write_text("2" * 32, encoding="ascii")
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(["record", "--json"], "project", owner_prefix=prefix)
    assert refused.value.code == "owner_refused"
    changed = lineage._run(["session-role", "--json"], owner_prefix=prefix)
    assert changed is not None and changed.returncode != 0 and not changed.stdout
    assert "build identity changed" in changed.stderr
    assert original.document() == saved
