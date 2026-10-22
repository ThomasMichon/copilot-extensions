"""Shared task/exclusive/logical-workspace matching within one retained queue."""

from __future__ import annotations


def execution_lineage_match_sql(retained: str, current: str) -> str:
    # Repository/dedup identity also covers direct claims without reservations.
    return (
        f"({retained}.id = {current}.id"
        f" OR {retained}.exclusive_key = {current}.exclusive_key"
        f" OR ({retained}.repo = {current}.repo"
        f" AND {retained}.dedup_key = {current}.dedup_key))"
    )
