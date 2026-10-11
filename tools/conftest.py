"""Repository tool test portfolio markers."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "runner_exhaustive: expanded environment-runner process scenarios (manual CI)",
    )
