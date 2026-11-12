"""Instance policy and shared public contract."""

from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
import zipfile

from agent_procutil.daemon_instances import (
    DaemonInstanceSpec,
    DaemonLeasePolicy,
    DaemonInstanceReceipt,
    instance_environment,
)
from agent_procutil import _daemon_instance_store as store


@pytest.mark.parametrize("retained", [False, True])
def test_leased_python_bootstraps_from_selected_source(tmp_path, monkeypatch, retained):
    from agent_procutil import _daemon_instance_child as child

    root = tmp_path / "retained" / "src"
    package = root / "agent_procutil"
    package.mkdir(parents=True)
    (package / "_daemon_instance_child.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(child, "normalize_python_command", lambda command: (command, {}))
    command = ["python", "-I", "-S", "-m", "fixture", "argument"]
    rewritten, _ = child.leased_python_command(
        command, bootstrap_root=root if retained else None,
    )
    expected = root if retained else Path(child.__file__).resolve().parent.parent
    assert rewritten[:5] == ["python", "-I", "-S", "-c", child._BOOTSTRAP]
    assert rewritten[5:] == [str(expected), "-m", "fixture", "argument"]


@pytest.mark.parametrize("relative", [False, True])
def test_invalid_leased_python_bootstrap_refuses_before_launch(tmp_path, relative):
    from agent_procutil._daemon_instance_child import leased_python_command

    root = Path("relative") if relative else tmp_path
    with pytest.raises(ValueError, match="bootstrap root"):
        leased_python_command(["python", "-c", "pass"], bootstrap_root=root)


@pytest.mark.parametrize("retained", [False, True])
def test_public_daemon_wrapper_forwards_bootstrap_root_to_leased_launch(
    tmp_path, monkeypatch, retained,
):
    import sys

    import agent_procutil
    from agent_procutil import _daemon_instance_child as child

    root = tmp_path / "retained" / "src" if retained else None
    marker = RuntimeError("leased launch reached")
    instance = DaemonInstanceSpec(
        "root-forwarding", tmp_path, Path(__file__),
        registry_root=tmp_path / "registry", purpose="unattended-test",
    )

    def reached(command, *, bootstrap_root):
        assert bootstrap_root == root
        raise marker

    monkeypatch.setattr(child, "leased_python_command", reached)
    with pytest.raises(RuntimeError) as caught:
        agent_procutil.spawn_daemon(
            [sys.executable, "-c", "pass"], cwd=tmp_path, log_path=tmp_path / "launch.log",
            instance=instance, **({"python_bootstrap_root": root} if retained else {}),
        )
    assert caught.value is marker
    assert caught.value.process_created is False


@pytest.mark.parametrize("root", [PurePosixPath("/runtime"), PureWindowsPath(r"C:\runtime")])
@pytest.mark.parametrize("nested", [False, True])
def test_runtime_slot_layout_preserves_generation_and_installation_root(root, nested):
    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    generation = "1.0+source." + "a" * 40 + "." + "b" * 16
    slot = root / "versions" / generation
    prefix = slot / ".venv" if nested else slot
    assert runtime_slot_paths(prefix) == (generation, slot, root)
    assert runtime_slot_paths(root / "ordinary-venv") is None
    assert runtime_slot_paths(slot / ".." / generation) is None
    assert runtime_slot_paths(type(root)("versions") / generation) is None


@pytest.mark.parametrize("contained", [False, True])
def test_native_lease_guard_preserves_independent_daemon_lifetime(tmp_path, monkeypatch, contained):
    import os
    from types import SimpleNamespace
    import agent_procutil

    created = []
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1" if contained else "0")
    monkeypatch.setattr(store, "os", SimpleNamespace(name="nt", getpid=os.getpid))
    monkeypatch.setattr(store, "_expiry_job", None)
    monkeypatch.setattr(store, "_guards", {})
    monkeypatch.setattr(store, "_monitor_pid", os.getpid())
    monkeypatch.setattr(store, "_ensure_monitor", lambda: None)
    monkeypatch.setattr(
        agent_procutil, "_assign_suspended_to_kill_on_close_job",
        lambda pid, *, allow_breakaway: created.append((pid, allow_breakaway)) or object(),
    )

    store._register_monitor(
        DaemonInstanceReceipt("a" * 32, tmp_path / "registry"),
        {"purpose": "unattended-test"},
    )

    assert created == [(os.getpid(), not contained)]


@pytest.fixture
def instance_store(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_INSTANCE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", raising=False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_DAEMON_REGISTRY", str(root))
    monkeypatch.setattr(store, "_ensure_monitor", lambda: None)
    monkeypatch.setattr(store, "_admitted", None)
    return root, runtime


def test_native_validation_refusal_cancels_exact_reservation(instance_store):
    import os
    import sys

    from agent_procutil import no_window_flags
    from agent_procutil.daemon_instances import spawn_instance

    root, runtime = instance_store
    with pytest.raises(ValueError):
        spawn_instance(
            [sys.executable, "-c", "pass"],
            instance=DaemonInstanceSpec("fixture", runtime, Path(__file__).resolve()),
            env={**os.environ, "INVALID": "embedded\0null"},
            creationflags=no_window_flags(),
        )
    assert store.inspect(root) == []


def test_unattended_policy_is_explicit_not_inferred_from_paths(tmp_path):
    production = DaemonInstanceSpec("fixture", tmp_path, Path(__file__).resolve())
    disposable = DaemonInstanceSpec(
        "fixture", tmp_path, Path(__file__).resolve(), purpose="unattended-test",
    )
    assert production.lease is None
    assert disposable.lease == DaemonLeasePolicy(600, 14400)


def test_interactive_policy_requires_explicit_pause_tolerant_limits(tmp_path):
    with pytest.raises(ValueError, match="pause-tolerant"):
        DaemonInstanceSpec(
            "fixture", tmp_path, Path(__file__).resolve(), purpose="interactive-test",
        )
    policy = DaemonLeasePolicy(86400, 172800)
    assert DaemonInstanceSpec(
        "fixture", tmp_path, Path(__file__).resolve(),
        purpose="interactive-test", lease=policy,
    ).lease == policy


@pytest.mark.parametrize("ttl,cap", [(0, 1), (2, 1), (float("inf"), 1), (True, 2)])
def test_policy_rejects_unbounded_or_invalid_limits(ttl, cap):
    with pytest.raises(ValueError):
        DaemonLeasePolicy(ttl, cap)


def test_receipt_environment_carries_only_locator(tmp_path):
    receipt = DaemonInstanceReceipt("a" * 32, tmp_path)
    assert instance_environment(receipt) == {
        "COPILOT_EXTENSIONS_DAEMON_INSTANCE": "a" * 32,
        "COPILOT_EXTENSIONS_DAEMON_REGISTRY": str(tmp_path),
    }


def test_reservation_admission_and_nested_custody(instance_store):
    root, runtime = instance_store
    first = store.admit("fixture", runtime_root=runtime, source=__file__, purpose="production")
    second = store.admit("nested", runtime_root=runtime, source=__file__)
    assert first.receipt == second.receipt
    assert len(store.inspect(root)) == 1
    second.close()
    assert store.inspect(root)[0]["state"] == "running"
    first.close()
    assert store.inspect(root)[0]["state"] == "finished"


def test_production_never_expires_and_cleanup_never_stops_live(instance_store, monkeypatch):
    root, runtime = instance_store
    guard = store.admit("fixture", runtime_root=runtime, source=__file__, purpose="production")
    monkeypatch.setattr(store, "_wall_clock", lambda: 10**20)
    assert store.cleanup(guard.receipt).status == "live"
    assert store.inspect(root)[0]["purpose"] == "production"
    guard.close()


def test_only_test_owner_can_renew_and_deadline_never_revives(instance_store, monkeypatch):
    _, runtime = instance_store
    receipt = store.reserve(DaemonInstanceSpec(
        "fixture", runtime, Path(__file__).resolve(), purpose="unattended-test",
        lease=DaemonLeasePolicy(10, 30),
    ))
    now = store._wall_clock()
    mono = store._monotonic_clock()
    monkeypatch.setattr(store, "_wall_clock", lambda: now + 5)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: mono + 5)
    store.renew(receipt)
    assert float(store.inspect(receipt.registry_root)[0]["deadline"]) == pytest.approx(now + 15)
    monkeypatch.setattr(store, "_wall_clock", lambda: now + 16)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: mono + 16)
    with pytest.raises(ValueError, match="cannot be revived"):
        store.renew(receipt)


