from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


SCRIPT = (
    Path(__file__).parents[1] / "skills" / "capturing-client-requests"
    / "scripts" / "snapshot_wire.py"
)
spec = importlib.util.spec_from_file_location("snapshot_wire_mcp", SCRIPT)
assert spec is not None and spec.loader is not None
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)

pytestmark = pytest.mark.guard


def declaration(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name, "description": "PRIVATE_SYNTHETIC_DESCRIPTION",
            "parameters": {"type": "object"},
        },
    }


def resolve_pointer(report: dict[str, Any], pointer: str) -> Any:
    value: Any = report
    for token in pointer.removeprefix("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def test_duplicate_servers_and_tools_do_not_expand_uncapped_evidence(monkeypatch) -> None:
    monkeypatch.setattr(wire, "MAX_MCP_CORRELATIONS_PER_TOOL", 3)
    monkeypatch.setattr(wire, "MAX_MCP_CORRELATIONS", 7)
    report = wire.analyze_request(
        {"tools": [declaration("sample_lookup") for _ in range(12)]},
        {"mcp_servers": [
            {"name": "sample", "tools": ["sample_lookup"] * 20, "source": "example/config.json"}
            for _ in range(12)
        ]},
    )
    tools = report["tools"]["observed"]
    coverage = report["coverage"]["mcp"]
    assert sum(len(tool["mcp_name_correlations"]) for tool in tools) == 7
    assert all(len(tool["mcp_name_correlations"]) <= 3 for tool in tools)
    assert tools[0]["ownership_ambiguous"] is True
    assert tools[1]["ownership_ambiguous"] is True
    assert tools[2]["ownership_ambiguous"] is None
    assert tools[-1]["ownership_ambiguous"] is None
    assert tools[-1]["ownership_classification"] == "unknown"
    assert not coverage["complete"] and coverage["unsearched_candidate_pairs"] > 0
    assert coverage["correlations_retained"] == 7
    assert any(w["code"] == "mcp_correlation_evidence_truncated" for w in report["warnings"])
    references = [
        match for row in report["mcp_servers"]["configured"] for match in row["observed_name_matches"]
    ]
    assert len(references) == 7
    assert all("match_methods" not in reference for reference in references)
    for reference in references:
        canonical = resolve_pointer(report, reference["correlation_pointer"])
        assert canonical["match_methods"] == ["configured_tool_name"]
        assert canonical["verified"] is False
    assert all("observed_name_matches" in row for row in report["mcp_servers"]["name_correlated"])
    assert "PRIVATE_SYNTHETIC_DESCRIPTION" not in json.dumps(report)


def test_duplicate_configured_tool_names_alone_do_not_create_ambiguity() -> None:
    report = wire.analyze_request(
        {"tools": [declaration("sample_lookup")]},
        {"mcp_servers": [{"name": "sample", "tools": ["sample_lookup"] * 12}]},
    )
    tool = report["tools"]["observed"][0]
    assert tool["mcp_name_match_count"] == 1
    assert tool["ownership_ambiguous"] is False
    assert tool["ownership_classification"] == "mcp_name_correlated"
    assert report["coverage"]["mcp"]["complete"] is True


@pytest.mark.parametrize("spelling", [
    "sample__lookup", "sample-lookup", "sample.lookup", "sample/lookup", "mcp__sample__lookup",
])
def test_namespace_spellings_remain_unverified_naming_evidence(spelling: str) -> None:
    report = wire.analyze_request(
        {"tools": [declaration(spelling)]},
        {"mcp_servers": [{"name": "sample", "tools": []}]},
    )
    tool = report["tools"]["observed"][0]
    assert tool["mcp_name_correlations"][0]["match_methods"] == ["namespace_prefix"]
    assert tool["mcp_name_correlations"][0]["verified"] is False
    assert tool["ownership_classification"] == "mcp_name_correlated"
    assert report["mcp_servers"]["configured"][0]["connectivity"] == "unknown"
    assert report["mcp_servers"]["exact_server_ownership"] == "unknown"


def test_work_exhaustion_does_not_mean_no_matches_or_unambiguous(monkeypatch) -> None:
    monkeypatch.setattr(wire, "MAX_MCP_COMPARISON_CHARACTERS", 80)
    report = wire.analyze_request(
        {"tools": [declaration("sample_lookup" * 100)]},
        {"mcp_servers": [{"name": "sample", "tools": []}]},
    )
    tool = report["tools"]["observed"][0]
    assert tool["mcp_name_match_count"] == 0
    assert tool["mcp_name_match_count_is_lower_bound"] is True
    assert tool["ownership_ambiguous"] is None
    assert not tool["mcp_name_search_complete"]
    coverage = report["coverage"]["mcp"]
    assert not coverage["comparison_work_complete"] and not coverage["complete"]
    assert coverage["comparison_characters_charged"] <= 80
    assert not report["mcp_servers"]["configured"][0]["observed_name_matches_complete"]
    assert any(w["code"] == "mcp_comparison_work_limit" for w in report["warnings"])


@pytest.mark.parametrize("collector_complete, tool_names_complete", [
    (False, True), (True, False), (False, False),
])
def test_partial_collector_and_tool_name_inventory_preserve_positive_collisions(
    collector_complete: bool, tool_names_complete: bool,
) -> None:
    report = wire.analyze_request(
        {"tools": [declaration("view"), declaration("unknown")]},
        {
            "coverage": {"mcp_complete": collector_complete},
            "mcp_servers": [{"name": "sample", "tools": ["view"], "tool_names_complete": tool_names_complete}],
        },
    )
    builtin, unknown = report["tools"]["observed"]
    assert builtin["mcp_name_match_count"] == 1
    assert builtin["ownership_ambiguous"] is True
    assert builtin["ownership_classification"] == "ambiguous_name_match"
    assert report["builtin_tools"]["name_matches"][0]["ownership_ambiguous"] is True
    assert unknown["ownership_ambiguous"] is None
    assert report["coverage"]["mcp"]["collector_complete"] is collector_complete
    assert not report["coverage"]["mcp"]["candidate_inventory_complete"]
    assert not builtin["mcp_name_search_complete"]
    codes = {w["code"] for w in report["warnings"]}
    assert ("mcp_collector_incomplete" in codes) is (not collector_complete)
    assert ("mcp_tool_name_inventory_incomplete" in codes) is (not tool_names_complete)


def test_builtin_collision_is_definite_even_when_per_tool_search_stops(monkeypatch) -> None:
    monkeypatch.setattr(wire, "MAX_MCP_CORRELATIONS_PER_TOOL", 1)
    report = wire.analyze_request(
        {"tools": [declaration("view"), declaration("sample_lookup")]},
        {"mcp_servers": [
            {"name": "sample", "tools": ["view", "sample_lookup"]},
            {"name": "other", "tools": ["view", "sample_lookup"]},
        ]},
    )
    builtin, ordinary = report["tools"]["observed"]
    assert builtin["ownership_ambiguous"] is True
    assert ordinary["ownership_ambiguous"] is None
    assert ordinary["ownership_classification"] == "mcp_name_correlated"
    assert not ordinary["mcp_name_search_complete"]


def test_incomplete_collector_without_retained_servers_is_not_negative_evidence() -> None:
    report = wire.analyze_request(
        {"tools": [declaration("view"), declaration("unknown")]},
        {"coverage": {"mcp_complete": False}, "mcp_servers": []},
    )
    assert all(t["ownership_ambiguous"] is None for t in report["tools"]["observed"])
    assert all(not t["mcp_name_search_complete"] for t in report["tools"]["observed"])
    assert not report["coverage"]["mcp"]["complete"]
    assert report["mcp_servers"]["name_correlated"] == []


def test_server_and_configured_name_caps_are_explicit(monkeypatch) -> None:
    monkeypatch.setattr(wire, "MAX_MCP_SERVERS", 2)
    monkeypatch.setattr(wire, "MAX_MCP_TOOL_NAMES_PER_SERVER", 1)
    monkeypatch.setattr(wire, "MAX_MCP_TOOL_NAMES", 1)
    report = wire.analyze_request(
        {"tools": [declaration("retained"), declaration("omitted")]},
        {"mcp_servers": [
            {"name": "first", "tools": ["retained", "omitted"]},
            {"name": "second", "tools": ["omitted"]},
            {"name": "third", "tools": ["omitted"]},
        ]},
    )
    coverage = report["coverage"]["mcp"]
    assert coverage["servers_retained"] == 2 and coverage["servers_omitted_count"] == 1
    assert coverage["tool_name_entries_examined"] == 1
    assert coverage["unsearched_tool_name_entries"] == 2
    assert not coverage["candidate_inventory_complete"] and not coverage["complete"]
    assert report["tools"]["observed"][1]["ownership_ambiguous"] is None
    assert report["tools"]["observed"][0]["mcp_name_match_count"] == 1
    assert {w["code"] for w in report["warnings"]} >= {
        "mcp_tool_name_inventory_truncated", "mcp_server_inventory_truncated",
    }


def test_advertised_tool_reference_points_into_the_advertised_report_view() -> None:
    report = wire.analyze_request({
        "tools": [declaration("not-correlated")],
        "messages": [{"role": "system", "content":
            "<available_tools><tool><name>sample/lookup</name></tool></available_tools>"
        }],
    }, {"mcp_servers": [{"name": "sample", "tools": []}]})
    reference = report["mcp_servers"]["configured"][0]["observed_name_matches"][0]
    assert reference["correlation_pointer"].startswith("/tools/advertised/0/")
    assert resolve_pointer(report, reference["correlation_pointer"])["server_name"] == "sample"
