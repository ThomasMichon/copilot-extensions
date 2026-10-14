"""Full-suite process-tree regression, deliberately outside the guard lane."""

import os
from pathlib import Path

import pytest

from test_first_install_bootstrap import _assert_posix_namespace_slot_rejected


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer integration")
@pytest.mark.timeout(120)
def test_posix_namespace_health_watchdog_bounds_staged_descendants(tmp_path: Path) -> None:
    _assert_posix_namespace_slot_rejected(tmp_path, stall_health_gate=True)
