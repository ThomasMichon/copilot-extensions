"""Private build tools admit equivalent native paths, never outside directories."""

import os
from pathlib import Path
import sys

import pytest

for library in ("installer-engine", "versioned-runtime"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / library))

import installer_source_build as builds


@pytest.mark.parametrize("extended", [False, True])
def test_private_build_tools_accept_equivalent_paths(tmp_path, extended):
    if extended and os.name != "nt":
        pytest.skip("native Windows extended-length paths")
    tooling = tmp_path / "tooling"
    packages = tooling / "Lib" / "site-packages"
    packages.mkdir(parents=True)
    reported = Path("\\\\?\\" + str(packages)) if extended else packages
    admitted = builds._build_tools_root(str(reported), tooling)
    assert admitted == reported
    assert admitted.samefile(packages)


@pytest.mark.parametrize("location", ["sibling", "outside", "relative"])
def test_private_build_tools_still_refuse_noncontained_roots(tmp_path, location):
    tooling = tmp_path / "tooling"
    tooling.mkdir()
    if location == "relative":
        reported = Path("Lib") / "site-packages"
    else:
        reported = tmp_path / ("tooling-other" if location == "sibling" else "outside")
        reported.mkdir()
    with pytest.raises(builds.WheelError, match="outside their private environment"):
        builds._build_tools_root(str(reported), tooling)
