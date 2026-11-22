import io
import json
import sqlite3
import sys
import threading
import time
from contextlib import closing
from dataclasses import replace

import pytest

from update_driver import (
    InterruptedError,
    InvalidRequestError,
    ParticipantError,
    RequestNotFoundError,
    RequestSpec,
    SpawnError,
    StateError,
    TrustedRunner,
    borrowed_custody,
    drain,
    enqueue,
    ensure_worker,
    participant,
    runtime_command,
    runtime_script_command,
    run_explicit,
    status,
    wait,
)


def sample(area, *, coalesce_running=False, **kwargs):
    root = area / "state"
    runner = TrustedRunner((sys.executable, "-c", "pass"), str(area))
    return enqueue(
        RequestSpec("synthetic", **kwargs), runner=runner, root=root,
        coalesce_running=coalesce_running,
    )


def test_readonly_absent(area):
    root = area / "missing"
    assert status(root=root) == ()
    assert not root.exists()
    with pytest.raises(RequestNotFoundError):
        status("missing", root=root)
    assert not root.exists()


def test_manager_policy_rechecks_queued_auto_without_blocking_manual(area):
    manager = area / "manager"
    manager.mkdir()
    path = manager / "config.toml"
    path.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    context = (("WORKTREE_MANAGER_ROOT", str(manager)),)
    automatic = sample(area, argv=("update", "--auto"), context=context)
    manual = sample(area, argv=("update",), context=context)
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    observed = []
    assert drain(root=area / "state", executor=lambda tx: observed.append(tx.request.id) or 0) == 0
    assert observed == [manual.id]
    refused = status(automatic.id, root=area / "state")
    assert refused.exit_code == 3 and refused.started_at is None
    assert "maintenance.automatic is false" in refused.error
    assert status(manual.id, root=area / "state").exit_code == 0


@pytest.mark.parametrize("no_update", [False, True])
@pytest.mark.parametrize("root_form", ["relative", "home", "missing-parent"])
def test_queued_relative_manager_policy_uses_original_cwd(area, monkeypatch, no_update, root_form):
    monkeypatch.setenv("HOME", str(area))
    monkeypatch.setenv("USERPROFILE", str(area))
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    caller = area / "caller"
    manager = caller / "manager"
    manager.mkdir(parents=True)
    path = manager / "config.toml"
    path.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    runner = TrustedRunner((sys.executable, "-c", "pass"), str(area))
    flags = ("--no-update",) if no_update else ()
    arguments = ("--auto", *flags, "--with-reconcile")
    roots = {"relative": "manager", "home": "~/caller/manager", "missing-parent": "missing/../manager"}
    context = (("WORKTREE_MANAGER_ROOT", roots[root_form]),)
    automatic = enqueue(RequestSpec(
        "engine-launch-update", arguments, cwd=str(caller), context=context,
    ), runner=runner, root=area / "state")
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    monkeypatch.chdir(area)
    observed = []
    assert drain(root=area / "state", executor=lambda tx: observed.append(tx.request.id) or 0) == 0
    assert observed == []
    refused = status(automatic.id, root=area / "state")
    assert refused.exit_code == 3 and refused.started_at is None
    assert "maintenance.automatic is false" in refused.error
    manual = enqueue(RequestSpec(
        "engine-launch-update", flags, cwd=str(caller), context=context, repair=True,
    ), runner=runner, root=area / "state")
    assert drain(root=area / "state", executor=lambda tx: observed.append(tx.request.id) or 0) == 0
    assert observed == [manual.id]
    assert status(manual.id, root=area / "state").exit_code == 0


@pytest.mark.parametrize("no_update", [False, True])
def test_queued_launch_rechecks_maintenance_even_without_software_updates(area, no_update):
    manager = area / "manager"
    manager.mkdir()
    path = manager / "config.toml"
    path.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    runner = TrustedRunner((sys.executable, "-c", "pass"), str(area))
    flags = ("--no-update",) if no_update else ()
    automatic = enqueue(RequestSpec(
        "engine-launch-update", ("--auto", *flags, "--with-reconcile"),
        context=(("WORKTREE_MANAGER_ROOT", str(manager)),),
    ), runner=runner, root=area / "state")
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    observed = []
    assert drain(root=area / "state", executor=lambda tx: observed.append(tx.request.id) or 0) == 0
    assert observed == []
    refused = status(automatic.id, root=area / "state")
    assert refused.exit_code == 3 and refused.started_at is None
    assert "maintenance.automatic is false" in refused.error


