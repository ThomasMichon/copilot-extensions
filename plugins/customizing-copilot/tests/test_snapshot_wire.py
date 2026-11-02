from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


SCRIPT = (
    Path(__file__).parents[1] / "skills" / "capturing-client-requests"
    / "scripts" / "snapshot_wire.py"
)
spec = importlib.util.spec_from_file_location("snapshot_wire", SCRIPT)
assert spec is not None and spec.loader is not None
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)

pytestmark = pytest.mark.guard


def function(
    name: str, properties: dict[str, Any] | None = None, description: str = "",
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {}},
        },
    }


def test_search_domains_and_constant_selectors_are_not_mislabeled() -> None:
    report = wire.analyze_request({
        "messages": [], "tools": [
            function("web_search", {"query": {"type": "string"}}),
            function("tool_search", {"query": {"type": "string"}}),
            function("task_search", {"agent_type": {"oneOf": [
                {"const": "example-one"}, {"const": "example-two"},
            ]}}),
        ],
    }, {})
    surfaces = {row["name"]: row for row in report["discovery"]["surfaces"]}
    assert surfaces["web_search"]["kinds"] == ["search_surface"]
    assert "tool_search" in surfaces["tool_search"]["kinds"]
    assert "task_search" in surfaces["task_search"]["kinds"]
    selector = surfaces["task_search"]["selectors"][0]
    assert selector["enum_names"] == ["example-one", "example-two"]
    assert selector["free_string"] is False


def test_loader_removed_edge_newlines_are_an_explicit_body_transformation() -> None:
    report = wire.analyze_request(
        {"messages": [{"role": "system", "content": "# Synthetic instructions"}]},
        {"instructions": [{
            "path": "example/file.instructions.md",
            "text": "---\napplyTo: '**'\n---\n\n# Synthetic instructions\n",
        }]},
    )
    section = report["instruction_files"]
    assert section["present_paths"] == ["example/file.instructions.md"]
    assert any("edge_newlines_trimmed" in match["match_method"]
               for match in section["files"][0]["matches"])
    mixed = wire.analyze_request(
        {"messages": [{"role": "system", "content": "synthetic\nsynthetic"}]},
        {"instructions": [{"path": "example/file.md", "text": "synthetic\n"}]},
    )
    assert len(mixed["instruction_files"]["files"][0]["matches"]) == 2


