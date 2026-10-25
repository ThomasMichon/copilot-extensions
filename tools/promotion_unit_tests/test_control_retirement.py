"""Trusted control retirement with in-memory Git objects and private files."""
from __future__ import annotations

import hashlib
import json

import pytest

import promotion_control as control

OLD = ".github/workflows/old.yml"
CURRENT = ".github/workflows/current.yml"
STATE = ".github/release-pipeline-state.json"
PRIOR, MAIN, OLDER = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture
def objects(tmp_path, monkeypatch):
    blobs, trees, calls = {}, {}, []
    ancestors = {PRIOR, MAIN, OLDER}

    def blob(raw, mode="100644"):
        raw = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
        oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        blobs[oid] = raw
        return mode, "blob", oid

    def manifest(paths):
        return blob({"schema": control.SCHEMA, "version": 1, "files": paths})

    trees[PRIOR] = {
        control.MANIFEST_PATH: manifest([OLD, CURRENT]),
        OLD: blob(b"old trusted\n"), CURRENT: blob(b"retained trusted\n"),
    }
    receipt = {
        "main_head": PRIOR,
        "manifest_blob": trees[PRIOR][control.MANIFEST_PATH][2],
        "files": {name: entry[2] for name, entry in trees[PRIOR].items()},
    }
    state = {
        "schema": "copilot-extensions.release-pipeline-state", "version": 1,
        "paused": False, "pause_reason": None,
        "last_promotion": {"control_preserved": receipt}, "last_rollback": None,
    }
    trees[MAIN] = {
        control.MANIFEST_PATH: manifest([CURRENT]),
        CURRENT: blob(b"new trusted\n"), STATE: blob(state),
    }
    candidate_entries = {}

    def git(_repo, args):
        calls.append(args)
        if args[:2] == ["ls-files", "--stage"]:
            return b"".join(
                f"{mode} {'1' * 40} 0\t{path}".encode() + b"\0"
                for path, mode in candidate_entries.items()
            )
        if args[0] == "ls-tree":
            return b"".join(
                f"{mode} {kind} {oid}\t{path}".encode() + b"\0"
                for path, (mode, kind, oid) in trees[args[-1]].items()
            )
        if args[0] == "merge-base":
            return (args[1] if args[1] in ancestors else "d" * 40).encode()
        assert args[0] == "cat-file"
        if args[1] == "-t":
            return b"commit"
        raw = blobs[args[2]]
        return str(len(raw)).encode() if args[1] == "-s" else raw

    monkeypatch.setattr(control, "_git", git)
    old = tmp_path / OLD
    old.parent.mkdir(parents=True)
    old.write_bytes(b"obsolete dev\n")
    (tmp_path / CURRENT).write_bytes(b"dev version\n")
    (tmp_path / "payload.txt").write_bytes(b"unchanged payload\n")

    def save_state():
        trees[MAIN][STATE] = blob(state)

    return tmp_path, trees, state, receipt, blob, save_state, candidate_entries, ancestors, calls


def preserve(objects):
    root = objects[0]
    return control.preserve_control(root, main_head=MAIN, repo=root)


def test_deleted_control_cannot_resurrect_from_dev(objects):
    root = objects[0]
    result = preserve(objects)
    assert not (root / OLD).exists()
    assert (root / CURRENT).read_bytes() == b"new trusted\n"
    assert (root / "payload.txt").read_bytes() == b"unchanged payload\n"
    assert result["retired"] == {OLD: PRIOR}
    assert set(result["files"]) == {control.MANIFEST_PATH, CURRENT}


def test_tombstone_survives_subsequent_promotions(objects):
    root, trees, state, _receipt, _blob, save, *_rest = objects
    result = preserve(objects)
    trees[OLDER] = dict(trees[MAIN])
    result["main_head"] = OLDER
    state["last_promotion"]["control_preserved"] = result
    save()
    (root / OLD).write_bytes(b"still in dev\n")
    assert preserve(objects)["retired"] == {OLD: PRIOR}
    assert not (root / OLD).exists()


@pytest.mark.parametrize("declared", [False, True])
def test_main_reintroduction_clears_tombstone_and_preserves_ownership_rule(objects, declared):
    root, trees, state, receipt, blob, save, *_rest = objects
    trees[OLDER] = {
        name: entry for name, entry in trees[MAIN].items() if name != STATE
    }
    receipt.update({
        "main_head": OLDER, "manifest_blob": trees[OLDER][control.MANIFEST_PATH][2],
        "files": {name: entry[2] for name, entry in trees[OLDER].items()},
        "retired": {OLD: PRIOR},
    })
    trees[MAIN][OLD] = blob(b"main reintroduced\n")
    if declared:
        trees[MAIN][control.MANIFEST_PATH] = blob({
            "schema": control.SCHEMA, "version": 1, "files": [CURRENT, OLD],
        })
    save()
    result = preserve(objects)
    assert "retired" not in result
    assert (root / OLD).read_bytes() == (
        b"main reintroduced\n" if declared else b"obsolete dev\n"
    )


@pytest.mark.parametrize("legacy", ["no-state", "no-promotion", "no-controls"])
def test_missing_historical_authority_does_not_authorize_deletion(objects, legacy):
    root, trees, state, _receipt, _blob, save, *_rest = objects
    if legacy == "no-state":
        del trees[MAIN][STATE]
    else:
        state["last_promotion"] = None if legacy == "no-promotion" else {}
        save()
    assert "retired" not in preserve(objects)
    assert (root / OLD).read_bytes() == b"obsolete dev\n"


