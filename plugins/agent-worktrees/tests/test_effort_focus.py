"""Active-effort binding, lifecycle, and orientation coverage."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import effort_focus as ef
from agent_worktrees import tracking


def _effort(
    repo: Path,
    *,
    slug: str = "durable-loop",
    status: str = "Active",
    participant: str = "Driver",
    slice_name: str = "Phase 2 - Bind active effort",
    archived: bool = False,
    complete: bool = False,
    repo_group: str | None = None,
) -> str:
    if archived:
        group = f"{repo_group}/" if repo_group else ""
        relative = f"efforts/2026/{group}08/28 {slug}/README.md"
    else:
        group = f"{repo_group}/" if repo_group else ""
        relative = f"efforts/active/{group}{slug}/README.md"
    path = repo / Path(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    check = "x" if complete else " "
    path.write_text(
        f"""# Durable Loop

- **Slug:** `{slug}`
- **Status:** {status}

## Participants

| Participant | Role |
|-------------|------|
| {participant} | Owns the slice |

## Coordination

{participant} drives {slice_name}.

## Plan

### {slice_name}

- [{check}] Implement the binding.

## Validation Plan

- [{check}] Verify the binding.
""",
        encoding="utf-8",
    )
    return relative


def _record(repo: Path, worktree_id: str = "wt-effort") -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=worktree_id,
        branch=f"worktree/{worktree_id}",
        worktree_path=str(repo),
        repo="example",
        machine="machine",
        platform="wsl",
        started_at="2026-08-28T00:00:00",
        last_resumed_at="2026-08-28T00:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
    )


def _args(action: str, **overrides) -> argparse.Namespace:
    values = dict(
        action=action,
        path=None,
        participant=None,
        effort_slice=None,
        replace=False,
        completed=False,
        transfer=None,
        worktree_id=None,
        json=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def cli_env(tmp_path, tmp_tracking_dir, monkeypatch_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    record = _record(repo)
    tracking.save_record(record, tmp_tracking_dir / f"{record.worktree_id}.yaml")
    monkeypatch.setattr(m.cfg, "load_config", lambda: object())
    monkeypatch.setattr(m, "_infer_worktree_id", lambda _wid, _config=None: record.worktree_id)
    monkeypatch.setattr(m, "_resolve_worktree_id", lambda wid: wid)
    monkeypatch.setattr(ef, "repository_root", lambda _path: repo)
    return repo, record, tmp_tracking_dir


def test_binding_round_trips_and_legacy_record_stays_lean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    ref = ef.make_active_effort(
        "efforts/active/durable-loop/README.md",
        "Driver",
        "Phase 2 - Bind active effort",
    )
    bound = _record(repo)
    bound.active_effort = ref
    bound_path = tmp_path / "bound.yaml"
    tracking.save_record(bound, bound_path)

    loaded = tracking.load_record(bound_path)
    assert loaded.active_effort == ref
    assert "active_effort:" in bound_path.read_text(encoding="utf-8")

    legacy_path = tmp_path / "legacy.yaml"
    tracking.save_record(_record(repo, "wt-legacy"), legacy_path)
    assert "active_effort:" not in legacy_path.read_text(encoding="utf-8")
    assert tracking.load_record(legacy_path).active_effort is None


@pytest.mark.parametrize(
    "bad_path",
    (
        "/absolute/README.md",
        "../escape/README.md",
        "C:/escape/README.md",
        "efforts\\active\\x\\README.md",
        "efforts/active/x/not-readme.md",
    ),
)
def test_binding_rejects_non_relative_or_non_readme_paths(bad_path):
    with pytest.raises(ef.EffortFocusError):
        ef.make_active_effort(bad_path, "Driver", "Phase 2")


def test_binding_rejects_symlink_escape(tmp_path):
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    (repo / "efforts" / "active").mkdir(parents=True)
    outside.mkdir()
    (outside / "README.md").write_text("outside", encoding="utf-8")
    (repo / "efforts" / "active" / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ef.EffortFocusError, match="link or reparse"):
        ef.resolve_effort_path(repo, "efforts/active/linked/README.md")


def test_validate_binding_requires_declared_participant_and_slice(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    relative = _effort(repo)
    valid = ef.make_active_effort(relative, "Driver", "Phase 2 - Bind active effort")
    assert ef.validate_binding(repo, valid).active

    wrong_participant = ef.make_active_effort(
        relative, "Reviewer", "Phase 2 - Bind active effort"
    )
    with pytest.raises(ef.EffortFocusError, match="participant"):
        ef.validate_binding(repo, wrong_participant)

    wrong_slice = ef.make_active_effort(relative, "Driver", "Phase 9")
    with pytest.raises(ef.EffortFocusError, match="slice"):
        ef.validate_binding(repo, wrong_slice)

    substring_participant = ef.make_active_effort(
        relative, "Drive", "Phase 2 - Bind active effort"
    )
    with pytest.raises(ef.EffortFocusError, match="participant"):
        ef.validate_binding(repo, substring_participant)

    substring_slice = ef.make_active_effort(relative, "Driver", "Phase 2")
    with pytest.raises(ef.EffortFocusError, match="slice"):
        ef.validate_binding(repo, substring_slice)


def test_binding_requires_canonical_active_effort_prefix(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    relative = _effort(repo)
    source = repo / Path(*relative.split("/"))
    wrong = repo / "other/efforts/archive/active/durable-loop/README.md"
    wrong.parent.mkdir(parents=True)
    wrong.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    ref = ef.make_active_effort(
        wrong.relative_to(repo).as_posix(),
        "Driver",
        "Phase 2 - Bind active effort",
    )

    with pytest.raises(ef.EffortFocusError, match="efforts/active"):
        ef.validate_binding(repo, ref)

    too_deep = repo / "efforts/active/one/two/durable-loop/README.md"
    too_deep.parent.mkdir(parents=True)
    too_deep.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    deep_ref = ef.make_active_effort(
        too_deep.relative_to(repo).as_posix(),
        "Driver",
        "Phase 2 - Bind active effort",
    )
    with pytest.raises(ef.EffortFocusError, match="efforts/active"):
        ef.validate_binding(repo, deep_ref)


def test_duplicate_slug_or_status_headers_are_stale(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    relative = _effort(repo)
    path = repo / Path(*relative.split("/"))
    path.write_text(
        path.read_text(encoding="utf-8") + "\n- **Status:** Done\n",
        encoding="utf-8",
    )
    ref = ef.make_active_effort(relative, "Driver", "Phase 2 - Bind active effort")

    inspection = ef.inspect_effort(repo, ref)
    assert inspection.state == "stale"
    assert "status more than once" in inspection.reason


def test_duplicate_required_sections_and_unknown_status_are_stale(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    relative = _effort(repo, status="Dnoe")
    path = repo / Path(*relative.split("/"))
    ref = ef.make_active_effort(relative, "Driver", "Phase 2 - Bind active effort")

    inspection = ef.inspect_effort(repo, ref)
    assert inspection.state == "stale"
    assert "not recognized" in inspection.reason

    text = path.read_text(encoding="utf-8").replace(
        "- **Status:** Dnoe", "- **Status:** Done"
    )
    path.write_text(text + "\n## Plan\n\n- [ ] hidden work\n", encoding="utf-8")
    inspection = ef.inspect_effort(repo, ref)
    assert inspection.state == "stale"
    assert "Plan more than once" in inspection.reason


def test_renamed_participant_section_can_bind(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    relative = _effort(repo)
    path = repo / Path(*relative.split("/"))
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "## Participants", "## Machines"
        ),
        encoding="utf-8",
    )
    ref = ef.make_active_effort(relative, "Driver", "Phase 2 - Bind active effort")

    assert ef.validate_binding(repo, ref).active


def test_duplicate_binding_is_scoped_by_repo_and_slice(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = "efforts/active/durable-loop/README.md"
    first = ef.make_active_effort(path, "Driver", "Phase 2")
    second = ef.make_active_effort(path, "Driver", "Phase 3")
    same_slice_other_participant = ef.make_active_effort(path, "Reviewer", "phase 2")
    record = _record(repo, "wt-a")
    record.active_effort = first

    assert ef.duplicate_binding([record], "wt-b", "example", first) == "wt-a"
    assert (
        ef.duplicate_binding(
            [record], "wt-b", "example", same_slice_other_participant
        )
        == "wt-a"
    )
    assert ef.duplicate_binding([record], "wt-b", "example", second) is None
    assert ef.duplicate_binding([record], "wt-b", "other", first) is None


def test_closed_or_stale_effort_is_not_oriented(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    relative = _effort(repo, status="Done")
    ref = ef.make_active_effort(relative, "Driver", "Phase 2 - Bind active effort")

    assert ef.inspect_effort(repo, ref).state == "closed"
    assert ef.orientation(repo, ref) == ""
    (repo / Path(*relative.split("/"))).unlink()
    assert ef.inspect_effort(repo, ref).state == "stale"
    assert ef.orientation(repo, ref) == ""


def test_bind_show_and_transfer_release(cli_env, capsys, monkeypatch):
    repo, record, tracking_dir = cli_env
    relative = _effort(repo)

    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    loaded = tracking.load_record(tracking_dir / f"{record.worktree_id}.yaml")
    assert loaded.active_effort is not None
    assert loaded.follow_up is True
    assert loaded.summary == "Effort durable-loop: Phase 2 - Bind active effort"

    capsys.readouterr()
    shown = {}
    monkeypatch.setattr(m, "_json_output", lambda payload: shown.update(payload))
    assert m.cmd_effort_focus(_args("show", json=True)) == 0
    assert shown["active_effort"]["active"] is True
    assert shown["follow_up"] is True

    assert m.cmd_effort_focus(_args(
        "release", transfer="issue #42"
    )) == 0
    released = tracking.load_record(tracking_dir / f"{record.worktree_id}.yaml")
    assert released.active_effort is None
    assert released.follow_up is False
    assert released.summary == "Transferred effort durable-loop to issue #42"


def test_release_requires_done_or_named_transfer(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    assert m.cmd_effort_focus(_args("release", completed=True)) == 1

    path = repo / Path(*relative.split("/"))
    path.write_text(path.read_text(encoding="utf-8").replace(
        "- **Status:** Active", "- **Status:** Done"
    ), encoding="utf-8")
    assert m.cmd_effort_focus(_args("release", completed=True)) == 1
    path.write_text(
        path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
        encoding="utf-8",
    )
    assert m.cmd_effort_focus(_args("release", completed=True)) == 0


def test_template_status_comment_and_all_task_markers(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    path = repo / Path(*relative.split("/"))
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "- **Status:** Active",
        "- **Status:** Done <!-- Draft | Active | Blocked | Done -->",
    ).replace("- [ ] Implement", "* [ ] Implement").replace(
        "- [ ] Verify", "+ [x] Verify"
    )
    path.write_text(text, encoding="utf-8")

    assert m.cmd_effort_focus(_args("release", completed=True)) == 1
    path.write_text(
        path.read_text(encoding="utf-8").replace("* [ ]", "* [x]"),
        encoding="utf-8",
    )
    assert m.cmd_effort_focus(_args("release", completed=True)) == 0


def test_deferred_and_ordered_unchecked_tasks_block_release(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    path = repo / Path(*relative.split("/"))
    text = path.read_text(encoding="utf-8")
    text = text.replace("- **Status:** Active", "- **Status:** Done")
    text = text.replace("- [ ] Implement", "1. [ ] Implement")
    text = text.replace("- [ ] Verify", "- [~] Verify")
    path.write_text(text, encoding="utf-8")

    assert m.cmd_effort_focus(_args("release", completed=True)) == 1


def test_checked_deferred_task_requires_named_transfer_target(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    path = repo / Path(*relative.split("/"))
    text = path.read_text(encoding="utf-8")
    text = text.replace("- **Status:** Active", "- **Status:** Done")
    text = text.replace(
        "- [ ] Implement the binding.",
        "- [x] Deferred: Implement the binding.",
    ).replace("- [ ] Verify", "- [x] Verify")
    path.write_text(text, encoding="utf-8")

    assert m.cmd_effort_focus(_args("release", completed=True)) == 1

    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Deferred: Implement the binding.",
            "Deferred to `issue #42`: Implement the binding.",
        ),
        encoding="utf-8",
    )
    assert m.cmd_effort_focus(_args("release", completed=True)) == 0


def test_checked_blocked_task_requires_named_transfer_target(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    path = repo / Path(*relative.split("/"))
    text = path.read_text(encoding="utf-8")
    text = text.replace("- **Status:** Active", "- **Status:** Done")
    text = text.replace(
        "- [ ] Implement the binding.",
        "- [x] Blocked: Implement the binding.",
    ).replace("- [ ] Verify", "- [x] Verify")
    path.write_text(text, encoding="utf-8")

    assert m.cmd_effort_focus(_args("release", completed=True)) == 1

    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Blocked: Implement the binding.",
            "Blocked; transferred to `efforts/active/follow-up/README.md`: "
            "Implement the binding.",
        ),
        encoding="utf-8",
    )
    assert m.cmd_effort_focus(_args("release", completed=True)) == 0


def test_completed_tasks_starting_with_status_words_are_not_transfers():
    text = """\
