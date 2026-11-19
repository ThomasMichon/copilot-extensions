"""Execute the constructed SQL-compatible WIQL subset against an independent oracle."""

from __future__ import annotations

import sqlite3
from typing import cast

import pytest

from agent_index.sources.azure_devops_query_scope import restrict_wiql_to_candidates


def _selected(query: str) -> list[int]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE WorkItems "
            "([System.Id] INTEGER, [System.State] TEXT, [System.Title] TEXT)",
        )
        connection.executemany(
            "INSERT INTO WorkItems VALUES (?, ?, ?)",
            [
                (1, "Active", "Alpha"),
                (2, "Closed", "Beta"),
                (3, "Active", "Gamma"),
                (4, "Resolved", 'OR AND café "quoted"'),
                (5, "Closed", "@Today"),
            ],
        )
        return [row[0] for row in connection.execute(query)]
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("predicate", "candidates", "original", "restricted"),
    [
        (
            "[System.State] = 'Active' OR [System.Title] = 'Beta' AND [System.Id] > 1",
            [2, 4],
            [3, 2, 1],
            [2],
        ),
        (
            "([System.State] = 'Active' OR [System.Title] = 'Beta') AND [System.Id] >= 2",
            [1, 2, 4],
            [3, 2],
            [2],
        ),
        ("[System.Title] = 'OR AND café \"quoted\"'", [4, 5], [4], [4]),
        ("[System.Title] = '@Today'", [1, 5], [5], [5]),
        (
            "[System.State] IN ('Active', 'Resolved') AND [System.Id] NOT IN (3, 5)",
            [1, 2, 4],
            [4, 1],
            [4, 1],
        ),
        ("[System.Id] < 3.5", [4, 2, 2], [3, 2, 1], [2]),
        ("[System.Title] = 'Absent'", [1, 2], [], []),
    ],
)
def test_restriction_preserves_selection_precedence_literals_and_order(
    predicate: str,
    candidates: list[int],
    original: list[int],
    restricted: list[int],
) -> None:
    query = (
        "sElEcT [System.Id], [System.Title]\nFROM WorkItems\n"
        f"WHERE {predicate}\nORDER BY [System.Id] DESC  "
    )
    assert _selected(query) == original
    assert _selected(restrict_wiql_to_candidates(query, candidates)) == restricted


def test_unordered_query_and_full_candidate_budget() -> None:
    query = "SELECT [System.Id] FROM WorkItems WHERE [System.Id] >= 1"
    assert _selected(restrict_wiql_to_candidates(query, tuple(range(2, 102)))) == [2, 3, 4, 5]


@pytest.mark.parametrize(
    "query",
    [
        "SELECT [System.Id] FROM WorkItemLinks WHERE [System.Id] = 1",
        "SELECT [System.Id] FROM WorkItems WHERE [System.Id] = 1 ASOF '2026-01-01'",
        "SELECT [System.Id] FROM WorkItems WHERE [System.Id] = 1; SELECT [System.Id] FROM WorkItems",
        "SELECT [System.Id] FROM WorkItems WHERE [System.Id] = 1 ORDER BY [System.Id] sideways",
        *[
            f"SELECT [System.Id] FROM WorkItems WHERE {predicate}"
            for predicate in [
                "[System.Title] = @Me",
                "[System.Id] = [Custom.OtherId]",
                "[System.Title] IN GROUP 'Team'",
                "[System.Title] WAS EVER 'Active'",
                "[System.Title] = 'O''Brien'",
                "[System.Title] = 'path\\value'",
                '[System.Title] = "Double quoted"',
                "[System.Id] = 1 -- comment",
                "[System.Id] IN ()",
                "([System.Id] = 1 OR [System.Id] = 2",
                "[System.Id] = 1 AND",
                "[System.Id] = 1OR [System.Id] = 2",
                "[System.Id] = 1 [System.Id] = 2",
            ]
        ],
    ],
)
def test_unsupported_syntax_cannot_become_unrestricted_selection(query: str) -> None:
    with pytest.raises(ValueError, match="Azure DevOps bounded WIQL"):
        restrict_wiql_to_candidates(query, [1])


@pytest.mark.parametrize("candidates", [[], [0], [-1], [True], ["1"], [1.0], [2**31], [1] * 101])
def test_invalid_candidate_inputs_refuse(candidates: list[object]) -> None:
    with pytest.raises(ValueError, match="candidate IDs"):
        restrict_wiql_to_candidates(
            "SELECT [System.Id] FROM WorkItems WHERE [System.Id] > 0",
            cast(list[int], candidates),
        )


@pytest.mark.parametrize("query", ["", " ", "x" * 32_001, None])
def test_invalid_query_inputs_refuse(query: object) -> None:
    with pytest.raises(ValueError, match="nonempty query"):
        restrict_wiql_to_candidates(cast(str, query), [1])


def test_grouping_and_output_growth_are_bounded() -> None:
    prefix = "SELECT [System.Id] FROM WorkItems WHERE "
    with pytest.raises(ValueError, match="grouping budget"):
        restrict_wiql_to_candidates(prefix + "(" * 33 + "[System.Id] = 1" + ")" * 33, [1])
    with pytest.raises(ValueError, match="token budget"):
        restrict_wiql_to_candidates(prefix + " OR ".join(["[System.Id] = 1"] * 1100), [1])
    query = prefix + "[System.Title] = '" + "x" * 31_900 + "'"
    with pytest.raises(ValueError, match="restriction exceeds"):
        restrict_wiql_to_candidates(query, list(range(1, 101)))