@pytest.mark.parametrize("no_update", [False, True])
def test_launch_software_hold_does_not_inhibit_intentional_reconciliation(area, no_update):
    from update_driver.core import automatic_update_refusal

    switch = area / "hold.json"
    switch.write_text('{"active":true}', encoding="utf-8")
    flags = ("--no-update",) if no_update else ()
    reason = automatic_update_refusal(RequestSpec(
        "engine-launch-update", ("--auto", *flags, "--with-reconcile"),
        context=(("WORKTREE_MANAGER_ROOT", str(area / "manager")),),
        killswitch_path=str(switch),
    ))
    assert (reason is None) is no_update
    if not no_update:
        assert "KillSwitch active" in reason


@pytest.mark.parametrize("content", ["[", "[maintenance]\nautomatic = 'false'\n"])
def test_invalid_manager_policy_blocks_only_explicitly_automatic_work(area, content):
    from update_driver.core import automatic_update_refusal

    manager = area / "manager"
    manager.mkdir()
    (manager / "config.toml").write_text(content, encoding="utf-8")
    context = (("WORKTREE_MANAGER_ROOT", str(manager)),)
    assert automatic_update_refusal(RequestSpec("engine", ("--auto",), context=context))
    assert automatic_update_refusal(RequestSpec("engine", (), context=context)) is None


def test_manager_policy_preserves_unrelated_cell_scope(area, monkeypatch):
    from pathlib import Path
    from update_driver.core import automatic_update_refusal

    monkeypatch.setattr(Path, "home", lambda: area)
    manager = area / ".worktree-manager"
    manager.mkdir()
    (manager / "config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    cell = (("COPILOT_EXTENSIONS_CONTEXT", "separate-cell"),)
    assert automatic_update_refusal(RequestSpec("engine", ("--auto",), context=cell)) is None
    addressed = (*cell, ("WORKTREE_MANAGER_ROOT", str(manager)))
    assert "maintenance.automatic is false" in automatic_update_refusal(
        RequestSpec("engine", ("--auto",), context=addressed),
    )
def test_explicit_foreground_uses_native_custody_and_preserves_exit(area, monkeypatch):
    from update_driver import core

    monkeypatch.setattr(core, "_reconcile_foundation", lambda *args: pytest.fail("no queue repair"))
    seen = []
    result = run_explicit(
        RequestSpec("source-recovery", source="frozen-selection"),
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
        root=area / "state", executor=lambda tx: seen.append(tx.request.id) or 17,
    )
    assert result.state == "completed" and result.exit_code == 17
    assert seen == [result.id]


def test_explicit_foreground_never_replays_foreign_pending_work(area, monkeypatch):
    from update_driver import core

    peer = sample(area, source="peer")
    before = status(root=area / "state")
    monkeypatch.setattr(core, "_reconcile_foundation", lambda *args: pytest.fail("no queue repair"))
    with pytest.raises(StateError, match="nothing replayed"):
        run_explicit(
            RequestSpec("source-recovery"),
            runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
            root=area / "state", executor=lambda tx: pytest.fail("no peer execution"),
        )
    assert status(root=area / "state") == before
    assert status(peer.id, root=area / "state").state == "pending"


def test_explicit_foreground_distinguishes_live_and_abandoned_running_work(area, monkeypatch):
    from update_driver import core

    peer = sample(area, source="peer")
    root = area / "state"
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET state='running',started_at=1 WHERE id=?", (peer.id,))
    before = status(root=root)
    monkeypatch.setattr(core, "_reconcile_foundation", lambda *args: pytest.fail("no queue repair"))

    def attempt():
        return run_explicit(
            RequestSpec("source-recovery"),
            runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
            root=root, executor=lambda tx: pytest.fail("no peer execution"),
        )

    with core.OperationLock(root / "execution.lock"):
        with pytest.raises(StateError, match="busy"):
            attempt()
    with pytest.raises(InterruptedError, match="unsettled"):
        attempt()
    assert status(root=root) == before


def test_explicit_foreground_stops_before_a_later_peer(area):
    later = []

    def execute(tx):
        later.append(sample(area, source="later"))
        return 0

    result = run_explicit(
        RequestSpec("source-recovery"),
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
        root=area / "state", executor=execute,
    )
    assert result.state == "completed"
    assert len(later) == 1
    assert status(later[0].id, root=area / "state").state == "pending"


def test_explicit_foreground_native_child_borrows_the_same_custody(area):
    child = (
        "import sys\nfrom pathlib import Path\n"
        "from update_driver import borrowed_custody\n"
        "custody=borrowed_custody(root=Path(sys.argv[1]))\n"
        "assert custody is not None\n"
        "with custody:\n"
        "    custody.assert_owned()\n"
        "    print('native-source-custody', flush=True)\n"
        "raise SystemExit(17)\n"
    )
    result = run_explicit(
        RequestSpec("source-recovery", source="frozen-selection"),
        runner=TrustedRunner((sys.executable, "-I", "-c", child, str(area / "state")), str(area)),
        root=area / "state",
    )
    assert result.state == "completed" and result.exit_code == 17, result.log_path.read_text(encoding="utf-8")
    assert "native-source-custody" in result.log_path.read_text(encoding="utf-8")


def test_killswitch_retires_pending_normal_work_but_executes_repair(area, monkeypatch, capsys):
    root = area / "state"
    switch = area / "killswitch.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(switch))
    normal = sample(area, argv=("update", "--auto"))
    repair = sample(area, repair=True)
    assert repair.id != normal.id
    switch.write_text('{"active":true}', encoding="utf-8")
    seen = []
    assert drain(root=root, executor=lambda tx: seen.append(tx.request.id) or 0) == 0
    assert seen == [repair.id]
    caller_output = io.StringIO()
    skipped = wait(normal.id, root=root, output=caller_output)
    assert skipped.state == "completed" and skipped.exit_code == 3
    assert skipped.started_at is None and skipped.boot_id is None and not skipped.unresolved
    assert skipped.error == "automatic-update KillSwitch active; nothing ran"
    assert caller_output.getvalue() == skipped.error + "\n"
    assert not skipped.log_path.exists()
    assert status(repair.id, root=root).exit_code == 0
    assert "nothing ran" in capsys.readouterr().err
    switch.write_text('{"active":false}', encoding="utf-8")
    later = sample(area)
    assert drain(root=root, executor=lambda tx: seen.append(tx.request.id) or 0) == 0
    assert seen == [repair.id, later.id]


def test_killswitch_rechecked_between_jobs_without_interrupting_active_work(area, monkeypatch):
    root = area / "state"
    switch = area / "killswitch.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(switch))
    first = sample(area, source="first")
    later = sample(area, source="later", argv=("update", "--auto"))
    seen = []

    def execute(tx):
        seen.append(tx.request.id)
        switch.write_text('{"active":true}', encoding="utf-8")
        return 0

    assert drain(root=root, executor=execute) == 0
    assert seen == [first.id]
    assert status(first.id, root=root).exit_code == 0
    assert status(later.id, root=root).started_at is None
    assert status(later.id, root=root).exit_code == 3


def test_killswitch_real_runner_never_starts_normal_child(area, monkeypatch):
    root = area / "state"
    switch = area / "killswitch.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", "killswitch.json")
    switch.write_text('{"active":true}', encoding="utf-8")
    normal_marker, repair_marker = area / "normal", area / "repair"

    def submit(marker, repair):
        return enqueue(
            RequestSpec("fixture", repair=repair, cwd=str(area),
                        argv=() if repair else ("update", "--auto")), root=root,
            runner=TrustedRunner((
                sys.executable, "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
                str(marker),
            ), str(area)),
        )

    normal, repair = submit(normal_marker, False), submit(repair_marker, True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(area / "unrelated-worker-switch.json"))
    assert drain(root=root) == 0
    assert wait(normal.id, root=root).exit_code == 3
    assert wait(repair.id, root=root).exit_code == 0
    assert not normal_marker.exists()
    assert repair_marker.read_text() == "ran"


