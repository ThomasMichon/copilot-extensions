"""Version reporting tests."""

from importlib.metadata import version

import agent_machines


def test_version_matches_package_metadata() -> None:
    assert agent_machines.__version__ == version("agent-machines")
