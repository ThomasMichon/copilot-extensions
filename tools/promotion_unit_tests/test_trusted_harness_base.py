"""Reuse image contract units; real child-process controls stay in the native suite."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[2]
specification = importlib.util.spec_from_file_location(
    "promotion_original_trusted_harness_base_tests",
    ROOT / ".devcontainer/trusted-harness-base/tests/test_contract.py",
)
assert specification and specification.loader
original = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = original
specification.loader.exec_module(original)

NATIVE_CASES = {
    "test_actual_descendant_pipe_timeout_is_bounded_and_contained",
    "test_actual_directory_fsync",
    "test_actual_job_docker_environment_isolated_at_cli_boundary",
    "test_actual_subprocess_timeout_is_bounded",
    "test_real_cli_describe",
    "test_real_subprocess_preserves_metacharacters",
}
CASES = [
    (class_name, method)
    for class_name in ("ContractTests", "RuntimePersistenceTests")
    for method in unittest.defaultTestLoader.getTestCaseNames(getattr(original, class_name))
    if method not in NATIVE_CASES
]


@pytest.mark.parametrize(
    "class_name,method", CASES,
    ids=[f"{class_name}.{method}" for class_name, method in CASES],
)
def test_trusted_harness_contract_unit(tmp_path, monkeypatch, class_name, method):
    case = getattr(original, class_name)(method)
    # Rehome only original setup's scratch directory, not its production-source reads.
    try:
        with monkeypatch.context() as setup_patch:
            setup_patch.setattr(original, "ROOT", tmp_path / "project/.devcontainer/trusted-harness-base")
            case.setUp()
        try:
            getattr(case, method)()
        finally:
            case.tearDown()
    finally:
        assert case.doCleanups(), "unittest cleanup failed"
