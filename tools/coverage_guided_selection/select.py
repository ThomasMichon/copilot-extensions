"""Diff-scoped test selection against a coverage baseline.

Pure stdlib, operates only on the portable JSON baseline `baseline.py`
produces -- no `coverage.py` dependency at selection time. This is the
"CI-time counterpart of the baseline's offline evidence" the vision's
`diff-scoped selection` Concept names.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FallbackReason:
    file: str
    line: int
    reason: str  # "no_baseline_entry" | "line_not_attributed"


@dataclass(frozen=True)
class SelectionResult:
    selected_tests: tuple[str, ...]
    fallback_triggered: bool
    fallback_reasons: tuple[FallbackReason, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "selected_tests": list(self.selected_tests),
            "fallback_triggered": self.fallback_triggered,
            "fallback_reasons": [
                {"file": r.file, "line": r.line, "reason": r.reason}
                for r in self.fallback_reasons
            ],
        }


def select_tests(baseline: dict, changed_lines: dict) -> SelectionResult:
    """Return the tests covering `changed_lines` per `baseline`.

    `changed_lines` maps a file path (relative the same way the baseline's
    own keys are) to the list of line numbers a diff touched in it.

    Per the vision's "never quieter than the evidence supports" Behavior: a
    changed line with no baseline entry at all, or whose file has *other*
    attributed lines but not this one (a partially-attributed file is not a
    fully-covered one), both trigger the fallback for that specific line --
    never a silently narrower selection.
    """
    selected: set[str] = set()
    reasons: list[FallbackReason] = []

    for file, lines in changed_lines.items():
        file_coverage = baseline.get("coverage", {}).get(file)
        for line in lines:
            if file_coverage is None:
                reasons.append(FallbackReason(file, line, "no_baseline_entry"))
                continue
            tests = file_coverage.get(str(line))
            if not tests:
                reasons.append(FallbackReason(file, line, "line_not_attributed"))
                continue
            selected.update(tests)

    return SelectionResult(
        selected_tests=tuple(sorted(selected)),
        fallback_triggered=bool(reasons),
        fallback_reasons=tuple(reasons),
    )
