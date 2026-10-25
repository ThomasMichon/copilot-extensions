"""Repository tool test portfolio markers."""

import os

import pytest

from plugin_test_command_safety import install_command_guard

install_command_guard()


@pytest.fixture
def native_file_symlink():
    def create(path, target):
        try:
            path.symlink_to(target)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                pytest.skip("native file symlink creation requires Windows privilege")
            raise

    return create


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "runner_exhaustive: expanded environment-runner process scenarios (manual CI)",
    )
