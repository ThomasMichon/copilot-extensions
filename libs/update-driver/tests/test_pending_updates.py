"""Latest-wins admission uses the real journal, never a production installation."""

import io
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace

import pytest

from update_driver import (
    InterruptedError, RequestSpec, StateError, TrustedRunner, drain, enqueue, status, wait,
)


def submit(area, source, *, spec=None, runner=None):
    return enqueue(
        spec or RequestSpec("manager", source=source, cwd=str(area), repair=True),
        runner=runner or TrustedRunner((sys.executable, "-c", "pass"), str(area)),
        root=area / "state",
    )


def test_running_update_preserved_and_only_newest_waiter_executes(area):
    first = submit(area, "active")
    executed = []
    replaced = []

    def execute(tx):
        executed.append(tx.request.id)
        if tx.request.id == first.id:
            old = submit(area, "old-bootstrap")
            newest = submit(area, "new-bootstrap")
            assert status(first.id, root=tx.root) == tx.request
            assert status(old.id, root=tx.root).spec == old.spec
            output = io.StringIO()
            result = wait(old.id, root=tx.root, timeout=0, output=output)
            assert result.state == "completed" and result.exit_code == 125
            assert result.started_at is None and not result.unresolved
            assert newest.id in result.error and "nothing ran" in output.getvalue()
            assert [r.id for r in status(root=tx.root) if r.state == "pending"] == [newest.id]
            replaced.extend((old, newest))
        return 0

    assert drain(root=area / "state", executor=execute) == 0
    old, newest = replaced
    assert executed == [first.id, newest.id]
    assert status(old.id, root=area / "state").exit_code == 125


@pytest.mark.parametrize("backend", ["manager", "engine", "engine-launch-update"])
def test_distinct_runner_source_and_options_replace_waiting_selection(area, backend):
    spec = RequestSpec(backend, ("--force",), cwd=str(area), source="old", repair=True)
    old = submit(area, "", spec=spec)
    newer = submit(
        area, "", spec=replace(spec, argv=("--no-manager",), source="new"),
        runner=TrustedRunner((sys.executable, "-c", "raise SystemExit(17)"), str(area)),
    )
    assert status(old.id, root=area / "state").exit_code == 125
    assert newer.spec.argv == ("--no-manager",)
    assert drain(root=area / "state", executor=lambda tx: 17) == 0
    assert wait(newer.id, root=area / "state", timeout=0).exit_code == 17


def test_equivalent_waiter_coalesces_but_completed_request_does_not_debounce(area):
    first = submit(area, "same")
    assert submit(area, "same") == first
    assert drain(root=area / "state", executor=lambda tx: 0) == 0
    assert submit(area, "same").id != first.id


def test_equivalent_waiter_collapses_old_client_backlog(area, monkeypatch):
    from update_driver import core

    with monkeypatch.context() as legacy:
        legacy.setattr(core, "_supersede_waiting", lambda *args: None)
        first = submit(area, "first")
        other = submit(area, "other")
    assert submit(area, "first") == first
    assert status(other.id, root=area / "state").exit_code == 125
    assert first.id in status(other.id, root=area / "state").error


def test_plugin_receipts_in_one_cell_share_waiting_update_slot(area):
    cell = area / "marketplaces" / "sample--0123456789abcdef"
    first = submit(area, "", spec=RequestSpec(
        "engine", cwd=str(area),
        context=(("COPILOT_EXTENSIONS_CONTEXT", str(cell / "plugins" / "one" / "install.json")),),
    ))
    new = submit(area, "", spec=RequestSpec(
        "engine", cwd=str(area),
        context=(("COPILOT_EXTENSIONS_CONTEXT", str(cell / "plugins" / "two" / "install.json")),),
    ))
    assert status(first.id, root=area / "state").exit_code == 125
    assert new.id in status(first.id, root=area / "state").error


def test_background_request_cannot_replace_waiting_manual_but_manual_replaces_auto(area):
    automatic = RequestSpec("manager", ("--auto",), cwd=str(area))
    old = submit(area, "", spec=automatic)
    manual = submit(area, "manual")
    assert status(old.id, root=area / "state").exit_code == 125
    before = status(root=area / "state")
    with pytest.raises(StateError, match="explicit update already waiting"):
        submit(area, "", spec=replace(automatic, source="new-auto"))
    assert status(root=area / "state") == before
    assert status(manual.id, root=area / "state").state == "pending"


