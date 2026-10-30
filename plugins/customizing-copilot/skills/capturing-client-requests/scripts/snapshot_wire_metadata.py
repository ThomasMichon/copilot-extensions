"""Recognize rendered metadata, never infer loaded bodies or runtime provenance."""

from __future__ import annotations

import html
from itertools import chain, islice, zip_longest
import json
import re
from typing import Any

MAX_METADATA_RECORDS = 1024
MAX_METADATA_INDEX_ENTRIES = 8192
MAX_CORRELATIONS_PER_RECORD = 16
MAX_METADATA_CORRELATIONS = 2048
MAX_METADATA_COMPARISON_CHARACTERS = 128 * 1024 * 1024
MAX_MARKER_CANDIDATES = 1024
MAX_MARKER_CHARACTERS = 32 * 1024 * 1024
UTF8_CHECKPOINT_STRIDE = 4096


class _CorrelationState:
    def __init__(self, inventory: dict[str, Any]) -> None:
        self.warnings: list[dict[str, Any]] = []
        self.coverage: dict[str, Any] = {
            "complete": True, "record_limit": MAX_METADATA_RECORDS,
            "records_retained": 0, "records_omitted_lower_bound": 0,
            "records_complete": True,
            "index_entry_limit": MAX_METADATA_INDEX_ENTRIES, "index_entries_retained": 0,
            "index_complete": {"skills": True, "agents": True, "tools": True},
            "correlation_per_record_limit": MAX_CORRELATIONS_PER_RECORD,
            "correlation_global_limit": MAX_METADATA_CORRELATIONS,
            "correlations_retained": 0, "correlations_omitted": 0,
            "correlation_counts_scope": "retained_metadata_records",
            "correlation_omitted_is_lower_bound": False,
            "prefix_candidates_examined": 0, "prefix_candidates_omitted": 0,
            "comparison_character_limit": MAX_METADATA_COMPARISON_CHARACTERS,
            "comparison_characters_charged": 0, "comparison_work_complete": True,
        }
        self.indexes: dict[str, dict[str, list[tuple[dict[str, Any], str, str]]]] = {}
        self._warning_keys: set[tuple[str, str]] = set()
        for kind in ("skills", "agents"):
            self.indexes[kind] = self._index(inventory.get(kind, []), kind)
        self.indexes["tools"] = {}

    def warn(self, code: str, kind: str) -> None:
        key = (code, kind)
        if key not in self._warning_keys:
            self.warnings.append({"code": code, "pointer": "/inventory/" + kind})
            self._warning_keys.add(key)
        self.coverage["complete"] = False

    def charge(self, characters: int, kind: str) -> bool:
        cost = max(1, characters)
        if self.coverage["comparison_characters_charged"] + cost > MAX_METADATA_COMPARISON_CHARACTERS:
            self.coverage["comparison_work_complete"] = False
            self.warn("metadata_comparison_work_limit", kind)
            return False
        self.coverage["comparison_characters_charged"] += cost
        return True

    def _index(
        self, sources: list[dict[str, Any]], kind: str,
    ) -> dict[str, list[tuple[dict[str, Any], str, str]]]:
        index: dict[str, list[tuple[dict[str, Any], str, str]]] = {}
        for number, source in enumerate(sources):
            aliases = source.get("aliases", [])
            aliases = aliases if isinstance(aliases, list) else []
            seen = set()
            for name in chain((source.get("name"),), aliases):
                if not isinstance(name, str):
                    continue
                if (
                    self.coverage["index_entries_retained"] >= MAX_METADATA_INDEX_ENTRIES
                    or not self.charge(len(name) + 1, kind)
                ):
                    self.coverage["index_complete"][kind] = False
                    self.coverage["correlation_omitted_is_lower_bound"] = True
                    self.warn("metadata_source_index_truncated", kind)
                    return index
                if not name or name in seen:
                    continue
                seen.add(name)
                method = "name" if name == source.get("name") else "aliases"
                index.setdefault(name, []).append((source, f"/inventory/{kind}/{number}", method))
                self.coverage["index_entries_retained"] += 1
        return index

    def claim_record(self, end: int, size: int, kind: str) -> bool:
        if (
            self.coverage["records_retained"] >= MAX_METADATA_RECORDS
            or not self.charge(2 * end + size, kind)
        ):
            self.coverage["records_complete"] = False
            self.coverage["records_omitted_lower_bound"] += 1
            self.coverage["correlation_omitted_is_lower_bound"] = True
            self.warn("metadata_records_truncated", kind)
            return False
        self.coverage["records_retained"] += 1
        return True


