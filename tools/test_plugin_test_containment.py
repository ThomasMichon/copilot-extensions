from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tools import plugin_test_containment
from tools.plugin_test_containment import Limits, isolated_environment


def test_tree_size_counts_nested_files_without_repeated_path_stats(tmp_path, monkeypatch):
    (tmp_path / "nested").mkdir()
    (tmp_path / "a").write_bytes(b"abc")
    (tmp_path / "nested" / ".hidden").write_bytes(b"12345")

    def repeated_walk(*args, **kwargs):
        raise AssertionError("The monitor must retain scandir's cached file metadata")

    monkeypatch.setattr(Path, "rglob", repeated_walk)
    assert plugin_test_containment._tree_size(tmp_path) == 8
    assert plugin_test_containment._tree_size(tmp_path / "absent") == 0


def test_tree_size_does_not_follow_file_or_directory_links(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"not-counted")
    (root / "data").write_bytes(b"counted")
    try:
        (root / "linked-file").symlink_to(outside / "data")
        (root / "linked-directory").symlink_to(outside, target_is_directory=True)
        (root / "loop").symlink_to(root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symbolic link creation is unavailable: {error}")
    assert plugin_test_containment._tree_size(root) == len(b"counted")


def test_native_temp_budget_still_terminates_the_process_tree(tmp_path, capfd):
    environment = isolated_environment(os.environ, tmp_path)
    code = (
        "from pathlib import Path; import time; "
        "Path('allocation').write_bytes(b'x' * (2 * 1024 * 1024)); "
        "time.sleep(60)"
    )
    result = plugin_test_containment.run_contained(
        [sys.executable, "-c", code], cwd=tmp_path, env=environment,
        sandbox=tmp_path, limits=Limits(
            wall_seconds=10, max_processes=8, max_memory_mb=256, max_temp_mb=1,
        ),
    )
    assert result == 124
    assert "temporary-storage limit exceeded" in capfd.readouterr().err


def test_run_contained_detects_and_fails_on_persistent_environment_drift(
    tmp_path, capfd, monkeypatch
):
    before = plugin_test_containment._WindowsEnvironmentSnapshot(
        user={"Path": ("before", 1)},
        machine={},
    )
    after = plugin_test_containment._WindowsEnvironmentSnapshot(
        user={"Path": ("after", 1)},
        machine={},
    )
    snapshots = iter((before, after))
    monkeypatch.setattr(
        plugin_test_containment,
        "_read_registry_environment",
        lambda: next(snapshots),
    )
    env = isolated_environment(os.environ, tmp_path)

    rc = plugin_test_containment.run_contained(
        [sys.executable, "-c", "raise SystemExit(0)"],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=10,
            max_processes=8,
            max_memory_mb=256,
            max_temp_mb=32,
        ),
    )

    assert rc == 125
    assert "detected without rollback: User:Path" in capfd.readouterr().err