@pytest.mark.parametrize("state", ["unknown", "reused"])
def test_uncertain_birth_identity_is_a_hold(instance_store, monkeypatch, state):
    _, runtime = instance_store
    guard = store.admit("fixture", runtime_root=runtime, source=__file__, purpose="production")
    monkeypatch.setattr(store, "identity_state", lambda *_: state)
    monkeypatch.setattr(store, "stop_identity", lambda *_: pytest.fail("must not signal"))
    assert store.cleanup(guard.receipt).reason == "process-" + state
    guard.close()


def test_cleanup_removes_only_declared_files_preserving_source_and_proof(instance_store, monkeypatch):
    _, runtime = instance_store
    artifact = runtime / "daemon.pid"
    proof = runtime / "proof.txt"
    artifact.write_text("fixture")
    proof.write_text("retain")
    guard = store.admit("fixture", runtime_root=runtime, source=__file__, purpose="production")
    guard.add_artifact(artifact)
    guard.close()
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    assert store.cleanup(guard.receipt).status == "settled"
    assert not artifact.exists()
    assert proof.read_text() == "retain"
    assert Path(__file__).is_file()
    assert guard.receipt.record_path.with_suffix(".lock").is_file()


def test_changed_artifact_holds_before_process_stop(instance_store, monkeypatch):
    _, runtime = instance_store
    artifact = runtime / "daemon.pid"
    artifact.write_text("fixture")
    guard = store.admit("fixture", runtime_root=runtime, source=__file__, purpose="production")
    guard.add_artifact(artifact)
    replacement = runtime / "new"
    replacement.write_text("not owned")
    replacement.replace(artifact)
    monkeypatch.setattr(store, "stop_identity", lambda *_: pytest.fail("must not signal"))
    assert store.cleanup(guard.receipt).reason == "artifact-identity-changed"
    guard.close()


