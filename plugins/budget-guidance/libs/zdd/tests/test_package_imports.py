"""Short-lived routing clients do not import daemon lifecycle machinery."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_routing_only_import_leaves_unrelated_daemon_modules_unloaded():
    completed = subprocess.run(
        [sys.executable, "-I", "-c",
         "import json,sys; from zdd import routing; print(json.dumps(sorted(sys.modules)))"],
        capture_output=True, text=True, check=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    loaded = set(json.loads(completed.stdout))
    assert "zdd.routing" in loaded
    assert not loaded.intersection({
        "zdd.breadcrumb", "zdd.cutover", "zdd.diagnostics",
        "zdd.singleton_manager", "zdd.singleton_linux", "zdd.singleton_state",
    })


@pytest.mark.parametrize("module,names", [
    ("breadcrumb", "clear_breadcrumb read_breadcrumb recover_stale_cutover write_breadcrumb"),
    ("claims", "ClaimConflict Claimable decide_acquire generation_id is_recoverable"),
    ("cutover", "CutoverError CutoverOrchestrator CutoverResult"),
    ("cutover_lock", "CutoverLock CutoverLockedError"),
    ("diagnostics", "DaemonCandidate DiagnosticContext apply_daemon_health audit_daemon_health lock_data_is_live process_start_time terminate_pid_if_identity"),
    ("routing", "Endpoint clear_if_owner publish_active read_active_endpoint read_table reap_stale_active routing_table_path"),
    ("singleton_manager", "ManagerResult SingletonManager UnmanagedDaemonError"),
    ("singleton_state", "ManagerAlreadyRunning"),
])
def test_root_exports_preserve_their_canonical_objects(module, names):
    import zdd

    canonical = importlib.import_module(f"zdd.{module}")
    assert getattr(zdd, module) is canonical
    for name in names.split():
        assert getattr(zdd, name) is getattr(canonical, name)
        assert name in zdd.__all__ and name in dir(zdd)


def test_star_import_and_unknown_attributes_preserve_package_contract():
    import zdd

    namespace = {}
    exec("from zdd import *", namespace)
    assert {name for name in namespace if name != "__builtins__"} == set(zdd.__all__)
    with pytest.raises(AttributeError):
        getattr(zdd, "not_a_zdd_export")


def test_concurrent_root_access_returns_one_canonical_class():
    import zdd
    from zdd.routing import Endpoint

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(lambda _: zdd.Endpoint, range(32))) == [Endpoint] * 32
