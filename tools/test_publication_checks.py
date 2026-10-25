import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest


def runner():
    path = Path(__file__).with_name("check-publication.py")
    spec = importlib.util.spec_from_file_location("publication_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("identifier_status,blob_status", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_both_guards_overlap_and_each_refusal_blocks_with_exact_inputs(
    monkeypatch, identifier_status, blob_status,
):
    module, reached, calls = runner(), threading.Barrier(2), {}

    def guard(name):
        def main(argv):
            calls[name] = argv
            reached.wait(timeout=3)
            return identifier_status if name == "check-no-internal-identifiers" else blob_status
        return SimpleNamespace(main=main)

    monkeypatch.setattr(module, "_guard", guard)
    outcome = module.main([
        "--base", "pinned-base", "--head", "pinned-head",
        "--published", "old-head", "--published", "release-head",
    ])
    assert outcome == (0 if (identifier_status, blob_status) == (0, 0) else 1)
    assert calls == {
        "check-no-internal-identifiers": [
            "--base", "pinned-base", "--git-ref", "pinned-head",
        ],
        "check-large-files": [
            "--base", "pinned-base", "--head", "pinned-head",
            "--published", "old-head", "--published", "release-head",
        ],
    }


def test_unexpected_guard_failure_still_waits_for_the_other_guard(monkeypatch):
    module, reached, settled = runner(), threading.Barrier(2), threading.Event()

    def guard(name):
        def main(argv):
            reached.wait(timeout=3)
            if name == "check-no-internal-identifiers":
                raise RuntimeError("synthetic guard failure")
            settled.set()
            return 0
        return SimpleNamespace(main=main)

    monkeypatch.setattr(module, "_guard", guard)
    with pytest.raises(RuntimeError, match="synthetic guard failure"):
        module.main(["--base", "base", "--head", "head"])
    assert settled.is_set()
