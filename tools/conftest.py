"""Repository tool test portfolio markers."""

import os

import pytest


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
    config.addinivalue_line(
        "markers",
        "runner_exhaustive: expanded environment-runner process scenarios (manual CI)",
    )