def test_each_request_retains_its_own_override_not_the_workers(area, monkeypatch):
    root = area / "state"
    active, inactive = area / "active.json", area / "inactive.json"
    active.write_text('{"active":true}', encoding="utf-8")
    inactive.write_text('{"active":false}', encoding="utf-8")
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(active))
    first = sample(area, argv=("update", "--auto"))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(inactive))
    second = sample(area, argv=("update", "--auto"))
    assert first.id != second.id
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(active))
    seen = []
    assert drain(root=root, executor=lambda tx: seen.append(tx.request.id) or 0) == 0
    assert seen == [second.id]
    assert status(first.id, root=root).exit_code == 3


@pytest.mark.parametrize("legacy", [False, True])
def test_killswitch_default_path_and_namespaced_isolation(area, monkeypatch, legacy):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: area)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    base = area / ".copilot-extensions"
    switch = base / ("bootstrap-killswitch.json" if legacy else "killSwitches/bootstrap-killswitch.json")
    switch.parent.mkdir(parents=True)
    switch.write_text('{"active":true}', encoding="utf-8")
    normal = sample(area, argv=("update", "--auto"))
    namespaced = sample(
        area, argv=("--auto",), context=(("COPILOT_EXTENSIONS_CONTEXT", "explicit-cell"),),
    )
    seen = []
    assert drain(root=area / "state", executor=lambda tx: seen.append(tx.request.id) or 0) == 0
    assert seen == [namespaced.id]
    assert status(normal.id, root=area / "state").exit_code == 3


