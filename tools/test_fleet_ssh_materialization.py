"""The optional Fleet SSH closure is packaged, not cross-plugin vendoring."""

from pathlib import Path
import shutil

import materialize_main
import seasoned_uv  # noqa: F401 -- select the canonical policy reader
from installer_dependency_policy import _toml


def test_actual_fleet_ssh_metadata_survives_release_materialization(tmp_path):
    checkout = tmp_path / "checkout"
    source = checkout / "fleet-manager"
    source.mkdir(parents=True)
    original = Path(__file__).resolve().parents[1] / "fleet-manager" / "pyproject.toml"
    shutil.copy2(original, source / "pyproject.toml")
    release = tmp_path / "release"
    destination = release / "fleet-manager"
    destination.mkdir(parents=True)
    shutil.copy2(original, destination / "pyproject.toml")
    before = (destination / "pyproject.toml").read_bytes()
    result = materialize_main.materialize_uv_editable_ref_into(
        source_consumer_dir=source, dest_consumer_dir=destination,
        canonical_root=checkout, dest_root=release,
    )
    assert not any(line.startswith("SKIP") for line in result), result
    assert (destination / "pyproject.toml").read_bytes() == before
    assert not (destination / "libs" / "agent-ssh").exists()
    metadata = _toml(destination / "pyproject.toml")
    assert metadata["project"]["dependencies"] == []
    requirements = metadata["project"]["optional-dependencies"]["ssh-client"]
    assert any(value.startswith("agent-ssh>=") for value in requirements)
    assert "agent-single-instance-lease" in requirements
    assert all("@" not in value and "../" not in value for value in requirements)
