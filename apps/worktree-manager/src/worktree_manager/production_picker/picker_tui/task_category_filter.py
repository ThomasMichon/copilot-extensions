"""Present Dispatch categories through one Tasks provider and a local filter."""
from __future__ import annotations

from dataclasses import replace


def dispatch_category(pivot):
    if pivot is None:
        return None
    owner = (pivot.source_owner or "").split("@", 1)[0]
    if (owner and owner != "agent-dispatch") or (
        not owner and pivot.name not in ("agent-dispatch", "agent-dispatch-maintenance")
    ):
        return None
    command = pivot.list_cmd
    if "--category" not in command:
        return None
    index = command.index("--category") + 1
    return command[index] if index < len(command) else None


def combine_task_categories(descriptors):
    """Keep one all-category subscription per attributed Dispatch command."""
    result, providers = [], {}
    for descriptor in descriptors:
        pivot = descriptor.get("pivot")
        category = dispatch_category(pivot)
        if category not in ("work", "maintenance", "all"):
            result.append(descriptor)
            continue
        command = list(pivot.list_cmd)
        command[command.index("--category") + 1] = "all"
        if "--limit-per-category" not in command:
            command.append("--limit-per-category")
        key = (pivot.source_owner, tuple(command))
        combined = {
            **descriptor, "label": "Tasks",
            "pivot": replace(
                pivot, label="Tasks", list_cmd=tuple(command),
                empty_hint="No work tasks in this scope. Enable Include maintenance to reveal maintenance tasks.",
            ),
        }
        if key not in providers:
            providers[key] = (len(result), category)
            result.append(combined)
        elif category == "work" and providers[key][1] != "work":
            index, _ = providers[key]
            result[index] = combined
            providers[key] = (index, category)
    return result


def visible_tasks(pivot, rows, include_maintenance):
    if dispatch_category(pivot) is None or include_maintenance:
        return rows
    return [row for row in rows if row.get("category", "work") != "maintenance"]
