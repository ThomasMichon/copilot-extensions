"""The actual legacy-ACL repair decision is independent of native filesystem I/O."""

import importlib.util
import itertools
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "libs/versioned-runtime/versioned_runtime_leases.py"
SPEC = importlib.util.spec_from_file_location("unit_windows_lease_acl", SOURCE)
leases = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(leases)


@pytest.mark.parametrize("directory", [False, True], ids=["file", "directory"])
def test_exact_generated_legacy_acl_is_recognized(directory):
    flags = "OICI" if directory else "ID"
    for principals in itertools.permutations(("OW", "SY", "BA")):
        value = ("O:BAD:P" if directory else "O:BAD:") + "".join(
            f"(A;{flags};FA;;;{principal})" for principal in principals
        )
        assert leases._legacy_windows_lease_acl(value, directory=directory)


CASES = {
    "foreign-owner": "O:SYD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "public-write": "O:BAD:P(A;OICI;FA;;;WD)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "missing-entry": "O:BAD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)",
    "extra-entry": "O:BAD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;WD)",
    "changed-rights": "O:BAD:P(A;OICI;FR;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "deny-entry": "O:BAD:P(D;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "unprotected-directory": "O:BAD:(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
    "wrong-inheritance": "O:BAD:P(A;ID;FA;;;OW)(A;ID;FA;;;SY)(A;ID;FA;;;BA)",
    "trailing-data": "O:BAD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)garbage",
    "empty": "",
}


@pytest.mark.parametrize("name", CASES)
def test_foreign_public_partial_or_edited_acl_is_not_repair_authority(name):
    assert not leases._legacy_windows_lease_acl(CASES[name], directory=True)


@pytest.mark.parametrize(("kind", "flags", "mask", "sid", "expected"), [
    (0, 0, 0x1F01FF, "S-1-5-21-1001", True),
    (0, 0x10, 0x1F01FF, "S-1-5-21-1001", True),
    (0, 8, 0x1F01FF, "S-1-5-21-1001", False),
    (1, 0, 0x1F01FF, "S-1-5-21-1001", False),
    (0, 0, 0x120089, "S-1-5-21-1001", False),
    (0, 0, 0x1F01FF, "S-1-5-32-544", False),
    (0, 0, 0x1F01FF, "S-1-3-4", False),
    (0, 0, 0x1F01FF, "S-1-5-21-1002", False),
], ids=["direct", "inherited", "inherit-only", "deny", "read-only", "admin-only",
        "owner-rights", "other-account"])
def test_legacy_repair_requires_actual_account_full_control(kind, flags, mask, sid, expected):
    assert leases._windows_account_full_control(kind, flags, mask, sid, "S-1-5-21-1001") is expected