def test_unbound_reservation_is_not_guessed_dead(instance_store):
    _, runtime = instance_store
    receipt = store.reserve(DaemonInstanceSpec("fixture", runtime, Path(__file__).resolve()))
    assert store.cleanup(receipt).reason == "launch-outcome-unknown"


def test_explicit_test_lifecycle_overlay_sets_per_instance_purpose(instance_store, monkeypatch):
    from agent_procutil.daemon_instances import daemon_test_lifecycle

    root, runtime = instance_store
    with daemon_test_lifecycle(owner="fixture-runner", registry_root=root) as overlay:
        for key, value in overlay.items():
            monkeypatch.setenv(key, value)
        first = store.reserve(DaemonInstanceSpec("one", runtime, Path(__file__).resolve()))
        second = store.reserve(DaemonInstanceSpec("two", runtime, Path(__file__).resolve()))
        assert first != second
        records = store.inspect(root)
        assert all(record["purpose"] == "unattended-test" for record in records)
        assert len({record["run_id"] for record in records}) == 1


def test_corrupt_record_is_held(instance_store):
    root, runtime = instance_store
    receipt = store.reserve(DaemonInstanceSpec("fixture", runtime, Path(__file__).resolve()))
    receipt.record_path.write_text("bad-record")
    assert store.inspect(root)[0]["state"] == "held"
    assert store.cleanup(receipt).status == "held"


def test_supplied_environment_overlay_owns_renewal_without_mutating_ambient(instance_store):
    from agent_procutil.daemon_instances import daemon_test_lifecycle, reserve_instance

    root, runtime = instance_store
    with daemon_test_lifecycle(owner="explicit-overlay", registry_root=root) as overlay:
        receipt = reserve_instance(
            DaemonInstanceSpec("fixture", runtime, Path(__file__).resolve()),
            environment=overlay,
        )
        row = store.inspect(root)[0]
        assert row["run_id"]
        assert row["owner"] == "explicit-overlay"
        assert row["purpose"] == "unattended-test"
        store.renew(receipt)


