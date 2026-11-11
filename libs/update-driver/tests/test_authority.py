import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from update_driver import (
    InterruptedError,
    RequestSpec,
    StateError,
    TrustedRunner,
    change_authority,
    current_authority,
    drain,
    enqueue,
    status,
)


@pytest.fixture
def area(tmp_path_factory):
    return tmp_path_factory.mktemp("authority").resolve()


def _runner(area):
    import sys

    return TrustedRunner((sys.executable, "-c", "pass"), str(area))


def _submit(area, *, authority="", project="", context=(), backend="engine"):
    return enqueue(
        RequestSpec(backend, project=project, context=context, authority=authority),
        runner=_runner(area), root=area / "state",
    )


def _receipt(area, marketplace, plugin):
    path = area / "marketplaces" / marketplace / "plugins" / plugin / "install.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def test_absent_authority_keeps_standalone_behavior_without_creating_state(area):
    root = area / "state"
    assert current_authority(root=root).mode == "standalone"
    assert current_authority(root=root).configured is False
    assert not root.exists()
    request = _submit(area)
    assert request.state == "pending"
    with pytest.raises(StateError, match="authority"):
        _submit(area, authority="project", project="example")


def test_authority_read_during_first_queue_publication_is_read_only(area, monkeypatch):
    from update_driver import core

    root = area / "state"
    publishing, release = threading.Event(), threading.Event()
    errors = []
    validate = core._validate_db

    def pause_publication(db):
        validate(db)
        if db.in_transaction and threading.current_thread() is writer:
            publishing.set()
            assert release.wait(5)

    def submit():
        try:
            _submit(area)
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(core, "_validate_db", pause_publication)
    writer = threading.Thread(target=submit)
    writer.start()
    try:
        assert publishing.wait(5)
        before = (root / "queue.sqlite3").read_bytes()
        assert current_authority(root=root).configured is False
        assert (root / "queue.sqlite3").read_bytes() == before
    finally:
        release.set()
        writer.join(10)
    assert not writer.is_alive() and not errors
    assert current_authority(root=root).configured is False


@pytest.mark.parametrize("state", ["guard", "table", "version"])
def test_pristine_authority_read_never_accepts_incomplete_or_foreign_state(area, state):
    from update_driver.authority import ensure_guard

    root = area / "state"
    root.mkdir()
    with sqlite3.connect(root / "queue.sqlite3") as db:
        if state == "guard":
            ensure_guard(root)
        elif state == "table":
            db.execute("CREATE TABLE unrelated (id INTEGER)")
        else:
            db.execute("PRAGMA user_version=99")
    with pytest.raises(StateError):
        current_authority(root=root)


def test_switch_waits_for_active_writer_and_enforces_both_modes(area):
    root = area / "state"
    request = _submit(area)
    entered, release, switched = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def execute(_transaction):
        entered.set()
        assert release.wait(5)
        return 0

    def switch():
        try:
            change_authority(
                "project", project="example", root=root, timeout=5,
                verify_capabilities=lambda _deadline: True,
            )
        except BaseException as error:
            errors.append(error)
        finally:
            switched.set()

    worker = threading.Thread(target=lambda: drain(root=root, executor=execute))
    worker.start()
    assert entered.wait(5)
    changer = threading.Thread(target=switch)
    changer.start()
    try:
        assert not switched.wait(.1)
        assert current_authority(root=root).mode == "standalone"
        assert current_authority(root=root).configured is False
    finally:
        release.set()
        worker.join(10)
        changer.join(10)
    assert not worker.is_alive() and not changer.is_alive() and not errors
    assert status(request.id, root=root).exit_code == 0
    assert current_authority(root=root).mode == "project"
    with pytest.raises(StateError, match="authority"):
        _submit(area)
    with pytest.raises(StateError, match="authority"):
        _submit(area, authority="project", project="different")
    selected = _submit(area, authority="project", project="example")
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    assert status(selected.id, root=root).exit_code == 0
    change_authority("standalone", root=root, timeout=5, verify_capabilities=lambda _deadline: True)
    assert current_authority(root=root).mode == "standalone"
    assert current_authority(root=root).configured is True
    with pytest.raises(StateError, match="authority"):
        _submit(area, authority="project", project="example")
    assert _submit(area, authority="standalone").state == "pending"


