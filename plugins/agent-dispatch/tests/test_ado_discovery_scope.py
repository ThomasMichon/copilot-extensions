"""Tests for Azure DevOps work-item discovery scoping."""

import pytest

from agent_dispatch.ado_discovery_scope import (
    validate_discovery_scope,
    wiql_scope_clauses,
)
from agent_dispatch.registrar import RegistrarError


def test_validate_discovery_scope_returns_none_when_absent():
    assert validate_discovery_scope({}, provider="azure-devops") is None


def test_validate_discovery_scope_rejects_non_azure_devops_provider():
    with pytest.raises(RegistrarError, match="only supported"):
        validate_discovery_scope(
            {"discovery_scope": {"area_path": "x"}}, provider="github"
        )


def test_validate_discovery_scope_rejects_unknown_keys():
    with pytest.raises(RegistrarError, match="unknown key"):
        validate_discovery_scope(
            {"discovery_scope": {"bogus": 1}}, provider="azure-devops"
        )


def test_validate_discovery_scope_requires_at_least_one_dimension():
    with pytest.raises(RegistrarError, match="must narrow by at least one"):
        validate_discovery_scope({"discovery_scope": {}}, provider="azure-devops")


def test_validate_discovery_scope_rejects_non_positive_max_age_days():
    with pytest.raises(RegistrarError, match="max_age_days"):
        validate_discovery_scope(
            {"discovery_scope": {"max_age_days": 0}}, provider="azure-devops"
        )


def test_validate_discovery_scope_normalizes_full_config():
    scope = validate_discovery_scope(
        {
            "discovery_scope": {
                "work_item_types": ["Bug", "Task"],
                "area_path": "Proj\\Team",
                "max_age_days": 30,
            }
        },
        provider="azure-devops",
    )
    assert scope == {
        "work_item_types": ["Bug", "Task"],
        "area_path": "Proj\\Team",
        "max_age_days": 30,
    }


def test_wiql_scope_clauses_empty_without_scope():
    assert wiql_scope_clauses(None) == ""


def test_wiql_scope_clauses_combines_all_dimensions():
    clause = wiql_scope_clauses(
        {
            "work_item_types": ["Bug", "Task"],
            "area_path": "Proj\\Team",
            "max_age_days": 30,
        }
    )
    assert "[System.WorkItemType] = 'Bug'" in clause
    assert "[System.WorkItemType] = 'Task'" in clause
    assert "[System.AreaPath] Under 'Proj\\Team'" in clause
    assert "[System.CreatedDate] >=" in clause


def test_wiql_scope_clauses_escapes_single_quotes():
    clause = wiql_scope_clauses({"area_path": "O'Brien\\Team"})
    assert "O''Brien\\Team" in clause
