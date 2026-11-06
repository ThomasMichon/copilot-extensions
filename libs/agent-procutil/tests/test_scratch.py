"""Labeled scratch breadcrumbs, link-safe removal and the read-only report."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from agent_procutil import scratch


def _make_dir_link(link, target) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True, capture_output=True,
        )
    else:
        os.symlink(target, link, target_is_directory=True)


def test_breadcrumb_records_owner_purpose_and_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session-1")
    monkeypatch.setenv("AGENT_WORKTREES_BIND_WORKTREE_ID", "worktree-1")
    monkeypatch.delenv("AGENT_WORKTREES_LAUNCH_ID", raising=False)
    with scratch.labeled_scratch(
        "demo-task", "unit test", owner_refs={"plugin": "demo"}, retention="until exit",
        root=tmp_path,
    ) as path:
        assert path.parent == tmp_path
        assert path.name.startswith("demo-task-")
        record = json.loads((path / scratch.BREADCRUMB).read_text(encoding="utf-8"))
    assert record["schema"] == scratch.SCHEMA
    assert record["purpose"] == "unit test"
    assert record["retention"] == "until exit"
    assert record["pid"] == os.getpid()
    assert record["owner_refs"] == {
        "session_id": "session-1", "worktree_id": "worktree-1", "plugin": "demo",
    }
    assert record["created_at"].endswith("+00:00")
    assert not path.exists()


def test_cleanup_false_retains_and_env_root_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv(scratch.ROOT_ENV, str(tmp_path))
    with scratch.labeled_scratch("kept", "retained", cleanup=False) as path:
        (path / "nested").mkdir()
        (path / "nested" / "data.txt").write_text("x")
    assert path.parent == tmp_path and (path / "nested" / "data.txt").exists()
    assert scratch.remove_scratch(path) and not path.exists()


def test_cleanup_runs_when_body_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with scratch.labeled_scratch("boom", "failure", root=tmp_path) as path:
            raise RuntimeError("body failed")
    assert not path.exists()


def test_invalid_prefix_and_relative_root_are_refused(tmp_path):
    with pytest.raises(ValueError):
        scratch.create_labeled_scratch("../escape", "x", root=tmp_path)
    with pytest.raises(ValueError):
        scratch.create_labeled_scratch("ok", "x", root="relative")


def test_removal_does_not_follow_directory_links(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    with scratch.labeled_scratch("links", "link safety", root=tmp_path / "root") as path:
        try:
            _make_dir_link(path / "link", outside)
        except (OSError, subprocess.CalledProcessError) as exc:
            pytest.skip(f"directory links unavailable: {exc}")
    assert not path.exists()
    assert (outside / "keep.txt").read_text() == "precious"


def test_removal_handles_missing_path(tmp_path):
    assert scratch.remove_scratch(tmp_path / "absent")


def test_prefix_normalization_groups_random_suffixes():
    assert scratch.prefix_of("agbridge-host-ab12cd") == "agbridge-host-*"
    assert scratch.prefix_of("agbridge-host-abcdefgh") == "agbridge-host-*"
    assert scratch.prefix_of("uv-setuptools-0123abcd.lock") == "uv-setuptools-*"
    assert scratch.prefix_of("demo-20261106T000000Z-x1") == "demo-*"
    assert scratch.prefix_of("tmpq1w2e3") == "tmp*"
    assert scratch.prefix_of("plain") == "plain"


def test_report_groups_labeled_and_unlabeled_without_deleting(tmp_path):
    labeled = scratch.create_labeled_scratch("job", "reported", root=tmp_path)
    (labeled / "payload.bin").write_bytes(b"12345")
    (tmp_path / "job-ab12cd").mkdir()
    for index in range(3):
        (tmp_path / f"uv-setuptools-{index:016x}.lock").write_bytes(b"")
    before = sorted(os.listdir(tmp_path))
    groups = {(g["prefix"], g["kind"]): g for g in scratch.scratch_report(tmp_path)}
    assert sorted(os.listdir(tmp_path)) == before
    job = groups[("job-*", "dir")]
    assert (job["count"], job["labeled"], job["unlabeled"]) == (2, 1, 1)
    assert job["purposes"] == {"reported": 1}
    assert job["bytes"] >= 5
    locks = groups[("uv-setuptools-*", "file")]
    assert (locks["count"], locks["labeled"], locks["unlabeled"]) == (3, 0, 3)


def test_report_cli_emits_json_and_has_no_delete_command(tmp_path, capsys):
    (tmp_path / "abc-x1").mkdir()
    assert scratch.main(["report", "--root", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["prefix"] == "abc-*"
    with pytest.raises(SystemExit):
        scratch.main(["clean", "--root", str(tmp_path)])
    assert (tmp_path / "abc-x1").exists()
