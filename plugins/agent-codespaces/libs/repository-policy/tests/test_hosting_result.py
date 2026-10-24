from __future__ import annotations

import pytest

from repository_policy import AgentHostingError, validate_hosting_result


def answer(**changes) -> dict:
    return {
        "version": 1, "allowed": True, "repository": "/workspace",
        "expose_agent": None, "sources": [], "denied_by": [],
        **changes,
    }


@pytest.mark.parametrize("value", [None, True])
def test_complete_admission(value) -> None:
    validate_hosting_result(answer(expose_agent=value))


@pytest.mark.parametrize("changes", [
    {"version": True}, {"version": 1.0}, {"version": "1"}, {"version": 2},
    {"allowed": False}, {"allowed": 1}, {"allowed": "true"},
    {"repository": ""}, {"repository": " "}, {"repository": None},
    {"expose_agent": False}, {"expose_agent": 1}, {"expose_agent": "true"},
    {"sources": "config.yaml"}, {"sources": [None]}, {"sources": [""]},
    {"sources": [" "]}, {"denied_by": {}}, {"denied_by": None},
    {"denied_by": ["config.yaml"]}, {"error": ""}, {"code": None},
])
def test_malformed_success_is_not_admission(changes) -> None:
    with pytest.raises(AgentHostingError, match="version-1"):
        validate_hosting_result(answer(**changes))


@pytest.mark.parametrize("key", [
    "version", "allowed", "repository", "expose_agent", "sources", "denied_by",
])
def test_incomplete_success_is_not_admission(key) -> None:
    value = answer()
    del value[key]
    with pytest.raises(AgentHostingError):
        validate_hosting_result(value)


@pytest.mark.parametrize("value", [None, True, [], "allowed"])
def test_non_object_is_not_admission(value) -> None:
    with pytest.raises(AgentHostingError):
        validate_hosting_result(value)
