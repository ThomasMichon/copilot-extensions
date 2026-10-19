from __future__ import annotations

from pathlib import Path

import pytest

from machine_transport import ControlPlaneError, load_control_plane, parse_control_plane


def test_pair_preserves_roles_and_legacy_launch_projection():
    raw = {
        "control_plane": {
            "project": "example-harness",
            "harness": "example-harness",
            "knowledge": "example-knowledge",
        }
    }
    pair = parse_control_plane(raw)
    assert pair is not None
    assert pair.to_dict() == {
        "harness": "example-harness",
        "knowledge": "example-knowledge",
        "legacy_project": "example-harness",
        "paired": True,
        "legacy_compatible": True,
    }
    assert raw["control_plane"]["project"] == pair.harness
    assert raw["control_plane"]["project"] != pair.knowledge


@pytest.mark.parametrize(
    "declaration",
    ["example-control", " example-control ", {"project": "example-control"}],
)
def test_legacy_declaration_does_not_invent_a_knowledge_repository(declaration):
    result = parse_control_plane({"control_plane": declaration})
    assert result is not None
    assert result.harness == "example-control"
    assert result.legacy_project == result.harness
    assert result.knowledge is None
    assert not result.paired
    assert result.legacy_compatible


def test_pair_without_legacy_projection_is_not_reported_old_daemon_compatible():
    result = parse_control_plane(
        {"control_plane": {"harness": "example-harness", "knowledge": "example-knowledge"}}
    )
    assert result is not None
    assert result.paired
    assert result.legacy_project is None
    assert not result.legacy_compatible


@pytest.mark.parametrize("declaration", [None, "", " ", {}])
def test_absent_declaration_stays_absent(declaration):
    assert parse_control_plane({"control_plane": declaration}) is None


@pytest.mark.parametrize(
    "declaration",
    [
        False,
        5,
        ["example-harness", "example-knowledge"],
        {"harness": "example-harness"},
        {"knowledge": "example-knowledge"},
        {"harness": "example-harness", "knowledge": ""},
        {"harness": "example-harness", "knowledge": "EXAMPLE-HARNESS"},
        {"harness": {"repo": "example-harness"}, "knowledge": "example-knowledge"},
        {"harness": "example-harness", "knowledge": "../state"},
        {"harness": "example-harness", "knowledge": "state\nother"},
        {"project": {"harness": "example-harness"}},
        {
            "project": "example-knowledge",
            "harness": "example-harness",
            "knowledge": "example-knowledge",
        },
        {
            "project": "EXAMPLE-HARNESS",
            "harness": "example-harness",
            "knowledge": "example-knowledge",
        },
        {"harness": "example-harness", "knowledge": "example-knowledge", "unknown": True},
    ],
)
def test_invalid_roles_refuse_without_coercion_or_fallback(declaration):
    with pytest.raises(ControlPlaneError):
        parse_control_plane({"control_plane": declaration})


def test_real_yaml_consumer_retains_pair_and_old_daemon_scalar(tmp_path: Path):
    source = tmp_path / "machines.yaml"
    source.write_text(
        "control_plane:\n"
        "  project: example-harness\n"
        "  harness: example-harness\n"
        "  knowledge: example-knowledge\n"
        "machines: {}\n",
        encoding="utf-8",
    )
    result = load_control_plane(source)
    assert result is not None
    assert result.harness == "example-harness"
    assert result.knowledge == "example-knowledge"
    assert result.legacy_compatible


@pytest.mark.parametrize("text", ["", "[]", "false", "control_plane: ["])
def test_bad_yaml_or_root_is_explicit(tmp_path: Path, text: str):
    source = tmp_path / "machines.yaml"
    source.write_text(text, encoding="utf-8")
    with pytest.raises(ControlPlaneError):
        load_control_plane(source)


def test_missing_source_is_not_a_successful_absent_relationship(tmp_path: Path):
    with pytest.raises(ControlPlaneError, match="cannot read"):
        load_control_plane(tmp_path / "missing.yaml")
