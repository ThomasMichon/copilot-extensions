"""Selector text is rollback state; only nonblank names need runtime custody."""

from contextlib import contextmanager
from pathlib import Path
import stat

import pytest

from worktree_manager import self_install as si


CASES = [
    ("missing", "healthy"), ("blank", "healthy"), ("whitespace", "healthy"),
    ("missing-slot", "healthy"), ("healthy", "healthy"), ("invalid", "healthy"),
    ("healthy", "missing"), ("healthy", "blank"), ("healthy", "whitespace"),
    ("healthy", "missing-slot"), ("healthy", "invalid"), ("missing", "missing"),
    ("blank", "blank"), ("whitespace", "whitespace"),
    ("blank", "whitespace"), ("whitespace", "blank"),
]


def _contents(state, version):
    return {
        "missing": None, "blank": b"", "whitespace": b" \t\r\n",
        "missing-slot": b"0.8.0\n", "healthy": (version + "\r\n").encode(),
        "invalid": b"../unsafe\n",
    }[state]


def _snapshot(paths):
    return {
        path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None
        for path in paths
    }


@pytest.mark.parametrize("current_state,last_good_state", CASES)
@pytest.mark.parametrize("operation", ["apply", "rollback-before-second", "rollback-after-second"])
def test_selector_state_matrix(tmp_path, monkeypatch, current_state, last_good_state, operation):
    adapter = si._runtime_installer()
    runtime = adapter.runtime
    publication = runtime._runtime_component("versioned_runtime_publication")
    root = tmp_path / "root"
    for generation in ("0.9.0", "1.0.0", "2.0.0"):
        runtime.mark_complete(root, generation, payload_hash="a" * 64)
    current, last_good = root / "current-version", root / "last-known-good"
    for path, state, version in (
        (current, current_state, "1.0.0"), (last_good, last_good_state, "0.9.0"),
    ):
        data = _contents(state, version)
        if data is not None:
            path.write_bytes(data)
    before = _snapshot((current, last_good))
    completion = {path: path.read_bytes() for path in root.glob("versions/*/.install-complete.json")}
    expected = si.current_version(root)
    required = {"2.0.0"}
    if current_state == "healthy":
        required.add("1.0.0")
    if last_good_state == "healthy":
        required.add("0.9.0")
    held, publishing, faults, recoveries = set(), [], [], []
    retain = runtime.retention_lease
    lease = runtime._publication_lease
    publish = publication.publish_selectors
    recover = publication._recover_uncontested_locked
    replace = publication.os.replace

    @contextmanager
    def retaining(root, version, **kwargs):
        with retain(root, version, **kwargs) as slot:
            assert version, "blank names must never be acquired"
            held.add(version)
            try:
                yield slot
            finally:
                held.remove(version)

    @contextmanager
    def leasing(root):
        with lease(root):
            publishing.append(True)
            try:
                yield
            finally:
                publishing.pop()

    def publishing_selectors(*args):
        assert publishing == [True] and held == required
        return publish(*args)

    def recovering(*args):
        assert publishing == [True] and held == required
        recoveries.append(True)
        return recover(*args)

    def replacing(source, target):
        if operation != "apply" and Path(target) == last_good and not faults:
            assert current.read_bytes() == b"2.0.0\n"
            assert publishing == [True] and held == required
            faults.append(True)
            if operation == "rollback-after-second":
                replace(source, target)
                assert last_good.read_bytes() == b"2.0.0\n"
            raise PermissionError("selector-state fault")
        return replace(source, target)

    monkeypatch.setattr(runtime, "retention_lease", retaining)
    monkeypatch.setattr(runtime, "_publication_lease", leasing)
    monkeypatch.setattr(publication, "publish_selectors", publishing_selectors)
    monkeypatch.setattr(publication, "_recover_uncontested_locked", recovering)
    monkeypatch.setattr(publication.os, "replace", replacing)
    if "invalid" in (current_state, last_good_state):
        with pytest.raises(ValueError):
            adapter.activate(root, "2.0.0", expected=expected)
        assert faults == [] and recoveries == []
        assert _snapshot((current, last_good)) == before
    elif operation == "apply":
        adapter.activate(root, "2.0.0", expected=expected)
        assert current.read_bytes() == last_good.read_bytes() == b"2.0.0\n"
        assert faults == [] and recoveries == []
    else:
        with pytest.raises(PermissionError, match="selector-state fault"):
            adapter.activate(root, "2.0.0", expected=expected)
        assert faults == [True] and recoveries == [True]
        assert _snapshot((current, last_good)) == before
    assert held == set() and publishing == []
    assert all(path.read_bytes() == data for path, data in completion.items())


@pytest.mark.parametrize("target", ["", " \t\r\n", "../unsafe", "bad/name", "bad."])
def test_blank_or_unsafe_target_is_still_refused(tmp_path, target):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    adapter.runtime.mark_complete(root, "2.0.0", payload_hash="a" * 64)
    current, last_good = root / "current-version", root / "last-known-good"
    current.write_bytes(b" \t\r\n")
    last_good.write_bytes(b"")
    before = _snapshot((current, last_good))
    with pytest.raises(ValueError):
        adapter.activate(root, target, expected=None)
    assert _snapshot((current, last_good)) == before


@pytest.mark.parametrize("data", [None, b"", b" \t\r\n", b"1.0.0\n"])
def test_prior_text_does_not_relax_expected_selection(tmp_path, data):
    adapter = si._runtime_installer()
    root = tmp_path / "root"
    adapter.runtime.mark_complete(root, "2.0.0", payload_hash="a" * 64)
    marker = root / "current-version"
    if data is not None:
        marker.write_bytes(data)
    before = _snapshot((marker, root / "last-known-good"))
    with pytest.raises(RuntimeError, match="selection changed"):
        adapter.activate(root, "2.0.0", expected="0.8.0")
    assert _snapshot(before) == before