def test_dev_pipeline_state_is_not_deletion_authority(objects):
    root, trees, state, _receipt, _blob, _save, *_rest = objects
    del trees[MAIN][STATE]
    (root / STATE).write_text(json.dumps(state), encoding="utf-8")
    preserve(objects)
    assert (root / OLD).read_bytes() == b"obsolete dev\n"


@pytest.mark.parametrize("history", ["declared", "retired", "malformed"])
def test_manifest_removal_cannot_downgrade_recorded_control_history(objects, history):
    root, trees, _state, receipt, blob, save, *_rest = objects
    del trees[MAIN][control.MANIFEST_PATH]
    if history == "retired":
        trees[OLDER] = {
            control.MANIFEST_PATH: blob({"schema": control.SCHEMA, "version": 1, "files": [CURRENT]}),
            CURRENT: trees[MAIN][CURRENT],
        }
        receipt.update({
            "main_head": OLDER, "manifest_blob": trees[OLDER][control.MANIFEST_PATH][2],
            "files": {name: entry[2] for name, entry in trees[OLDER].items()},
            "retired": {OLD: PRIOR},
        })
    elif history == "malformed":
        receipt["files"] = []
    save()
    with pytest.raises(control.ControlPreservationError):
        preserve(objects)
    assert (root / OLD).read_bytes() == b"obsolete dev\n"
    assert (root / CURRENT).read_bytes() == b"dev version\n"


@pytest.mark.parametrize("legacy", ["no-state", "no-promotion", "no-controls"])
def test_manifest_absence_without_control_history_retains_legacy_behavior(objects, legacy):
    root, trees, state, _receipt, _blob, save, *_rest = objects
    del trees[MAIN][control.MANIFEST_PATH]
    if legacy == "no-state":
        del trees[MAIN][STATE]
    else:
        state["last_promotion"] = None if legacy == "no-promotion" else {}
        save()
    assert preserve(objects) is None
    assert (root / OLD).read_bytes() == b"obsolete dev\n"


@pytest.mark.parametrize("raw", [
    b"{", b"\xff", b'{"schema":"a","schema":"b"}',
])
def test_malformed_or_duplicate_main_state_refuses_before_writes(objects, raw):
    root, trees, _state, _receipt, blob, *_rest = objects
    trees[MAIN][STATE] = blob(raw)
    with pytest.raises(control.ControlPreservationError):
        preserve(objects)
    assert (root / OLD).read_bytes() == b"obsolete dev\n"
    assert (root / CURRENT).read_bytes() == b"dev version\n"


def test_tombstone_capacity_is_bounded_before_origin_reads(objects):
    root, _trees, _state, receipt, _blob, save, *_rest = objects
    receipt["retired"] = {f".github/workflows/gone-{index}.yml": PRIOR for index in range(65)}
    save()
    with pytest.raises(control.ControlPreservationError, match="retired control provenance"):
        preserve(objects)
    assert (root / OLD).read_bytes() == b"obsolete dev\n"


@pytest.mark.parametrize("change", [
    "schema", "version", "promotion", "receipt", "extra", "main-head", "manifest",
    "inventory", "blob", "ancestor", "missing-manifest", "missing-file", "mode",
    "retired-type", "retired-origin", "retired-path", "retired-undeclared", "retired-overlap",
])
def test_invalid_historical_authority_fails_before_any_candidate_mutation(objects, change):
    root, trees, state, receipt, _blob, save, _index, ancestors, _calls = objects
    if change == "schema":
        state["schema"] = "other"
    elif change == "version":
        state["version"] = True
    elif change == "promotion":
        state["last_promotion"] = []
    elif change == "receipt":
        state["last_promotion"]["control_preserved"] = []
    elif change == "extra":
        receipt["unknown"] = True
    elif change == "main-head":
        receipt["main_head"] = "HEAD"
    elif change == "manifest":
        receipt["manifest_blob"] = "0" * 40
    elif change == "inventory":
        receipt["files"].pop(OLD)
    elif change == "blob":
        receipt["files"][OLD] = "0" * 40
    elif change == "ancestor":
        ancestors.remove(PRIOR)
    elif change == "missing-manifest":
        del trees[PRIOR][control.MANIFEST_PATH]
    elif change == "missing-file":
        del trees[PRIOR][OLD]
    elif change == "mode":
        trees[PRIOR][OLD] = ("120000", *trees[PRIOR][OLD][1:])
    elif change == "retired-type":
        receipt["retired"] = []
    elif change == "retired-origin":
        receipt["retired"] = {".github/workflows/gone.yml": "main"}
    elif change == "retired-path":
        receipt["retired"] = {"payload.txt": PRIOR}
    elif change == "retired-undeclared":
        receipt["retired"] = {".github/workflows/gone.yml": PRIOR}
    else:
        receipt["retired"] = {OLD: PRIOR}
    save()
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(control.ControlPreservationError):
        preserve(objects)
    assert {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("shape", ["directory", "alias", "symlink-index", "submodule-index"])
def test_unsafe_retired_destination_fails_before_control_writes(objects, shape):
    root, _trees, _state, _receipt, _blob, _save, index, *_rest = objects
    old = root / OLD
    if shape == "directory":
        old.unlink()
        old.mkdir()
    elif shape == "alias":
        old.rename(old.with_name("Old.yml"))
    else:
        index[OLD] = "120000" if shape == "symlink-index" else "160000"
    with pytest.raises(control.ControlPreservationError):
        preserve(objects)
    assert (root / CURRENT).read_bytes() == b"dev version\n"
    assert not (root / control.MANIFEST_PATH).exists()
