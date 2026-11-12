"""Pinned raw-object batch protocol; no actual Git storage or subprocess execution."""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

import promotion_source_archive as source_io
from promotion_unit_tests.test_complete_source import case


def test_scratch_creation_disables_hooks_only_for_the_checkout_command(tmp_path, monkeypatch):
    import promote_release as promotion
    import time

    monkeypatch.setattr(time, "time", lambda: 100.0)
    calls = []

    def git(arguments, **options):
        calls.append((arguments, options))
        return str(tmp_path / ".git") if arguments[0] == "rev-parse" else ""

    monkeypatch.setattr(promotion, "_git", git)
    scratch = promotion.add_scratch_worktree("a" * 40, repo=tmp_path)
    assert calls == [
        (["rev-parse", "--git-common-dir"], {"cwd": tmp_path}),
        (["-c", f"core.hooksPath={os.devnull}", "worktree", "add",
          "--detach", str(scratch), "a" * 40], {"cwd": tmp_path}),
    ]
    other = promotion.add_scratch_worktree("a" * 40, repo=tmp_path)
    assert other != scratch and other.parent == scratch.parent
    assert other.is_dir() and scratch.is_dir()


def test_composition_budget_bounds_git_and_restores_the_original_transport(tmp_path, monkeypatch):
    import source_preparation_process as process

    clock = [100.0]
    monkeypatch.setattr(source_io.time, "monotonic", lambda: clock[0])
    calls = []
    commands = []

    def run(argv, **options):
        commands.append(argv)
        calls.append(options)
        return SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(process, "run", run)
    with source_io.composition_budget(110):
        source_io.git_text(tmp_path, ["status"])
        clock[0] = 106
        source_io._git(tmp_path, ["status"])
        with source_io.composition_budget(200):
            source_io.is_ancestor(tmp_path, "a", "b")
        source_io.changefile_existed_at("a", "example.json", repo=tmp_path)
        assert all(
            argv[:4] == ["git", "--no-replace-objects", "-c", "core.fsmonitor=false"]
            for argv in commands
        )
        assert [row["timeout"] for row in calls] == [10, 4, 4, 4]
        assert all(row["deadline"] == 110 for row in calls)
        clock[0] = 111
        with pytest.raises(ValueError, match="deadline"):
            source_io.git_text(tmp_path, ["status"])
        assert len(calls) == 4
    assert source_io.git_timeout_options() == {}


