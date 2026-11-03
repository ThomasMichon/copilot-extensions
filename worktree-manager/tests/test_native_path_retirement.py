"""Real disposable filesystem checks for the native physical-effect boundary."""
from __future__ import annotations

import errno
import os
import pytest

from worktree_manager.native_path_retirement import PinnedPath

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows handle retirement")


@WINDOWS
@pytest.mark.parametrize("filename", ["entry", "python.exe"])
def test_pinned_file_and_directory_dispose_original_objects(tmp_path, filename):
    directory = tmp_path / "owned"
    directory.mkdir()
    path = directory / filename
    path.write_bytes(b"owned")
    expected = path.stat()
    with PinnedPath(path, directory=False, protect=(tmp_path,)) as pinned:
        assert os.read(pinned.fd, 5) == b"owned"
        assert pinned.state()["inode"] == expected.st_ino
        assert pinned.state()["kind"] == "file"
        assert pinned.state()["mode"] == expected.st_mode
        pinned.dispose()
    assert not path.exists()
    with PinnedPath(directory, directory=True) as pinned:
        assert pinned.state()["inode"] == directory.stat().st_ino
        assert pinned.state()["kind"] == "directory"
        pinned.dispose()
    assert not directory.exists()


@WINDOWS
def test_child_retirement_keeps_ancestors_rename_pinned(tmp_path):
    directory = tmp_path / "owned"
    directory.mkdir()
    path = directory / "entry"
    path.write_bytes(b"owned")
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"foreign")
    with PinnedPath(path, directory=False) as pinned:
        with pytest.raises(OSError):
            foreign.replace(path)
        with pytest.raises(OSError):
            directory.rename(tmp_path / "moved")
        pinned.dispose()
    assert not path.exists()
    assert directory.exists()
    assert foreign.read_bytes() == b"foreign"


@WINDOWS
def test_directory_replacement_cannot_change_handle_disposition(tmp_path):
    directory = tmp_path / "owned"
    directory.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "operator-data").write_bytes(b"foreign")
    with PinnedPath(directory, directory=True) as pinned:
        with pytest.raises(OSError):
            directory.rename(tmp_path / "moved")
        with pytest.raises(OSError):
            foreign.replace(directory)
        pinned.dispose()
    assert not directory.exists()
    assert (foreign / "operator-data").read_bytes() == b"foreign"


@WINDOWS
def test_directory_pin_permits_child_teardown_but_refuses_unknown_children(tmp_path):
    directory = tmp_path / "owned"
    directory.mkdir()
    child = directory / "child"
    child.write_bytes(b"owned")
    with PinnedPath(directory, directory=True) as pinned:
        child.unlink()
        unknown = directory / "new-unowned-child"
        unknown.write_bytes(b"preserve")
        with pytest.raises(OSError):
            pinned.dispose()
        assert unknown.read_bytes() == b"preserve"
    assert directory.exists()


@WINDOWS
def test_hardlink_and_reparse_aliases_are_not_retired(tmp_path):
    path = tmp_path / "entry"
    path.write_bytes(b"preserve")
    alias = tmp_path / "alias"
    os.link(path, alias)
    with pytest.raises((OSError, ValueError)):
        with PinnedPath(path, directory=False):
            pytest.fail("A hardlinked target must not be admitted")
    assert path.read_bytes() == alias.read_bytes() == b"preserve"


@WINDOWS
def test_reparse_ancestor_is_not_followed(tmp_path):
    directory = tmp_path / "actual"
    directory.mkdir()
    path = directory / "entry"
    path.write_bytes(b"preserve")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("This contained Windows account cannot create a test reparse point")
    with pytest.raises((OSError, ValueError)):
        with PinnedPath(alias / "entry", directory=False):
            pytest.fail("A reparse ancestor must not be admitted")
    assert path.read_bytes() == b"preserve"


@pytest.mark.skipif(os.name == "nt", reason="POSIX refusal contract")
def test_unsupported_platform_never_mutates_target(tmp_path):
    path = tmp_path / "entry"
    path.write_bytes(b"preserve")
    with pytest.raises(OSError) as caught:
        PinnedPath(path, directory=False)
    assert caught.value.errno == errno.ENOTSUP
    assert path.read_bytes() == b"preserve"
