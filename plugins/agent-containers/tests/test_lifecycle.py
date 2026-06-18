"""Tests for docker discovery parsing and fleet index allocation."""

from __future__ import annotations

from agent_containers.config import FLEET_LABEL, ContainersConfig
from agent_containers.fleet import _next_indices
from agent_containers.lifecycle import (
    DockerContainerInfo,
    _is_fleet_member,
    _parse_labels,
    _row_to_info,
)


def test_parse_labels():
    labels = _parse_labels("a=1,b=two,devcontainer.local_folder=/x/y")
    assert labels["a"] == "1"
    assert labels["b"] == "two"
    assert labels["devcontainer.local_folder"] == "/x/y"


def test_parse_labels_empty():
    assert _parse_labels("") == {}


def test_is_fleet_member_by_fleet_label():
    c = ContainersConfig()
    assert _is_fleet_member({FLEET_LABEL: "myrepo"}, "anything", c)


def test_is_fleet_member_by_devcontainer_label():
    c = ContainersConfig()
    assert _is_fleet_member({"devcontainer.local_folder": "/x"}, "img", c)


def test_is_fleet_member_by_image_prefix():
    c = ContainersConfig()
    assert _is_fleet_member({}, "vsc-myrepo-abc123", c)


def test_is_not_fleet_member():
    c = ContainersConfig()
    assert not _is_fleet_member({}, "ubuntu:22.04", c)


def _info(name: str) -> DockerContainerInfo:
    return DockerContainerInfo(
        name=name, container_id="x", image="img", state="exited", status=""
    )


def test_next_indices_empty():
    assert _next_indices([], "myrepo", 3) == [1, 2, 3]


def test_next_indices_fills_gaps():
    existing = [_info("myrepo-1"), _info("myrepo-3")]
    assert _next_indices(existing, "myrepo", 2) == [2, 4]


def test_container_repo_from_local_folder():
    c = DockerContainerInfo(
        name="x", container_id="i", image="img", state="running", status="",
        local_folder="C:\\Users\\me\\src\\myrepo",
    )
    assert c.repo == "myrepo"
    assert c.is_running


def test_row_to_info_parses_fleet_member():
    c = ContainersConfig()
    line = "myrepo-1\tabc123\tvsc-myrepo-x\trunning\tUp 2 minutes\t" + \
        f"{FLEET_LABEL}=myrepo,devcontainer.local_folder=/x/myrepo"
    info = _row_to_info(line, c)
    assert info is not None
    assert info.name == "myrepo-1"
    assert info.state == "running"
    assert info.fleet == "myrepo"
    assert info.local_folder == "/x/myrepo"


def test_row_to_info_skips_non_member():
    c = ContainersConfig()
    line = "rando\tid\tubuntu:22.04\trunning\tUp\t"
    assert _row_to_info(line, c) is None


def test_row_to_info_malformed_returns_none():
    c = ContainersConfig()
    assert _row_to_info("too\tfew", c) is None