def test_installer_reconcile_never_settles_production_record(instance_store, monkeypatch):
    root, runtime = instance_store
    receipt = store.reserve(DaemonInstanceSpec("fixture", runtime, Path(__file__).resolve()))
    monkeypatch.setattr(store, "cleanup", lambda *_a, **_kw: pytest.fail("production sweep"))
    assert store.reconcile(root, 128, 5) == []
    assert receipt.record_path.exists()


@pytest.mark.parametrize("supervised", [False, True])
def test_reconcile_defaults_to_no_termination_for_expired_live_instances(
    instance_store, monkeypatch, supervised,
):
    root, runtime = instance_store
    receipt = store.reserve(DaemonInstanceSpec(
        "expired", runtime, Path(__file__).resolve(), purpose="unattended-test",
    ))
    with store._locked(receipt) as path:
        record = store._read(path)
        record.update(pid="4321", birth="fixture", state="running")
        if supervised:
            record.update(supervisor_pid="4322", supervisor_birth="supervisor")
        store._write(path, record)
    before = receipt.record_path.read_bytes()
    expired_wall, expired_mono = store._wall_clock() + 20000, store._monotonic_clock() + 20000
    monkeypatch.setattr(store, "_wall_clock", lambda: expired_wall)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: expired_mono)
    monkeypatch.setattr(store, "identity_state", lambda *_: "matching")
    monkeypatch.setattr(store, "stop_identity", lambda *_a, **_k: pytest.fail("installer stop"))

    result = store.reconcile(root, 128, 5)

    assert result[0].status == "held"
    assert result[0].reason == ("descendant-settlement-unconfirmed" if supervised else "stop-required")
    assert receipt.record_path.read_bytes() == before


def test_reconcile_explicit_owner_termination_preserves_expired_cleanup(instance_store, monkeypatch):
    from unittest.mock import Mock

    root, runtime = instance_store
    receipt = store.reserve(DaemonInstanceSpec(
        "expired", runtime, Path(__file__).resolve(), purpose="unattended-test",
    ))
    with store._locked(receipt) as path:
        record = store._read(path)
        record.update(pid="4321", birth="fixture", state="running")
        store._write(path, record)
    expired_wall, expired_mono = store._wall_clock() + 20000, store._monotonic_clock() + 20000
    monkeypatch.setattr(store, "_wall_clock", lambda: expired_wall)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: expired_mono)
    monkeypatch.setattr(store, "identity_state", lambda *_: "matching")
    stop = Mock(return_value=True)
    monkeypatch.setattr(store, "stop_identity", stop)

    results = store.reconcile(root, 128, 5, terminate=True, runtime_root=runtime)

    assert results[0].status == "settled"
    stop.assert_called_once_with(4321, "fixture", timeout=5)

@pytest.mark.parametrize("case_variant", [
    False,
    pytest.param(True, marks=pytest.mark.skipif(store.os.name != "nt", reason="Windows case aliases")),
])
def test_reconcile_scopes_dead_records_and_artifacts_to_exact_runtime(
    instance_store, monkeypatch, case_variant,
):
    root, runtime = instance_store
    foreign = runtime / "other-installation"
    foreign.mkdir()
    receipts = []
    for target in (runtime, foreign):
        receipt = store.reserve(DaemonInstanceSpec(
            "expired", target, Path(__file__).resolve(), purpose="unattended-test",
        ))
        store.bind_process(receipt, store.os.getpid())
        artifact = target / "expired.pid"
        artifact.write_text("fixture")
        store.add_artifact(receipt, artifact)
        with store._locked(receipt) as path:
            record = store._read(path)
            record.update(pid="4321", birth="fixture", state="finished")
            store._write(path, record)
        receipts.append(receipt)
    foreign_bytes = receipts[1].record_path.read_bytes()
    expired_wall, expired_mono = store._wall_clock() + 20000, store._monotonic_clock() + 20000
    monkeypatch.setattr(store, "_wall_clock", lambda: expired_wall)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: expired_mono)
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")

    scope = Path(str(runtime).swapcase()) if case_variant else runtime
    results = store.reconcile(root, 128, 5, runtime_root=scope)

    assert [result.instance_id for result in results] == [receipts[0].instance_id]
    assert results[0].status == "settled"
    assert not (runtime / "expired.pid").exists()
    assert (foreign / "expired.pid").read_text() == "fixture"
    assert receipts[1].record_path.read_bytes() == foreign_bytes
    assert store._read_cursor(root, scope) == store._read_cursor(root, runtime)


