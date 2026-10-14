"""Observed check diagnostics, distinct from required merge-policy evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TextIO


@dataclass(frozen=True)
class CheckFailure:
    """One failing provider observation; requiredness is deliberately unknown."""

    name: str
    state: str
    source: str
    url: str = ""
    description: str = ""


@dataclass(frozen=True)
class ChecksReport:
    """Rollup and bounded diagnostics from the same provider reads."""

    state: str = ""
    failures: tuple[CheckFailure, ...] = ()
    complete: bool = False
    omitted: int = 0


def checks_payload(
    state: str, failures: tuple[CheckFailure, ...], *,
    complete: bool | None = None, omitted: int = 0,
) -> dict:
    """Serialize observed failures without implying required-check completeness."""
    return {
        "checks_state": state,
        "checks_scope": "observed",
        "checks_complete": complete,
        "check_failures": [asdict(failure) for failure in failures],
        "check_failures_omitted": omitted,
    }


def print_check_failures(payload: dict, *, stream: TextIO, prefix: str) -> None:
    """Render diagnostic names and links on the human watch/status surfaces."""
    if payload.get("checks_complete") is False:
        print(f"{prefix}  check evidence is incomplete", file=stream)
    failures = payload.get("check_failures") or []
    if not failures:
        if payload.get("checks_state") == "failure":
            print(f"{prefix}observed checks failed; named diagnostics unavailable", file=stream)
        return
    print(f"{prefix}observed failures (not a required-CI verdict):", file=stream)
    for failure in failures:
        print(
            f"{prefix}  {failure['name']}: {failure['state']} [{failure['source']}]",
            file=stream,
        )
        if failure.get("description"):
            print(f"{prefix}    {failure['description']}", file=stream)
        if failure.get("url"):
            print(f"{prefix}    {failure['url']}", file=stream)
    if payload.get("check_failures_omitted"):
        print(f"{prefix}  {payload['check_failures_omitted']} more failures omitted", file=stream)