@pytest.mark.parametrize("held", [True, False])
def test_manual_and_auto_admission_do_not_coalesce_or_force_the_native_runner(area, monkeypatch, held):
    root = area / "state"
    switch = area / "hold.json"
    switch.write_text(json.dumps({"active": held}), encoding="utf-8")
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(switch))
    marker = area / "executed"
    runner = TrustedRunner((
        sys.executable, "-c",
        "import sys; from pathlib import Path; "
        "assert len(sys.argv)==2; "
        "p=Path(sys.argv[1]); p.write_text(p.read_text()+'ran\\n' if p.exists() else 'ran\\n')",
        str(marker),
    ), str(area))
    # Even identical construction/runner arguments must retain distinct admission.
    automatic = enqueue(RequestSpec("fixture", argv=("update", "--auto")), runner=runner, root=root)
    manual = enqueue(RequestSpec("fixture", repair=True), runner=runner, root=root)
    assert automatic.id != manual.id
    assert enqueue(RequestSpec("fixture", repair=True), runner=runner, root=root).id == manual.id
    assert drain(root=root) == 0
    assert wait(manual.id, root=root).exit_code == 0
    auto_result = wait(automatic.id, root=root)
    assert auto_result.exit_code == (3 if held else 0)
    if held:
        assert auto_result.started_at is None
        assert not auto_result.log_path.exists()
        assert auto_result.error == "automatic-update KillSwitch active; nothing ran"
    assert marker.read_text().splitlines() == ["ran"] * (1 if held else 2)


@pytest.mark.parametrize(
    "argv,repair,inhibited",
    [
        (("update",), False, False),
        (("install",), False, False),
        (("setup",), False, False),
        (("doctor", "--json"), False, False),
        (("list",), False, False),
        (("launch",), False, False),
        (("install", "--auto"), False, False),
        (("setup", "--auto"), False, False),
        (("doctor", "--auto"), False, False),
        (("launch", "--auto"), False, False),
        (("stage-update", "--auto"), False, False),
        (("update", "--auto"), False, True),
        (("update", "--auto", "--force"), True, True),
    ],
)
def test_active_switch_only_inhibits_explicit_automatic_requests(area, monkeypatch, argv, repair, inhibited):
    switch = area / "auto-only.json"
    switch.write_text('{"active":true}', encoding="utf-8")
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(switch))
    request = sample(area, argv=argv, repair=repair)
    ran = []
    assert drain(root=area / "state", executor=lambda tx: ran.append(tx.request.id) or 0) == 0
    result = status(request.id, root=area / "state")
    assert result.exit_code == (3 if inhibited else 0)
    assert ran == ([] if inhibited else [request.id])


@pytest.mark.parametrize("backend", ["manager", "engine"])
def test_retained_update_backend_without_verb_still_obeys_automatic_hold(area, backend):
    from update_driver.core import _automatic_update_inhibited

    switch = area / "automatic-hold.json"
    switch.write_text('{"active":true}', encoding="utf-8")
    assert _automatic_update_inhibited(RequestSpec(
        backend, ("--auto",), killswitch_path=str(switch),
    ))
    assert not _automatic_update_inhibited(RequestSpec(
        backend, ("--force",), killswitch_path=str(switch), repair=True,
    ))


@pytest.mark.parametrize("content", ['{"active":"true"}', "{", '{"active":false}'])
def test_invalid_or_inactive_killswitch_preserves_existing_fail_open_policy(
    area, monkeypatch, capsys, content,
):
    switch = area / "killswitch.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(switch))
    switch.write_text(content, encoding="utf-8")
    request = sample(area, argv=("update", "--auto"))
    assert drain(root=area / "state", executor=lambda tx: 0) == 0
    assert status(request.id, root=area / "state").exit_code == 0
    if content != '{"active":false}':
        assert "invalid state" in capsys.readouterr().err


def test_legacy_normal_identity_omits_default_repair_field(area, monkeypatch):
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    request = sample(area)
    with sqlite3.connect(area / "state" / "queue.sqlite3") as db:
        stored = json.loads(db.execute("SELECT spec FROM requests WHERE id=?", (request.id,)).fetchone()[0])
    assert "repair" not in stored
    assert "killswitch_path" not in stored
    assert sample(area, repair=False).id == request.id
    assert status(request.id, root=area / "state").spec.repair is False
    with pytest.raises(InvalidRequestError, match="explicit boolean"):
        sample(area, repair="yes")


def test_unsearchable_switch_directory_does_not_block_queued_repair(area, monkeypatch, capsys):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: area)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    primary = area / ".copilot-extensions/killSwitches/bootstrap-killswitch.json"
    exists = Path.exists

    def denied(path):
        if path == primary:
            raise PermissionError("synthetic unsearchable directory")
        return exists(path)

    monkeypatch.setattr(Path, "exists", denied)
    normal, repair = sample(area, argv=("update", "--auto")), sample(area, repair=True)
    seen = []
    assert drain(root=area / "state", executor=lambda tx: seen.append(tx.request.id) or 0) == 0
    assert seen == [normal.id, repair.id]
    assert "unreadable state; automatic update allowed: PermissionError" in capsys.readouterr().err


