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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
    with pytest.raises(StateError, match="authority"):
        _submit(area)
    assert _submit(area, authority="standalone").state == "pending"


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


def test_prevalidated_legacy_writer_racing_switch_cannot_execute(area):
    from update_driver import core

    root = area / "state"
    _submit(area)
    assert drain(root=root, executor=lambda _transaction: 0) == 0
    change_authority("standalone", root=root, timeout=1, verify_capabilities=lambda _deadline: True)
    spec = core._spec_document(core._spec(RequestSpec("engine", project="example")))
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


def test_process_death_mid_switch_leaves_a_retryable_fence(area):
    import agent_procutil
    import single_instance_lease
    import update_driver

    root = area / "state"
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
    assert change_authority(
        "project", project="example", root=root, timeout=5,
        verify_capabilities=lambda _deadline: True,
    ).mode == "project"


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
