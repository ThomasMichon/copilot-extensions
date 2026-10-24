"""Native prompt and hosted-provider admission share one record transaction."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Iterator, Literal

import pytest
import yaml

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg
from agent_worktrees import launch_seed_state as seeds
from agent_worktrees import output, resolve_cli, tracking, worktree_identity

NativeRecord = tuple[Path, list[dict], Path]
pytestmark = [pytest.mark.guard, pytest.mark.contract("agent_worktrees.native_seed_fence")]


def test_resolve_advertises_native_seed_fence_before_mutation() -> None:
    parser = argparse.ArgumentParser()
    resolve_cli.add_parsers(parser.add_subparsers())
    args = parser.parse_args([
        "resolve", "--new", "--json", "--stage-launch-seed",
        "--require-native-seed", "--seed", "accepted draft",
    ])
    assert args.require_native_seed and args.stage_launch_seed
    assert args.seed == "accepted draft"


@pytest.fixture
def native_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NativeRecord:
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="windows", repo_name="demo",
        repos={"demo": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: value)
    (tmp_path / "checkout").mkdir()
    path = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "checkout"), "demo",
        "test", "windows", tmp_path,
    ).yaml_path
    outputs: list[dict] = []
    monkeypatch.setattr(output, "_json_output", outputs.append)
    monkeypatch.setattr(
        output, "_json_error",
        lambda message, exit_code=1: outputs.append({"error": message}) or exit_code,
    )
    blob = tmp_path / "hosted.json"
    blob.write_text("{}", encoding="utf-8")
    return path, outputs, blob


def _activation(action: str, blob: Path) -> argparse.Namespace:
    return argparse.Namespace(
        action=action, worktree_id="wt-a", provider="ahp", state="active",
        binding_revision=1, blob_file=str(blob), if_match_revision=0,
        operation="ensure", reservation_token=None, reservation_owner="test-owner",
        reservation_owner_pid=None, reservation_owner_start_time=None, lease_seconds=300,
    )


@pytest.mark.parametrize("action", ["reserve", "set"])
@pytest.mark.parametrize("kind", ["new", "resume"])
def test_provider_wins_between_preflight_and_staging(
    native_record: NativeRecord, action: str, kind: Literal["new", "resume"],
) -> None:
    path, outputs, blob = native_record
    assert tracking.derive_execution_leg(tracking.load_record(path)) is None
    assert cli.cmd_execution_leg(_activation(action, blob)) == 0
    provider_record = path.read_bytes()
    with pytest.raises(ValueError, match="native execution leg"):
        seeds.stage(path, kind=kind, text="accepted draft")
    assert path.read_bytes() == provider_record
    assert not seeds.state_path(path).exists()
    assert outputs[-1]["execution_leg"]["provider"] == "ahp"


def test_disposed_provider_does_not_block_native_prompt_staging(
    native_record: NativeRecord,
) -> None:
    path, _outputs, _blob = native_record
    record = tracking.load_record(path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp", state="disposed", binding_revision=1, blob={},
    )
    tracking.save_record(record, path)
    persisted = tracking.parse_record_data(yaml.safe_load(path.read_text(encoding="utf-8")), path=path)
    assert persisted.execution_leg is not None and persisted.execution_leg.state == "disposed"
    seed = seeds.stage(path, kind="resume", text="accepted draft")
    assert seed is not None and seeds.peek(path) == seed


def test_peek_reenters_strict_record_lock_without_relocking_or_releasing_sidecar(
    native_record: NativeRecord, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _outputs, _blob = native_record
    seed = seeds.stage(path, kind="resume", text="accepted draft")
    assert seed is not None and seeds.state_path(path).exists()
    with tracking._RecordLock(path, require_sidecar=True) as outer:
        assert outer._held in {"windows", "posix"}
        counts = tracking._thread_sidecar_counts()
        assert counts[outer._sidecar_key] == 1
        with monkeypatch.context() as patch:
            patch.setattr(
                tracking._RecordLock, "_sidecar_try",
                lambda *_: pytest.fail("nested peek must reuse the held OS sidecar"),
            )
            assert seeds.peek(path) == seed
            assert counts[outer._sidecar_key] == 1
            with tracking._RecordLock(path, require_sidecar=True) as inner:
                assert inner.acquired and inner._nested_sidecar and inner._fd is None
                assert counts[outer._sidecar_key] == 2
            assert counts[outer._sidecar_key] == 1
        def contender() -> bool:
            with tracking._RecordLock(path, blocking=False, require_sidecar=True) as lock:
                return lock.acquired
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(contender).result(timeout=5) is False
    with tracking._RecordLock(path, require_sidecar=True) as later:
        assert later.acquired


@pytest.mark.parametrize("action", ["reserve", "set"])
@pytest.mark.parametrize("kind", ["new", "resume", "legacy"])
def test_seed_winner_fences_provider_without_consuming_pending_intent(
    native_record: NativeRecord, action: str, kind: Literal["new", "resume", "legacy"],
) -> None:
    path, outputs, blob = native_record
    if kind == "legacy":
        record = tracking.load_record(path)
        record.pending_seed = "accepted draft"
        record.pending_seed_revision += 1
        tracking.save_record(record, path)
        seed = None
    else:
        seed = seeds.stage(path, kind=kind, text="accepted draft")
    record_before = path.read_bytes()
    assert cli.cmd_execution_leg(_activation(action, blob)) == 3
    assert "pending native launch prompt" in outputs[-1]["error"]
    assert path.read_bytes() == record_before
    assert seeds.peek(path) == seed
    assert not cli._execution_leg_reservation_path(path).exists()
    if seed is None:
        seed = seeds.stage(path)
    assert seed is not None
    receipt = seeds.take(path, seed_id=seed.seed_id)
    assert seeds.finish(path, receipt)["finished"]
    assert cli.cmd_execution_leg(_activation(action, blob)) == 0


@pytest.mark.parametrize("opaque_field", ["execution_leg_opaque", "session_backend_opaque"])
def test_opaque_provider_refuses_staging_without_touching_pending_intent(
    native_record: NativeRecord, monkeypatch: pytest.MonkeyPatch, opaque_field: str,
) -> None:
    path, _outputs, _blob = native_record
    seed = seeds.stage(path, kind="resume", text="original intent")
    original_record = seeds._record
    def opaque_record(args: dict) -> tracking.WorktreeRecord:
        record = original_record(args)
        setattr(record, opaque_field, True)
        return record
    monkeypatch.setattr(seeds, "_record", opaque_record)
    with pytest.raises(ValueError, match="unsupported"):
        seeds.stage(path, kind="resume", text="replacement")
    assert seeds.peek(path) == seed
    assert seed is not None
    with pytest.raises(ValueError, match="unsupported"):
        seeds.take(path, seed_id=seed.seed_id)
    assert seeds.peek(path) == seed


def test_provider_waits_for_the_actual_seed_publication_transaction(
    native_record: NativeRecord, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, outputs, blob = native_record
    publishing, activate_attempted, continue_publication = Event(), Event(), Event()
    write = seeds._write
    mutation = seeds.execution_leg_mutation

    def pause_publication(record_path: Path, seed: seeds.LaunchSeed) -> None:
        publishing.set()
        assert continue_publication.wait(5)
        write(record_path, seed)

    @contextmanager
    def observe_activation(
        record_path: Path, action: str, leg_state: str | None,
    ) -> Iterator[tracking.WorktreeRecord]:
        activate_attempted.set()
        with mutation(record_path, action, leg_state) as record:
            yield record

    monkeypatch.setattr(seeds, "_write", pause_publication)
    monkeypatch.setattr(seeds, "execution_leg_mutation", observe_activation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        native = pool.submit(seeds.stage, path, kind="resume", text="accepted draft")
        try:
            assert publishing.wait(5)
            hosted = pool.submit(cli.cmd_execution_leg, _activation("reserve", blob))
            assert activate_attempted.wait(5)
            assert not hosted.done()
        finally:
            continue_publication.set()
        seed = native.result(timeout=5)
        assert hosted.result(timeout=5) == 3
    assert seeds.peek(path) == seed
    assert tracking.derive_execution_leg(tracking.load_record(path)) is None
    assert "pending native launch prompt" in outputs[-1]["error"]
