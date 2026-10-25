"""Direct unittest adapters must restore registered resources on every exit."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "tools/promotion_unit_tests" / filename,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("lifecycle_base_adapter", "test_trusted_harness_base.py")
host = load("lifecycle_host_adapter", "test_host_profile.py")


@pytest.mark.parametrize("family", ["base", "host"])
@pytest.mark.parametrize("failure", ["none", "setup", "teardown", "cleanup"])
def test_registered_cleanups_run_on_every_exit(tmp_path, monkeypatch, family, failure):
    key = "UNIT_LIFECYCLE_SENTINEL"
    before = os.environ.get(key)
    calls = []

    class Case(unittest.TestCase):
        def setUp(self):
            calls.append("setup")
            patcher = mock.patch.dict(os.environ, {key: "active"})
            patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(self.cleanup)
            if failure == "setup":
                raise RuntimeError("setup fixture")

        def runTest(self):
            calls.append("call")
            assert os.environ[key] == "active"

        def tearDown(self):
            calls.append("teardown")
            if failure == "teardown":
                raise RuntimeError("teardown fixture")

        def cleanup(self):
            calls.append("cleanup")
            if failure == "cleanup":
                raise RuntimeError("cleanup fixture")

    if family == "base":
        monkeypatch.setattr(base.original, "ContractTests", Case)
        invoke = lambda: base.test_trusted_harness_contract_unit(
            tmp_path, monkeypatch, "ContractTests", "runTest",
        )
    else:
        monkeypatch.setattr(host.host_tests, "HostProfileTests", Case)
        invoke = lambda: host.test_host_profile_contract_unit(
            tmp_path, monkeypatch, "host", "runTest",
        )
    if failure == "none":
        invoke()
    else:
        expected = AssertionError if failure == "cleanup" else RuntimeError
        with pytest.raises(expected):
            invoke()
    assert os.environ.get(key) == before
    expected_calls = ["setup", "cleanup"] if failure == "setup" else [
        "setup", "call", "teardown", "cleanup",
    ]
    assert calls == expected_calls