def test_scoped_cleanup_rechecks_record_runtime_under_lock(instance_store, monkeypatch):
    _, runtime = instance_store
    foreign = runtime / "other-installation"
    foreign.mkdir()
    receipt = store.reserve(DaemonInstanceSpec(
        "foreign", foreign, Path(__file__).resolve(), purpose="unattended-test",
    ))
    before = receipt.record_path.read_bytes()
    monkeypatch.setattr(store, "identity_state", lambda *_: pytest.fail("foreign process query"))

    result = store.cleanup(receipt, terminate=False, runtime_root=runtime)

    assert result.status == "held" and result.reason == "runtime-scope-mismatch"
    assert receipt.record_path.read_bytes() == before


def test_interleaved_scoped_collectors_do_not_advance_each_others_progress(
    instance_store, monkeypatch,
):
    root, runtime = instance_store
    other = runtime / "other-installation"
    other.mkdir()
    receipts = []
    for target in (runtime, other):
        receipt = store.reserve(DaemonInstanceSpec(
            "expired", target, Path(__file__).resolve(), purpose="unattended-test",
        ))
        with store._locked(receipt) as path:
            record = store._read(path)
            record.update(pid="4321", birth="fixture", state="finished")
            store._write(path, record)
        receipts.append(receipt)
    receipts.sort(key=lambda receipt: receipt.instance_id)
    scopes = [Path(store._read(receipt.record_path)["runtime_root"]) for receipt in receipts]
    expired_wall, expired_mono = store._wall_clock() + 20000, store._monotonic_clock() + 20000
    monkeypatch.setattr(store, "_wall_clock", lambda: expired_wall)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: expired_mono)
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    monkeypatch.setattr(store, "stop_identity", lambda *_a, **_k: pytest.fail("collector stop"))

    assert store.reconcile(root, 1, 5, runtime_root=scopes[1]) == []
    first = store.reconcile(root, 1, 5, runtime_root=scopes[0])
    second = store.reconcile(root, 1, 5, runtime_root=scopes[1])

    assert [result.instance_id for result in first] == [receipts[0].instance_id]
    assert [result.instance_id for result in second] == [receipts[1].instance_id]
    assert all(result.status == "settled" for result in (*first, *second))


def test_public_reconcile_forwards_explicit_termination_and_runtime_scope(instance_store, monkeypatch):
    from unittest.mock import Mock
    from agent_procutil.daemon_instances import reconcile_instances

    root, runtime = instance_store
    reconcile = Mock(return_value=[])
    monkeypatch.setattr(store, "reconcile", reconcile)
    assert reconcile_instances(registry_root=root, runtime_root=runtime, terminate=False) == []
    reconcile.assert_called_once_with(root, 128, 5, terminate=False, runtime_root=runtime)


def test_zip_member_provenance_records_real_archive_and_member(instance_store):
    root, runtime = instance_store
    archive = runtime / "service.pyz"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("service/main.py", "pass\n")
    source = archive / "service" / "main.py"
    guard = store.admit("zip-source", source=source, purpose="production")
    row = store.inspect(root)[0]
    assert row["source"] == str(archive)
    assert row["source_member"] == "service/main.py"
    assert row["runtime_root"] == str(runtime)
    guard.close()