- **Slug:** status-words
- **Status:** Done

## Plan

- [x] Blocked requests return 403.
- [x] Deferred execution preserves ordering.

## Validation Plan

- [x] Verify both behaviors.
"""
    assert ef._completion_ready(text) is True


def test_archived_effort_can_release_after_original_path_moves(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    active = repo / Path(*relative.split("/"))
    archived_relative = _effort(repo, status="Done", archived=True, complete=True)
    active.unlink()
    assert (repo / Path(*archived_relative.split("/"))).is_file()

    assert m.cmd_effort_focus(_args("release", completed=True)) == 0


def test_by_repo_archived_effort_can_release(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo, repo_group="example")
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    active = repo / Path(*relative.split("/"))
    archived_relative = _effort(
        repo, status="Done", archived=True, complete=True, repo_group="example"
    )
    active.unlink()
    assert (repo / Path(*archived_relative.split("/"))).is_file()

    assert m.cmd_effort_focus(_args("release", completed=True)) == 0


def test_noncanonical_archive_date_cannot_release(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    active = repo / Path(*relative.split("/"))
    archived = repo / "efforts/2026/not-a-month/not-a-day durable-loop/README.md"
    archived.parent.mkdir(parents=True)
    archived.write_text(
        active.read_text(encoding="utf-8")
        .replace("- **Status:** Active", "- **Status:** Done")
        .replace("- [ ]", "- [x]"),
        encoding="utf-8",
    )
    active.unlink()

    assert m.cmd_effort_focus(_args("release", completed=True)) == 1


def test_stale_nonmissing_effort_does_not_match_old_archive(cli_env):
    repo, _record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    _effort(repo, status="Done", archived=True, complete=True)
    active = repo / Path(*relative.split("/"))
    active.write_text("not an effort", encoding="utf-8")

    assert m.cmd_effort_focus(_args("release", completed=True)) == 1


def test_transfer_and_show_work_when_checkout_is_unavailable(
    cli_env, monkeypatch
):
    repo, record, tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0
    monkeypatch.setattr(
        ef,
        "repository_root",
        lambda _path: (_ for _ in ()).throw(
            ef.EffortFocusError("tracked worktree is unavailable")
        ),
    )
    shown = {}
    monkeypatch.setattr(m, "_json_output", lambda payload: shown.update(payload))

    assert m.cmd_effort_focus(_args("show", json=True)) == 0
    assert shown["active_effort"]["state"] == "stale"
    assert m.cmd_effort_focus(_args("release", transfer="issue #42")) == 0
    released = tracking.load_record(tracking_dir / f"{record.worktree_id}.yaml")
    assert released.active_effort is None


def test_manual_resolved_is_blocked_by_open_effort(cli_env, capsys):
    repo, record, _tracking_dir = cli_env
    relative = _effort(repo)
    assert m.cmd_effort_focus(_args(
        "bind",
        path=relative,
        participant="Driver",
        effort_slice="Phase 2 - Bind active effort",
    )) == 0

    rc = m._cmd_status_write(
        argparse.Namespace(worktree_id=record.worktree_id),
        summary=None,
        follow_up=False,
    )
    assert rc == 1
    assert "effort remains bound" in capsys.readouterr().out

    path = repo / Path(*relative.split("/"))
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("- **Status:** Active", "- **Status:** Done")
        .replace("- [ ]", "- [x]"),
        encoding="utf-8",
    )
    rc = m._cmd_status_write(
        argparse.Namespace(worktree_id=record.worktree_id),
        summary=None,
        follow_up=False,
    )
    assert rc == 1


def test_worktree_json_derives_effort_summary_and_follow_up(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    relative = _effort(repo)
    record = _record(repo)
    record.active_effort = ef.make_active_effort(
        relative, "Driver", "Phase 2 - Bind active effort"
    )
    record.follow_up = False
    record.summary = "manual"

    data = m._worktree_to_dict(record)

    assert data["active_effort"]["active"] is True
    assert data["follow_up"] is True
    assert data["summary"] == "Effort durable-loop: Phase 2 - Bind active effort"


def test_history_digest_includes_bounded_effort_pointer(
    cli_env, monkeypatch, capsys
):
    repo, record, tracking_dir = cli_env
    relative = _effort(repo)
    record.active_effort = ef.make_active_effort(
        relative, "Driver", "Phase 2 - Bind active effort"
    )
    tracking.save_record(record, tracking_dir / f"{record.worktree_id}.yaml")
    monkeypatch.setattr(m, "_resolve_worktree_for_read", lambda *_args: record.worktree_id)

    assert m.cmd_history_digest(
        argparse.Namespace(worktree_id=None, worktree_dir=None, session_id=None, limit=8)
    ) == 0
    output = capsys.readouterr().out
    assert "Active effort:" in output
    assert relative in output
    assert len(output) <= 801


def test_stale_record_save_preserves_newer_effort_revision(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = tmp_path / "record.yaml"
    tracking.save_record(_record(repo), path)
    stale = tracking.load_record(path)

    current = tracking.load_record(path)
    current.active_effort = ef.make_active_effort(
        "efforts/active/durable-loop/README.md", "Driver", "Phase 2"
    )
    current.effort_revision += 1
    current.follow_up = True
    current.summary = "Effort durable-loop: Phase 2"
    tracking.save_record(current, path)

    stale.status = "finalized"
    tracking.save_record(stale, path)
    reloaded = tracking.load_record(path)
    assert reloaded.active_effort == current.active_effort
    assert reloaded.effort_revision == 1
    assert reloaded.follow_up is True
    assert reloaded.summary == "Effort durable-loop: Phase 2"
