"""Labeled scratch breadcrumbs, link-safe removal and the read-only report."""

from __future__ import annotations

import errno
import json
import os
import subprocess

import pytest

from agent_procutil import scratch


def _make_dir_link(link, target) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
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


def test_expected_directory_identity_refuses_replaced_content(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    before = original.stat()
    original.rename(tmp_path / "retained")
    original.mkdir()
    (original / "foreign.txt").write_text("preserve")
    assert not scratch.remove_scratch(
        original, expected_directory_identity=(before.st_dev, before.st_ino),
    )
    assert (original / "foreign.txt").read_text() == "preserve"
    current = original.stat()
    assert scratch.remove_scratch(
        original, expected_directory_identity=(current.st_dev, current.st_ino),
    )


@pytest.mark.parametrize("suffix", [os.sep, os.sep * 2, os.sep + ".", os.sep + ".."])
def test_removal_rejects_directory_link_suffixes(tmp_path, suffix):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    link = tmp_path / "link"
    try:
        _make_dir_link(link, outside)
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"directory links unavailable: {exc}")
    assert not scratch.remove_scratch(str(link) + suffix)
    assert (outside / "keep.txt").read_text() == "precious"
    assert scratch._is_link(link.lstat())


def test_directory_swapped_for_link_after_inspection_is_not_followed(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    path = scratch.create_labeled_scratch("swap", "race", root=tmp_path / "root")
    (path / "inner").mkdir()
    (path / "inner" / "data.txt").write_text("x")
    real_pin = scratch._pin

    def swapping_pin(parent, child_path, expected):
        if os.path.basename(child_path) == "inner":
            os.rename(child_path, os.path.join(os.path.dirname(child_path), "moved"))
            _make_dir_link(child_path, outside)
        return real_pin(parent, child_path, expected)

    monkeypatch.setattr(scratch, "_pin", swapping_pin)
    try:
        removed = scratch.remove_scratch(path)
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"directory links unavailable: {exc}")
    assert not removed
    assert (outside / "keep.txt").read_text() == "precious"
    assert (path / scratch.BREADCRUMB).is_file()


@pytest.mark.parametrize("readable", [False, True])
def test_incomplete_cleanup_keeps_breadcrumb(tmp_path, monkeypatch, readable):
    path = scratch.create_labeled_scratch("partial", "stuck child", root=tmp_path)
    original = (path / scratch.BREADCRUMB).read_bytes()
    (path / "stuck.bin").write_bytes(b"1")
    (path / "free.bin").write_bytes(b"2")
    real_remove = scratch._remove_file
    if not readable:
        monkeypatch.setattr(scratch, "_saved_breadcrumb", lambda *args: None)

    def refusing_remove(target, st, dir_fd=None):
        if os.path.basename(target) == "stuck.bin":
            raise PermissionError("in use")
        real_remove(target, st, dir_fd)

    monkeypatch.setattr(scratch, "_remove_file", refusing_remove)
    assert not scratch.remove_scratch(path)
    assert sorted(os.listdir(path)) == [scratch.BREADCRUMB, "stuck.bin"]
    assert (path / scratch.BREADCRUMB).read_bytes() == original


def test_unreadable_breadcrumb_is_retained_even_after_other_children_are_gone(tmp_path, monkeypatch):
    path = scratch.create_labeled_scratch("unreadable", "retain ownership", root=tmp_path)
    original = (path / scratch.BREADCRUMB).read_bytes()
    (path / "free.bin").write_bytes(b"1")
    monkeypatch.setattr(scratch, "_saved_breadcrumb", lambda *args: None)
    assert not scratch.remove_scratch(path)
    assert list(path.iterdir()) == [path / scratch.BREADCRUMB]
    assert (path / scratch.BREADCRUMB).read_bytes() == original


def test_final_rmdir_failure_restores_breadcrumb(tmp_path, monkeypatch):
    path = scratch.create_labeled_scratch("final", "rmdir refused", root=tmp_path)
    original = (path / scratch.BREADCRUMB).read_bytes()
    real_rmdir = os.rmdir

    def refusing_rmdir(target, *args, **kwargs):
        if os.fspath(target) == str(path):
            raise PermissionError("directory in use")
        return real_rmdir(target, *args, **kwargs)

    monkeypatch.setattr(scratch.os, "rmdir", refusing_rmdir)
    assert not scratch.remove_scratch(path)
    assert (path / scratch.BREADCRUMB).read_bytes() == original


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
def test_report_does_not_block_on_fifo_breadcrumb(tmp_path):
    (tmp_path / "odd-x1").mkdir()
    try:
        os.mkfifo(tmp_path / "odd-x1" / scratch.BREADCRUMB)
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.ENOSYS):
            pytest.skip("fixture filesystem does not support FIFOs")
        raise
    groups = {(g["prefix"], g["kind"]): g for g in scratch.scratch_report(tmp_path)}
    assert groups[("odd-*", "dir")]["unlabeled"] == 1


def test_breadcrumb_read_remains_bounded_when_file_grows(tmp_path, monkeypatch):
    crumb = tmp_path / scratch.BREADCRUMB
    crumb.write_bytes(b"{}")
    monkeypatch.setattr(scratch, "_BREADCRUMB_LIMIT", 8)
    real_read = os.read
    reads = 0

    def growing_read(fd, count):
        nonlocal reads
        reads += 1
        assert reads <= 4, "breadcrumb growth bypassed the cumulative read limit"
        with crumb.open("ab") as stream:
            stream.write(b"more")
        return real_read(fd, count)

    monkeypatch.setattr(scratch.os, "read", growing_read)
    assert scratch._read_regular(str(crumb)) is None
    assert crumb.stat().st_size <= 16


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