def test_unknown_virtual_source_member_is_not_invented(instance_store):
    _, runtime = instance_store
    archive = runtime / "service.pyz"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("service/main.py", "pass\n")
    with pytest.raises(ValueError, match="member does not exist"):
        store.admit("zip-source", source=archive / "missing.py", purpose="production")


def test_undeclared_raw_source_cannot_create_unbounded_production(instance_store):
    root, runtime = instance_store
    with pytest.raises(ValueError, match="explicit purpose"):
        store.admit("unknown-source", runtime_root=runtime, source=__file__)
    assert not root.exists()


def test_undeclared_decorator_refuses_before_body_or_runtime_resolver(instance_store):
    from agent_procutil.daemon_instances import daemon_entrypoint

    calls = []

    @daemon_entrypoint("unknown", source=__file__, runtime_root=lambda: calls.append("resolver"))
    def service():
        calls.append("body")

    with pytest.raises(ValueError, match="explicit purpose"):
        service()
    assert calls == []


def test_declared_fresh_runtime_root_is_created_before_admission(instance_store):
    root, runtime = instance_store
    fresh = runtime / "new" / "state"
    guard = store.admit(
        "fresh-service", runtime_root=fresh, source=__file__, purpose="production",
    )
    assert fresh.is_dir()
    assert store.inspect(root)[0]["runtime_root"] == str(fresh)
    guard.close()


def test_application_cannot_claim_inventory_lock_as_artifact(instance_store):
    root, runtime = instance_store
    guard = store.admit(
        "fixture", runtime_root=runtime.parent, source=__file__, purpose="production",
    )
    with pytest.raises((ValueError, OSError)):
        guard.add_artifact(guard.receipt.record_path.with_suffix(".lock"))
    assert guard.receipt.record_path.with_suffix(".lock").exists()
    guard.close()


def test_lifecycle_primitives_do_not_borrow_mutable_global_sleep_or_clocks(monkeypatch):
    clock = store._wall_clock
    monotonic = store._monotonic_clock
    with monkeypatch.context() as patch:
        patch.setattr(store.time, "time", lambda: pytest.fail("borrowed wall clock"))
        patch.setattr(store.time, "monotonic", lambda: pytest.fail("borrowed monotonic"))
        patch.setattr(store.time, "sleep", lambda _: pytest.fail("borrowed sleep"))
        assert clock() > 0
        assert monotonic() > 0
        assert not store._pause.wait(0)


def test_filtered_child_environment_cannot_drop_actual_test_owner(instance_store, monkeypatch):
    from agent_procutil.daemon_instances import daemon_test_lifecycle, reserve_instance

    root, runtime = instance_store
    with daemon_test_lifecycle(owner="real-test-owner", registry_root=root) as overlay:
        for key, value in overlay.items():
            monkeypatch.setenv(key, value)
        receipt = reserve_instance(
            DaemonInstanceSpec("filtered-env", runtime, Path(__file__).resolve()),
            environment={"ONLY_APPLICATION_VALUE": "retained"},
        )
        row = store.inspect(root)[0]
        assert row["owner"] == "real-test-owner"
        assert row["run_id"]
        assert row["purpose"] == "unattended-test"
        assert instance_environment(receipt)["COPILOT_EXTENSIONS_DAEMON_INSTANCE"] == receipt.instance_id


def test_interactive_policy_cannot_be_silently_replaced_by_unattended_context(instance_store, monkeypatch):
    from agent_procutil.daemon_instances import daemon_test_lifecycle, reserve_instance

    root, runtime = instance_store
    with daemon_test_lifecycle(owner="unattended-owner", registry_root=root) as overlay:
        with pytest.raises(ValueError, match="pause-tolerant lifecycle owner"):
            reserve_instance(DaemonInstanceSpec(
                "interactive", runtime, Path(__file__).resolve(),
                purpose="interactive-test", lease=DaemonLeasePolicy(86400, 172800),
            ), environment=overlay)