def test_worker_acknowledgement_is_published_only_after_close(area, monkeypatch):
    from update_driver import core

    root = area / "state"
    token = "a" * 32
    published = root / f".worker-started-{token}"
    pending = root / f".worker-starting-{token}"
    monkeypatch.setenv("UPDATE_DRIVER_STARTUP", token)
    fsync = core.os.fsync
    observed = []

    def inspect_before_close(fd):
        assert pending.is_file()
        assert not published.exists()
        observed.append(True)
        fsync(fd)

    monkeypatch.setattr(core.os, "fsync", inspect_before_close)
    assert drain(root=root) == 0
    assert observed == [True]
    assert published.read_bytes() == b"queue-ready\n"
    assert not pending.exists()
    published.unlink()


def test_pending_identity_and_distinct_arguments(area):
    root = area / "state"
    first = sample(area, argv=("--ref", "main"), context=(("repo", "public"),))
    assert sample(area, argv=("--ref", "main"), context=(("repo", "public"),)).id == first.id
    assert sample(area, argv=("--ref", "dev"), context=(("repo", "public"),)).id != first.id
    assert sample(area, argv=("--ref", "main"), context=(("repo", "different"),)).id != first.id
    assert len(status(root=root)) == 3


def test_identity_includes_runner_and_source(area):
    root = area / "state"
    first = sample(area, source="public:main")
    changed_runner = replace(first.runner, argv=(sys.executable, "-c", "print(1)"))
    assert enqueue(first.spec, runner=changed_runner, root=root).id != first.id
    assert sample(area, source="public:dev").id != first.id


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_nonboolean_active_coalescing_refuses_before_writes(area, value):
    with pytest.raises(InvalidRequestError, match="explicit boolean"):
        sample(area, coalesce_running=value)
    assert not (area / "state").exists()


@pytest.mark.parametrize("keys", [
    ("FIXTURE_SCOPE", "FIXTURE_SCOPE"),
    ("invalid-key",),
    ("UPDATE_DRIVER_WITNESS",),
    ("PYTHONPATH",),
    ("__PYVENV_LAUNCHER__",),
    tuple(f"FIXTURE_{index}" for index in range(129)),
])
def test_runner_context_projection_rejects_invalid_or_authority_keys(area, keys):
    with pytest.raises(InvalidRequestError, match="context keys"):
        enqueue(
            RequestSpec("fixture"), root=area / "state",
            runner=TrustedRunner((sys.executable, "-c", "pass"), str(area), context_keys=keys),
        )


def test_runner_context_projection_participates_in_pending_identity(area):
    first = sample(area, context=(("FIXTURE_SCOPE", "selected"),))
    declared = replace(first.runner, context_keys=("FIXTURE_SCOPE",))
    second = enqueue(first.spec, runner=declared, root=area / "state")
    assert second.id != first.id
    assert status(second.id, root=area / "state").runner.context_keys == ("FIXTURE_SCOPE",)


def test_running_cannot_swallow_later_unresolved_target(area):
    root = area / "state"
    first = sample(area)
    seen = []

    def execute(tx):
        seen.append(tx.request.id)
        if tx.request.id == first.id:
            assert sample(area).id != first.id
            assert sample(area).id == status(root=root)[1].id
            assert sample(area, coalesce_running=True).id == first.id
        return 0

    assert drain(root=root, executor=execute) == 0
    assert len(seen) == 2


def test_running_shares_pinned_requests_but_queues_distinct_targets(area):
    root = area / "state"
    first = sample(area)
    seen = []

    def execute(tx):
        seen.append(tx.request.id)
        assert tx.request.state == "running" and tx.request.started_at is not None
        if tx.request.id == first.id:
            assert sample(area, coalesce_running=True).id == first.id
            assert sample(area, source="next-target").id != first.id
            assert sample(area, source="next-target").id == status(root=root)[1].id
        return 0

    assert drain(root=root, executor=execute) == 0
    assert len(seen) == 2
    assert all(item.state == "completed" for item in status(root=root))


@pytest.mark.parametrize("code", [0, 37])
def test_completed_request_does_not_debounce_a_new_attempt(area, code):
    root = area / "state"
    first = sample(area)
    assert drain(root=root, executor=lambda tx: code) == 0
    assert status(first.id, root=root).exit_code == code
    second = sample(area, coalesce_running=True)
    assert second.id != first.id
    assert second.state == "pending"


