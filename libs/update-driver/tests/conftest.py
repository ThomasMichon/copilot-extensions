"""Keep mutable fixtures out of source retained by concurrent installer checks."""

import pytest


@pytest.fixture
def area(tmp_path_factory):
    return tmp_path_factory.mktemp("ud")