def test_unicode_blocks_byte_spans_and_no_mutation_or_content_disclosure() -> None:
    request = {
        "messages": [
            {"role": "system", "content": [
                {"type": "text", "text": "é🙂\r\nPRIVATE_INSTRUCTION\r\n"},
                {"type": "image_url", "image_url": {"url": "https://example.com/image"}},
                {"type": "input_text", "text": "SECOND_PRIVATE_BODY"},
            ]},
            {"role": "user", "content": "UNRELATED_PRIVATE_USER_TEXT"},
        ],
    }
    inventory = {"instructions": [
        {"path": "example\\rule.md", "text": "PRIVATE_INSTRUCTION\n"},
    ]}
    original_request, original_inventory = deepcopy(request), deepcopy(inventory)
    report = wire.analyze_request(request, inventory)
    match = report["instruction_files"]["files"][0]["matches"][0]
    assert match["match_method"] == "full_file_crlf_normalized"
    assert match["evidence"] == {
        "pointer": "/messages/0/content/0/text",
        "start_utf8_byte": len("é🙂\r\n".encode()),
        "end_utf8_byte": len("é🙂\r\nPRIVATE_INSTRUCTION\r\n".encode()),
    }
    system_bytes = len("é🙂\r\nPRIVATE_INSTRUCTION\r\nSECOND_PRIVATE_BODY".encode())
    assert report["builtin_prompt"]["system_message_utf8_bytes"] == system_bytes
    assert report["builtin_prompt"]["source_matched_union_utf8_bytes"] == len(
        "PRIVATE_INSTRUCTION\r\n".encode()
    )
    assert report["request"]["canonical_json_utf8_bytes"] == len(json.dumps(
        request, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode())
    assert request == original_request and inventory == original_inventory
    encoded = json.dumps(report)
    assert all(value not in encoded for value in (
        "PRIVATE_INSTRUCTION", "SECOND_PRIVATE_BODY", "UNRELATED_PRIVATE_USER_TEXT",
    ))
    assert {"code": "uninterpreted_content_block", "pointer": "/messages/0/content/1"} in report["warnings"]


def test_instruction_body_selector_and_developer_are_distinguished() -> None:
    frontmatter = "---\napplyTo: '**'\n---\n"
    body = "SYNTHETIC_RULE_BODY\n"
    selector = (
        '<!-- copilot-extension-instruction-projection '
        '{"deliveryKind":"selector","destination":"example/rules.md"} -->'
    )
    report = wire.analyze_request({
        "messages": [
            {"role": "system", "content": selector},
            {"role": "developer", "content": body + body},
        ],
    }, {"instructions": [{"path": "example/rules.md", "text": frontmatter + body}]})
    item = report["instruction_files"]["files"][0]
    assert len(item["matches"]) == 2
    assert {m["match_method"] for m in item["matches"]} == {"frontmatter_stripped_body_exact"}
    assert not item["file_read_proven"] and not item["origin_authenticated"]
    assert report["instruction_files"]["selector_references"][0]["full_content_proven"] is False
    assert report["builtin_prompt"]["source_matched_union_utf8_bytes"] == 0
    selector_only = wire.analyze_request(
        {"messages": [{"role": "system", "content": selector}]},
        {"instructions": [{"path": "example/rules.md", "text": frontmatter + body}]},
    )
    assert not selector_only["instruction_files"]["files"][0]["full_content_present"]


def test_nested_overlapping_identical_candidates_union_without_losing_ambiguity() -> None:
    source = "αababa"
    report = wire.analyze_request(
        {"messages": [{"role": "system", "content": source}]},
        {"instructions": [
            {"path": "example/outer.md", "text": source},
            {"path": "example/inner.md", "text": "aba"},
            {"path": "example/inner.md", "text": "aba"},
            {"path": "example/duplicate.md", "text": "aba"},
        ]},
    )
    files = report["instruction_files"]["files"]
    assert len(files) == 4 and len(files[1]["matches"]) == 2
    match = files[1]["matches"][0]
    assert match["ambiguous"] and len(match["candidate_sources"]) == 3
    assert len(match["candidate_paths"]) == 2
    assert report["builtin_prompt"]["source_matched_union_utf8_bytes"] == len(source.encode())
    assert report["builtin_prompt"]["unclassified_system_message_utf8_bytes"] == 0
    assert report["builtin_prompt"]["exact_utf8_bytes"] is None


def test_full_file_frontmatter_and_empty_candidates() -> None:
    text = "---\r\nname: sample\r\n---\r\névidence\r\n"
    report = wire.analyze_request(
        {"messages": [{"role": "system", "content": text}]},
        {"instructions": [
            {"path": "example/full.md", "text": text},
            {"path": "example/empty.md", "text": ""},
        ]},
    )
    methods = {m["match_method"] for m in report["instruction_files"]["files"][0]["matches"]}
    assert methods == {"full_file_exact", "frontmatter_stripped_body_exact"}
    assert not report["instruction_files"]["files"][1]["matches"]
    assert report["builtin_prompt"]["source_matched_union_utf8_bytes"] == len(text.encode())


def test_rendered_skills_plus_name_only_aliases_and_frontmatter_are_not_loaded() -> None:
    entries = "".join(
        f"<skill><name>sample-{i}</name><description>PRIVATE_DESCRIPTION_{i}</description>"
        "<location>project</location></skill>" for i in range(21)
    )
    region = (
        "<available_skills>" + entries
        + "\nAdditional skills available (invoke by name): sample-0, sample-extra, sample-alias\n"
        + "</available_skills>"
    )
    report = wire.analyze_request(
        {"messages": [{"role": "system", "content": region}]},
        {"skills": [
            {
                "path": "example/skill/SKILL.md", "name": "sample-0",
                "description": "PRIVATE_DESCRIPTION_0",
                "frontmatter": "---\nname: sample-0\n---", "aliases": ["sample-alias"],
            },
            {"path": "example/other/SKILL.md", "name": "sample-0", "aliases": []},
        ]},
    )
    skills = report["skills"]
    assert skills["rendered_metadata_count"] == 21 and skills["name_only_count"] == 3
    first = skills["observed"][0]
    assert first["observed_fields"] == ["name", "description", "location"]
    assert first["ambiguous"] and len(first["source_matches"]) == 2
    assert first["source_matches"][0]["matching_source_fields"] == ["name", "description"]
    assert not first["source_matches"][0]["frontmatter_text_present"]
    alias = next(r for r in skills["observed"] if r["name"] == "sample-alias")
    assert alias["source_matches"][0]["match_methods"] == ["alias_exact"]
    assert alias["observed_fields"] == ["name"] and not alias["loaded_body_proven"]
    assert "PRIVATE_DESCRIPTION" not in json.dumps(report)


def test_agent_colons_multiline_descriptions_enums_and_footer_boundary() -> None:
    description = (
        "Available agent types:\n"
        "- **inspect**: PRIVATE_INSPECT\n"
        "\nUser-provided custom agents:\n"
        "- **addon:worker**: PRIVATE_WORKER\n"
        "  continued detail\n"
        "\n"
        "Footer rules are not agent descriptions.\n"
        "- **footer:not-agent**: must not become an agent\n"
    )
    tool = function("task", {
        "agent_type": {"type": "string", "enum": ["inspect", "addon:worker"]},
    }, description)
    report = wire.analyze_request({"tools": [tool]}, {"agents": [{
        "path": "example/worker.agent.md", "name": "addon:worker",
        "description": "PRIVATE_WORKER\ncontinued detail", "frontmatter": "PRIVATE_FRONTMATTER",
    }]})
    rendered = [
        r for r in report["subagents"]["observed"] if r["representation"] == "rendered_metadata"
    ]
    assert [r["name"] for r in rendered] == ["inspect", "addon:worker"]
    assert report["subagents"]["enum_name_count"] == 2
    assert rendered[1]["source_matches"][0]["match_methods"] == [
        "name_exact", "description_whitespace_normalized",
    ]
    ev = rendered[1]["evidence"]
    recovered = description.encode()[ev["start_utf8_byte"]:ev["end_utf8_byte"]].decode()
    assert "continued detail" in recovered and "Footer" not in recovered
    assert all(text not in json.dumps(report) for text in (
        "PRIVATE_INSPECT", "PRIVATE_WORKER", "PRIVATE_FRONTMATTER", "continued detail",
    ))
    assert report["discovery"]["surfaces"][0]["referenced_metadata_counts"]["subagents"] == 2


def test_unindented_agent_continuations_do_not_hide_later_registered_records() -> None:
    description = (
        "User-provided custom agents:\n"
        "- **addon:first**: First line.\n"
        "Unindented continuation.\n\nExamples:\n- not a new agent.\n"
        "- **addon:second**: Second line.\n"
        "Another unindented continuation.\n"
        "Footer rules are not agent descriptions.\n"
        "- **footer:not-an-agent**: Ignore this footer bullet.\n"
    )
    report = wire.analyze_request({"tools": [function("task", {
        "agent_type": {"type": "string", "enum": ["addon:first", "addon:second"]},
    }, description)]}, {"agents": [{
        "name": "addon:second", "path": "example/second.agent.md",
        "description": "Second line.\nAnother unindented continuation.",
    }]})
    rendered = [r for r in report["subagents"]["observed"] if r["representation"] == "rendered_metadata"]
    assert [r["name"] for r in rendered] == ["addon:first", "addon:second"]
    assert {"description_exact", "description_whitespace_normalized"} & set(
        rendered[1]["source_matches"][0]["match_methods"]
    )
    span = rendered[1]["evidence"]
    captured = description.encode()[span["start_utf8_byte"]:span["end_utf8_byte"]].decode()
    assert "Footer" not in captured


def test_optional_agent_and_advertised_tool_containers() -> None:
    report = wire.analyze_request({
        "messages": [{"role": "system", "content":
            "<available_agents><agent><name>sample-agent</name>"
            "<description>PRIVATE_AGENT_DESCRIPTION</description></agent></available_agents>"
            "<available_tools><tool><name>sample__lookup</name>"
            "<description>PRIVATE_TOOL_DESCRIPTION</description></tool></available_tools>"
        }],
    }, {"mcp_servers": [{"name": "sample", "tools": [], "source": "example/config.json"}]})
    assert report["subagents"]["rendered_metadata_count"] == 1
    advertised = report["tools"]["advertised"][0]
    assert advertised["schema_status"] == "advertised_only"
    assert advertised["mcp_name_correlations"][0]["match_methods"] == ["namespace_prefix"]
    assert not report["tools"]["observed"]
    assert "PRIVATE_" not in json.dumps(report)


def test_function_custom_and_mcp_builtin_name_collisions_remain_unverified() -> None:
    request = {"tools": [
        function("view"), function("sample__lookup"), function("unclassified"),
        {"type": "custom", "custom": {
            "name": "apply_patch", "description": "PRIVATE_CUSTOM_DESCRIPTION",
            "format": {"type": "grammar", "syntax": "lark", "definition": "PRIVATE_GRAMMAR"},
        }},
        {"type": "function", "name": "direct", "parameters": {"type": "object"}},
        {"type": "function", "function": {"name": "declared"}},
    ]}
    report = wire.analyze_request(request, {"mcp_servers": [
        {"name": "sample", "tools": ["view"], "source": "example/a.json"},
        {"name": "second", "tools": ["view"], "source": "example/b.json"},
    ]})
    tools = report["tools"]["observed"]
    assert len(tools) == 6 and tools[3]["schema_kind"] == "grammar"
    assert tools[4]["pointer"] == "/tools/4" and tools[5]["schema_status"] == "declaration_only"
    assert tools[0]["ownership_ambiguous"] and len(tools[0]["mcp_name_correlations"]) == 2
    assert tools[1]["mcp_name_correlations"][0]["verified"] is False
    assert tools[2]["ownership"] == "unknown"
    assert {m["name"] for m in report["builtin_tools"]["name_matches"]} == {"view", "apply_patch"}
    assert all(m["match_method"] == "name_based" for m in report["builtin_tools"]["name_matches"])
    assert all(t["origin_authenticated"] is False for t in tools)
    assert "PRIVATE_" not in json.dumps(report)


def test_search_task_skill_selectors_and_no_fabricated_search_results() -> None:
    request = {"tools": [
        function("tool_search", {"query": {"type": "string"}, "limit": {"type": "integer"}}),
        function("task", {"agent_type": {"anyOf": [
            {"type": "string", "enum": ["addon:worker"]}, {"type": "null"},
        ]}}),
        function("skill", {"skill": {"type": "string"}}),
        function("find_tool", {"tool_name": {"type": ["string", "null"]}}),
    ]}
    report = wire.analyze_request(request, {})
    surfaces = {s["name"]: s for s in report["discovery"]["surfaces"]}
    assert surfaces["tool_search"]["kinds"] == ["tool_search"]
    assert [p["name"] for p in surfaces["tool_search"]["input_properties"]] == ["limit", "query"]
    task = surfaces["task"]["selectors"][0]
    assert task["enum_names"] == ["addon:worker"] and not task["free_string"]
    assert surfaces["skill"]["selectors"][0]["free_string"] is True
    assert surfaces["find_tool"]["selectors"][0]["free_string"] is True
    assert "catalog_surface" in surfaces["find_tool"]["kinds"]
    assert all(s["results_observed"] is None for s in surfaces.values())
    assert not report["discovery"]["complete_deferred_catalog_proven"]


def test_memory_mentions_and_marker_candidates_never_prove_exact_records_or_builtin() -> None:
    text = "Ordinary memory guidance.\n<memories><memory>PRIVATE_MEMORY</memory></memories>"
    report = wire.analyze_request({"messages": [{"role": "system", "content": text}]}, {})
    assert report["memories"]["exact_count"] is None
    assert report["memories"]["exact_utf8_bytes"] is None
    assert report["memories"]["candidate_markup"]
    assert report["builtin_prompt"]["system_message_utf8_bytes"] == len(text.encode())
    assert report["builtin_prompt"]["unclassified_system_message_utf8_bytes"] == len(text.encode())
    assert report["builtin_prompt"]["exact_utf8_bytes"] is None
    assert "PRIVATE_MEMORY" not in json.dumps(report)
    empty = wire.analyze_request({}, {})
    assert empty["memories"]["candidate_markup"] == []
    assert empty["memories"]["exact_count"] is None
    assert empty["builtin_prompt"]["exact_utf8_bytes"] is None


def test_unknown_shapes_and_inventory_warnings_are_visible_but_redacted() -> None:
    report = wire.analyze_request({
        "messages": [False, {"role": "user", "content": {"unexpected": "PRIVATE_CONTENT"}}],
        "tools": [False],
    }, {
        "instructions": ["PRIVATE_INVENTORY"],
        "warnings": [{"code": "inventory_unavailable", "message": "PRIVATE_WARNING"}],
    })
    codes = {w["code"] for w in report["warnings"]}
    assert codes >= {
        "unsupported_message_shape", "unsupported_content_shape",
        "unsupported_tool_declaration", "unsupported_inventory_record", "inventory_unavailable",
    }
    assert "PRIVATE_" not in json.dumps(report)
    with pytest.raises(TypeError, match="dictionaries"):
        wire.analyze_request([], {})


def test_streaming_match_caps_stop_scanning_and_expose_partial_union(monkeypatch) -> None:
    monkeypatch.setattr(wire, "MAX_MATCHES", 3)
    monkeypatch.setattr(wire, "MAX_MATCHES_PER_SOURCE", 2)
    report = wire.analyze_request(
        {"messages": [{"role": "system", "content": "aaaaaa"}]},
        {"instructions": [
            {"path": "example/first.md", "text": "a"},
            {"path": "example/second.md", "text": "a"},
        ]},
    )
    section = report["instruction_files"]
    assert [len(file["matches"]) for file in section["files"]] == [2, 1]
    assert [file["omitted_match_count"] for file in section["files"]] == [1, 1]
    assert section["match_evidence"]["retained_count"] == 3
    assert section["match_evidence"]["omitted_count"] == 2
    assert section["match_evidence"]["counts_exact"] is False
    assert section["match_evidence"]["omitted_count_is_lower_bound"] is True
    assert not section["match_evidence"]["complete"]
    assert section["system_omitted_match_count"] == 2
    assert not section["system_union_accounting_complete"]
    assert section["files"][0]["matches"][1]["ambiguous"] is None
    prompt = report["builtin_prompt"]
    assert prompt["source_matched_union_utf8_bytes"] == 2
    assert prompt["unclassified_system_message_utf8_bytes"] == 4
    assert prompt["source_matched_union_is_lower_bound"] is True
    assert prompt["unclassified_remainder_is_upper_bound"] is True
    assert {warning["code"] for warning in report["warnings"]} >= {
        "per_source_match_evidence_truncated", "global_match_evidence_truncated",
    }


def test_comparison_work_limit_is_unknown_not_a_false_absence(monkeypatch) -> None:
    monkeypatch.setattr(wire, "MAX_COMPARISON_CHARACTERS", 3)
    report = wire.analyze_request(
        {"messages": [{"role": "system", "content": "LONG"}]},
        {"instructions": [{"path": "example/file.md", "text": "L"}]},
    )
    assert report["instruction_files"]["files"][0]["full_content_present"] is None
    assert not report["instruction_files"]["system_union_accounting_complete"]
    assert report["builtin_prompt"]["source_matched_union_is_lower_bound"]
    assert any(w["code"] == "comparison_work_limit" for w in report["warnings"])


def test_ambiguity_caps_bound_expanded_evidence_without_corrupting_union(monkeypatch) -> None:
    monkeypatch.setattr(wire, "MAX_CANDIDATES_PER_MATCH", 2)
    monkeypatch.setattr(wire, "MAX_AMBIGUITY_REFERENCES", 3)
    report = wire.analyze_request(
        {"messages": [{"role": "system", "content": "aa"}]},
        {"instructions": [
            {"path": f"example/rule-{index}.md", "text": "a"} for index in range(5)
        ]},
    )
    section = report["instruction_files"]
    matches = [match for file in section["files"] for match in file["matches"]]
    assert len(matches) == 10
    assert all(len(match["candidate_sources"]) <= 2 for match in matches)
    assert sum(len(match["candidate_sources"]) for match in matches) == 3
    assert all(match["ambiguous"] is True for match in matches)
    assert all(match["candidate_sources_count"] == 5 for match in matches)
    assert sum(match["candidate_sources_omitted_count"] for match in matches) == 47
    assert section["ambiguity_evidence"]["omitted_reference_count"] == 47
    assert not section["ambiguity_evidence"]["complete"]
    assert section["match_evidence"]["complete"]
    assert section["system_union_accounting_complete"]
    assert report["builtin_prompt"]["source_matched_union_utf8_bytes"] == 2
    assert any(w["code"] == "ambiguity_evidence_truncated" for w in report["warnings"])


def test_inventory_warning_classification_is_preserved_without_messages() -> None:
    report = wire.analyze_request({}, {"warnings": [{
        "kind": "inventory_read_failed", "path": "example/metadata.md",
        "error_type": "PermissionError", "class": "io", "plugin": "sample-plugin",
        "message": "PRIVATE_WARNING_DETAIL",
    }]})
    assert report["warnings"] == [{
        "kind": "inventory_read_failed", "path": "example/metadata.md",
        "error_type": "PermissionError", "class": "io", "plugin": "sample-plugin",
    }]
    assert "PRIVATE_WARNING_DETAIL" not in json.dumps(report)


def test_instruction_matches_in_tool_descriptions_do_not_reduce_system_remainder() -> None:
    request = {
        "messages": [{"role": "system", "content": "UNCLASSIFIED_SYSTEM"}],
        "tools": [function("sample", description="PRIVATE_RULE\nPRIVATE_RULE")],
    }
    report = wire.analyze_request(
        request, {"instructions": [{"path": "example/rule.md", "text": "PRIVATE_RULE"}]},
    )
    matches = report["instruction_files"]["files"][0]["matches"]
    assert len(matches) == 2
    assert {match["evidence"]["pointer"] for match in matches} == {
        "/tools/0/function/description",
    }
    prompt = report["builtin_prompt"]
    assert prompt["source_matched_union_utf8_bytes"] == 0
    assert prompt["unclassified_system_message_utf8_bytes"] == len("UNCLASSIFIED_SYSTEM")
    assert prompt["source_matched_union_accounting_complete"] is True
    assert "PRIVATE_RULE" not in json.dumps(report)


def test_metadata_correlation_expansion_is_capped_with_exact_indexed_ambiguity(monkeypatch) -> None:
    metadata = wire._METADATA
    monkeypatch.setattr(metadata, "MAX_CORRELATIONS_PER_RECORD", 3)
    monkeypatch.setattr(metadata, "MAX_METADATA_CORRELATIONS", 7)
    entries = "".join(
        "<skill><name>shared</name><description>PRIVATE_METADATA_DESCRIPTION</description></skill>"
        for _ in range(12)
    )
    report = wire.analyze_request(
        {"messages": [{"role": "system", "content": "<available_skills>" + entries + "</available_skills>"}]},
        {"skills": [{
            "name": "shared", "aliases": ["shared", "shared-alias"],
            "path": f"example/skill-{index}/SKILL.md",
            "description": "PRIVATE_METADATA_DESCRIPTION",
        } for index in range(12)]},
    )
    records = report["skills"]["observed"]
    coverage = report["coverage"]["metadata"]
    assert len(records) == 12
    assert sum(len(record["source_matches"]) for record in records) == 7
    assert all(len(record["source_matches"]) <= 3 for record in records)
    assert all(record["source_matches_count"] == 12 for record in records)
    assert all(record["ambiguous"] is True for record in records)
    assert not any(record["source_matches_complete"] for record in records)
    assert coverage["index_entries_retained"] == 24
    assert coverage["correlations_omitted"] == 137
    assert coverage["correlation_omitted_is_lower_bound"] is False
    assert coverage["records_complete"] and not coverage["complete"]
    assert any(w["code"] == "metadata_correlations_truncated" for w in report["warnings"])
    assert "PRIVATE_METADATA_DESCRIPTION" not in json.dumps(report)


def test_metadata_records_stop_at_cap_with_explicit_lower_bound_counts(monkeypatch) -> None:
    monkeypatch.setattr(wire._METADATA, "MAX_METADATA_RECORDS", 3)
    entries = "".join(f"<skill><name>sample-{i}</name></skill>" for i in range(8))
    report = wire.analyze_request({
        "messages": [{"role": "system", "content": "<available_skills>" + entries + "</available_skills>"}],
    }, {})
    assert len(report["skills"]["observed"]) == 3
    assert report["skills"]["counts_scope"] == "retained_metadata_records"
    assert not report["skills"]["counts_complete"]
    coverage = report["coverage"]["metadata"]
    assert coverage["records_retained"] == 3
    assert coverage["records_omitted_lower_bound"] == 1
    assert not coverage["records_complete"]
    assert any(w["code"] == "metadata_records_truncated" for w in report["warnings"])


def test_metadata_index_limit_does_not_claim_unambiguous_or_absent_sources(monkeypatch) -> None:
    monkeypatch.setattr(wire._METADATA, "MAX_METADATA_INDEX_ENTRIES", 1)
    report = wire.analyze_request({
        "messages": [{"role": "system", "content":
            "<available_skills><skill><name>shared</name></skill>"
            "<skill><name>not-indexed</name></skill></available_skills>"
        }],
    }, {"skills": [
        {"name": "shared", "path": "example/a/SKILL.md"},
        {"name": "shared", "path": "example/b/SKILL.md"},
        {"name": "not-indexed", "path": "example/c/SKILL.md"},
    ]})
    first, second = report["skills"]["observed"]
    assert first["source_matches_count"] == 1 and second["source_matches_count"] == 0
    assert first["ambiguous"] is None and second["ambiguous"] is None
    assert all(record["source_matches_count_is_lower_bound"] for record in (first, second))
    assert all(not record["source_matches_complete"] for record in (first, second))
    assert not report["coverage"]["metadata"]["index_complete"]["skills"]
    assert any(w["code"] == "metadata_source_index_truncated" for w in report["warnings"])


def test_long_metadata_field_comparisons_are_charged_and_unknown_when_skipped(monkeypatch) -> None:
    monkeypatch.setattr(wire._METADATA, "MAX_METADATA_COMPARISON_CHARACTERS", 1000)
    report = wire.analyze_request({
        "messages": [{"role": "system", "content":
            "<available_skills><skill><name>sample</name><description>x</description>"
            "</skill></available_skills>"
        }],
    }, {"skills": [{
        "name": "sample", "path": "example/sample/SKILL.md",
        "description": "PRIVATE_DESCRIPTION" * 1000,
        "frontmatter": "PRIVATE_FRONTMATTER" * 1000,
    }]})
    record = report["skills"]["observed"][0]
    source = record["source_matches"][0]
    assert source["matching_source_fields"] == ["name"]
    assert source["frontmatter_text_present"] is None
    assert not source["field_comparisons_complete"]
    assert not record["source_field_comparisons_complete"]
    coverage = report["coverage"]["metadata"]
    assert coverage["comparison_characters_charged"] <= 1000
    assert not coverage["comparison_work_complete"]
    assert any(w["code"] == "metadata_comparison_work_limit" for w in report["warnings"])
    assert "PRIVATE_" not in json.dumps(report)


def test_last_agent_source_prefix_comparison_respects_character_budget(monkeypatch) -> None:
    monkeypatch.setattr(wire._METADATA, "MAX_METADATA_COMPARISON_CHARACTERS", 1000)
    description = (
        "User-provided custom agents:\n- **sample:worker**: Summary.\n"
        + "PRIVATE_FOOTER_" * 1000
    )
    report = wire.analyze_request({"tools": [function("task", {
        "agent_type": {"type": "string", "enum": ["sample:worker"]},
    }, description)]}, {"agents": [{
        "name": "sample:worker", "path": "example/worker.agent.md", "description": "Summary.",
    }]})
    rendered = next(r for r in report["subagents"]["observed"] if r["representation"] == "rendered_metadata")
    assert rendered["description_scope"] == "inline_and_indented_prefix"
    coverage = report["coverage"]["metadata"]
    assert coverage["prefix_candidates_examined"] == 0
    assert coverage["prefix_candidates_omitted"] == 1
    assert not coverage["comparison_work_complete"]
    assert coverage["comparison_characters_charged"] <= 1000
    assert "PRIVATE_FOOTER" not in json.dumps(report)


@pytest.mark.parametrize("kind", ["selector_reference", "memory_marker"])
def test_marker_collectors_cap_candidates_and_preserve_multibyte_evidence(monkeypatch, kind: str) -> None:
    monkeypatch.setattr(wire._METADATA, "MAX_MARKER_CANDIDATES", 2)
    marker = (
        '<!-- copilot-extension-instruction-projection '
        '{"deliveryKind":"selector","destination":"example/rules.md"} -->'
        if kind == "selector_reference" else "<memory>PRIVATE_MEMORY_é🙂</memory>"
    )
    text = "é🙂\n" + (marker + "\né🙂\n") * 20
    report = wire.analyze_request({"messages": [{"role": "system", "content": text}]}, {})
    records, coverage = (
        (report["instruction_files"]["selector_references"],
         report["instruction_files"]["selector_reference_evidence"])
        if kind == "selector_reference" else
        (report["memories"]["candidate_markup"], report["memories"]["candidate_evidence"])
    )
    assert len(records) == coverage["retained_count"] == 2
    assert coverage["candidates_examined"] == 2
    assert coverage["omitted_candidates_lower_bound"] == 1
    assert coverage["retained_count_is_lower_bound"] and not coverage["complete"]
    start = 0
    for record in records:
        start = text.index(marker, start)
        assert record["evidence"] == {
            "pointer": "/messages/0/content",
            "start_utf8_byte": len(text[:start].encode()),
            "end_utf8_byte": len(text[:start + len(marker)].encode()),
        }
        start += len(marker)
    assert any(w["code"] == kind + "_evidence_truncated" for w in report["warnings"])
    assert report["memories"]["exact_count"] is None
    assert "PRIVATE_MEMORY_" not in json.dumps(report)


def test_marker_scan_budget_exhaustion_exposes_unknown_not_complete_zero(monkeypatch) -> None:
    monkeypatch.setattr(wire._METADATA, "MAX_MARKER_CHARACTERS", 1)
    text = (
        '<!-- copilot-extension-instruction-projection '
        '{"deliveryKind":"selector","destination":"example/rules.md"} -->'
        "<memory>PRIVATE_MEMORY</memory>"
    )
    report = wire.analyze_request({"messages": [{"role": "system", "content": text}]}, {})
    for coverage in (
        report["instruction_files"]["selector_reference_evidence"],
        report["memories"]["candidate_evidence"],
    ):
        assert coverage["retained_count"] == 0 and not coverage["complete"]
        assert coverage["retained_count_is_lower_bound"]
        assert coverage["work_characters_charged"] <= 1
    assert report["memories"]["exact_count"] is None
    assert report["memories"]["exact_utf8_bytes"] is None
    assert not report["coverage"]["metadata"]["complete"]
    assert {w["code"] for w in report["warnings"]} >= {
        "selector_reference_work_limit", "memory_marker_work_limit",
    }


def test_marker_offset_work_is_budgeted_before_retaining_evidence(monkeypatch) -> None:
    metadata = wire._METADATA
    marker = "<memory>é</memory>"
    prefix = "🙂" * 200
    text = prefix + marker * 10
    first_cost = len(text) + 2 * len(marker) + 2 * len(prefix) + len(marker)
    limit = first_cost + 2 * len(marker)
    monkeypatch.setattr(metadata, "MAX_MARKER_CHARACTERS", limit)
    result = metadata.collect_markers([{"text": text, "pointer": "/messages/0/content"}], "memory_marker")
    assert len(result["records"]) == 1
    assert result["coverage"]["candidates_examined"] == 2
    assert result["coverage"]["work_characters_charged"] == limit
    assert not result["coverage"]["complete"]
    assert result["records"][0]["evidence"]["start_utf8_byte"] == len(prefix.encode())
    assert result["records"][0]["content_utf8_bytes"] == len("é".encode())
    assert result["warnings"][0]["code"] == "memory_marker_work_limit"


def test_sparse_utf8_offsets_do_not_reencode_growing_prefixes(monkeypatch) -> None:
    metadata = wire._METADATA
    monkeypatch.setattr(metadata, "UTF8_CHECKPOINT_STRIDE", 8)
    monkeypatch.setattr(metadata, "MAX_MARKER_CHARACTERS", 2000)
    text = "é🙂abc" * 100
    region = {"text": text, "pointer": "/messages/0/content"}
    budget = {"used": 0}
    for start in range(4, 404, 4):
        location = metadata.evidence(region, start, start + 1, budget)
        assert location is not None
        assert location["start_utf8_byte"] == len(text[:start].encode())
        assert location["end_utf8_byte"] == len(text[:start + 1].encode())
    assert budget["used"] <= len(text) + 100 * 2 * 7
    assert len(region["_utf8_checkpoints"]) <= len(text) // 8 + 1


@pytest.mark.parametrize("kind, unfinished", [
    ("memory_marker", "<memory>"),
    ("memory_marker", "<memory"),
    ("selector_reference", '<!-- copilot-extension-instruction-projection {'),
])
def test_unterminated_marker_openings_stop_without_rescanning_suffixes(kind: str, unfinished: str) -> None:
    completed = (
        '<!-- copilot-extension-instruction-projection '
        '{"deliveryKind":"selector","destination":"example/é.md"} -->'
        if kind == "selector_reference" else "<memory>é🙂</memory>"
    )
    prefix = "é🙂\n"
    text = prefix + completed + unfinished * 20000
    result = wire._METADATA.collect_markers([{"text": text, "pointer": "/messages/0/content"}], kind)
    coverage = result["coverage"]
    assert len(result["records"]) == coverage["retained_count"] == 1
    assert coverage["candidates_examined"] == 2 and coverage["failed_candidates"] == 1
    assert not coverage["complete"] and coverage["retained_count_is_lower_bound"]
    assert coverage["omitted_candidates_lower_bound"] == 1
    assert coverage["work_characters_charged"] <= len(text) + 4 * len(completed) + 32
    assert result["records"][0]["evidence"] == {
        "pointer": "/messages/0/content", "start_utf8_byte": len(prefix.encode()),
        "end_utf8_byte": len((prefix + completed).encode()),
    }
    assert result["warnings"][0]["code"] == kind + "_unterminated"


def test_malformed_closed_selector_starts_consume_candidate_budget(monkeypatch) -> None:
    monkeypatch.setattr(wire._METADATA, "MAX_MARKER_CANDIDATES", 3)
    text = '<!-- copilot-extension-instruction-projection { -->' * 20000
    result = wire._METADATA.collect_markers(
        [{"text": text, "pointer": "/messages/0/content"}], "selector_reference",
    )
    coverage = result["coverage"]
    assert result["records"] == [] and coverage["candidates_examined"] == 3
    assert coverage["failed_candidates"] == 3 and not coverage["complete"]
    assert coverage["omitted_candidates_lower_bound"] == 1
    assert coverage["retained_count_is_lower_bound"]
    assert {w["code"] for w in result["warnings"]} == {
        "invalid_projection_marker_json", "selector_reference_evidence_truncated",
    }


@pytest.mark.parametrize("nested", [False, True])
def test_custom_grammar_layouts_count_unicode_without_echoing_body(nested: bool) -> None:
    body = "start: /SYNTHETIC_GRAMMAR_é🙂/"
    grammar = {"syntax": "lark", "definition": body}
    schema = {"type": "grammar", **(
        {"grammar": grammar, "definition": "FLAT_FALLBACK_MUST_NOT_WIN"} if nested else grammar
    )}
    report = wire.analyze_request({"tools": [{
        "type": "custom", "custom": {"name": "apply_patch", "format": schema},
    }]}, {})
    record = report["tools"]["observed"][0]
    assert record["schema_kind"] == "grammar"
    assert record["grammar_definition_utf8_bytes"] == len(body.encode("utf-8"))
    assert "SYNTHETIC_GRAMMAR_" not in json.dumps(report)
    assert "FLAT_FALLBACK_MUST_NOT_WIN" not in json.dumps(report)
