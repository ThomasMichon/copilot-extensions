"""The fresh-box oracle must not turn skipped or absent consumers into a pass."""

import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

VERIFY = (
    Path(__file__).resolve().parent / "clean-room" / "scenarios"
    / "fleet-manager-client-admission" / "verify.py"
)
NAMES = [
    "test_fresh_installed_console_admission_and_durable_reopen_without_hosting",
    "test_missing_packaged_resource_is_not_replaced_by_repository_helper",
    *[
        f"test_actual_installed_console_generation_transitions[{transition}]"
        for transition in ("construct", "register-commands", "unregister-commands", "remove-runtime")
    ],
]


@pytest.mark.parametrize("fault", [
    None, "missing", "admission-only", "empty", "duplicate", "unknown", "skipped", "error", "failure",
])
@pytest.mark.parametrize("managed", [False, True])
def test_fresh_client_oracle_requires_all_non_skipped_consumers(tmp_path, fault, managed):
    names = [
        f"test_actual_installed_console_managed_lifecycle[{transition}]"
        for transition in ("retry", "forward-missing", "forward-malformed", "higher", "uninstall", "foreign", "legacy", "installed-owner")
    ] if managed else NAMES
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    cases = [ET.SubElement(suite, "testcase", name=name) for name in names]
    if fault == "missing":
        suite.remove(cases[-1])
    elif fault in ("admission-only", "empty"):
        for case in cases[2 if fault == "admission-only" else 0:]:
            suite.remove(case)
    elif fault == "duplicate":
        cases[-1].set("name", names[0])
    elif fault == "unknown":
        cases[-1].set("name", "unrelated_consumer")
    elif fault:
        ET.SubElement(cases[-1], fault)
    report = tmp_path / "consumer.xml"
    ET.ElementTree(root).write(report, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", str(VERIFY), str(report), *(["--managed"] if managed else [])],
        capture_output=True, text=True, timeout=10,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert (result.returncode == 0) is (fault is None)
    assert ("_PASS" in result.stdout) is (fault is None)
