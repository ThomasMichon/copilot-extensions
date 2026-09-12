"""Import guard for the split-out spawn/liveness/conclusion default callables.

Every function here is already thoroughly exercised through the
``agent_dispatch.supervisor`` facade (``test_supervisor.py``, ``test_fleet.py``,
``test_loop_governance.py``, ``test_cli.py``) -- this file only guards that
``agent_dispatch.spawn_factories`` remains directly importable with its own
stable public names, independent of the facade.
"""

from __future__ import annotations

from agent_dispatch.spawn_factories import (
    SpawnPreparationRetained,
    _parse_fleet_body_handle,
    _parse_local_body_handle,
    _reservation_made_progress,
    _target_directory_missing,
    make_embody_spawn,
    make_headless_spawn,
    make_label_routed_spawn,
    make_redrive_sender,
)


def test_parse_fleet_body_handle_is_directly_importable():
    assert _parse_fleet_body_handle("fleet-body:host-a:sess-1") == (
        "host-a",
        "sess-1",
    )
    assert _parse_fleet_body_handle("local-body:sess-1") is None
    assert _parse_fleet_body_handle(None) is None


def test_parse_local_body_handle_is_directly_importable():
    assert _parse_local_body_handle("local-body:sess-1") == "sess-1"
    assert _parse_local_body_handle("fleet-body:host-a:sess-1") is None


def test_reservation_made_progress_is_directly_importable():
    assert _reservation_made_progress({"reserved_at": 100.0}, {"latest_progress": {"ts": 200.0}})
    assert not _reservation_made_progress(
        {"reserved_at": 300.0}, {"latest_progress": {"ts": 200.0}}
    )


def test_target_directory_missing_is_directly_importable(tmp_path):
    existing_dir = tmp_path / "present"
    existing_dir.mkdir()
    assert _target_directory_missing(str(existing_dir)) is False
    assert _target_directory_missing(str(tmp_path / "absent")) is True
    assert _target_directory_missing("relative/path") is None


def test_make_embody_spawn_is_directly_importable():
    spawn = make_embody_spawn()
    assert callable(spawn)
    assert spawn.requires_reusable_worktree is True


def test_make_headless_spawn_is_directly_importable():
    spawn = make_headless_spawn()
    assert callable(spawn)
    assert spawn.requires_reusable_worktree is True


def test_make_label_routed_spawn_is_directly_importable():
    default = make_embody_spawn()
    routed = make_label_routed_spawn(default, overrides={})
    assert routed is default


def test_make_redrive_sender_is_directly_importable():
    redrive = make_redrive_sender()
    assert callable(redrive)


def test_spawn_preparation_retained_is_directly_importable():
    assert issubclass(SpawnPreparationRetained, RuntimeError)
