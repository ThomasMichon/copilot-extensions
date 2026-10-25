"""Reuse frozen host-policy units with scratch state confined to pytest fixtures."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, relative):
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


host_tests = _load(
    "promotion_original_host_profile_tests",
    "fleet-manager/tests/test_host_profile.py",
)
policy_tests = _load(
    "promotion_original_trusted_systemd_tests",
    "fleet-manager/tests/test_trusted_systemd_profile.py",
)
CASES = [
    (family, method)
    for family, module, class_name in (
        ("host", host_tests, "HostProfileTests"),
        ("policy", policy_tests, "TrustedSystemdProfileTests"),
    )
    for method in unittest.defaultTestLoader.getTestCaseNames(
        getattr(module, class_name)
    )
]
assert sum(family == "host" for family, _ in CASES) == 35
assert sum(family == "policy" for family, _ in CASES) == 7


@pytest.mark.parametrize(
    "family,method",
    CASES,
    ids=[f"{family}.{method}" for family, method in CASES],
)
def test_host_profile_contract_unit(tmp_path, monkeypatch, family, method):
    if family == "policy":
        case = policy_tests.TrustedSystemdProfileTests(method)
        try:
            case.setUp()
            try:
                getattr(case, method)()
            finally:
                case.tearDown()
        finally:
            assert case.doCleanups(), "unittest cleanup failed"
        return
    case = host_tests.HostProfileTests(method)
    fake_source = tmp_path / "project/fleet-manager/tests/test_host_profile.py"
    fake_source.parent.mkdir(parents=True)
    try:
        with monkeypatch.context() as setup_patch:
            setup_patch.setattr(host_tests, "__file__", str(fake_source))
            case.setUp()
        try:
            with monkeypatch.context() as effects_patch:
                if (
                    method
                    == "test_parser_deadline_and_output_bound_clean_up_only_owned_process"
                ):
                    effects_patch.setattr(host_tests.host.time, "sleep", lambda *_: None)
                getattr(case, method)()
        finally:
            case.tearDown()
    finally:
        assert case.doCleanups(), "unittest cleanup failed"
