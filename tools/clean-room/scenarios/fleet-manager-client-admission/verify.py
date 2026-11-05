"""Require all real consumer checks; zero tests or platform skips are not proof."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = ET.parse(Path(sys.argv[1])).getroot()
cases = list(root.iter("testcase"))
assert sys.argv[2:] in ([], ["--managed"])
managed = sys.argv[2:] == ["--managed"]
expected = {
    f"test_actual_installed_console_managed_lifecycle[{transition}]"
    for transition in ("retry", "forward-missing", "forward-malformed", "higher", "uninstall", "foreign", "legacy", "installed-owner")
} if managed else {
    "test_fresh_installed_console_admission_and_durable_reopen_without_hosting",
    "test_missing_packaged_resource_is_not_replaced_by_repository_helper",
    *{
        f"test_actual_installed_console_generation_transitions[{transition}]"
        for transition in ("construct", "register-commands", "unregister-commands", "remove-runtime")
    },
}
assert len(cases) == len(expected) and {case.attrib["name"] for case in cases} == expected
assert not any(child.tag in {"failure", "error", "skipped"} for case in cases for child in case)
print("INSTALLED_MANAGED_CLIENT_LIFECYCLE_PASS" if managed
      else "INSTALLED_CLIENT_ADMISSION_AND_EXACT_GENERATION_LIFECYCLE_PASS")
