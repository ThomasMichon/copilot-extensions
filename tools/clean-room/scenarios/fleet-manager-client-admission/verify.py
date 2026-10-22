"""Require both real consumer checks; zero tests or platform skips are not proof."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = ET.parse(Path(sys.argv[1])).getroot()
cases = list(root.iter("testcase"))
expected = {
    "test_fresh_installed_console_admission_and_durable_reopen_without_hosting",
    "test_missing_packaged_resource_is_not_replaced_by_repository_helper",
}
assert len(cases) == 2 and {case.attrib["name"] for case in cases} == expected
assert not any(child.tag in {"failure", "error", "skipped"} for case in cases for child in case)
print("INSTALLED_CLIENT_ADMISSION_RETRY_REVOCATION_REOPEN_AND_NO_SOURCE_FALLBACK_PASS")