def test_result_and_output(area):
    request = sample(area)
    root = area / "state"

    def execute(tx):
        tx.log_path.write_text("canonical failure\n", encoding="utf-8")
        return 37

    drain(root=root, executor=execute)
    output = io.StringIO()
    result = wait(request.id, root=root, output=output, timeout=1)
    assert result.exit_code == 37
    assert result.state == "completed"
    assert output.getvalue().replace("\r\n", "\n") == "canonical failure\n"


def test_follow_preserves_utf8_across_bounded_read_frames(area):
    request = sample(area)
    root = area / "state"
    text = "a" * 65535 + "\N{EURO SIGN}" + "tail"

    def execute(tx):
        tx.log_path.write_bytes(text.encode("utf-8"))
        return 0

    drain(root=root, executor=execute)
    output = io.StringIO()
    assert wait(request.id, root=root, output=output, timeout=1).exit_code == 0
    assert output.getvalue() == text


def test_terminal_result_precedes_worker_liveness_observation(area):
    request = sample(area)
    root = area / "state"
    drain(root=root, executor=lambda tx: 19)

    class UnneededObserver:
        def poll(self):
            pytest.fail("terminal canonical result must win over worker observation")

    assert wait(request.id, root=root, worker=UnneededObserver()).exit_code == 19


def test_boot_identity_is_captured_once_before_ack_and_reused(area, monkeypatch):
    from update_driver import boot

    first, second = sample(area), sample(area, argv=("second",))
    calls, observed = [], []

    def identity():
        calls.append(True)
        return "windows:11111111-1111-4111-8111-111111111111"

    monkeypatch.setattr(boot, "kernel_boot_identity", identity)
    drain(
        root=area / "state",
        executor=lambda tx: observed.append(tx.request.boot_id) or 0,
    )
    assert len(calls) == 1
    assert observed == [
        status(first.id, root=area / "state").boot_id,
        status(second.id, root=area / "state").boot_id,
    ]


@pytest.mark.parametrize("repair", [False, True])
def test_exception_fences_without_replay(area, repair):
    request = sample(area)
    later = sample(area, argv=("later",))
    root = area / "state"

    def execute(tx):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        drain(root=root, executor=execute)
    assert status(request.id, root=root).state == "interrupted"
    with pytest.raises(InterruptedError):
        sample(area, argv=("refused",), repair=repair)
    with pytest.raises(InterruptedError):
        drain(root=root, executor=lambda tx: pytest.fail("unsafe replay"))
    assert status(later.id, root=root).state == "pending"


def test_abandoned_running_fences_only_under_new_custody(area):
    request = sample(area)
    root = area / "state"
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET state='running' WHERE id=?", (request.id,))
    assert status(request.id, root=root).state == "running"
    with pytest.raises(InterruptedError):
        drain(root=root)
    result = wait(request.id, root=root, timeout=1)
    assert result.exit_code is None and result.state == "interrupted"
    assert "settlement unconfirmed" in result.error


def test_failed_spawn_is_visible_and_never_foreground(area, monkeypatch):
    import agent_procutil
    request = sample(area)
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise OSError("do not persist secret-bearing exception text")

    monkeypatch.setattr(agent_procutil, "spawn_daemon", fail)
    with pytest.raises(SpawnError):
        ensure_worker((sys.executable, "-m", "update_driver", "worker"), root=area / "state")
    assert calls[0]["console_children"] is True
    assert calls[0]["breakaway"] is True and calls[0]["contained_fallback"] is False
    with pytest.raises(SpawnError):
        wait(request.id, root=area / "state", timeout=1)
    assert "secret" not in status(request.id, root=area / "state").error


def test_worker_admission_refuses_pending_execution_evidence(area, monkeypatch):
    import agent_procutil

    root = area / "state"
    request = sample(area)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET started_at=1 WHERE id=?", (request.id,))
    monkeypatch.setattr(
        agent_procutil, "spawn_daemon",
        lambda *_args, **_kwargs: pytest.fail("fenced worker was started"),
    )
    with pytest.raises(InterruptedError):
        ensure_worker((sys.executable, "-m", "update_driver", "worker"), root=root)
    assert status(request.id, root=root).state == "pending"


@pytest.mark.parametrize("retained", [False, True])
def test_worker_preserves_native_handle_and_selected_python_bootstrap(area, monkeypatch, retained):
    from types import SimpleNamespace
    import agent_procutil

    sample(area)
    handle = SimpleNamespace(poll=lambda: None)
    root = area / "state"
    source = area / "retained" / "src"

    def spawn(command, **options):
        assert options.get("python_bootstrap_root") == (source if retained else None)
        token = options["env_overrides"]["UPDATE_DRIVER_STARTUP"]
        (root / f".worker-started-{token}").touch()
        return handle

    monkeypatch.setattr(agent_procutil, "spawn_daemon", spawn)
    assert ensure_worker(
        (sys.executable, "-m", "fixture"), root=root,
        python_bootstrap_root=source if retained else None,
    ) is handle
    assert not list(root.glob(".worker-started-*"))


