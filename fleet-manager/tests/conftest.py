"""Immutable build inputs shared by independent installed-package contracts."""

import pytest


@pytest.fixture(scope="session")
def wheel(tmp_path_factory):
    from test_client_distribution import build, project_at

    root = tmp_path_factory.mktemp("client-wheel")
    project, library = project_at(root)
    output = root / "artifacts"
    result = build(project, output)
    assert result.returncode == 0, result.stderr
    artifact = next(output.glob("*.whl"))
    original = artifact.read_bytes()
    yield artifact, library
    assert artifact.read_bytes() == original
