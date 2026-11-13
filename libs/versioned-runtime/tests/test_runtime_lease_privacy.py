"""Native POSIX kernel leases and first-touch filesystem privacy."""

import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "libs/versioned-runtime/versioned_runtime_leases.py"
SPEC = importlib.util.spec_from_file_location("native_runtime_lease_privacy", SOURCE)
assert SPEC and SPEC.loader
leases = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = leases
SPEC.loader.exec_module(leases)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Actual POSIX filesystem/kernel lease")


@pytest.mark.parametrize("kind", ["construction", "retention"])
def test_posix_fresh_lease_root_passes_strict_private_admission(tmp_path, monkeypatch, kind):
    monkeypatch.syspath_prepend(str(ROOT / "fleet-manager/src"))
    from fleet_manager.private_state import private_directory

    root = tmp_path / kind
    status = Path("/proc/self/status")
    observed = next((line.split(":", 1)[1].strip() for line in status.read_text().splitlines()
                     if line.startswith("Umask:")), "unknown") if status.exists() else "not-linux-proc"
    print({"observed_POSIX_umask": observed, "controller_umask_changed": False})
    with leases._kernel_lease(root, "fixture", kind=kind, shared=kind == "retention", timeout=1):
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        private_directory(root)
        assert stat.S_IMODE((root / ("." + kind + "-leases")).stat().st_mode) == 0o700
    private_directory(root)


def test_posix_existing_root_is_not_repaired_for_private_admission(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "fleet-manager/src"))
    from fleet_manager.private_state import private_directory

    root = tmp_path / "existing"
    root.mkdir(mode=0o700)
    root.chmod(0o755)  # Deliberately introduced fixture grant, never a repair.
    before = root.stat()
    with leases._kernel_lease(root, "fixture", kind="construction", shared=False, timeout=1):
        assert stat.S_IMODE(root.stat().st_mode) == 0o755
    with pytest.raises(ValueError, match="not private"):
        private_directory(root)
    after = root.stat()
    assert (after.st_ino, after.st_uid, after.st_mode) == (before.st_ino, before.st_uid, before.st_mode)
