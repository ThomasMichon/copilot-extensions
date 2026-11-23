"""Unknown checkout data is not disposal authority."""

from pathlib import Path
import subprocess

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import checkout_data, finalized_checkout, git_ops, sessions, status_bar_cli, tracking


@pytest.mark.parametrize("kind", ["absent", "agenda"])
def test_absent_checkout_or_known_history_needs_no_git_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    root = tmp_path / "checkout"
    if kind == "agenda":
        (root / ".effort-agenda").mkdir(parents=True)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("absence and known history must not start another inspection")

    monkeypatch.setattr(checkout_data, "run_caller_owned", unexpected)
    reason = checkout_data.preservation_reason(root)
    assert (reason is None) is (kind == "absent")
    if reason is not None:
        assert ".effort-agenda" in reason


@pytest.mark.parametrize("code,text,held", [
    (0, "", False), (0, "100644\tfile.txt\0", False), (0, "160000\tnested\0", False),
    (0, "040000\tsparse\0", True), (128, "", True), (0, "100644\tpartial", True),
])
def test_index_observation_is_bounded_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, text: str, held: bool,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    (tmp_path / ".git").write_text("fixture", encoding="utf-8")

    def observed(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, code, text, "")

    monkeypatch.setattr(checkout_data, "run_caller_owned", observed)
    assert (checkout_data.preservation_reason(tmp_path) is not None) is held
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert "ls-files" in argv and "--ignored" not in argv
    assert kwargs["max_stdout_bytes"] == 32 * 1024 * 1024
    timeout = kwargs["timeout"]
    assert isinstance(timeout, (int, float)) and 0 < timeout <= 5


def test_ignored_only_checkout_is_disposable_but_ignored_agenda_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".gitignore").write_text("__pycache__/\nbuild/\n*.egg-info/\n.effort-agenda/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
    for ignored in ("__pycache__/mod.cpython.pyc", "build/lib/out.bin", "pkg.egg-info/PKG-INFO"):
        (root / ignored).parent.mkdir(parents=True, exist_ok=True)
        (root / ignored).write_bytes(b"generated")
    monkeypatch.setattr(checkout_data, "repository_identity_env", lambda: {})
    assert checkout_data.preservation_reason(root) is None

    (root / ".effort-agenda" / "lane").mkdir(parents=True)
    assert ".effort-agenda" in (checkout_data.preservation_reason(root) or "")


def test_unavailable_index_inspection_retains_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").write_text("fixture", encoding="utf-8")

    def unavailable(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(checkout_data, "run_caller_owned", unavailable)
    assert "cannot inspect the checkout index" in (checkout_data.preservation_reason(tmp_path) or "")


def test_nonempty_nonrepository_is_not_inspected_as_its_parent_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = tmp_path / "unknown.txt"
    payload.write_bytes(b"preserve")

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("a parent Git repository cannot classify this unqualified directory")

    monkeypatch.setattr(checkout_data, "run_caller_owned", unexpected)
    assert "no inspectable Git root" in (checkout_data.preservation_reason(tmp_path) or "")
    assert payload.read_bytes() == b"preserve"


def test_classified_list_and_both_status_renderers_do_not_scan_ignored_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").write_text("fixture", encoding="utf-8")
    record = tracking.WorktreeRecord(
        worktree_id="rendered", branch="worktree/rendered", worktree_path=str(tmp_path),
        repo="example", machine="fixture-host", platform="linux", started_at="",
        last_resumed_at="", resume_count=0, title="", status="finalized", completed_at=None,
    )
    info = git_ops.WorktreeStateInfo(git_ops.WorktreeState.COMPLETED)

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("presentation must not start a fresh checkout-data observation")

    monkeypatch.setattr(checkout_data, "run_caller_owned", unexpected)
    monkeypatch.setattr(finalized_checkout, "matches", lambda rec: False)
    monkeypatch.setattr(tracking, "is_repo_fetch_fresh", lambda repo: False)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda recs: sessions.SessionContext())
    monkeypatch.setattr(status_bar_cli, "_segment_git_observation", lambda *a, **k: (record, record.branch, info))
    monkeypatch.setattr(status_bar_cli, "_apply_tracking_override", lambda rec, observed: observed)
    monkeypatch.setattr(core, "_render_status_segment", status_bar_cli._render_status_segment)
    monkeypatch.setattr(core, "_status_segment_json", status_bar_cli._status_segment_json)

    row = core._worktree_to_dict(record, state_info=info, current_only=True)
    assert row["id"] == "rendered" and row["cleanup_bucket"] == "clean"
    assert status_bar_cli._render_status_segment(str(tmp_path), plain=True, no_title=True)
    assert status_bar_cli._status_segment_json(str(tmp_path)) is not None
