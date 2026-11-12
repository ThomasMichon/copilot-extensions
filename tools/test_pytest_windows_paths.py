"""Optional pytest convenience links never weaken native symlink controls."""

import os
from types import SimpleNamespace

import _pytest.pathlib
import _pytest.tmpdir
import pytest

from pytest_portfolio_guard import configure_temp_links


def test_windows_suppresses_only_current_link_helper_and_restores_it():
    original = _pytest.pathlib._force_symlink
    tmpdir_original = getattr(_pytest.tmpdir, "_force_symlink", None)
    real_symlink = os.symlink
    cleanup = []
    configure_temp_links(SimpleNamespace(add_cleanup=cleanup.append), platform="win32")
    try:
        assert _pytest.pathlib._force_symlink is not original
        assert _pytest.pathlib._force_symlink("unused", "unused", "unused") is None
        if tmpdir_original is not None:
            assert _pytest.tmpdir._force_symlink("unused", "unused", "unused") is None
        else:
            assert not hasattr(_pytest.tmpdir, "_force_symlink")
        assert os.symlink is real_symlink
        assert len(cleanup) == 1
    finally:
        for callback in cleanup:
            callback()
    assert _pytest.pathlib._force_symlink is original
    assert getattr(_pytest.tmpdir, "_force_symlink", None) is tmpdir_original


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_other_platforms_keep_pytest_current_links(platform):
    original = _pytest.pathlib._force_symlink
    cleanup = []
    configure_temp_links(SimpleNamespace(add_cleanup=cleanup.append), platform=platform)
    assert _pytest.pathlib._force_symlink is original
    assert not cleanup
