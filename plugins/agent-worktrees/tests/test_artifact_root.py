"""Private root admission uses actual OS objects, not breadcrumb assertions."""

import os
from pathlib import Path

import pytest

from agent_procutil._daemon_instance_owner import mkdir_private
from agent_worktrees.artifact_root import RootAdmissionError, admit_root


def test_private_root_and_short_exclusive_child(tmp_path):
    path = tmp_path / "scratch"
    with admit_root(path, forbidden=(tmp_path / "source",), create=True) as root:
        root.create_child("run-opaque")
        with root.child("run-opaque") as (fd, child):
            assert os.fstat(fd).st_ino == child.stat().st_ino
            assert child.parent == path
        with pytest.raises(FileExistsError):
            root.create_child("run-opaque")
        identity = root.identity
    with admit_root(path, forbidden=(), expected=identity) as root:
        root.verify()


@pytest.mark.parametrize("relative", ["", "relative", "../scratch"])
def test_ambiguous_root_is_refused(relative):
    with pytest.raises(RootAdmissionError):
        with admit_root(Path(relative), forbidden=(), create=True):
            pytest.fail("unsafe root admitted")


@pytest.mark.parametrize("relation", ["inside", "same", "encloses"])
def test_root_must_not_overlap_checkout_git_or_history(tmp_path, relation):
    protected = tmp_path / "source"
    selected = {
        "inside": protected / "scratch", "same": protected, "encloses": tmp_path,
    }[relation]
    with pytest.raises(RootAdmissionError, match="overlaps"):
        with admit_root(selected, forbidden=(protected,), create=True):
            pytest.fail("overlap admitted")
    assert not protected.exists()


def test_missing_intermediate_directory_is_not_implicitly_provisioned(tmp_path):
    with pytest.raises(FileNotFoundError):
        with admit_root(tmp_path / "absent" / "scratch", forbidden=(), create=True):
            pytest.fail("unselected ancestors created")
    assert not (tmp_path / "absent").exists()


def test_replaced_root_cannot_replay_its_admission(tmp_path):
    path = tmp_path / "scratch"
    with admit_root(path, forbidden=(), create=True) as root:
        identity = root.identity
    path.rename(tmp_path / "retained-original")
    mkdir_private(path)
    with pytest.raises(RootAdmissionError, match="identity"):
        with admit_root(path, forbidden=(), expected=identity):
            pytest.fail("replacement admitted")


@pytest.mark.parametrize("name", ["..", ".", "../other", "x/y", "x\\y", "x:stream", ""])
def test_allocation_names_cannot_escape_root(tmp_path, name):
    with admit_root(tmp_path / "scratch", forbidden=(), create=True) as root:
        with pytest.raises(RootAdmissionError):
            root.create_child(name)
        with pytest.raises(RootAdmissionError):
            with root.child(name):
                pytest.fail("invalid child admitted")


def test_existing_permissions_are_not_silently_repaired(tmp_path):
    path = tmp_path / "scratch"
    path.mkdir(mode=0o755)
    if os.name == "nt":
        # A normal inherited temporary directory lacks the protected DACL.
        with pytest.raises(RootAdmissionError):
            with admit_root(path, forbidden=()):
                pytest.fail("inherited root admitted")
    else:
        path.chmod(0o755)
        with pytest.raises(RootAdmissionError, match="private permissions"):
            with admit_root(path, forbidden=()):
                pytest.fail("shared root admitted")
        assert path.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and mode semantics")
def test_ancestor_and_final_links_are_refused(tmp_path):
    target = tmp_path / "target"
    mkdir_private(target)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    for selected in (link, link / "scratch"):
        with pytest.raises(RootAdmissionError, match="links"):
            with admit_root(selected, forbidden=(), create=True):
                pytest.fail("link admitted")
    assert not (target / "scratch").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory rename semantics")
def test_live_replacement_and_permission_drift_refuse_next_effect(tmp_path):
    path = tmp_path / "scratch"
    with admit_root(path, forbidden=(), create=True) as root:
        path.chmod(0o755)
        with pytest.raises(RootAdmissionError):
            root.create_child("no-create")
        path.chmod(0o700)
        path.rename(tmp_path / "original")
        mkdir_private(path)
        with pytest.raises(RootAdmissionError, match="replaced"):
            root.create_child("no-create")
    assert not (path / "no-create").exists()
    assert not (tmp_path / "original" / "no-create").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX ancestor permissions")
def test_replaceable_shared_ancestor_is_not_admitted(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    parent.chmod(0o777)
    with pytest.raises(RootAdmissionError, match="replaceable ancestor"):
        with admit_root(parent / "scratch", forbidden=(), create=True):
            pytest.fail("writable ancestor admitted")
    assert not (parent / "scratch").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows DACL validation")
def test_protected_dacl_with_foreign_read_access_is_not_private(tmp_path):
    import ctypes
    from ctypes import wintypes
    from agent_procutil._daemon_instance_owner import _apis, _current_sids

    path = tmp_path / "scratch"
    mkdir_private(path)
    advapi, kernel = _apis()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    user = _current_sids(os.getpid())[0]
    descriptor = ctypes.c_void_p()
    sddl = f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FR;;;WD)"
    assert advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None,
    )
    try:
        assert advapi.SetFileSecurityW(str(path), 0x80000004, descriptor)
    finally:
        kernel.LocalFree(descriptor)
    with pytest.raises(RootAdmissionError, match="another principal"):
        with admit_root(path, forbidden=()):
            pytest.fail("foreign read access admitted")
