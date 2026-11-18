"""Unknown checkout data is not disposal authority."""

from pathlib import Path
import subprocess

import pytest

from agent_worktrees import checkout_data


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
    (0, "", False), (0, "evidence/\0", True), (128, "", True), (0, "x" * 65537, True),
])
def test_ignored_data_observation_is_bounded_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, text: str, held: bool,
) -> None:
    calls: list[dict[str, object]] = []
    (tmp_path / ".git").write_text("fixture", encoding="utf-8")

    def observed(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        assert "ls-files" in argv
        return subprocess.CompletedProcess(argv, code, text, "")

    monkeypatch.setattr(checkout_data, "run_caller_owned", observed)
    assert (checkout_data.preservation_reason(tmp_path) is not None) is held
    assert len(calls) == 1
    assert calls[0]["max_stdout_bytes"] == 65536
    assert calls[0]["timeout"] == 5


def test_unavailable_ignored_data_inspection_retains_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").write_text("fixture", encoding="utf-8")

    def unavailable(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(checkout_data, "run_caller_owned", unavailable)
    assert "cannot classify" in (checkout_data.preservation_reason(tmp_path) or "")


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
