"""Pure validation before the batch-removal owner may acquire any custody."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs" / "versioned-runtime" / "versioned_runtime_leases.py"
SPEC = importlib.util.spec_from_file_location("manager_uninstall_unit_leases", SOURCE)
assert SPEC and SPEC.loader
leases = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(leases)


@pytest.mark.parametrize(
    "versions",
    [
        [],
        ["dev"],
        ["../slot"],
        ["slot."],
        ["1.2.3", "1.2.3"],
        ["1.2.3+ABC", "1.2.3+abc"],
        [""],
    ],
    ids=[
        "empty",
        "dev",
        "parent",
        "trailing-dot",
        "duplicate",
        "case-alias",
        "empty-name",
    ],
)
def test_batch_removal_refuses_invalid_generations_before_custody(versions):
    with pytest.raises(ValueError):
        with leases.batch_removal_custody(object(), versions):
            pytest.fail("invalid generations entered filesystem custody")