def test_switch_preserves_pending_and_uncertain_work(area):
    root = area / "state"
    request = _submit(area)
    with pytest.raises(StateError, match="pending"):
        change_authority(
            "project", project="example", root=root, timeout=.1,
            verify_capabilities=lambda _deadline: True,
        )
    assert status(request.id, root=root).state == "pending"

    def interrupted(_transaction):
        raise RuntimeError("unsettled")

    with pytest.raises(RuntimeError, match="unsettled"):
        drain(root=root, executor=interrupted)
    with pytest.raises(InterruptedError):
        change_authority(
            "project", project="example", root=root, timeout=1,
            verify_capabilities=lambda _deadline: True,
        )
    assert current_authority(root=root).mode == "standalone"


def test_first_protocol_upgrade_waits_for_unrelated_legacy_requests(area):
    root = area / "state"
    request = _submit(area, backend="other-product")
    with pytest.raises(StateError, match="pending"):
        change_authority("project", project="example", root=root, timeout=.1,
                         verify_capabilities=lambda _deadline: True)
    assert status(request.id, root=root).state == "pending"
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    assert change_authority(
        "project", project="example", root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"


def test_authority_is_scoped_and_unrelated_requests_remain_independent(area):
    root = area / "state"
    first = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    sibling = _receipt(area, "first--0123456789abcdef", "agent-bridge")
    second = _receipt(area, "second--0123456789abcdef", "agent-worktrees")
    change_authority(
        "project", project="example", context=str(first), root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    )
    assert current_authority(root=root, context=str(first)).mode == "project"
    assert current_authority(root=root, context=str(sibling)).mode == "project"
    assert current_authority(root=root, context=str(second)).mode == "standalone"
    assert current_authority(root=root).mode == "standalone"
    with pytest.raises(StateError, match="authority"):
        _submit(area, context=(("COPILOT_EXTENSIONS_CONTEXT", str(sibling)),))
    assert _submit(area, context=(("COPILOT_EXTENSIONS_CONTEXT", str(second)),)).state == "pending"
    assert _submit(area, backend="other-product").state == "pending"
    assert change_authority(
        "standalone", context=str(sibling), root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "standalone"


def test_invalid_cell_context_fails_closed_after_authority_is_configured(area):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        current_authority(root=root, context=str(area / "missing.json"))
    with pytest.raises(StateError, match="authority"):
        _submit(area, context=(("COPILOT_EXTENSIONS_CONTEXT", str(area / "missing.json")),))


def test_removed_queued_receipt_is_refused_without_stalling_unrelated_work(area):
    root = area / "state"
    receipt = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    change_authority("project", project="example", context=str(receipt), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    stale = _submit(
        area, authority="project", project="example",
        context=(("COPILOT_EXTENSIONS_CONTEXT", str(receipt)),),
    )
    unrelated = _submit(area, backend="other-product")
    receipt.unlink()
    ran = []
    assert drain(root=root, executor=lambda transaction: ran.append(transaction.request.id) or 0) == 0
    assert status(stale.id, root=root).exit_code == 3
    assert "context" in status(stale.id, root=root).error
    assert status(unrelated.id, root=root).exit_code == 0
    assert ran == [unrelated.id]


def test_missing_global_guard_still_fences_all_pending_work(area):
    root = area / "state"
    change_authority("standalone", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    request = _submit(area, authority="standalone")
    (root / "authority.guard").unlink()
    with pytest.raises(StateError, match="authority"):
        drain(root=root, executor=lambda _transaction: pytest.fail("guard was ignored"))
    assert status(request.id, root=root).state == "pending"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows short-name aliases")
def test_short_receipt_path_selects_the_same_cell(area):
    import ctypes

    root = area / "state"
    receipt = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(receipt), buffer, len(buffer)):
        pytest.skip("volume does not provide short-name aliases")
    alias = Path(buffer.value)
    if alias == receipt:
        pytest.skip("volume did not create an alternate short name")
    change_authority("project", project="example", context=str(receipt), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    assert current_authority(root=root, context=str(alias)).mode == "project"


def test_linked_receipt_does_not_select_an_authority(area):
    root = area / "state"
    receipt = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    link = receipt.parent.parent / "agent-bridge" / "install.json"
    link.parent.mkdir()
    try:
        link.symlink_to(receipt)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    change_authority("project", project="example", context=str(receipt), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        current_authority(root=root, context=str(link))


def test_invalid_or_unqualified_authority_never_changes_mode(area):
    root = area / "state"
    with pytest.raises(StateError, match="capabilit"):
        change_authority("project", project="example", root=root, timeout=1,
                         verify_capabilities=lambda _deadline: False)
    assert current_authority(root=root).mode == "standalone"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("UPDATE suite_authority SET project='invalid project' WHERE scope=''")
    with pytest.raises(StateError, match="authority"):
        current_authority(root=root)
    with pytest.raises(StateError, match="authority"):
        _submit(area)


def test_configured_standalone_fences_legacy_queue_protocol(area):
    root = area / "state"
    change_authority("standalone", root=root, timeout=1, verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
    with pytest.raises(StateError, match="authority"):
        _submit(area)
    assert _submit(area, authority="standalone").state == "pending"
    native = _submit(area, backend="plugin-native-install")
    assert native.state == "pending" and native.spec.authority == "standalone"


def test_existing_project_selection_requires_verified_protocol_upgrade(area):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    with pytest.raises(StateError, match="protocol upgrade"):
        current_authority(root=root)
    calls = []
    selected = change_authority(
        "project", project="example", root=root, timeout=1,
        verify_capabilities=lambda deadline: calls.append(deadline) or True,
    )
    assert selected.mode == "project" and len(calls) == 1
    with sqlite3.connect(root / "queue.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
    assert current_authority(root=root) == selected


@pytest.mark.parametrize("previous", ["standalone", "project"])
def test_interrupted_v5_upgrade_preserves_the_previous_selection(area, monkeypatch, previous):
    from update_driver import authority

    root = area / "state"
    change_authority(previous, project="example" if previous == "project" else "",
                     root=root, timeout=1, verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    write = authority.write

    def interrupt(*args):
        write(*args)
        raise RuntimeError("interrupted during upgrade")

    with monkeypatch.context() as change:
        change.setattr(authority, "write", interrupt)
        with pytest.raises(RuntimeError, match="interrupted"):
            change_authority("project", project="example", root=root, timeout=1,
                             verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("SELECT mode FROM suite_authority WHERE scope=''").fetchone()[0] == previous
    if previous == "project":
        with pytest.raises(StateError, match="protocol upgrade"):
            current_authority(root=root)
    else:
        assert current_authority(root=root).mode == "standalone"
    assert change_authority(
        "project", project="example", root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"


def test_protocol_upgrade_waits_for_pending_standalone_native_writer(area):
    root = area / "state"
    change_authority("standalone", root=root, timeout=1, verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    request = _submit(area, backend="plugin-native-install")
    with pytest.raises(StateError, match="pending"):
        change_authority("project", project="example", root=root, timeout=.1,
                         verify_capabilities=lambda _deadline: True)
    assert status(request.id, root=root).state == "pending"
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    assert status(request.id, root=root).exit_code == 0
    assert change_authority(
        "project", project="example", root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"


def test_v5_project_upgrade_preserves_independent_cell_work(area):
    root = area / "state"
    selected = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    other = _receipt(area, "second--0123456789abcdef", "agent-worktrees")
    change_authority("project", project="example", context=str(selected), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    request = _submit(area, backend="plugin-native-install", project=str(other.parent))
    with pytest.raises(StateError, match="pending"):
        change_authority("project", project="example", context=str(selected), root=root, timeout=.1,
                         verify_capabilities=lambda _deadline: True)
    assert status(request.id, root=root).state == "pending"
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    assert status(request.id, root=root).exit_code == 0
    assert change_authority(
        "project", project="example", context=str(selected), root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"


def test_capability_probe_receives_deadline_and_cannot_publish_late(area):
    root = area / "state"
    deadlines = []

    def probe(deadline):
        deadlines.append(deadline)
        time.sleep(.15)
        return True

    with pytest.raises(StateError, match="capabilit"):
        change_authority("project", project="example", root=root, timeout=.1,
                         verify_capabilities=probe)
    assert len(deadlines) == 1
    assert not (root / "authority.guard").exists()
    assert current_authority(root=root).configured is False


@pytest.mark.parametrize("backend", ["manager-self-install", "engine-dev-release"])
def test_project_authority_fences_direct_suite_owner_changes(area, backend):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend=backend)
    assert _submit(area, backend="other-product").state == "pending"


@pytest.mark.parametrize(
    "backend",
    ["engine-native-install", "plugin-native-install", "manager-bootstrap",
     "manager-uninstall", "owning-plugin-provision"],
)
def test_project_authority_fences_direct_native_writers(area, backend):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend=backend)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend=backend, authority="project", project="example")
    assert _submit(area, backend="other-product").state == "pending"


def test_direct_native_writer_is_restored_after_return_to_standalone(area):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend="plugin-native-install")
    change_authority("standalone", root=root, timeout=1, verify_capabilities=lambda _deadline: True)
    request = _submit(area, backend="plugin-native-install")
    assert request.spec.authority == "standalone"
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    assert status(request.id, root=root).exit_code == 0


def test_cell_authority_fences_direct_writers_only_in_that_cell(area):
    root = area / "state"
    selected = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    other = _receipt(area, "second--0123456789abcdef", "agent-worktrees")
    change_authority("project", project="example", context=str(selected), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend="engine-native-install",
                context=(("COPILOT_EXTENSIONS_CONTEXT", str(selected)),))
    assert _submit(area, backend="engine-native-install",
                   context=(("COPILOT_EXTENSIONS_CONTEXT", str(other)),)).state == "pending"


def test_global_project_authority_preserves_independent_cell_native_writer(area):
    root = area / "state"
    cell = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    request = _submit(area, backend="plugin-native-install", project=str(cell.parent),
                      context=(("COPILOT_EXTENSIONS_CONTEXT", str(cell)),))
    assert request.state == "pending"


def test_global_switch_waits_for_shared_writer_with_cell_context(area):
    root = area / "state"
    cell = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    change_authority("standalone", root=root, timeout=1, verify_capabilities=lambda _deadline: True)
    request = _submit(area, backend="manager-bootstrap", project=str(area / "legacy"),
                      context=(("COPILOT_EXTENSIONS_CONTEXT", str(cell)),))
    assert request.spec.authority == "standalone"
    with pytest.raises(StateError, match="pending"):
        change_authority("project", project="example", root=root, timeout=.1,
                         verify_capabilities=lambda _deadline: True)
    assert status(request.id, root=root).state == "pending"
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    assert change_authority("project", project="example", root=root, timeout=1,
                            verify_capabilities=lambda _deadline: True).mode == "project"


def test_native_writer_cell_root_cannot_bypass_authority_by_omitting_context(area):
    root = area / "state"
    selected = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    other = _receipt(area, "second--0123456789abcdef", "agent-worktrees")
    change_authority("project", project="example", context=str(selected), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend="engine-native-install", project=str(selected.parent))
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend="engine-native-install", project=str(selected.parent),
                context=(("COPILOT_EXTENSIONS_CONTEXT", str(other)),))
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend="engine-native-install", project=str(selected.parent.parent / "missing"))
    assert _submit(area, backend="engine-native-install", project=str(other.parent)).state == "pending"


def test_native_writer_rechecks_inferred_cell_receipt_before_execution(area):
    root = area / "state"
    receipt = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    change_authority("standalone", context=str(receipt), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    request = _submit(area, backend="engine-native-install", project=str(receipt.parent))
    assert request.spec.authority == "standalone"
    receipt.unlink()
    assert drain(root=root, executor=lambda _transaction: pytest.fail("stale cell ran")) == 0
    result = status(request.id, root=root)
    assert result.exit_code == 3 and result.started_at is None


@pytest.mark.parametrize(
    "backend",
    ["engine-native-install", "plugin-native-install", "manager-bootstrap",
     "manager-uninstall", "manager-self-install", "owning-plugin-provision"],
)
def test_legacy_direct_writer_cannot_bypass_global_authority_with_cell_context(area, backend):
    root = area / "state"
    other = _receipt(area, "second--0123456789abcdef", "agent-worktrees")
    legacy = area / "legacy-runtime"
    legacy.mkdir()
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend=backend, project=str(legacy),
                context=(("COPILOT_EXTENSIONS_CONTEXT", str(other)),))


def test_legacy_direct_writer_also_honors_its_cell_authority(area):
    root = area / "state"
    cell = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    legacy = area / "legacy-runtime"
    legacy.mkdir()
    change_authority("project", project="example", context=str(cell), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend="plugin-native-install", project=str(legacy),
                context=(("COPILOT_EXTENSIONS_CONTEXT", str(cell)),))


def test_native_writer_cell_alias_cannot_select_global_authority(area):
    root = area / "state"
    selected = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    alias = area / "cell-alias"
    try:
        alias.symlink_to(selected.parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    change_authority("project", project="example", context=str(selected), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend="plugin-native-install", project=str(alias))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows short-name aliases")
def test_native_writer_short_cell_path_uses_cell_authority(area):
    import ctypes

    root = area / "state"
    selected = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(selected.parent), buffer, len(buffer)):
        pytest.skip("volume does not provide short-name aliases")
    alias = Path(buffer.value)
    if alias == selected.parent:
        pytest.skip("volume did not create an alternate short name")
    change_authority("project", project="example", context=str(selected), root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        _submit(area, backend="plugin-native-install", project=str(alias))


def test_project_authority_fences_reconcile_even_when_launch_update_is_disabled(area):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        enqueue(
            RequestSpec("engine-launch-update", ("automatic", "--no-update", "--with-reconcile")),
            runner=_runner(area), root=root,
        )
    request = enqueue(
        RequestSpec("engine-launch-update", ("automatic", "--no-update")),
        runner=_runner(area), root=root,
    )
    assert request.state == "pending"


@pytest.mark.parametrize("mode", ["standalone", "project"])
@pytest.mark.parametrize("backend", ["engine", "engine-native-install", "plugin-native-install"])
def test_prevalidated_legacy_writer_racing_switch_cannot_execute(area, backend, mode):
    from update_driver import core

    root = area / "state"
    _submit(area)
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    change_authority(mode, project="example" if mode == "project" else "", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    spec = core._spec_document(core._spec(RequestSpec(backend, project="example")))
    runner = asdict(core._runner(_runner(area)))
    spec_json, runner_json = core._json(spec), core._json(runner)
    identity = hashlib.sha256((spec_json + "\n" + runner_json).encode()).hexdigest()
    request_id = "f" * 32
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute(
            "INSERT INTO requests(id,identity,spec,runner,state,created_at) VALUES(?,?,?,?,?,?)",
            (request_id, identity, spec_json, runner_json, "pending", time.time()),
        )
    ran = []
    assert drain(root=root, executor=lambda _transaction: ran.append(True) or 0) == 0
    assert ran == []
    assert status(request_id, root=root).exit_code == 3


def test_interrupted_authority_switch_fails_closed_and_can_be_retried(area, monkeypatch):
    from update_driver import authority

    root = area / "state"
    write = authority.write

    def interrupt(*args):
        write(*args)
        raise RuntimeError("interrupted before commit")

    monkeypatch.setattr(authority, "write", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        change_authority("project", project="example", root=root, timeout=1,
                         verify_capabilities=lambda _deadline: True)
    with pytest.raises(StateError, match="authority"):
        current_authority(root=root)
    monkeypatch.setattr(authority, "write", write)
    result = change_authority("project", project="example", root=root, timeout=1,
                              verify_capabilities=lambda _deadline: True)
    assert result.mode == "project" and result.project == "example"


def _prevalidated_legacy_submission(area, identifier, backend, *, authority=""):
    from update_driver import core

    spec = core._spec_document(core._spec(RequestSpec(backend, project="example", authority=authority)))
    runner = asdict(core._runner(_runner(area)))
    spec_json, runner_json = core._json(spec), core._json(runner)
    identity = hashlib.sha256((spec_json + "\n" + runner_json).encode()).hexdigest()
    return identifier, identity, spec_json, runner_json, time.time()


def _commit_legacy_submission(root, request):
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute(
            "INSERT INTO requests(id,identity,spec,runner,state,created_at) VALUES(?,?,?,?,'pending',?)",
            request,
        )


def test_v5_project_upgrade_refuses_late_native_writer_but_preserves_unrelated_work(area):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    late = _prevalidated_legacy_submission(area, "a" * 32, "plugin-native-install")
    unrelated = _prevalidated_legacy_submission(area, "b" * 32, "other-product")
    _commit_legacy_submission(root, late)
    _commit_legacy_submission(root, unrelated)
    with pytest.raises(StateError, match="pending"):
        change_authority("project", project="example", root=root, timeout=.1,
                         verify_capabilities=lambda _deadline: True)
    refused = status(late[0], root=root)
    assert refused.state == "completed" and refused.exit_code == 3 and refused.started_at is None
    assert "authority upgrade" in refused.error
    assert status(unrelated[0], root=root).state == "pending"
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    assert change_authority(
        "project", project="example", root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"


@pytest.mark.parametrize("reason", ["capability", "execution-evidence"])
def test_v5_upgrade_never_discards_unqualified_native_work(area, reason):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    late = _prevalidated_legacy_submission(area, "d" * 32, "plugin-native-install")
    _commit_legacy_submission(root, late)
    if reason == "execution-evidence":
        with sqlite3.connect(root / "queue.sqlite3") as db:
            db.execute("UPDATE requests SET started_at=1 WHERE id=?", (late[0],))
    with pytest.raises(StateError if reason == "capability" else InterruptedError):
        change_authority("project", project="example", root=root, timeout=1,
                         verify_capabilities=lambda _deadline: reason != "capability")
    assert status(late[0], root=root).state == "pending"
    if reason == "execution-evidence":
        with pytest.raises(InterruptedError):
            drain(root=root, executor=lambda _transaction: pytest.fail("uncertain request ran"))
        assert status(late[0], root=root).state == "pending"
        with pytest.raises(InterruptedError):
            change_authority("project", project="example", root=root, timeout=1,
                             verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5


@pytest.mark.parametrize("backend", [
    "plugin-native-install", "manager-bootstrap", "owning-plugin-provision",
])
def test_v5_standalone_executes_previously_accepted_unstamped_native_work(area, backend):
    root = area / "state"
    change_authority("standalone", root=root, timeout=1, verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    accepted = _prevalidated_legacy_submission(area, "e" * 32, backend)
    _commit_legacy_submission(root, accepted)
    ran = []
    assert drain(root=root, executor=lambda transaction: ran.append(transaction.request.id) or 0) == 0
    assert ran == [accepted[0]] and status(accepted[0], root=root).exit_code == 0
    assert change_authority("project", project="example", root=root, timeout=1,
                            verify_capabilities=lambda _deadline: True).mode == "project"


@pytest.mark.parametrize("field,value", [
    ("started_at", 1), ("finished_at", 1), ("boot_id", "uncertain"),
])
def test_pending_execution_evidence_fences_worker_and_authority_change(area, field, value):
    from update_driver import recover_for_repair

    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    request = _submit(area, backend="other-product")
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute(f"UPDATE requests SET {field}=? WHERE id=?", (value, request.id))
    with pytest.raises(InterruptedError):
        drain(root=root, executor=lambda _transaction: pytest.fail("uncertain request ran"))
    with pytest.raises(InterruptedError):
        change_authority("standalone", root=root, timeout=1,
                         verify_capabilities=lambda _deadline: True)
    preserved = status(request.id, root=root)
    assert preserved.state == "pending" and getattr(preserved, field) == value
    assert preserved.unresolved
    released, = recover_for_repair(authorized=True, root=root)
    assert released.id == request.id and released.state == "interrupted"
    assert getattr(released, field) == value and released.exit_code is None
    assert released.recovery_authority == "operator_repair" and not released.unresolved
    assert change_authority("standalone", root=root, timeout=1,
                            verify_capabilities=lambda _deadline: True).mode == "standalone"


def test_v5_project_read_requires_matching_live_custody_and_scope(area, monkeypatch):
    from update_driver import borrowed_custody
    from update_driver.participant import ENVIRONMENT_KEY

    root = area / "state"
    first = _receipt(area, "first--0123456789abcdef", "agent-worktrees")
    second = _receipt(area, "second--0123456789abcdef", "agent-worktrees")
    for receipt in (first, second):
        change_authority("project", project="example", context=str(receipt), root=root, timeout=1,
                         verify_capabilities=lambda _deadline: True)
    request = _submit(area, authority="project", project="example",
                      context=(("COPILOT_EXTENSIONS_CONTEXT", str(first)),))
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    with pytest.raises(StateError, match="protocol upgrade"):
        current_authority(root=root, context=str(first))
    witnesses = []

    def execute(transaction):
        environment = transaction.participant_env()
        witnesses.append(environment[ENVIRONMENT_KEY])
        with borrowed_custody(root=root, environ=environment) as custody:
            custody.assert_owned()
            assert current_authority(root=root, context=str(first)).project == "example"
            with pytest.raises(StateError, match="protocol upgrade"):
                current_authority(root=root, context=str(second))
            with pytest.raises(StateError, match="protocol upgrade"):
                _submit(area, authority="project", project="example",
                        context=(("COPILOT_EXTENSIONS_CONTEXT", str(first)),))
        forged = json.loads(witnesses[0])
        forged["key"] = "00" * 32
        with monkeypatch.context() as change:
            change.setenv(ENVIRONMENT_KEY, json.dumps(forged))
            with pytest.raises(StateError, match="custody"):
                current_authority(root=root, context=str(first))
        return 0

    assert drain(root=root, executor=execute) == 0
    assert status(request.id, root=root).exit_code == 0
    with monkeypatch.context() as change:
        change.setenv(ENVIRONMENT_KEY, witnesses[0])
        with pytest.raises(StateError, match="custody"):
            current_authority(root=root, context=str(first))


def test_v5_project_read_rejects_unrelated_running_custody(area):
    from update_driver import borrowed_custody

    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    request = _submit(area, backend="other-product", authority="project", project="example")
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")

    def execute(transaction):
        with borrowed_custody(root=root, environ=transaction.participant_env()):
            with pytest.raises(StateError, match="protocol upgrade"):
                current_authority(root=root)
        return 0

    assert drain(root=root, executor=execute) == 0
    assert status(request.id, root=root).exit_code == 0


def test_v5_project_upgrade_preserves_accepted_project_request(area):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute("PRAGMA user_version=5")
    accepted = _prevalidated_legacy_submission(area, "c" * 32, "engine", authority="project")
    _commit_legacy_submission(root, accepted)
    with pytest.raises(StateError, match="pending"):
        change_authority("project", project="example", root=root, timeout=.1,
                         verify_capabilities=lambda _deadline: True)
    assert status(accepted[0], root=root).state == "pending"
    ran = []
    assert drain(root=root, executor=lambda transaction: ran.append(transaction.request.id) or 0) == 0
    assert ran == [accepted[0]] and status(accepted[0], root=root).exit_code == 0
    assert change_authority(
        "project", project="example", root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"


def _interrupt_first_authority_switch(root, monkeypatch):
    from update_driver import authority

    write = authority.write

    def interrupt(*args):
        write(*args)
        raise RuntimeError("interrupted before commit")

    with monkeypatch.context() as change:
        change.setattr(authority, "write", interrupt)
        with pytest.raises(RuntimeError, match="interrupted"):
            change_authority("project", project="example", root=root, timeout=1,
                             verify_capabilities=lambda _deadline: True)
    assert (root / "authority.guard").is_file()
    with sqlite3.connect(root / "queue.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4


def test_retry_refuses_late_legacy_suite_without_discarding_unrelated_work(area, monkeypatch):
    root = area / "state"
    late_suite = _prevalidated_legacy_submission(area, "a" * 32, "engine")
    unrelated = _prevalidated_legacy_submission(area, "b" * 32, "other-product")
    _interrupt_first_authority_switch(root, monkeypatch)
    _commit_legacy_submission(root, late_suite)
    _commit_legacy_submission(root, unrelated)
    with pytest.raises(StateError, match="pending"):
        change_authority("project", project="example", root=root, timeout=.1,
                         verify_capabilities=lambda _deadline: True)
    refused = status(late_suite[0], root=root)
    assert refused.state == "completed" and refused.exit_code == 3
    assert refused.started_at is None and "authority" in refused.error
    assert status(unrelated[0], root=root).state == "pending"
    ran = []
    assert drain(root=root, executor=lambda transaction: ran.append(transaction.request.id) or 0) == 0
    assert ran == [unrelated[0]]
    assert change_authority(
        "project", project="example", root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"


def test_incomplete_switch_recovery_requires_capabilities_and_is_atomic(area, monkeypatch):
    from update_driver import authority

    root = area / "state"
    late_suite = _prevalidated_legacy_submission(area, "c" * 32, "engine")
    _interrupt_first_authority_switch(root, monkeypatch)
    _commit_legacy_submission(root, late_suite)
    with pytest.raises(StateError, match="capabilit"):
        change_authority("project", project="example", root=root, timeout=.2,
                         verify_capabilities=lambda _deadline: False)
    assert status(late_suite[0], root=root).state == "pending"
    write = authority.write

    def interrupt(*args):
        write(*args)
        raise RuntimeError("recovery interrupted")

    with monkeypatch.context() as change:
        change.setattr(authority, "write", interrupt)
        with pytest.raises(RuntimeError, match="recovery interrupted"):
            change_authority("project", project="example", root=root, timeout=1,
                             verify_capabilities=lambda _deadline: True)
    assert status(late_suite[0], root=root).state == "pending"
    assert change_authority(
        "project", project="example", root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"
    refused = status(late_suite[0], root=root)
    assert refused.state == "completed" and refused.exit_code == 3
    assert refused.started_at is None


@pytest.mark.parametrize(("field", "value"), [
    ("started_at", 1.0), ("finished_at", 1.0), ("boot_id", "unverified-boot"),
])
def test_incomplete_switch_never_refuses_pending_execution_evidence(area, monkeypatch, field, value):
    root = area / "state"
    late_suite = _prevalidated_legacy_submission(area, "e" * 32, "engine")
    _interrupt_first_authority_switch(root, monkeypatch)
    _commit_legacy_submission(root, late_suite)
    with sqlite3.connect(root / "queue.sqlite3") as db:
        db.execute(f"UPDATE requests SET {field}=? WHERE id=?", (value, late_suite[0]))
    with pytest.raises(InterruptedError, match="execution evidence"):
        change_authority("project", project="example", root=root, timeout=1,
                         verify_capabilities=lambda _deadline: True)
    assert status(late_suite[0], root=root).state == "pending"
    with pytest.raises(StateError, match="authority"):
        current_authority(root=root)


def test_process_death_mid_switch_leaves_a_retryable_fence(area):
    import agent_procutil
    import single_instance_lease
    import update_driver

    root = area / "state"
    late_suite = _prevalidated_legacy_submission(area, "d" * 32, "engine")
    paths = [
        str(Path(module.__file__).resolve().parent.parent)
        for module in (update_driver, single_instance_lease, agent_procutil)
    ]
    program = """
import json, os, pathlib, sys
sys.path[:0] = json.loads(sys.argv[1])
from update_driver import authority, change_authority
original = authority.write
def crash(*args):
    original(*args)
    os._exit(42)
authority.write = crash
change_authority('project', project='example', root=pathlib.Path(sys.argv[2]),
                 timeout=5, verify_capabilities=lambda _deadline: True)
"""
    child = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program, json.dumps(paths), str(root)],
        capture_output=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert child.returncode == 42, child.stderr.decode(errors="replace")
    with pytest.raises(StateError, match="authority"):
        current_authority(root=root)
    _commit_legacy_submission(root, late_suite)
    assert change_authority(
        "project", project="example", root=root, timeout=5,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"
    refused = status(late_suite[0], root=root)
    assert refused.state == "completed" and refused.exit_code == 3 and refused.started_at is None


@pytest.mark.parametrize("missing", ["database", "guard"])
def test_missing_authority_state_does_not_restore_legacy_mode(area, missing):
    root = area / "state"
    change_authority("project", project="example", root=root, timeout=1,
                     verify_capabilities=lambda _deadline: True)
    (root / ("queue.sqlite3" if missing == "database" else "authority.guard")).unlink()
    with pytest.raises(StateError, match="authority"):
        current_authority(root=root)
    with pytest.raises(StateError, match="authority"):
        change_authority("standalone", root=root, timeout=1, verify_capabilities=lambda _deadline: True)
