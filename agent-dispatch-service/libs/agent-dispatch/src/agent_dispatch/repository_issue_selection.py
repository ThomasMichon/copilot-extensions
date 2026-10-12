"""Selection helpers for repository-issue-loop issue discovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .registrar import RegistrarError


def validate_issue_numbers(
    data: Mapping[str, Any], key: str = "issue_numbers"
) -> tuple[int, ...]:
    value = data.get(key, ())
    if value in (None, (), []):
        return ()
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0
        for item in value
    ):
        raise RegistrarError(
            f"repository-issue-loop {key}: expected a list of positive integers"
        )
    return tuple(dict.fromkeys(value))


def eligible_issues(
    config: Mapping[str, Any],
    issues: Sequence[Any],
    *,
    now: float,
    latest_reservations: Callable[[Any], Mapping[str, Mapping[str, Any]]],
) -> list[Any]:
    selected_issue_numbers = tuple(int(n) for n in config.get("issue_numbers", ()))
    selected_order = {number: index for index, number in enumerate(selected_issue_numbers)}
    include = set(config["include_labels"])
    exclude = set(config["exclude_labels"]) | {"bootstrap"}
    priorities = {
        label: index for index, label in enumerate(config["priority_labels"])
    }

    def rank(issue: Any) -> tuple[int, float, int]:
        issue_ranks = [priorities[label] for label in issue.labels if label in priorities]
        return (min(issue_ranks, default=len(priorities)), issue.created_at, issue.number)

    selected = []
    for issue in issues:
        if selected_order and issue.number not in selected_order:
            continue
        labels = set(issue.labels)
        if include and not include <= labels:
            continue
        if labels & exclude:
            continue
        if now - issue.updated_at < config["quiet_period_seconds"]:
            continue
        if any(
            reservation.get("state") in {"reserved", "claimed"}
            for reservation in latest_reservations(issue).values()
        ):
            continue
        selected.append(issue)
    selected = (
        sorted(selected, key=lambda issue: selected_order[issue.number])
        if selected_order else sorted(selected, key=rank)
    )
    if selected_order and len(selected) != len(selected_issue_numbers):
        return []
    return selected[:config["batch_size"]]
