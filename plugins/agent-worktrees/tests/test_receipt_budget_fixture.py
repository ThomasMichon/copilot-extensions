"""Pure preparation checks: no resident, backend or native lifecycle is launched."""

import importlib.util
from pathlib import Path

import pytest


HELPER = Path(__file__).with_name("native") / "receipt_budget_fixture.py"
SPEC = importlib.util.spec_from_file_location("receipt_budget_fixture_contract", HELPER)
budget = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(budget)


@pytest.mark.parametrize("value, expected", [(None, 1), ("1", 1), ("2", 2), ("3", 3)])
def test_factor_is_default_one_and_exact_integer_environment(value, expected):
    environment = {} if value is None else {"RECEIPT_TEST_BUDGET_FACTOR": value}
    result = budget.receipt_budget_factor(environment)
    assert type(result) is int and result == expected


@pytest.mark.parametrize("value", ["0", "4", "2.0", " 2", "2 ", "", 2, True])
def test_factor_rejects_ambiguous_or_unsupported_representation(value):
    with pytest.raises(ValueError):
        budget.receipt_budget_factor({"RECEIPT_TEST_BUDGET_FACTOR": value})


@pytest.mark.parametrize("factor", [True, 2.0, "2", 0, 4])
def test_fixture_contract_rejects_non_integer_factor_before_reading_files(factor):
    with pytest.raises(ValueError, match="must be an integer"):
        budget.validate_receipt_fixture_budgets("unused", "unused", factor)


@pytest.mark.parametrize("factor", [1, 2, 3])
def test_fixture_contract_requires_all_three_matching_time_constants(tmp_path, factor):
    client = tmp_path / "client"
    extensions = client / "extensions" / "context-handoff"
    extensions.mkdir(parents=True)
    provider = tmp_path / "provider"
    provider.mkdir()
    timeout = extensions / "cli-timeouts.mjs"
    timeout.write_text(f"export const PROMPT_RECEIPT_TIMEOUT_MS = {2000 * factor};\n")
    (extensions / "resident-receipt-client.mjs").write_text(
        f"export const SETTLEMENT_RESERVE_MS = {100 * factor};\n",
    )
    (provider / "handoff_prompt_receipt_control.py").write_text(
        f"deadline = now + min(remaining, {2000 * factor}) / 1000\n",
    )
    result = budget.validate_receipt_fixture_budgets(client, provider, factor)
    assert result["budgetFactor"] == factor
    assert result["nominalBudgetMs"] == 2000
    assert result["experimentBudgetMs"] == 2000 * factor
    assert 0.21 * factor > result["scanMs"] / 1000
    timeout.write_text("export const PROMPT_RECEIPT_TIMEOUT_MS = 2000.0;\n")
    with pytest.raises(ValueError, match="time constant mismatch"):
        budget.validate_receipt_fixture_budgets(client, provider, factor)