def test_local_composition_reserves_cleanup_inside_the_original_deadline(tmp_path, monkeypatch):
    import local_runtime_promotion as local

    clock = [100.0]
    monkeypatch.setattr(source_io.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(local.promotion, "_rev_parse", lambda ref, **kw: ref.split("^")[0])
    monkeypatch.setattr(local.promotion, "add_scratch_worktree", lambda *a, **kw: tmp_path / "scratch")
    cleanup = []

    def consume(*args, **options):
        assert source_io.git_timeout_options() == {"timeout": 8}
        clock[0] = 108.5
        return {}

    monkeypatch.setattr(local.promotion, "consume_pending_changes", consume)
    monkeypatch.setattr(
        local.promotion, "remove_scratch_worktree",
        lambda *a, **kw: cleanup.append(source_io.git_timeout_options()),
    )
    with pytest.raises(ValueError, match="deadline"):
        local.compose(tmp_path, source_commit="a" * 40, main_seed="b" * 40, deadline=110)
    assert cleanup == [{"timeout": 1.5}]
    assert source_io.git_timeout_options() == {}


@pytest.fixture
def objects(monkeypatch):
    files, identity = case()
    blobs, records = {}, []
    for name, (mode, raw) in sorted(files.items()):
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        blobs[blob] = raw
        records.append(f"{mode} blob {blob}\t{name}".encode() + b"\0")
    identifiers = sorted(blobs)
    checks = [f"{blob} blob {len(blobs[blob])}\n".encode() for blob in identifiers]
    outputs = {
        "commit": f"tree {identity['tree']}\nparent {identity['main_before']}\n\ncandidate\n".encode(),
        "tree": b"".join(records), "metadata": b"".join(checks),
        "blobs": b"".join(header + blobs[blob] + b"\n" for header, blob in zip(checks, identifiers, strict=True)),
    }
    calls = []

    def git(repo, arguments, *, data=None):
        calls.append((repo, arguments, data))
        if arguments[0] == "ls-tree":
            assert arguments == ["ls-tree", "-rz", identity["tree"]] and data is None
            return outputs["tree"]
        if arguments[1] == "commit":
            assert arguments == ["cat-file", "commit", identity["candidate_sha"]] and data is None
            return outputs["commit"]
        assert data == ("\n".join(identifiers) + "\n").encode()
        return outputs["metadata" if arguments == ["cat-file", "--batch-check"] else "blobs"]

    monkeypatch.setattr(source_io, "_git", git)
    return files, identity, outputs, calls


def test_source_reader_uses_one_bounded_metadata_and_blob_batch_without_export_attributes(objects, tmp_path):
    files, identity, _outputs, calls = objects
    assert source_io.read_tree(
        tmp_path, **{key: identity[key] for key in ("candidate_sha", "main_before", "tree")},
    ) == files
    assert [arguments[:2] for _repo, arguments, _data in calls] == [
        ["cat-file", "commit"], ["ls-tree", "-rz"], ["cat-file", "--batch-check"], ["cat-file", "--batch"],
    ]
    assert not any("archive" in arguments or "--filters" in arguments for _repo, arguments, _data in calls)


@pytest.mark.parametrize("change", [
    "parent", "tree", "submodule", "mode", "duplicate", "metadata-count", "metadata-kind",
    "size", "blob-header", "blob-truncated", "blob-extra", "identity",
])
def test_source_reader_refuses_wrong_generation_unsupported_types_and_batch_corruption(
    objects, tmp_path, change,
):
    _files, identity, outputs, calls = objects
    if change == "parent":
        outputs["commit"] = outputs["commit"].replace(identity["main_before"].encode(), b"1" * 40)
    elif change == "tree":
        outputs["commit"] = outputs["commit"].replace(identity["tree"].encode(), b"1" * 40)
    elif change == "submodule":
        outputs["tree"] = outputs["tree"].replace(b"100644 blob", b"160000 commit", 1)
    elif change == "mode":
        outputs["tree"] = outputs["tree"].replace(b"100644", b"100600", 1)
    elif change == "duplicate":
        outputs["tree"] += outputs["tree"].split(b"\0")[0] + b"\0"
    elif change == "metadata-count":
        outputs["metadata"] = outputs["metadata"].splitlines(keepends=True)[0]
    elif change == "metadata-kind":
        outputs["metadata"] = outputs["metadata"].replace(b" blob ", b" tree ", 1)
    elif change == "size":
        first = outputs["metadata"].splitlines()[0]
        outputs["metadata"] = outputs["metadata"].replace(first, first.rsplit(b" ", 1)[0] + b" 999999999", 1)
    elif change == "blob-header":
        outputs["blobs"] = b"x" + outputs["blobs"]
    elif change == "blob-truncated":
        outputs["blobs"] = outputs["blobs"][:-1]
    elif change == "blob-extra":
        outputs["blobs"] += b"x"
    else:
        identity["candidate_sha"] = "not-a-sha"
    with pytest.raises(ValueError):
        source_io.read_tree(
            tmp_path, **{key: identity[key] for key in ("candidate_sha", "main_before", "tree")},
        )
    if change in ("size", "metadata-kind", "metadata-count"):
        assert len(calls) == 3
    if change == "identity":
        assert not calls


def test_main_version_inputs_batch_only_selected_raw_blobs(monkeypatch, tmp_path):
    raw = b'{"version":"0.4.0-dev8"}\n'
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    paths = ["plugins/a/plugin.json", "plugins/b/plugin.json"]
    calls = []

    def git(repo, args, *, data=None):
        assert repo == tmp_path
        calls.append((args, data))
        if args[0] == "ls-tree":
            assert args == ["ls-tree", "-rz", "a" * 40]
            return b"".join(
                f"100644 blob {blob}\t{path}\0".encode()
                for path in [*paths, "unrelated.json"]
            )
        assert data == (blob + "\n").encode()
        header = f"{blob} blob {len(raw)}\n".encode()
        return header if args[-1] == "--batch-check" else header + raw + b"\n"

    monkeypatch.setattr(source_io, "_git", git)
    assert source_io.main_version_inputs(tmp_path, "a" * 40, [*paths, "missing"]) == {
        path: raw.decode() for path in paths
    }
    assert len(calls) == 3
    assert source_io.main_version_inputs(tmp_path, "a" * 40, []) == {}
    assert len(calls) == 3


@pytest.mark.parametrize("kind", ["ordinary", "candidate", "ancestry", "changefile"])
def test_promotion_git_calls_cannot_reintroduce_foreign_selectors(monkeypatch, tmp_path, kind):
    import promote_release as promotion
    from promotion_candidate import candidate_environment

    for key in ("GIT_DIR", "git_work_tree", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"):
        monkeypatch.setenv(key, "foreign")
    monkeypatch.setenv("PROMOTION_TEST_RETAINED", "retained")
    calls = []

    def run(args, **kwargs):
        clean = kwargs["env"]
        assert not {"GIT_DIR", "git_work_tree", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"} & clean.keys()
        assert clean["PROMOTION_TEST_RETAINED"] == "retained"
        assert kwargs["creationflags"] == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert kwargs["cwd"] == str(tmp_path)
        assert args[:2] == ["git", "--no-replace-objects"]
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"source\n", b"")

    monkeypatch.setattr(subprocess, "run", run)
    if kind == "ancestry":
        assert promotion._is_ancestor("a", "b", cwd=tmp_path)
    elif kind == "changefile":
        assert promotion._changefile_existed_at("a", "pending.json", repo=tmp_path)
    else:
        environment = candidate_environment("2026-01-02T03:04:05Z") if kind == "candidate" else None
        assert promotion._git(["rev-parse", "HEAD"], cwd=tmp_path, env=environment) == "source"
        if environment is not None:
            assert environment["GIT_DIR"] == "foreign"
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [None, "composition", "tree", "selection"])
def test_local_composition_delegates_to_existing_owners_and_always_settles(
    monkeypatch, tmp_path, failure,
):
    import local_runtime_promotion as local

    calls = []
    scratch = tmp_path / "scratch"
    source, main, tree = "a" * 40, "b" * 40, "c" * 40
    control = {"files": {"control": "d" * 40}}
    monkeypatch.setattr(local.promotion, "_rev_parse", lambda ref, **kw: ref.split("^")[0])
    monkeypatch.setattr(local.promotion, "add_scratch_worktree", lambda *a, **kw: scratch)
    monkeypatch.setattr(
        local.promotion, "remove_scratch_worktree",
        lambda path, **kw: calls.append(("cleanup", path, kw)),
    )

    def consume(path, **kwargs):
        assert path == scratch
        assert kwargs == {
            "main_head": main, "repo": tmp_path, "trusted_source": local.promotion.REPO,
            "allocate_versions": False,
        }
        calls.append("composition")
        if failure == "composition":
            raise ValueError(failure)
        return {"control_preserved": control}

    def build(path, **kwargs):
        assert path == scratch and kwargs == {"control": control}
        calls.append("tree")
        if failure == "tree":
            raise ValueError(failure)
        return tree

    def select(path):
        assert path == scratch
        calls.append("selection")
        if failure == "selection":
            raise ValueError(failure)
        return ("selected/input.py",)

    monkeypatch.setattr(local.promotion, "consume_pending_changes", consume)
    monkeypatch.setattr(local.promotion, "build_promotion_tree", build)
    if failure is None:
        result = local.compose(tmp_path, source_commit=source, main_seed=main, select_inputs=select)
        assert result == local.LocalComposition(source, main, tree, ("selected/input.py",))
        assert calls[:-1] == ["composition", "tree", "selection"]
    else:
        with pytest.raises(ValueError, match=failure):
            local.compose(tmp_path, source_commit=source, main_seed=main, select_inputs=select)
    assert calls[-1] == ("cleanup", scratch, {"repo": tmp_path})


@pytest.mark.parametrize("target", ["directory", "file"])
def test_changefile_reader_rejects_indirection_before_consumption(monkeypatch, tmp_path, target):
    import changefile

    directory = tmp_path / ".changefiles"
    directory.mkdir()
    path = directory / "pending.json"
    path.write_text('{"changes":[]}\n', encoding="utf-8")
    monkeypatch.setattr(changefile, "CHANGEFILES_DIR", directory)
    linked = directory if target == "directory" else path
    original = type(path).lstat
    monkeypatch.setattr(
        type(path), "lstat",
        lambda candidate: SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        if candidate == linked else original(candidate),
    )
    with pytest.raises(ValueError, match="unsafe changefile path"):
        changefile.read_changefiles()
    assert path.read_text(encoding="utf-8") == '{"changes":[]}\n'