@pytest.mark.parametrize("kind", ["version", "schema", "corrupt", "row"])
def test_malformed_state_fails_explicitly(area, kind):
    request = sample(area)
    root = area / "state"
    if kind == "corrupt":
        (root / "queue.sqlite3").write_bytes(b"not sqlite")
    else:
        with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
            if kind == "version":
                db.execute("PRAGMA user_version=999")
            elif kind == "schema":
                db.execute("ALTER TABLE requests ADD COLUMN unexpected TEXT")
            else:
                db.execute("UPDATE requests SET spec='{}' WHERE id=?", (request.id,))
    with pytest.raises(StateError):
        status(root=root)
    with pytest.raises(StateError):
        sample(area)


def test_invalid_requests_and_credential_env_refused(area):
    runner = TrustedRunner((sys.executable,), str(area))
    with pytest.raises(InvalidRequestError):
        enqueue(RequestSpec("bad backend"), runner=runner, root=area / "state")
    with pytest.raises(InvalidRequestError):
        enqueue(RequestSpec("valid"), runner=replace(runner, env=(("TOKEN", "never"),)),
                root=area / "state")
    assert not (area / "state").exists()


@pytest.mark.parametrize("spec", [
    RequestSpec(None), RequestSpec("valid", context=None),
    RequestSpec("valid", argv="not-a-vector"),
    RequestSpec("valid", context=(("duplicate", "a"), ("duplicate", "b"))),
])
def test_malformed_api_inputs_have_narrow_errors(area, spec):
    with pytest.raises(InvalidRequestError):
        enqueue(spec, runner=TrustedRunner((sys.executable,), str(area)), root=area / "state")


def test_waiter_reports_fenced_pending_request(area):
    root = area / "state"
    request = sample(area)
    pending = sample(area, argv=("later",))

    def execute(tx):
        raise RuntimeError("uncertain")

    with pytest.raises(RuntimeError):
        drain(root=root, executor=execute)
    with pytest.raises(InterruptedError):
        wait(pending.id, root=root, timeout=1)
    assert status(request.id, root=root).state == "interrupted"


@pytest.mark.parametrize("target", ["uncertain", "later"])
def test_waiter_reports_pending_execution_evidence_without_worker_or_timeout(area, monkeypatch, target):
    from update_driver import core

    root = area / "state"
    uncertain = sample(area)
    later = sample(area, argv=("later",))
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            "UPDATE requests SET started_at=1,error='unconfirmed' WHERE id=?", (uncertain.id,),
        )
    monkeypatch.setattr(core.time, "sleep", lambda _seconds: pytest.fail("fenced waiter slept"))
    with pytest.raises(InterruptedError):
        wait(uncertain.id if target == "uncertain" else later.id, root=root)
    assert status(uncertain.id, root=root).state == "pending"
    assert status(later.id, root=root).state == "pending"


def test_participant_live_forged_stale_and_missing(area):
    sample(area)
    root = area / "state"
    witness = []
    assert borrowed_custody(root=root, environ={}) is None
    with pytest.raises(ParticipantError):
        participant(root=root, witness="")

    def execute(tx):
        env = tx.participant_env()
        witness.append(env["UPDATE_DRIVER_WITNESS"])
        with borrowed_custody(root=root, environ=env) as custody:
            custody.assert_owned()
        with pytest.raises(ParticipantError):
            participant(root=root, request_id="different", witness=witness[0])
        forged = json.loads(witness[0])
        forged["key"] = "00" * 32
        with pytest.raises(ParticipantError):
            participant(root=root, witness=json.dumps(forged))
        return 0

    drain(root=root, executor=execute)
    with pytest.raises(ParticipantError):
        participant(root=root, witness=witness[0])


def test_returning_with_joined_participant_fences(area):
    sample(area)
    root = area / "state"
    joined = []

    def execute(tx):
        joined.append(borrowed_custody(root=root, environ=tx.participant_env()))
        return 0

    try:
        with pytest.raises(ParticipantError):
            drain(root=root, executor=execute)
        assert status(root=root)[0].state == "interrupted"
        with pytest.raises(ParticipantError):
            joined[0].assert_owned()
    finally:
        for custody in joined:
            custody.close()