def evidence(
    region: dict[str, Any], start: int, end: int, budget: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """Cache sparse UTF-8 checkpoints; never repeatedly encode whole prefixes."""
    text = region["text"]
    if not 0 <= start <= end <= len(text):
        raise ValueError("invalid evidence span")
    offsets = region.setdefault("_utf8_checkpoints", [0])
    stride = UTF8_CHECKPOINT_STRIDE
    work = max(0, end // stride - len(offsets) + 1) * stride + start % stride + end % stride
    if budget is not None and not _marker_charge(budget, work):
        return None
    while len(offsets) <= end // stride:
        base = (len(offsets) - 1) * stride
        offsets.append(offsets[-1] + len(text[base:base + stride].encode("utf-8")))
    positions = [offsets[pos // stride] + len(text[pos // stride * stride:pos].encode("utf-8"))
                 for pos in (start, end)]
    return {
        "pointer": region["pointer"], "start_utf8_byte": positions[0], "end_utf8_byte": positions[1],
    }


def _marker_charge(budget: dict[str, int], characters: int) -> bool:
    if budget["used"] + characters > MAX_MARKER_CHARACTERS:
        return False
    budget["used"] += characters
    return True


def collect_markers(regions: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """Bound syntax candidates, parsing, content sizing, and byte-offset work."""
    selector = kind == "selector_reference"
    opening = re.compile(r"<!--\s*copilot-extension-instruction-projection(?=\s|$)" if selector else
                         r"<(memory|memories|user_memory|user_memories|memory_context|stored_memories)\b")
    budget = {"used": 0}
    coverage = {"candidate_limit": MAX_MARKER_CANDIDATES, "candidates_examined": 0,
                "retained_count": 0, "omitted_candidates_lower_bound": 0, "failed_candidates": 0, "complete": True,
                "counts_scope": "syntactic_marker_candidates", "work_character_limit": MAX_MARKER_CHARACTERS}
    records: list[dict[str, Any]] = []
    warnings: dict[str, str] = {}
    for region in regions:
        if not _marker_charge(budget, len(region["text"])):
            coverage["complete"] = False
            warnings[kind + "_work_limit"] = region["pointer"]
            break
        text, cursor = region["text"], 0
        # The prepaid region scan moves forward only; a failed closer search ends it.
        while head := opening.search(text, cursor):
            if coverage["candidates_examined"] >= MAX_MARKER_CANDIDATES:
                warnings[kind + "_evidence_truncated"] = region["pointer"]
                coverage["complete"] = False
                coverage["omitted_candidates_lower_bound"] += 1
                break
            coverage["candidates_examined"] += 1
            body_start = head.end() if selector else text.find(">", head.end())
            body_start += 0 if selector or body_start < 0 else 1
            closer = "-->" if selector else "</" + head[1] + ">"
            body_end = text.find(closer, body_start) if body_start >= 0 else -1
            if body_end < 0:
                warnings[kind + "_unterminated"] = region["pointer"]
                coverage["failed_candidates"] += 1
                coverage["complete"] = False
                coverage["omitted_candidates_lower_bound"] += 1
                break
            cursor = body_end + len(closer)
            if not _marker_charge(budget, 2 * (cursor - head.start())):
                warnings[kind + "_work_limit"] = region["pointer"]
                coverage["complete"] = False
                coverage["omitted_candidates_lower_bound"] += 1
                break
            if selector:
                try:
                    payload = json.loads(text[body_start:body_end])
                except json.JSONDecodeError:
                    warnings.setdefault("invalid_projection_marker_json", region["pointer"])
                    coverage["failed_candidates"] += 1
                    continue
                if not isinstance(payload, dict) or payload.get("deliveryKind") != "selector":
                    continue
            location = evidence(region, head.start(), cursor, budget)
            if location is None:
                warnings[kind + "_work_limit"] = region["pointer"]
                coverage["complete"] = False
                coverage["omitted_candidates_lower_bound"] += 1
                break
            if selector:
                destination = payload.get("destination")
                records.append({
                    "path": destination if isinstance(destination, str) else None,
                    "representation": "selector_reference", "evidence": location,
                    "full_content_proven": False, "origin_authenticated": False,
                })
            else:
                records.append({"marker": head[1], "evidence": location,
                                "content_utf8_bytes": len(text[body_start:body_end].encode("utf-8"))})
        if not coverage["complete"]:
            break
    coverage.update(retained_count=len(records), work_characters_charged=budget["used"],
                    retained_count_is_lower_bound=not coverage["complete"])
    return {"records": records, "coverage": coverage,
            "warnings": [{"code": code, "pointer": pointer} for code, pointer in warnings.items()]}


def field(block: str, name: str) -> str | None:
    match = re.search(rf"<{name}>([\s\S]*?)</{name}>", block)
    return html.unescape(match[1].strip()) if match else None


def _whitespace_equal(left: str, right: str) -> bool:
    return all(
        a is not None and b is not None and a[0] == b[0]
        for a, b in zip_longest(re.finditer(r"\S+", left), re.finditer(r"\S+", right))
    )


def _correlate(
    name: str, description: str | None, region: dict[str, Any],
    state: _CorrelationState, kind: str,
) -> tuple[list[dict[str, Any]], int, bool]:
    candidates = state.indexes[kind].get(name, [])
    complete = state.coverage["index_complete"][kind]
    matches = []
    limit = min(MAX_CORRELATIONS_PER_RECORD, max(
        0, MAX_METADATA_CORRELATIONS - state.coverage["correlations_retained"],
    ))
    for source, pointer, identity in islice(candidates, limit):
        methods = ["name_exact" if identity == "name" else "alias_exact"]
        fields = [identity]
        fields_complete = True
        source_description = source.get("description")
        if description is not None and isinstance(source_description, str):
            if not state.charge(4 * (len(description) + len(source_description)), kind):
                fields_complete = False
            elif description == source_description:
                methods.append("description_exact")
                fields.append("description")
            elif _whitespace_equal(description, source_description):
                methods.append("description_whitespace_normalized")
                fields.append("description")
        frontmatter = source.get("frontmatter")
        frontmatter_present: bool | None = False
        if isinstance(frontmatter, str) and frontmatter:
            if state.charge(2 * len(frontmatter) + len(region["text"]), kind):
                frontmatter_present = bool(frontmatter.strip()) and frontmatter in region["text"]
            else:
                frontmatter_present, fields_complete = None, False
        if frontmatter_present:
            methods.append("frontmatter_text_exact")
            fields.append("frontmatter")
        matches.append({
            "path": source.get("path"), "inventory_pointer": pointer,
            "match_methods": methods,
            "matching_source_fields": fields,
            "frontmatter_text_present": frontmatter_present,
            "field_comparisons_complete": fields_complete,
        })
    omitted = len(candidates) - len(matches)
    state.coverage["correlations_retained"] += len(matches)
    state.coverage["correlations_omitted"] += omitted
    if omitted:
        state.warn("metadata_correlations_truncated", kind)
    return matches, len(candidates), complete


def _record(
    name: str, description: str | None, fields: list[str], representation: str,
    region: dict[str, Any], start: int, end: int, state: _CorrelationState, kind: str,
) -> dict[str, Any] | None:
    if not state.claim_record(end, len(name) + len(description or ""), kind):
        return None
    matches, count, complete = _correlate(name, description, region, state, kind)
    return {
        "name": name, "representation": representation, "observed_fields": fields,
        "description_utf8_bytes": (
            len(description.encode("utf-8")) if description is not None else None
        ),
        "evidence": evidence(region, start, end), "source_matches": matches,
        "source_matches_count": count, "source_matches_omitted_count": count - len(matches),
        "source_matches_count_is_lower_bound": not complete,
        "source_matches_complete": complete and count == len(matches),
        "source_field_comparisons_complete": (
            complete and count == len(matches)
            and all(match["field_comparisons_complete"] for match in matches)
        ),
        "ambiguous": True if count > 1 else (False if complete else None),
        "origin_authenticated": False,
        "loaded_body_proven": False,
    }


def _xml_records(
    regions: list[dict[str, Any]], container: str, item: str,
    state: _CorrelationState, kind: str,
) -> list[dict[str, Any]]:
    records = []
    for region in regions:
        for scope in re.finditer(rf"<{container}\b[^>]*>([\s\S]*?)</{container}>",
                                 region["text"]):
            for entry in re.finditer(rf"<{item}\b[^>]*>([\s\S]*?)</{item}>", scope[1]):
                name = field(entry[1], "name")
                if not name:
                    continue
                observed = [
                    key for key in ("name", "description", "location", "path")
                    if field(entry[1], key) is not None
                ]
                record = _record(
                    name, field(entry[1], "description"), observed, "rendered_metadata",
                    region, scope.start(1) + entry.start(),
                    scope.start(1) + entry.end(), state, kind,
                )
                if record is None:
                    return records
                records.append(record)
    return records


def _skills(
    regions: list[dict[str, Any]], state: _CorrelationState,
) -> list[dict[str, Any]]:
    records = _xml_records(regions, "available_skills", "skill", state, "skills")
    prefix = r"(?m)^[ \t]*Additional skills available \(invoke by name\):[ \t]*([^\r\n]+)"
    for region in regions:
        for scope in re.finditer(r"<available_skills\b[^>]*>([\s\S]*?)</available_skills>",
                                 region["text"]):
            for names in re.finditer(prefix, scope[1]):
                for token in re.finditer(r"[^,]+", names[1]):
                    name = token[0].strip()
                    if not name:
                        continue
                    offset = scope.start(1) + names.start(1) + token.start()
                    leading = len(token[0]) - len(token[0].lstrip())
                    record = _record(
                        name, None, ["name"], "name_only", region,
                        offset + leading, offset + leading + len(name), state, "skills",
                    )
                    if record is None:
                        return records
                    records.append(record)
    return records


_AGENT_HEADING = re.compile(
    r"(?m)^(Available agent types|User-provided custom agents):[ \t]*\r?$"
)
_AGENT_BULLET = re.compile(r"(?m)^-[ \t]+\*\*([^\r\n]+?)\*\*:[ \t]*([^\r\n]*)")


def _source_description_end(name: str, text: str, state: _CorrelationState) -> int | None:
    candidates = state.indexes["agents"].get(name, [])
    ends = []
    examined = 0
    for source, _, _ in islice(candidates, MAX_CORRELATIONS_PER_RECORD):
        description = source.get("description")
        if not isinstance(description, str):
            examined += 1
            continue
        if not state.charge(2 * (len(text) + len(description)), "agents"):
            break
        examined += 1
        actual = re.finditer(r"\S+", text)
        end = None
        for expected in re.finditer(r"\S+", description):
            word = next(actual, None)
            if word is None or word[0] != expected[0]:
                end = None
                break
            end = word.end()
        if end is not None:
            ends.append(end)
    state.coverage["prefix_candidates_examined"] += examined
    state.coverage["prefix_candidates_omitted"] += len(candidates) - examined
    if examined < len(candidates):
        state.warn("metadata_prefix_candidates_truncated", "agents")
    return max(ends) if ends else None


def _agents(
    regions: list[dict[str, Any]], definitions: list[dict[str, Any]],
    state: _CorrelationState,
) -> list[dict[str, Any]]:
    records = _xml_records(regions, "available_agents", "agent", state, "agents")
    for definition in definitions:
        schema = definition["schema"]
        if not isinstance(schema, dict):
            continue
        props = schema.get("properties", {})
        if not isinstance(props, dict) or not any(
            name in props for name in ("agent_type", "agent", "agent_name")
        ):
            continue
        region = definition["description_region"]
        text = region["text"]
        registered = {
            name for selector in definition["properties"]
            if selector["name"] in ("agent_type", "agent", "agent_name")
            for name in selector.get("enum_names", [])
        }
        headings = list(_AGENT_HEADING.finditer(text))
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            remaining = max(0, MAX_METADATA_RECORDS - state.coverage["records_retained"])
            bullets = list(islice((
                bullet for bullet in _AGENT_BULLET.finditer(text, heading.end(), end)
                if not registered or bullet[1] in registered
            ), remaining + 1))
            for number, bullet in enumerate(bullets):
                stop = bullets[number + 1].start() if number + 1 < len(bullets) else end
                # Without a schema/source boundary, consume only the indented prefix.
                continuation = re.match(
                    r"(?:\r?\n(?:[ \t]+[^\r\n]*|[ \t]*(?=\r?\n|$)))*",
                    text[bullet.end():stop],
                )
                record_end = bullet.end() + (continuation.end() if continuation else 0)
                scope = "inline_and_indented_prefix"
                if registered and number + 1 < len(bullets):
                    record_end, scope = stop, "between_registered_records"
                else:
                    source_end = _source_description_end(bullet[1], text[bullet.start(2):stop], state)
                    if source_end is not None:
                        record_end = bullet.start(2) + source_end
                        scope = "source_description_prefix"
                description = text[bullet.start(2):record_end].strip()
                record = _record(
                    bullet[1], description, ["name", "description"], "rendered_metadata",
                    region, bullet.start(), record_end, state, "agents",
                )
                if record is None:
                    return records
                record["section"] = (
                    "builtin_types" if heading[1] == "Available agent types" else "custom_types"
                )
                record["description_scope"] = scope
                records.append(record)
                if not registered and text[record_end:stop].strip():
                    break
        for selector in definition["properties"]:
            if selector["name"] not in ("agent_type", "agent", "agent_name"):
                continue
            for name in selector.get("enum_names", []):
                record = _record(
                    name, None, ["name"], "schema_enum", region, 0, 0, state, "agents",
                )
                if record is None:
                    return records
                record["evidence"] = {"pointer": selector["pointer"]}
                records.append(record)
    return records


def _selectors(properties: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = {
        "agent", "agent_type", "agent_name", "skill", "skill_name",
        "tool", "tool_name", "name", "server", "server_name", "namespace",
    }
    return [
        {
            "name": prop["name"], "pointer": prop["pointer"],
            "enum_names": prop.get("enum_names", []),
            "enum_count": prop["enum_count"],
            "free_string": (
                "string" in prop["types"] and not prop.get("enum_constrained", False)
            ),
        }
        for prop in properties if prop["name"] in names
    ]


def _discovery(
    definitions: list[dict[str, Any]], skills: list[dict[str, Any]],
    agents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    surfaces = []
    for definition in definitions:
        name = definition["name"] or ""
        props = definition["properties"]
        prop_names = {prop["name"] for prop in props}
        kinds = []
        if prop_names & {"agent", "agent_type", "agent_name"}:
            kinds.append("subagent_invocation")
        if prop_names & {"skill", "skill_name"}:
            kinds.append("skill_invocation")
        if re.search(r"(?:^|[_.:/-])(?:search|discover|find)(?:$|[_.:/-])", name):
            kinds.append(
                "skill_search" if "skill" in name else
                "task_search" if "task" in name else
                "agent_search" if "agent" in name else
                "tool_search" if "tool" in name else "search_surface"
            )
        if re.search(r"(?:^|[_.:/-])(?:list_resources|find_tool|load_tools|execute_tool)$",
                     name):
            kinds.append("catalog_surface")
        if not kinds:
            continue
        pointer = definition["pointer"]
        surfaces.append({
            "name": definition["name"], "kinds": kinds, "pointer": pointer,
            "input_properties": props, "selectors": _selectors(props),
            "referenced_metadata_counts": {
                "skills": sum(
                    record["evidence"]["pointer"] == pointer + "/description"
                    for record in skills
                ),
                "subagents": sum(
                    record["representation"] == "rendered_metadata"
                    and record["evidence"]["pointer"] == pointer + "/description"
                    for record in agents
                ),
            },
            "results_observed": None, "complete_catalog_proven": False,
        })
    return surfaces


def analyze_metadata(
    regions: list[dict[str, Any]], definitions: list[dict[str, Any]],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    """Return redacted metadata and discovery sections from recognized containers."""
    state = _CorrelationState(inventory)
    skills = _skills(regions, state)
    agents = _agents(regions, definitions, state)
    advertised = _xml_records(regions, "available_tools", "tool", state, "tools")
    for record in advertised:
        record["schema_status"] = "advertised_only"
        record["pointer"] = record["evidence"]["pointer"]
        record["ownership"] = "unknown"
    memory_candidates = collect_markers(regions, "memory_marker")
    state.warnings.extend(memory_candidates["warnings"])
    state.coverage["complete"] &= memory_candidates["coverage"]["complete"]
    return {
        "metadata_coverage": state.coverage,
        "metadata_warnings": state.warnings,
        "skills": {
            "observed": skills,
            "counts_scope": "retained_metadata_records",
            "counts_complete": state.coverage["records_complete"],
            "rendered_metadata_count": sum(
                record["representation"] == "rendered_metadata" for record in skills
            ),
            "name_only_count": sum(record["representation"] == "name_only" for record in skills),
        },
        "subagents": {
            "observed": agents,
            "counts_scope": "retained_metadata_records",
            "counts_complete": state.coverage["records_complete"],
            "rendered_metadata_count": sum(
                record["representation"] == "rendered_metadata" for record in agents
            ),
            "enum_name_count": sum(record["representation"] == "schema_enum" for record in agents),
        },
        "discovery": {
            "surfaces": _discovery(definitions, skills, agents),
            "metadata_counts_complete": state.coverage["records_complete"],
            "observed_metadata_counts": {
                "skill_records": len(skills), "subagent_records": len(agents),
                "advertised_tool_records": len(advertised),
            },
            "complete_deferred_catalog_proven": False,
        },
        "memories": {
            "status": "unknown",
            "exact_count": None, "exact_utf8_bytes": None,
            "reason": "no_authoritative_memory_boundary",
            "candidate_markup": memory_candidates["records"],
            "candidate_evidence": memory_candidates["coverage"],
        },
        "advertised_tools": advertised,
    }
