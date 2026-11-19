"""Azure DevOps acquisition response parsing."""

from __future__ import annotations

from typing import Any


def work_item_ids(data: Any) -> list[int]:
    """Parse query identities without treating an unknown response as empty."""
    query_types = ("flat", "oneHop", "tree")
    if not isinstance(data, dict):
        raise ValueError("Azure DevOps query response is not an object")
    query_type = data.get("queryType")
    if query_type is not None and query_type not in query_types:
        raise ValueError("Azure DevOps query response has an invalid query type")
    collections: dict[str, list[Any]] = {}
    for key in ("workItems", "workItemRelations"):
        value = data.get(key)
        if value is None and query_type in query_types:
            continue
        if key in data:
            if not isinstance(value, list):
                raise ValueError("Azure DevOps query response has an invalid collection")
            collections[key] = value
    # Query collections use EmitDefaultValue=false; QueryType does not.
    if not collections and query_type not in query_types:
        raise ValueError("Azure DevOps query response has no recognized result")

    ids: list[int] = []
    for item in collections.get("workItems", []):
        if not isinstance(item, dict) or type(item.get("id")) is not int:
            raise ValueError("Azure DevOps query response has an invalid work-item reference")
        ids.append(item["id"])
    for relation in collections.get("workItemRelations", []):
        if not isinstance(relation, dict):
            raise ValueError("Azure DevOps query response has an invalid relation")
        target = relation.get("target")
        if target is None:
            continue
        if not isinstance(target, dict) or type(target.get("id")) is not int:
            raise ValueError("Azure DevOps query response has an invalid target reference")
        ids.append(target["id"])
    return ids


def collection_values(data: Any, *, context: str = "collection response") -> list[dict[str, Any]]:
    """Accept an explicit value array or its legacy unwrapped representation."""
    values = data.get("value") if isinstance(data, dict) else data
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise ValueError(f"Azure DevOps {context} has invalid records")
    return values
