"""Test-local budget selection; production has no factor override."""

import os
from pathlib import Path
import re


def receipt_budget_factor(environment=None):
    environment = os.environ if environment is None else environment
    value = environment.get("RECEIPT_TEST_BUDGET_FACTOR", "1")
    if value not in ("1", "2", "3"):
        raise ValueError("RECEIPT_TEST_BUDGET_FACTOR must be exactly 1, 2 or 3")
    return int(value)


def validate_receipt_fixture_budgets(client_root, provider_root, factor):
    if type(factor) is not int or factor not in (1, 2, 3):
        raise ValueError("receipt fixture factor must be an integer 1, 2 or 3")
    client = Path(client_root) / "extensions" / "context-handoff"
    expectations = (
        (client / "cli-timeouts.mjs",
         r"export const PROMPT_RECEIPT_TIMEOUT_MS = (\d+);", 2000 * factor),
        (client / "resident-receipt-client.mjs",
         r"export const SETTLEMENT_RESERVE_MS = (\d+);", 100 * factor),
        (Path(provider_root) / "handoff_prompt_receipt_control.py",
         r"min\(remaining, (\d+)\) / 1000", 2000 * factor),
    )
    for path, pattern, expected in expectations:
        matches = re.findall(pattern, path.read_text(encoding="utf-8"))
        if matches != [str(expected)]:
            raise ValueError(f"receipt fixture time constant mismatch: {path.name}")
    return {
        "budgetFactor": factor,
        "nominalBudgetMs": 2000,
        "experimentBudgetMs": 2000 * factor,
        "settlementReserveMs": 100 * factor,
        "preEffectMs": 1000 * factor,
        "traceLockMs": 100 * factor,
        "scanMs": 200 * factor,
    }
