"""Only a missing native Windows file-link privilege is a capability skip."""

import os
from pathlib import Path

import pytest


@pytest.mark.parametrize("winerror", [1314, 5, None])
def test_file_link_capability_does_not_hide_other_errors(
    native_file_symlink, monkeypatch, tmp_path, winerror,
):
    error = OSError("fixture link creation failed")
    if winerror is not None:
        error.winerror = winerror

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(Path, "symlink_to", fail)
    expected = pytest.skip.Exception if os.name == "nt" and winerror == 1314 else OSError
    with pytest.raises(expected) as caught:
        native_file_symlink(tmp_path / "link", tmp_path / "target")
    if expected is OSError:
        assert caught.value is error


def test_file_link_capability_executes_real_link_when_available(native_file_symlink, tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"fixture")
    link = tmp_path / "link"
    native_file_symlink(link, target)
    assert link.is_symlink()
    assert link.read_bytes() == b"fixture"