def test_existing_borrowed_code_root_is_metadata_not_file_ownership(instance_store, monkeypatch):
    root, runtime = instance_store
    borrowed = runtime / "readonly-code"
    borrowed.mkdir()
    original = store._plain

    def ownership(path, *, directory=False, owned=True):
        if Path(path) == borrowed and owned:
            raise OSError("foreign code owner")
        return original(path, directory=directory, owned=owned)

    monkeypatch.setattr(store, "_plain", ownership)
    guard = store.admit(
        "borrowed-code", source=__file__, runtime_root=borrowed, purpose="production",
    )
    assert store.inspect(root)[0]["runtime_root"] == str(borrowed)
    assert not list(borrowed.iterdir())
    guard.close()


def test_finite_production_never_stops_live_and_only_settles_control_after_exit(instance_store, monkeypatch):
    root, runtime = instance_store
    receipt = store.reserve(DaemonInstanceSpec(
        "finite", runtime, Path(__file__).resolve(), lifecycle="finite-worker",
    ))
    artifact = runtime / "production-artifact"
    artifact.write_text("retain")
    with store._locked(receipt) as path:
        row = store._read(path)
        row.update(pid="4321", birth="fixture", state="finished", artifact_count="1")
        store._write(path, row)
    monkeypatch.setattr(store, "identity_state", lambda *_: "matching")
    monkeypatch.setattr(store, "stop_identity", lambda *_a, **_k: pytest.fail("finite production stop"))
    assert store.cleanup(receipt).status == "live"
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    assert store.reconcile(root, 128, 5)[0].status == "settled"
    assert artifact.read_text() == "retain"


def test_failed_finite_launch_remains_held_even_after_exit(instance_store, monkeypatch):
    _, runtime = instance_store
    receipt = store.reserve(DaemonInstanceSpec(
        "finite", runtime, Path(__file__).resolve(), lifecycle="finite-worker",
    ))
    with store._locked(receipt) as path:
        row = store._read(path)
        row.update(pid="4321", birth="fixture", state="finished", spawn_failure="exit-unconfirmed")
        store._write(path, row)
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    assert store.cleanup(receipt).reason == "spawn-failure-held"
    assert receipt.record_path.exists()


@pytest.mark.parametrize("operation", ["inspect", "reconcile"])
def test_inventory_enumeration_permission_error_is_not_empty(instance_store, monkeypatch, operation):
    root, _ = instance_store
    root.mkdir()
    canary = root / ("a" * 32 + ".instance")
    canary.write_text("retain")
    original = store.os.scandir

    def denied(path):
        if Path(path) == root:
            raise PermissionError("inventory denied")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(store.os, "scandir", denied)
        with pytest.raises(PermissionError):
            if operation == "inspect":
                store.inspect(root)
            else:
                store.reconcile(root, 128, 5)
    assert canary.read_text() == "retain"


@pytest.mark.parametrize("operation", ["inspect", "reconcile"])
def test_suppressed_exists_cannot_hide_inventory_status_error(instance_store, monkeypatch, operation):
    root, _ = instance_store
    original_exists, original_lstat = Path.exists, Path.lstat

    def exists(path):
        return False if path == root else original_exists(path)

    def lstat(path):
        if path == root:
            raise PermissionError("unsearchable inventory ancestor")
        return original_lstat(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "exists", exists)
        patch.setattr(Path, "lstat", lstat)
        with pytest.raises(PermissionError):
            if operation == "inspect":
                store.inspect(root)
            else:
                store.reconcile(root, 128, 5)


@pytest.mark.parametrize("operation", ["inspect", "reconcile"])
def test_dangling_inventory_root_is_not_empty(instance_store, operation):
    root, _ = instance_store
    try:
        root.symlink_to(root.parent / "missing-inventory-target", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation unavailable")
    with pytest.raises(OSError):
        if operation == "inspect":
            store.inspect(root)
        else:
            store.reconcile(root, 128, 5)


@pytest.mark.parametrize("operation", ["inspect", "reconcile"])
def test_genuinely_missing_inventory_remains_empty_without_creation(instance_store, operation):
    root, _ = instance_store
    result = store.inspect(root) if operation == "inspect" else store.reconcile(root, 128, 5)
    assert result == []
    with pytest.raises(FileNotFoundError):
        root.lstat()
