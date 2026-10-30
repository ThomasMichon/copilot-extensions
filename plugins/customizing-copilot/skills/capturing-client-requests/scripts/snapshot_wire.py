"""Pure offline request analysis; matching establishes presence, not provenance."""

from __future__ import annotations

from collections.abc import Iterator
import importlib.util
import json
from pathlib import Path
import re
from typing import Any


_SPEC = importlib.util.spec_from_file_location(
    "_snapshot_wire_metadata", Path(__file__).with_name("snapshot_wire_metadata.py"),
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("snapshot metadata helper unavailable")
_METADATA = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_METADATA)

# Deliberately conservative public IDs; a matching name never authenticates origin.
_PUBLIC_BUILTINS = frozenset({
    "powershell", "read_powershell", "stop_powershell", "list_powershell",
    "bash", "read_bash", "stop_bash", "list_bash", "apply_patch", "view",
    "fetch_copilot_cli_documentation", "skill", "sql", "session_store_sql",
    "read_agent", "list_agents", "write_agent", "rg", "glob", "task",
    "ask_user", "manage_schedule", "session_artifacts", "session_history",
    "get_context_remaining", "new_context", "semantic_code_search", "web_search",
})
_SELECTORS = frozenset({
    "agent", "agent_type", "agent_name", "skill", "skill_name",
    "tool", "tool_name", "name", "server", "server_name", "namespace",
})
MAX_MATCHES = 2048
MAX_MATCHES_PER_SOURCE = 256
MAX_CANDIDATES_PER_MATCH = 16
MAX_AMBIGUITY_REFERENCES = 4096
MAX_COMPARISON_CHARACTERS = 128 * 1024 * 1024
MAX_MCP_SERVERS = MAX_MCP_TOOL_NAMES_PER_SERVER = 256
MAX_MCP_TOOL_NAMES = 8192
MAX_MCP_CORRELATIONS_PER_TOOL = 16
MAX_MCP_CORRELATIONS = 2048
MAX_MCP_COMPARISON_CHARACTERS = 32 * 1024 * 1024


