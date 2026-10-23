"""Pure version/closure decisions; actual Git composition stays in development tests."""
from __future__ import annotations

import pytest

from promotion_version_allocation import payload_entries, select_version


@pytest.mark.parametrize("occupied,equivalent,expected", [
    ([], [], "1"),
    (["1"], ["1"], "1"),
    (["1"], [], "2"),
    (["1", "2"], [], "3"),
    (["1", "2"], ["2"], "2"),
])
def test_only_a_free_or_exact_matching_version_is_selected(occupied, equivalent, expected):
    seen = []

    def matches(version):
        seen.append(version)
        return version in equivalent

    selected = select_version(
        "1", occupied, matches=matches, advance=lambda value: str(int(value) + 1),
    )
    assert selected == expected
    assert all(value in occupied for value in seen)
    assert seen == occupied[:len(seen)]


def test_reservation_read_failure_cannot_be_treated_as_absence():
    def unavailable(version):
        raise ValueError("unreadable immutable reservation")

    with pytest.raises(ValueError, match="unreadable immutable reservation"):
        select_version("1", ["1"], matches=unavailable, advance=lambda value: "2")


@pytest.mark.parametrize("changed", [
    "plugins/demo/src/main.py",
    "plugins/demo/libs/events/src/events.py",
    "libs/shared/src/shared.py",
    ".gitattributes",
    "plugins/.gitattributes",
    "libs/.gitattributes",
])
def test_complete_closure_and_inherited_attributes_affect_reservation_match(changed):
    entries = {
        path: ("100644", "blob", "a" * 40)
        for path in [
            "plugins/demo/src/main.py", "plugins/demo/libs/events/src/events.py",
            "libs/shared/src/shared.py", ".gitattributes", "plugins/.gitattributes",
            "libs/.gitattributes", "plugins/unrelated/src/main.py",
        ]
    }
    roots = ["plugins/demo", "libs/shared"]
    altered = dict(entries)
    altered[changed] = ("100644", "blob", "b" * 40)
    assert payload_entries(entries, roots) != payload_entries(altered, roots)
    altered = dict(entries)
    altered["plugins/unrelated/src/main.py"] = ("100644", "blob", "b" * 40)
    assert payload_entries(entries, roots) == payload_entries(altered, roots)
