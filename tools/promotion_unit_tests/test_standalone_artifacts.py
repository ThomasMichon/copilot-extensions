"""Pure identity, candidate inventory and build-hook refusal controls."""
from __future__ import annotations

import pytest

import promotion_python_artifacts as artifacts
from artifact_source_build import validate_static_build
from standalone_python_artifacts import artifact_label, project_key


@pytest.mark.parametrize("record,label,key", [
    ({"plugin": "agent-pull-requests"}, "agent-pull-requests", "agent-pull-requests"),
    ({"component": "pr-service"}, "standalone-pr-service", "standalone:pr-service"),
    ({"component": "fleet-manager"}, "standalone-fleet-manager", "standalone:fleet-manager"),
], ids=["plugin", "pr-service", "fleet-manager"])
def test_explicit_standalone_identity_preserves_plugin_names(record, label, key):
    assert artifact_label(record) == label
    assert project_key(record) == key


@pytest.mark.parametrize("record", [
    {"component": "../services/pr"},
    {"component": "services/pr"},
    {"component": "worktree-manager"},
    {"component": []},
    {"component": "pr-service", "plugin": "pr-service"},
], ids=["traversal", "path", "unregistered", "invalid-type", "plugin-alias"])
def test_unknown_or_masquerading_components_refuse(record):
    with pytest.raises(artifacts.PromotionArtifactError, match="identity"):
        artifacts._artifact_label(record)


def test_candidate_inventory_keeps_same_name_plugin_and_standalone_distinct(tmp_path):
    for relative in ("plugins/pr-service", "services/pr", "fleet-manager"):
        source = tmp_path / relative
        source.mkdir(parents=True)
        (source / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\nversion = "1.0"\n', encoding="utf-8",
        )
    assert artifacts.candidate_projects(tmp_path) == {
        "pr-service": "1.0", "standalone:pr-service": "1.0", "standalone:fleet-manager": "1.0",
    }


@pytest.mark.parametrize("failure", ["missing", "malformed"])
def test_registered_candidate_metadata_cannot_be_silently_omitted(tmp_path, failure):
    source = tmp_path / "services" / "pr"
    source.mkdir(parents=True)
    if failure == "malformed":
        (source / "pyproject.toml").write_text("not TOML", encoding="utf-8")
    with pytest.raises(artifacts.PromotionArtifactError, match="invalid standalone"):
        artifacts.candidate_projects(tmp_path)


@pytest.mark.parametrize("kind", ["untrusted", "alternate", "missing-trusted", "trusted"])
def test_static_admission_allows_only_exact_selected_hook(tmp_path, kind):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        '[project]\nname = "fixture"\nversion = "1.0"\n', encoding="utf-8",
    )
    approved = b"raise RuntimeError('not executed by admission')\n"
    if kind != "missing-trusted":
        (tmp_path / ("setup.cfg" if kind == "alternate" else "setup.py")).write_bytes(
            b"untrusted" if kind == "untrusted" else approved,
        )
    if kind == "trusted":
        assert validate_static_build(tmp_path, trusted_setup=approved)["project"]["name"] == "fixture"
    else:
        with pytest.raises(artifacts.PromotionArtifactError):
            validate_static_build(tmp_path, trusted_setup=approved)
