from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from test_session_stamp_security import _hook, _paths_module, _run, _stamp_module
from stamp_fixture import no_shared_write_umask  # noqa: F401


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace permissions")
@pytest.mark.parametrize(("owner", "mode", "accepted"), [
    ("self", 0o755, True),
    ("root", 0o755, True),
    ("other", 0o755, False),
    ("self", 0o775, False),
    ("self", 0o1777, True),
    ("root", 0o1777, True),
    ("other", 0o1777, False),
])
def test_posix_ancestor_private_policy(owner: str, mode: int, accepted: bool) -> None:
    uid = {"self": os.geteuid(), "root": 0, "other": os.geteuid() + 100000}[owner]
    info = os.stat_result((stat.S_IFDIR | mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))
    module = _paths_module()
    assert module._posix_ancestor_private(info) is accepted


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace permissions")
def test_sticky_shared_directory_is_not_a_publication_root() -> None:
    info = os.stat_result(
        (stat.S_IFDIR | 0o1777, 0, 0, 1, os.geteuid(), 0, 0, 0, 0, 0)
    )
    assert not _paths_module()._posix_private(info)


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace mutation permissions")
@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_actual_hook_refuses_shared_writable_parent_before_creating_root(
    tmp_path: Path, shell: str,
) -> None:
    command, environment, repo = _hook(tmp_path, shell)
    parent = tmp_path / "shared"
    parent.mkdir()
    parent.chmod(0o777)
    root = parent / "uncreated-root"
    environment["AGENT_INDEX_HOME"] = str(root)
    result = _run(command, environment, repo)
    assert result.returncode != 0
    assert result.stdout == "{}"
    assert "ancestor permits untrusted mutation" in result.stderr
    assert list(parent.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace mutation permissions")
def test_publication_rechecks_parent_namespace_permissions(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir()
    root = parent / "root"
    root.mkdir(mode=0o755)
    module = _stamp_module()
    with module.Directory(root) as directory:
        parent.chmod(0o777)
        with pytest.raises(ValueError, match="ancestor permits untrusted mutation"):
            directory.publish("payload-dir", b"not published")
    assert list(root.iterdir()) == []
