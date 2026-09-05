from __future__ import annotations

from pathlib import Path

import yaml

from agent_worktrees import tracking


def record(tmp_path: Path) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="host-win-20260904-abcd",
        branch="worktree/host-win-20260904-abcd",
        worktree_path=str(tmp_path / "worktree"),
        repo="example",
        machine="host",
        platform="windows",
        started_at="2026-09-04T00:00:00+00:00",
        last_resumed_at="2026-09-04T00:00:00+00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
    )


def ahp_binding() -> tracking.SessionBackendBinding:
    return tracking.SessionBackendBinding(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="octocat",
        created_at="2026-09-04T00:00:01+00:00",
        last_seen_at="2026-09-04T00:00:02+00:00",
    )


def leg() -> tracking.ExecutionLegBinding:
    return tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=2,
        blob={"session_id": "22222222-2222-2222-2222-222222222222"},
    )


def test_no_binding_derives_nothing(tmp_path: Path):
    original = record(tmp_path)
    assert tracking.derive_execution_leg(original) is None


def test_unset_execution_leg_never_serializes(tmp_path: Path):
    """Additive-only: with no execution_leg set, on-disk output is unchanged."""
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    original.session_backend = ahp_binding()
    tracking.save_record(original, path)

    data = yaml.safe_load(path.read_text())
    assert "execution_leg" not in data
    assert "session_backend" in data


def test_legacy_session_backend_derives_generic_execution_leg(tmp_path: Path):
    original = record(tmp_path)
    original.session_backend = ahp_binding()

    derived = tracking.derive_execution_leg(original)
    assert derived is not None
    assert derived.provider == "ahp"
    assert derived.state == "active"
    assert derived.binding_revision == 1
    assert derived.blob["session_id"] == ahp_binding().session_id
    assert derived.blob["endpoint_url"] == ahp_binding().endpoint_url

    # The legacy field itself is untouched by deriving a view over it.
    assert original.session_backend == ahp_binding()
    assert original.execution_leg is None


def test_opaque_legacy_session_backend_derives_nothing(tmp_path: Path):
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    tracking.save_record(original, path)
    data = yaml.safe_load(path.read_text())
    data["session_backend"] = {
        "version": 2,
        "kind": "future",
        "opaque": {"value": True},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    loaded = tracking.load_record(path)
    assert loaded.session_backend_opaque is True
    assert tracking.derive_execution_leg(loaded) is None


def test_execution_leg_round_trips(tmp_path: Path):
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    original.execution_leg = leg()
    tracking.save_record(original, path)

    data = yaml.safe_load(path.read_text())
    assert data["execution_leg"]["provider"] == "ahp"
    assert data["execution_leg"]["blob"]["session_id"] == (
        "22222222-2222-2222-2222-222222222222"
    )

    loaded = tracking.load_record(path)
    assert loaded.execution_leg == original.execution_leg
    assert tracking.derive_execution_leg(loaded) == original.execution_leg


def test_newer_execution_leg_schema_is_preserved_opaquely(tmp_path: Path):
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    tracking.save_record(original, path)
    data = yaml.safe_load(path.read_text())
    data["execution_leg"] = {
        "version": 2,
        "provider": "future",
        "opaque": {"value": True},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    loaded = tracking.load_record(path)
    assert loaded.execution_leg is None
    assert loaded.execution_leg_opaque is True
    assert tracking.derive_execution_leg(loaded) is None
    tracking.save_record(loaded, path)
    assert yaml.safe_load(path.read_text())["execution_leg"] == data[
        "execution_leg"
    ]


def test_missing_provider_is_preserved_opaquely(tmp_path: Path):
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    tracking.save_record(original, path)
    data = yaml.safe_load(path.read_text())
    data["execution_leg"] = {
        "version": 1,
        "state": "active",
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    loaded = tracking.load_record(path)
    assert loaded.execution_leg is None
    assert loaded.execution_leg_opaque is True


def test_non_mapping_blob_is_preserved_opaquely(tmp_path: Path):
    """A non-mapping blob is never silently coerced/discarded (data loss);
    the whole entry is preserved opaquely instead, like an unrecognized
    schema."""
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    tracking.save_record(original, path)
    data = yaml.safe_load(path.read_text())
    data["execution_leg"] = {
        "version": 1,
        "provider": "ahp",
        "state": "active",
        "binding_revision": 1,
        "blob": "not-a-mapping",
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    loaded = tracking.load_record(path)
    assert loaded.execution_leg is None
    assert loaded.execution_leg_opaque is True
    tracking.save_record(loaded, path)
    assert yaml.safe_load(path.read_text())["execution_leg"] == data[
        "execution_leg"
    ]
