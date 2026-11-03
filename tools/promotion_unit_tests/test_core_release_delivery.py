"""Actor-free release-to-delivery source and custody decisions."""
from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from types import SimpleNamespace

import core_release_delivery as delivery
import pytest

COMMIT = "a" * 40
DEV = "b" * 40
TREE = "c" * 40
CONTENT = "d" * 40


@pytest.mark.parametrize("duplicate", [False, True], ids=["valid", "duplicate-state"])
def test_selected_source_derives_dev_and_content_from_committed_state(monkeypatch, duplicate):
    raw = json.dumps({"last_promotion": {"dev_head": DEV}})
    if duplicate:
        raw = '{"last_promotion":{"dev_head":"' + DEV + '","dev_head":"' + DEV + '"}}'
    calls = []

    def git(_repository, *arguments, deadline):
        calls.append(arguments)
        if arguments[0] == "rev-parse":
            return TREE if arguments[1].endswith("^{tree}") else COMMIT
        if arguments[0] == "merge-base":
            return ""
        if arguments[0] == "cat-file":
            return str(len(raw))
        if arguments[0] == "show":
            return raw
        raise AssertionError(arguments)

    monkeypatch.setattr(delivery, "_git", git)
    monkeypatch.setattr(delivery, "_content_tree", lambda _repo, tree, **_kw: (
        CONTENT if tree == TREE else pytest.fail("wrong source tree")
    ))
    if duplicate:
        with pytest.raises(ValueError):
            delivery._selected_source("fixture", COMMIT, COMMIT, deadline=time.monotonic() + 60)
        assert not any(args[0] == "rev-parse" and args[1].endswith("^{tree}") for args in calls)
    else:
        assert delivery._selected_source(
            "fixture", COMMIT, COMMIT, deadline=time.monotonic() + 60,
        ) == (DEV, CONTENT)


@pytest.mark.parametrize("fail_cleanup", [False, True], ids=["success", "cleanup-failure"])
def test_publication_follows_release_custody_exit(tmp_path, monkeypatch, fail_cleanup):
    repository = tmp_path / "source"
    repository.mkdir()
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    destination = tmp_path / "delivery.zip"
    content = b"candidate delivery"
    events = []
    core = SimpleNamespace(
        manifest_path=repository / "manifest.json", wheel_dir=repository / "wheels",
        index_path=repository / "index.json",
    )
    monkeypatch.setattr(delivery, "_selected_source", lambda *_a, **_kw: (DEV, CONTENT))

    @contextmanager
    def temporary(_parent):
        yield stage_root

    @contextmanager
    def retain(**options):
        assert options["dev_head"] == DEV and options["content_tree"] == CONTENT
        events.append("retained")
        yield core
        assert not destination.exists()
        events.append("released")
        if fail_cleanup:
            raise OSError("release cleanup failed")

    def prepare(*args, **kwargs):
        assert events == ["retained"]
        assert args[3:5] == (core.manifest_path, core.wheel_dir)
        assert kwargs["artifact_index"] == core.index_path
        args[7].write_bytes(content)
        events.append("built")
        return {
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            "size": len(content), "approval": "unattested",
        }

    original_link = os.link

    def link(source, target):
        events.append("linked")
        original_link(source, target)

    monkeypatch.setattr(delivery.delivery.native.dependencies, "_temporary_inputs", temporary)
    monkeypatch.setattr(delivery.delivery.native.dependencies, "_private_retained_root", lambda path: path)
    monkeypatch.setattr(delivery.delivery.source_owner, "native_retained_path", str)
    monkeypatch.setattr(delivery, "retain_released_core", retain)
    monkeypatch.setattr(delivery.delivery, "prepare_core_delivery", prepare)
    monkeypatch.setattr(delivery, "_open", lambda path: path.open("rb"))
    monkeypatch.setattr(delivery.os, "link", link)
    options = {
        "repository": "example/plugins", "source_checkout": repository, "source_commit": COMMIT,
        "main_head": COMMIT, "materials": {}, "material_paths": {}, "destination": destination,
        "uv": "fixture", "download_origins": (), "deadline": time.monotonic() + 60,
        "max_bytes": 1024, "native_lock": repository / "lock.toml",
    }
    if fail_cleanup:
        with pytest.raises(OSError, match="release cleanup failed"):
            delivery.prepare_release_delivery(**options)
        assert events == ["retained", "built", "released"] and not destination.exists()
    else:
        result = delivery.prepare_release_delivery(**options)
        assert events == ["retained", "built", "released", "linked"]
        assert destination.read_bytes() == content and result["approval"] == "unattested"