@pytest.mark.parametrize("difference", ["root", "project", "authority", "cell", "home"])
def test_independent_scope_keeps_its_waiting_update(area, difference):
    spec = RequestSpec("manager", cwd=str(area), repair=True)
    old = submit(area, "", spec=spec)
    if difference == "root":
        spec = replace(spec, context=(("WORKTREE_MANAGER_ROOT", str(area / "other")),))
    elif difference == "project":
        spec = replace(spec, project="other")
    elif difference == "authority":
        spec = replace(spec, authority="standalone")
    elif difference == "home":
        spec = replace(spec, context=(("AGENT_HOME", str(area / "other")),))
    else:
        spec = replace(spec, context=(("COPILOT_EXTENSIONS_CONTEXT", str(area / "other")),))
    newer = submit(area, "", spec=spec)
    assert newer.id != old.id
    assert all(r.state == "pending" for r in status(root=area / "state"))


@pytest.mark.parametrize("flags,project", [((), ""), (("--project", "sample"), "sample"),
                                         (("--project=sample",), "sample")])
def test_bootstrap_and_installed_manager_share_address_despite_different_project_fields(
    area, flags, project,
):
    manager = area / "manager"
    context = (("WORKTREE_MANAGER_ROOT", str(manager)),)
    old = submit(area, "", spec=RequestSpec(
        "manager", flags, project=str(manager), cwd=str(area), context=context, source="bootstrap",
    ))
    new = submit(area, "", spec=RequestSpec(
        "manager", project=project, cwd=str(area), context=context, source="installed", repair=True,
    ))
    assert status(old.id, root=area / "state").exit_code == 125
    assert new.id in status(old.id, root=area / "state").error


def test_bootstrap_retains_independent_project_requests(area):
    manager = area / "manager"
    spec = RequestSpec(
        "manager", ("--project", "one"), project=str(manager), cwd=str(area),
        context=(("WORKTREE_MANAGER_ROOT", str(manager)),),
    )
    first = submit(area, "", spec=spec)
    other = submit(area, "", spec=replace(spec, argv=("--project", "two")))
    assert first.id != other.id
    assert all(row.state == "pending" for row in status(root=area / "state"))


@pytest.mark.parametrize("backend", ["owning-plugin-provision", "plugin-native-install", "manager-bootstrap"])
def test_update_never_discards_required_component_work(area, backend):
    component = submit(area, "", spec=RequestSpec(backend))
    old = submit(area, "old")
    new = submit(area, "new")
    seen = []
    assert drain(root=area / "state", executor=lambda tx: seen.append(tx.request.id) or 0) == 0
    assert seen == [component.id, new.id]
    assert status(old.id, root=area / "state").exit_code == 125


@pytest.mark.parametrize("arguments", [
    ("reconcile",), ("bootstrap",), ("staged",),
    ("automatic", "--auto", "--no-update", "--with-reconcile"),
])
def test_independent_launch_phase_neither_replaces_nor_is_replaced_by_updates(area, arguments):
    first = submit(area, "first")
    phase = submit(area, "", spec=RequestSpec(
        "engine-launch-update", arguments, cwd=str(area),
    ))
    assert status(first.id, root=area / "state").state == "pending"
    newest = submit(area, "newest")
    seen = []
    assert drain(root=area / "state", executor=lambda tx: seen.append(tx.request.id) or 0) == 0
    assert seen == [phase.id, newest.id]
    assert status(first.id, root=area / "state").exit_code == 125


def test_failed_successor_insert_rolls_back_supersession(area):
    old = submit(area, "old")
    with closing(sqlite3.connect(area / "state" / "queue.sqlite3")) as db, db:
        db.execute(
            """CREATE TRIGGER reject_successor BEFORE INSERT ON requests
            BEGIN SELECT RAISE(ABORT, 'fixture insert failure'); END"""
        )
    with pytest.raises(StateError):
        submit(area, "new")
    assert status(root=area / "state") == (old,)


@pytest.mark.parametrize("field", ["started_at", "finished_at", "boot_id"])
def test_unknown_execution_is_not_superseded(area, field):
    old = submit(area, "old")
    with closing(sqlite3.connect(area / "state" / "queue.sqlite3")) as db, db:
        db.execute(f"UPDATE requests SET {field}=? WHERE id=?", (1, old.id))
    before = status(root=area / "state")
    with pytest.raises(InterruptedError):
        submit(area, "new")
    assert status(root=area / "state") == before
