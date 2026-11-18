"""Native short-name admission retains path and cleanup boundaries."""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from agent_procutil import _daemon_instance_store as store
from agent_procutil._daemon_instance_identity import path_identity
from agent_procutil.daemon_instances import (
    INSTANCE_ENV, TEST_CONTEXT_ENV, DaemonInstanceReceipt, DaemonInstanceSpec,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows DOS names only")


@pytest.fixture(autouse=True)
def isolated_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_ensure_monitor", lambda: None)
    monkeypatch.setattr(store, "_admitted", None)


@pytest.fixture
def short_runtime(tmp_path: Path) -> tuple[Path, Path]:
    if sys.platform != "win32":
        pytest.skip("Windows DOS names only")
    runtime = tmp_path.resolve() / "runtime anchor with long name"
    runtime.mkdir()
    shorten = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    shorten.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    shorten.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = shorten(str(runtime), buffer, len(buffer))
    if not length:
        raise ctypes.WinError(ctypes.get_last_error())
    assert length < len(buffer)
    short = Path(buffer.value)
    if short == runtime:
        pytest.skip("DOS short-name generation unavailable")
    return runtime, short


def test_runtime_anchor_accepts_native_short_name(short_runtime: tuple[Path, Path]) -> None:
    runtime, short = short_runtime
    assert store._runtime_anchor(runtime) == runtime
    assert store._runtime_anchor(short) == runtime
    assert short.samefile(runtime)


def test_reservation_accepts_native_short_runtime_and_registry(
    short_runtime: tuple[Path, Path],
) -> None:
    runtime, short = short_runtime
    receipt = store.reserve(DaemonInstanceSpec(
        "short-runtime", short, Path(__file__).resolve(),
        registry_root=short / "registry", purpose="unattended-test",
    ))
    try:
        rows = store.inspect(receipt.registry_root)
        assert len(rows) == 1
        assert rows[0]["runtime_root"] == str(runtime)
        assert rows[0]["state"] == "reserved"
        assert receipt.registry_root == runtime / "registry"
        assert store._runtime_scope_key(runtime) == store._runtime_scope_key(short)
        assert store.cleanup(
            receipt, terminate=False, runtime_root=short,
        ).reason == "launch-outcome-unknown"
        assert store.reconcile(receipt.registry_root, 128, runtime_root=short) == []
    finally:
        store.cancel_reservation(receipt, creation_refused=True)


def test_short_runtime_artifacts_keep_cleanup_custody(
    short_runtime: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, short = short_runtime
    artifact = short / "daemon.pid"
    proof = runtime / "proof.txt"
    artifact.write_text("fixture")
    proof.write_text("retain")
    guard = store.admit(
        "short-runtime", runtime_root=short, source=__file__, purpose="production",
    )
    try:
        guard.add_artifact(artifact)
    finally:
        guard.close()
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    assert store.cleanup(
        guard.receipt, terminate=False, runtime_root=short,
    ).status == "settled"
    assert not artifact.exists()
    assert proof.read_text() == "retain"


def test_short_receipt_cannot_claim_inventory_artifact(
    short_runtime: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, short = short_runtime
    monkeypatch.delenv(INSTANCE_ENV, raising=False)
    monkeypatch.delenv(TEST_CONTEXT_ENV, raising=False)
    guard = store.admit(
        "short-inventory", runtime_root=runtime, source=__file__,
        registry_root=short / "registry", purpose="production",
    )
    receipt = DaemonInstanceReceipt(guard.receipt.instance_id, short / "registry")
    artifact = guard.receipt.record_path.with_suffix(".lock")
    try:
        with pytest.raises(ValueError, match="inventory"):
            store.add_artifact(receipt, artifact)
        assert store.inspect(receipt.registry_root)[0]["artifact_count"] == "0"
        assert artifact.exists()
    finally:
        guard.close()


def test_short_receipt_cleanup_refuses_recorded_inventory_artifact(
    short_runtime: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, short = short_runtime
    monkeypatch.delenv(INSTANCE_ENV, raising=False)
    monkeypatch.delenv(TEST_CONTEXT_ENV, raising=False)
    guard = store.admit(
        "short-inventory", runtime_root=runtime, source=__file__,
        registry_root=short / "registry", purpose="production",
    )
    guard.close()
    receipt = DaemonInstanceReceipt(guard.receipt.instance_id, short / "registry")
    with store._locked(receipt) as path:
        artifact = path.with_suffix(".lock")
        device, inode = path_identity(artifact)
        record = store._read(path)
        record.update({
            "artifact_count": "1", "artifact_0_path": str(artifact),
            "artifact_0_device": device, "artifact_0_inode": inode,
        })
        store._write(path, record)
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    monkeypatch.setattr(
        store, "delete_owned_file", lambda *_: pytest.fail("inventory deletion attempted"),
    )
    result = store.cleanup(receipt, terminate=False, runtime_root=runtime)
    assert result.reason == "inventory-artifact-refused"
    assert result.removed == ()
    assert artifact.exists()
    assert receipt.record_path.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_runtime_anchor_short_parent_preserves_creation(
    short_runtime: tuple[Path, Path], existing: bool,
) -> None:
    runtime, short = short_runtime
    if existing:
        (runtime / "child").mkdir()
    assert store._runtime_anchor(short / "child") == runtime / "child"
    assert (runtime / "child").is_dir()


def test_runtime_anchor_rejects_parent_traversal(short_runtime: tuple[Path, Path]) -> None:
    runtime, short = short_runtime
    (runtime / "child").mkdir()
    with pytest.raises(OSError, match="redirected"):
        store._runtime_anchor(short / "child" / "..")


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_runtime_anchor_rejects_redirected_short_parent(
    short_runtime: tuple[Path, Path], kind: str,
) -> None:
    runtime, short = short_runtime
    target = runtime.parent / "target"
    target.mkdir()
    link = runtime / "redirected"
    if kind == "junction":
        import _winapi

        getattr(_winapi, "CreateJunction")(str(target), str(link))
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlink creation unavailable")
    with pytest.raises(OSError, match="redirected"):
        store._runtime_anchor(short / "redirected")
    with pytest.raises(OSError, match="redirected"):
        store._runtime_anchor(short / "redirected" / "must-not-create")
    assert not (target / "must-not-create").exists()