def test_unresponsive_witness_authentication_is_bounded(area):
    from multiprocessing.connection import Listener

    root = area / "state"
    root.mkdir()
    family = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
    address = (
        r"\\.\pipe\update-driver-idle-test-" + area.name
        if sys.platform == "win32" else str(root / "w-idle.sock")
    )
    listener = Listener(address, family=family, authkey=None)
    accepted = []
    received = threading.Event()
    finish = threading.Event()

    def idle():
        connection = listener.accept()
        accepted.append(connection)
        received.set()
        finish.wait(5)
        connection.close()

    server = threading.Thread(target=idle)
    server.start()
    witness = json.dumps({
        "version": 1, "root": str(root), "request_id": "fixture",
        "family": family, "address": address, "key": "00" * 32,
    })
    started = time.monotonic()
    try:
        with pytest.raises(ParticipantError):
            participant(root=root, witness=witness)
        assert time.monotonic() - started < 4
        assert received.is_set()
    finally:
        finish.set()
        server.join(5)
        listener.close()


def test_debug_contends_with_same_lock(area):
    first = sample(area)
    root = area / "state"
    entered = threading.Event()
    release = threading.Event()
    results = []

    def execute(tx):
        results.append(tx.request.id)
        entered.set()
        assert release.wait(5)
        return 0

    worker = threading.Thread(target=lambda: drain(root=root, executor=execute))
    worker.start()
    assert entered.wait(5)
    second = sample(area, argv=("debug",))
    debug = threading.Thread(
        target=lambda: drain(root=root, executor=lambda tx: results.append(tx.request.id) or 0),
    )
    debug.start()
    time.sleep(.1)
    assert results == [first.id]
    release.set()
    worker.join(5)
    debug.join(5)
    assert not worker.is_alive() and not debug.is_alive()
    assert results == [first.id, second.id]


@pytest.mark.parametrize("nested", [False, True])
def test_runtime_command_uses_original_bootstrap(area, monkeypatch, nested):
    import agent_procutil
    slot = area / "versions" / "1.2.3" if nested else area
    slot.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(slot / ".venv" if nested else slot))
    monkeypatch.setattr(agent_procutil, "direct_python", lambda exe: (exe, {}))
    argv, env = runtime_command("some.adapter", ("update",))
    assert argv == (sys.executable, "-m", "some.adapter", "update")
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text("# fixture", encoding="utf-8")
    argv, env = runtime_command("some.adapter", ("update",))
    assert argv == (
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(bootstrap),
        "-m", "some.adapter", "update",
    )


@pytest.mark.parametrize("nested", [False, True])
def test_runtime_script_command_shares_source_and_retained_selection(area, monkeypatch, nested):
    import agent_procutil

    slot = area / "versions" / "1.2.3" if nested else area
    slot.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(slot / ".venv" if nested else slot))
    monkeypatch.setattr(agent_procutil, "direct_python", lambda exe: (exe, {"selected": "runtime"}))
    script = area / "script \u03b1 space.py"
    script.write_text("# controlled script\n", encoding="utf-8")
    args = ["", "two words", "\u00fc"]
    argv, env = runtime_script_command(script, args)
    assert argv == (sys.executable, str(script), *args)
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text("# controlled fixture", encoding="utf-8")
    argv, env = runtime_script_command(script, args)
    assert argv == (
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(bootstrap),
        str(script), *args,
    )
    assert env == {"selected": "runtime"}


@pytest.mark.parametrize("nested", [False, True])
def test_completed_installed_runtime_never_uses_source_fallback(area, monkeypatch, nested):
    import agent_procutil
    slot = area / "versions" / "1.2.3" if nested else area
    slot.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(slot / ".venv" if nested else slot))
    monkeypatch.setattr(agent_procutil, "direct_python", lambda exe: (exe, {}))
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StateError, match="missing its retention bootstrap"):
        runtime_command("some.adapter")


@pytest.mark.parametrize("script", [False, True])
def test_runtime_invocation_preserves_core_command_seam(area, monkeypatch, script):
    from update_driver import core

    entry = area / "entry.py" if script else "some.adapter"
    arguments = ("", "two words", "\u03b1")
    expected = (("retained-command",), {"selected": "runtime"})
    calls = []

    def command(selected, args):
        calls.append((selected, args))
        return expected

    monkeypatch.setattr(core, "_runtime_command", command)
    invoke = runtime_script_command if script else runtime_command
    assert invoke(entry, arguments) is expected
    assert calls == [((str(entry),) if script else ("-m", entry), arguments)]


def test_linked_installed_bootstrap_refuses(area, monkeypatch):
    import agent_procutil
    monkeypatch.setattr(sys, "prefix", str(area))
    monkeypatch.setattr(agent_procutil, "direct_python", lambda exe: (exe, {}))
    target = area / "target.py"
    target.write_text("# fixture", encoding="utf-8")
    bootstrap = area / ".runtime-bootstrap"
    bootstrap.mkdir()
    try:
        (bootstrap / "runtime_launcher.py").symlink_to(target)
    except OSError as error:
        pytest.skip(f"filesystem cannot create this controlled symlink: {error.winerror}")
    with pytest.raises(StateError, match="linked or unsafe"):
        runtime_command("some.adapter")