def _pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _json_bytes(value: Any) -> int:
    return len(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8"))


def _warn(warnings: list[dict[str, Any]], code: str, pointer: str) -> None:
    warnings.append({"code": code, "pointer": pointer})


def _regions(
    request: dict[str, Any], warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    regions, metrics = [], []
    messages = request.get("messages", [])
    if not isinstance(messages, list):
        _warn(warnings, "unsupported_messages_shape", "/messages")
        return regions, metrics
    for index, message in enumerate(messages):
        pointer = f"/messages/{index}"
        if not isinstance(message, dict):
            _warn(warnings, "unsupported_message_shape", pointer)
            continue
        role = message.get("role")
        role = role if role in ("system", "developer", "user", "assistant", "tool") else "unknown"
        content = message.get("content")
        parts = []
        if isinstance(content, str):
            parts.append((pointer + "/content", content))
        elif isinstance(content, list):
            for part_index, part in enumerate(content):
                part_pointer = f"{pointer}/content/{part_index}"
                if (
                    isinstance(part, dict)
                    and part.get("type") in ("text", "input_text", "output_text")
                    and isinstance(part.get("text"), str)
                ):
                    parts.append((part_pointer + "/text", part["text"]))
                else:
                    _warn(warnings, "uninterpreted_content_block", part_pointer)
        elif content is not None:
            _warn(warnings, "unsupported_content_shape", pointer + "/content")
        for part_pointer, text in parts:
            regions.append({"pointer": part_pointer, "text": text, "role": role})
        metrics.append({
            "pointer": pointer, "role": role, "text_region_count": len(parts),
            "text_utf8_bytes": sum(len(text.encode("utf-8")) for _, text in parts),
            "canonical_json_utf8_bytes": _json_bytes(message),
        })
    return regions, metrics


def _schema_types(schema: dict[str, Any]) -> tuple[set[str], list[Any], bool]:
    types: set[str] = set()
    value = schema.get("type", [])
    types.update([value] if isinstance(value, str) else (
        [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
    ))
    enum = schema.get("enum", [])
    values = list(enum) if isinstance(enum, list) else []
    if "const" in schema:
        values.append(schema["const"])
    if values and all(isinstance(item, str) for item in values):
        types.add("string")
    constrained = "enum" in schema or "const" in schema
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key, [])
        if not isinstance(variants, list) or not variants:
            continue
        string_constraints = []
        for variant in variants:
            if isinstance(variant, dict):
                child_types, child_values, child_constrained = _schema_types(variant)
                types.update(child_types)
                values.extend(child_values)
                if "string" in child_types:
                    string_constraints.append(child_constrained)
        constrained = constrained or (
            bool(string_constraints) and all(string_constraints)
        )
    return types, values, constrained


def _properties(
    schema: Any, pointer: str, warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(schema, dict) or "properties" not in schema:
        return []
    if not isinstance(schema["properties"], dict):
        _warn(warnings, "unsupported_schema_properties", pointer + "/properties")
        return []
    properties = []
    for name, value in sorted(schema["properties"].items()):
        if not isinstance(value, dict):
            _warn(warnings, "unsupported_property_schema", pointer + "/properties/" + _pointer(name))
            continue
        types, enum, constrained = _schema_types(value)
        prop = {
            "name": name, "pointer": pointer + "/properties/" + _pointer(name),
            "types": sorted(types), "enum_count": len(enum),
            "enum_constrained": constrained,
        }
        if name in _SELECTORS:
            prop["enum_names"] = sorted({item for item in enum if isinstance(item, str)})
        properties.append(prop)
    return properties


def _tools(
    request: dict[str, Any], warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed, definitions = [], []
    tools = request.get("tools", [])
    if not isinstance(tools, list):
        _warn(warnings, "unsupported_tools_shape", "/tools")
        return observed, definitions
    for index, entry in enumerate(tools):
        pointer = f"/tools/{index}"
        if not isinstance(entry, dict) or entry.get("type") not in ("function", "custom"):
            _warn(warnings, "unsupported_tool_declaration", pointer)
            continue
        kind = entry["type"]
        definition = entry.get(kind, entry)
        if not isinstance(definition, dict):
            _warn(warnings, "unsupported_tool_definition", pointer)
            continue
        if kind in entry:
            pointer += "/" + kind
        name = definition.get("name")
        name = name if isinstance(name, str) else None
        if name is None:
            _warn(warnings, "missing_tool_name", pointer)
        description = definition.get("description", "")
        if not isinstance(description, str):
            _warn(warnings, "unsupported_tool_description", pointer + "/description")
            description = ""
        schema = definition.get("parameters") if kind == "function" else definition.get("format")
        schema_pointer = pointer + ("/parameters" if kind == "function" else "/format")
        if schema is not None and not isinstance(schema, dict):
            _warn(warnings, "unsupported_tool_schema", schema_pointer)
        props = _properties(schema, schema_pointer, warnings) if kind == "function" else []
        grammar = schema.get("definition") if isinstance(schema, dict) and kind == "custom" else None
        if isinstance(schema, dict) and kind == "custom" and isinstance(schema.get("grammar"), dict):
            grammar = schema["grammar"].get("definition", grammar)
        record = {
            "name": name, "kind": kind, "pointer": pointer,
            "schema_status": "full_schema" if isinstance(schema, dict) else "declaration_only",
            "schema_kind": "json_schema" if kind == "function" else (
                schema.get("type") if isinstance(schema, dict)
                and schema.get("type") in ("grammar", "text") else "unknown"
            ),
            "input_properties": props,
            "canonical_json_utf8_bytes": _json_bytes(entry),
            "schema_canonical_json_utf8_bytes": _json_bytes(schema) if schema is not None else None,
            "description_utf8_bytes": len(description.encode("utf-8")),
            "grammar_definition_utf8_bytes": len(grammar.encode("utf-8")) if isinstance(grammar, str) else None,
            "origin_authenticated": False, "ownership": "unknown",
        }
        observed.append(record)
        definitions.append({
            "name": name, "pointer": pointer, "schema": schema, "properties": props,
            "description_region": {
                "pointer": pointer + "/description", "text": description, "role": "tool_description",
            },
        })
    return observed, definitions


def _normalized(text: str) -> tuple[str, list[int]]:
    chars, boundaries = [], [0]
    index = 0
    while index < len(text):
        if text.startswith("\r\n", index):
            chars.append("\n")
            index += 2
        else:
            chars.append(text[index])
            index += 1
        boundaries.append(index)
    return "".join(chars), boundaries


def _occurrences(text: str, candidate: str) -> Iterator[tuple[int, int]]:
    start = text.find(candidate)
    while start >= 0:
        yield start, start + len(candidate)
        start = text.find(candidate, start + 1)


def _body(text: str) -> str:
    match = re.match(r"\A---[ \t]*\r?\n[\s\S]*?\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", text)
    return text[match.end():] if match else text


def _union(spans: list[tuple[int, int]]) -> int:
    total, end = 0, 0
    for start, stop in sorted(spans):
        total += max(0, stop - max(start, end))
        end = max(end, stop)
    return total


def _instructions(
    regions: list[dict[str, Any]], sources: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    files = []
    system_spans: dict[str, list[tuple[int, int]]] = {}
    normalized_regions = {r["pointer"]: _normalized(r["text"]) for r in regions}
    retained, omitted, system_omitted, global_omitted = 0, 0, 0, 0
    comparison_work, comparison_truncated, system_comparison_truncated = 0, False, False
    for source_index, source in enumerate(sources):
        text = source.get("text")
        path = source.get("path")
        if not isinstance(text, str) or not isinstance(path, str):
            _warn(warnings, "invalid_instruction_inventory_record", "/inventory/instructions")
            continue
        matches = []
        untrimmed_spans: dict[tuple[str, str], list[tuple[int, int]]] = {}
        source_omitted, source_limited = 0, False
        source_search_complete = True
        variants = [("full_file", text)]
        body = _body(text)
        if body != text:
            variants.append(("frontmatter_stripped_body", body))
        variants.extend(
            (kind + "_edge_newlines_trimmed", value.strip("\r\n"))
            for kind, value in list(variants) if value.strip("\r\n") != value
        )
        for region in regions:
            original = region["text"]
            normalized, boundaries = normalized_regions[region["pointer"]]
            for variant, candidate in variants:
                if not candidate.strip():
                    continue
                if comparison_work + len(normalized) > MAX_COMPARISON_CHARACTERS:
                    comparison_truncated = True
                    source_search_complete = False
                    system_comparison_truncated |= region["role"] == "system"
                    continue
                comparison_work += len(normalized)
                normalized_candidate = candidate.replace("\r\n", "\n")
                for start, stop in _occurrences(normalized, normalized_candidate):
                    start, stop = boundaries[start], boundaries[stop]
                    stem = variant.removesuffix("_edge_newlines_trimmed")
                    key = (region["pointer"], stem)
                    if variant != stem:
                        if any(a <= start and b >= stop for a, b in untrimmed_spans.get(key, [])):
                            continue
                    else:
                        untrimmed_spans.setdefault(key, []).append((start, stop))
                    if retained >= MAX_MATCHES or len(matches) >= MAX_MATCHES_PER_SOURCE:
                        omitted += 1
                        source_omitted += 1
                        system_omitted += region["role"] == "system"
                        if retained >= MAX_MATCHES:
                            global_omitted += 1
                        else:
                            source_limited = True
                        break
                    method = "exact" if original[start:stop] == candidate else "crlf_normalized"
                    location = _METADATA.evidence(region, start, stop)
                    matches.append({
                        "match_method": variant + "_" + method,
                        "evidence": location,
                        "matched_utf8_bytes": location["end_utf8_byte"] - location["start_utf8_byte"],
                    })
                    retained += 1
                    if region["role"] == "system":
                        system_spans.setdefault(region["pointer"], []).append((
                            location["start_utf8_byte"], location["end_utf8_byte"],
                        ))
        inventory_pointer = f"/inventory/instructions/{source_index}"
        if source_limited:
            _warn(warnings, "per_source_match_evidence_truncated", inventory_pointer)
        files.append({
            "path": path, "source_utf8_bytes": len(text.encode("utf-8")),
            "inventory_pointer": inventory_pointer,
            "full_content_present": (
                True if matches or source_omitted else False if source_search_complete else None
            ),
            "matches": matches,
            "retained_match_count": len(matches), "omitted_match_count": source_omitted,
            "omitted_count_is_lower_bound": source_omitted > 0,
            "match_evidence_complete": source_omitted == 0 and source_search_complete,
            "origin_authenticated": False, "file_read_proven": False,
        })
    if global_omitted:
        _warn(warnings, "global_match_evidence_truncated", "/inventory/instructions")
    if comparison_truncated:
        _warn(warnings, "comparison_work_limit", "/inventory/instructions")
    claims: dict[tuple[str, int, int], dict[str, Any]] = {}
    for file in files:
        for match in file["matches"]:
            ev = match["evidence"]
            key = (ev["pointer"], ev["start_utf8_byte"], ev["end_utf8_byte"])
            claim = claims.setdefault(key, {"count": 0, "sources": []})
            claim["count"] += 1
            if len(claim["sources"]) < MAX_CANDIDATES_PER_MATCH:
                claim["sources"].append({
                    "path": file["path"], "inventory_pointer": file["inventory_pointer"],
                })
    ambiguity_retained, ambiguity_omitted = 0, 0
    for file in files:
        for match in file["matches"]:
            ev = match["evidence"]
            key = (ev["pointer"], ev["start_utf8_byte"], ev["end_utf8_byte"])
            claim = claims[key]
            remaining = max(0, MAX_AMBIGUITY_REFERENCES - ambiguity_retained)
            candidates = claim["sources"][:remaining]
            missing = claim["count"] - len(candidates)
            ambiguity_retained += len(candidates)
            ambiguity_omitted += missing
            match["candidate_sources"] = candidates
            match["candidate_paths"] = sorted({candidate["path"] for candidate in candidates})
            match["candidate_sources_count"] = claim["count"]
            match["candidate_sources_omitted_count"] = missing
            match["candidate_sources_complete"] = missing == 0 and omitted == 0 and not comparison_truncated
            match["ambiguous"] = (
                True if claim["count"] > 1 else None if omitted or comparison_truncated else False
            )
    if ambiguity_omitted:
        _warn(warnings, "ambiguity_evidence_truncated", "/inventory/instructions")
    references = _METADATA.collect_markers(regions, "selector_reference")
    warnings.extend(references["warnings"])
    union = sum(_union(spans) for spans in system_spans.values())
    return {
        "files": files, "selector_references": references["records"],
        "selector_reference_evidence": references["coverage"],
        "present_paths": sorted({file["path"] for file in files if file["full_content_present"] is True}),
        "match_evidence": {
            "global_limit": MAX_MATCHES, "per_source_limit": MAX_MATCHES_PER_SOURCE,
            "retained_count": retained, "omitted_count": omitted,
            "counts_exact": omitted == 0 and not comparison_truncated,
            "omitted_count_is_lower_bound": omitted > 0 or comparison_truncated,
            "complete": omitted == 0 and not comparison_truncated,
            "comparison_character_limit": MAX_COMPARISON_CHARACTERS,
            "comparison_characters": comparison_work,
        },
        "ambiguity_evidence": {
            "per_match_limit": MAX_CANDIDATES_PER_MATCH,
            "global_reference_limit": MAX_AMBIGUITY_REFERENCES,
            "retained_reference_count": ambiguity_retained,
            "omitted_reference_count": ambiguity_omitted,
            "counts_scope": "retained_matches_only",
            "complete": ambiguity_omitted == 0 and omitted == 0 and not comparison_truncated,
        },
        "system_union_accounting_complete": system_omitted == 0 and not system_comparison_truncated,
        "system_omitted_match_count": system_omitted,
        "union_scope": "retained_match_evidence_only",
    }, union


def _ownership(tools: list[dict[str, Any]], servers: list[dict[str, Any]], collector_complete: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    configured, builtin, rows = [], [], []
    codes = set() if collector_complete else {"mcp_collector_incomplete"}
    coverage = {
        "collector_complete": collector_complete, "candidate_inventory_complete": False,
        "server_record_limit": MAX_MCP_SERVERS, "tool_names_per_server_limit": MAX_MCP_TOOL_NAMES_PER_SERVER,
        "tool_name_entry_limit": MAX_MCP_TOOL_NAMES, "tool_name_entries_examined": 0, "unsearched_tool_name_entries": 0,
        "correlation_per_tool_limit": MAX_MCP_CORRELATIONS_PER_TOOL,
        "correlation_global_limit": MAX_MCP_CORRELATIONS, "correlations_retained": 0,
        "comparison_character_limit": MAX_MCP_COMPARISON_CHARACTERS,
        "comparison_characters_charged": 0, "comparison_work_complete": True, "complete": True,
        "candidate_pairs_examined": 0, "unsearched_candidate_pairs": 0, "omitted_correlations_lower_bound": 0,
        "candidate_pairs_scope": "supplied_server_records", "correlation_pointer_root": "analysis_report",
    }
    def charge(cost: int) -> bool:
        if coverage["comparison_characters_charged"] + cost > MAX_MCP_COMPARISON_CHARACTERS:
            coverage["comparison_work_complete"] = False
            codes.add("mcp_comparison_work_limit")
            return False
        coverage["comparison_characters_charged"] += cost
        return True
    for index, server in enumerate(servers[:MAX_MCP_SERVERS]):
        if not isinstance(name := server.get("name"), str) or not name:
            codes.add("unsupported_mcp_server_name")
            continue
        source = value if isinstance(value := server.get("source"), str) else None
        if not charge(5 * len(name) + len(source or "") + 12):
            break
        names = server.get("tools", [])
        if not isinstance(names, list):
            codes.add("unsupported_mcp_tool_names")
            names = []
        limit = min(len(names), MAX_MCP_TOOL_NAMES_PER_SERVER,
                    max(0, MAX_MCP_TOOL_NAMES - coverage["tool_name_entries_examined"]))
        candidates = names[:limit]
        examined = len(candidates) if charge(sum(len(v) + 1 if isinstance(v, str) else 1 for v in candidates)) else 0
        indexed_names = {value for value in candidates[:examined] if isinstance(value, str)}
        coverage["tool_name_entries_examined"] += examined
        coverage["unsearched_tool_name_entries"] += len(names) - examined
        if examined < len(names):
            codes.add("mcp_tool_name_inventory_truncated")
        if server.get("tool_names_complete", True) is not True:
            codes.add("mcp_tool_name_inventory_incomplete")
        row = dict(name=name, source=source, inventory_pointer=f"/inventory/mcp_servers/{index}",
            configured_tool_name_count=len(names), observed_name_matches=[], connectivity="unknown", origin_authenticated=False,
            configured_tool_names_complete=examined == len(names) and server.get("tool_names_complete", True) is True)
        configured.append(row)
        rows.append((row, indexed_names, (name + "__", name + "-", name + ".", name + "/", "mcp__" + name + "__")))
    coverage["servers_retained"] = len(configured)
    coverage["servers_omitted_count"] = len(servers) - len(configured)
    if len(servers) > MAX_MCP_SERVERS:
        codes.add("mcp_server_inventory_truncated")
    inventory_complete = len(configured) == len(servers) and not codes
    coverage["candidate_inventory_complete"] = inventory_complete
    positions = {"observed": 0, "advertised": 0}
    for tool in tools:
        group = "advertised" if tool.get("schema_status") == "advertised_only" else "observed"
        report_pointer = f"/tools/{group}/{positions[group]}/mcp_name_correlations"
        positions[group] += 1
        tool_name = tool.get("name")
        is_public = isinstance(tool_name, str) and len(tool_name) <= 64 and tool_name in _PUBLIC_BUILTINS
        correlations = tool.setdefault("mcp_name_correlations", [])
        searched = 0
        for row, names, prefixes in rows:
            if len(correlations) >= MAX_MCP_CORRELATIONS_PER_TOOL or coverage["correlations_retained"] >= MAX_MCP_CORRELATIONS:
                codes.add("mcp_correlation_evidence_truncated")
                break
            if not isinstance(tool_name, str) or not charge(len(tool_name) + 5 * len(row["name"]) + 1):
                break
            searched += 1
            coverage["candidate_pairs_examined"] += 1
            methods = [method for matched, method in (
                (tool_name in names, "configured_tool_name"),
                (tool_name.startswith(prefixes), "namespace_prefix")) if matched]
            if methods:
                canonical_pointer = report_pointer + f"/{len(correlations)}"
                correlations.append({
                    "server_name": row["name"], "inventory_pointer": row["inventory_pointer"],
                    "match_methods": methods, "evidence": {"pointer": tool["pointer"]}, "verified": False,
                })
                row["observed_name_matches"].append({"tool_name": tool_name,
                    "correlation_pointer": canonical_pointer, "evidence": {"pointer": tool["pointer"]}})
                coverage["correlations_retained"] += 1
        complete = inventory_complete and searched == len(rows) and isinstance(tool_name, str)
        coverage["unsearched_candidate_pairs"] += len(servers) - searched
        coverage["complete"] = coverage["complete"] and complete
        tool.update({"mcp_name_search_complete": complete, "mcp_name_match_count": len(correlations),
                     "mcp_name_match_count_is_lower_bound": not complete})
        tool["ownership_ambiguous"] = True if len(correlations) > 1 or (is_public and correlations) else False if complete else None
        tool["ownership_classification"] = "ambiguous_name_match" if tool["ownership_ambiguous"] is True else (
            "builtin_name_match" if is_public else "mcp_name_correlated" if correlations else "unknown")
        if is_public:
            builtin.append(dict(name=tool["name"], pointer=tool["pointer"], match_method="name_based",
                origin_authenticated=False, schema_status=tool["schema_status"], ownership_ambiguous=tool["ownership_ambiguous"]))
    coverage["complete"] = coverage["complete"] and inventory_complete
    for row in configured:
        row["observed_name_matches_complete"] = coverage["complete"]
    return {"configured": configured, "exact_server_ownership": "unknown", "coverage": coverage,
        "name_correlated": [server for server in configured if server["observed_name_matches"]],
        "warnings": [{"code": code, "pointer": "/inventory/mcp_servers"} for code in sorted(codes)]}, {"name_matches": builtin, "allowlist_complete": False}


def analyze_request(request: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Analyze supplied JSON values without I/O, mutation, or content disclosure.

    All byte spans address decoded text, not JSON escape sequences. JSON byte
    metrics use the documented canonical serialization, not original wire bytes.
    """
    if not isinstance(request, dict) or not isinstance(inventory, dict):
        raise TypeError("request and inventory must be dictionaries")
    warnings: list[dict[str, Any]] = []
    clean_inventory = {}
    for key in ("instructions", "skills", "agents", "mcp_servers"):
        records = inventory.get(key, [])
        if not isinstance(records, list):
            _warn(warnings, "unsupported_inventory_shape", "/inventory/" + key)
            records = []
        clean_inventory[key] = [record for record in records if isinstance(record, dict)]
        if len(clean_inventory[key]) != len(records):
            _warn(warnings, "unsupported_inventory_record", "/inventory/" + key)
    inherited = inventory.get("warnings", [])
    if isinstance(inherited, list):
        for warning in inherited:
            if isinstance(warning, dict):
                warnings.append({
                    key: warning[key] for key in (
                        "code", "kind", "class", "error_type", "plugin", "path", "pointer",
                    )
                    if isinstance(warning.get(key), str)
                } or {"code": "inventory_warning_redacted"})
    else:
        _warn(warnings, "unsupported_inventory_warnings", "/inventory/warnings")
    regions, message_metrics = _regions(request, warnings)
    tools, definitions = _tools(request, warnings)
    all_regions = regions + [definition["description_region"] for definition in definitions]
    instructions, union = _instructions(all_regions, clean_inventory["instructions"], warnings)
    metadata = _METADATA.analyze_metadata(
        all_regions, definitions, clean_inventory,
    )
    metadata_coverage = metadata.pop("metadata_coverage")
    warnings.extend(metadata.pop("metadata_warnings"))
    advertised = metadata.pop("advertised_tools")
    collector_complete = isinstance(collector_coverage := inventory.get("coverage", {}), dict) and collector_coverage.get("mcp_complete", True) is True
    servers, builtins = _ownership(tools + advertised, clean_inventory["mcp_servers"], collector_complete)
    mcp_coverage = servers.pop("coverage")
    warnings.extend(servers.pop("warnings"))
    system_bytes = sum(len(r["text"].encode("utf-8")) for r in regions if r["role"] == "system")
    return {
        "schema": "copilot-extensions.client-request-analysis", "version": 1,
        "request": {
            "canonical_json_utf8_bytes": _json_bytes(request),
            "original_wire_utf8_bytes": None,
            "message_count": len(request.get("messages", [])) if isinstance(request.get("messages", []), list) else None,
            "tool_declaration_count": len(request.get("tools", [])) if isinstance(request.get("tools", []), list) else None,
            "messages": message_metrics,
            "message_text_utf8_bytes": sum(m["text_utf8_bytes"] for m in message_metrics),
            "tool_canonical_json_utf8_bytes": sum(t["canonical_json_utf8_bytes"] for t in tools),
        },
        "instruction_files": instructions, **metadata,
        "mcp_servers": servers, "tools": {"observed": tools, "advertised": advertised},
        "builtin_tools": builtins,
        "builtin_prompt": {
            "status": "unknown",
            "exact_utf8_bytes": None, "reason": "no_authenticated_prompt_origin_boundary",
            "system_message_utf8_bytes": system_bytes,
            "source_matched_union_utf8_bytes": union,
            "unclassified_system_message_utf8_bytes": system_bytes - union,
            "source_matched_union_accounting_complete": instructions["system_union_accounting_complete"],
            "source_matched_union_is_lower_bound": not instructions["system_union_accounting_complete"],
            "unclassified_remainder_is_upper_bound": not instructions["system_union_accounting_complete"],
        },
        "coverage": {
            "matching_proves": "text_presence_only", "origin_authenticated": False,
            "full_inventory_proven": False, "file_reads_proven": False,
            "byte_offsets": "half_open_decoded_string_utf8",
            "json_byte_metrics": "ensure_ascii_false_sorted_keys_compact",
            "source_match_scope": "instruction_inventory_text_only",
            "metadata": metadata_coverage,
            "mcp": mcp_coverage,
        },
        "warnings": warnings,
    }
