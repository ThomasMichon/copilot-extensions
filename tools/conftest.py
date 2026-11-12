"""Repository tool test portfolio markers."""

import os

import pytest

from plugin_test_command_safety import install_command_guard
from pytest_portfolio_guard import configure_temp_links

install_command_guard()


@pytest.fixture
def native_file_symlink():
    def create(path, target, *, target_is_directory=False):
        try:
            path.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                pytest.skip("native symlink creation requires Windows privilege")
            raise

    return create


def pytest_configure(config):
    configure_temp_links(config)
    config.addinivalue_line(
        "markers",
        "runner_exhaustive: expanded environment-runner process scenarios (manual CI)",
    )
