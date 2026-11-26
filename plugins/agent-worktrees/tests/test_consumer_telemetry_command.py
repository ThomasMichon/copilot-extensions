"""Optional `worktrees.lifecycle.v1` records for finite CLI runs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import config, consumer_telemetry, pr_publication_labels
from process_telemetry.finite_owner import capability

_RECEIVER = """\
import json, os, sys
from pathlib import Path
with Path("readback.jsonl").open("a", encoding="utf-8") as output:
    for line in sys.stdin:
        output.write(json.dumps({"record": json.loads(line), "cwd": os.getcwd()}) + "\\n")
"""


def _admit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "harness root"
    root.mkdir()
    (root / "receiver.py").write_text(_RECEIVER, encoding="utf-8")
    (root / ".agent-telemetry").mkdir()
    (root / ".agent-telemetry" / "config.json").write_text(json.dumps({
        "schema_version": 1, "mode": "command",
        "command": {"argv": [sys.executable, "-I", "receiver.py"]},
    }), encoding="utf-8")
    install = tmp_path / "install"
    install.mkdir()
    (install / "consumer-telemetry.json").write_text(json.dumps({
        "schema_version": 1, "root": str(root),
    }), encoding="utf-8")
    monkeypatch.setattr(config, "install_dir", lambda: install)
    return root


def _readback(root: Path) -> list[dict[str, Any]]:
    path = root / "readback.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["record"] for line in path.read_text(encoding="utf-8").splitlines()]


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], main: Any) -> Any:
    monkeypatch.setattr(sys, "argv", ["agent-worktrees", *argv])
    return consumer_telemetry.instrument(main)()


@pytest.mark.parametrize("argv, expected", [
    (["create", "--json"], ("stage", "worktree_create")),
    (["-p", "example", "create"], ("stage", "worktree_create")),
    (["--project=example", "bind-session"], ("stage", "session_bind")),
    (["lease", "acquire", "x"], ("stage", "lease")),
    (["pr-create"], ("pr", "create")),
    (["push-changes"], ("pr", "push")),
    (["pr-publication-labels", "--repo", "o/r"], ("pr", "label")),
    (["pr-merge", "1", "--now"], ("pr", "merge")),
    (["finalize"], ("pr", "merge")),
    (["finalize", "--abandon", "--handoff-to", "x"], ("pr", "abandon")),
    (["pr", "create"], ("pr", "create")),
    (["pr", "merge", "1", "--now"], ("pr", "merge")),
    (["--project", "example", "worktrees", "create", "--json"], ("stage", "worktree_create")),
    (["worktrees", "lease", "acquire", "x"], ("stage", "lease")),
    (["worktree", "push", "id"], ("pr", "push")),
    (["worktree", "finalize", "--abandon"], ("pr", "abandon")),
    (["lease", "--origin", "u", "renew", "k", "r"], ("stage", "lease")),
    (["lease", "--pretty", "release", "k", "r"], ("stage", "lease")),
    (["lease", "--project", "example", "acquire", "worktree", "r", "--holder", "h"],
     ("stage", "lease")),
    (["lease", "--project=example", "release", "k", "r"], ("stage", "lease")),
    (["lease", "-p", "example", "--origin", "u", "renew", "k", "r"], ("stage", "lease")),
    (["create", "--project", "example"], ("stage", "worktree_create")),
    (["lease", "--project", "example", "list"], None),
    (["pr", "status"], None), (["pr", "watch"], None), (["worktree", "list"], None),
    (["lease"], None), (["lease", "list"], None), (["create", "--help"], None),
    (["lease", "inspect", "k", "r"], None), (["lease", "--pretty", "list"], None),
    (["lease", "--origin", "u", "status", "k", "r"], None),
    (["lease", "--origin=u", "inspect", "k", "r"], None),
    (["status"], None), (["--version"], None), ([], None),
    (["create-pr", "--dry-run"], None), (["pr", "create", "--dry-run"], None),
    (["worktree", "create-pr", "--dry-run"], None), (["push-changes", "--dry-run"], None),
    (["push-changes", "--title-only", "--title", "t"], None),
    (["worktree", "push", "--title-only"], None), (["worktree", "push", "--dry"], None),
    (["finalize", "--explain-landing", "--json"], None), (["finalize", "--dry-run"], None),
    (["worktree", "finalize", "--explain-landing"], None),
    (["pr-merge", "1", "--dry-run"], None), (["pr", "merge", "--all", "--dry-run"], None),
    (["push-changes", "--d"], None), (["worktree", "push", "--title-o"], None),
    (["finalize", "--e", "--json"], None), (["worktree", "finalize", "--dr"], None),
    (["create-pr", "--dry"], None), (["create-pr", "--dr"], ("pr", "create")), (["pr", "merge", "1", "--d"], None),
    (["push-changes", "--title", "t"], ("pr", "push")),
    (["finalize", "--", "--dry-run"], ("pr", "merge")),
    (["finalize", "--ab", "--handoff-to", "x"], ("pr", "abandon")),
    (["worktree", "finalize", "--a", "--h", "x"], ("pr", "abandon")),
    (["pr-merge", "1", "--now", "--he"], None), (["finalize", "--hel"], None),
    (["push-changes", "--he"], None), (["create-pr", "--help=x"], None),
])
def test_classify_observes_only_mutating_lifecycle_verbs(
    argv: list[str], expected: tuple[str, str] | None,
) -> None:
    assert consumer_telemetry.classify(argv) == expected


def test_legacy_install_runs_unchanged_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    assert _run(monkeypatch, ["create"], lambda: 0) == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("argv", [
    ["status"], ["lease", "inspect", "k", "r"], ["lease", "--pretty", "list"],
    ["lease", "--origin", "u", "status", "k", "r"],
    ["create-pr", "--dry-run"], ["pr", "create", "--dry-run"],
    ["worktree", "push", "--title-only"], ["push-changes", "--dry-run"],
    ["finalize", "--explain-landing", "--json"], ["worktree", "finalize", "--explain-landing"],
    ["push-changes", "--d"], ["finalize", "--e", "--json"],
])
def test_unobserved_verbs_never_read_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str],
) -> None:
    lookups: list[str] = []

    def record() -> Path:
        lookups.append("install_dir")
        return tmp_path

    monkeypatch.setattr(config, "install_dir", record)
    assert _run(monkeypatch, argv, lambda: 7) == 7
    assert lookups == []


def test_observed_verbs_do_read_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookups: list[str] = []

    def record() -> Path:
        lookups.append("install_dir")
        return tmp_path

    monkeypatch.setattr(config, "install_dir", record)
    assert _run(monkeypatch, ["push-changes"], lambda: 0) == 0
    assert lookups


def _parser_options(parser: Any) -> frozenset[str]:
    return frozenset(
        option for action in parser._actions for option in action.option_strings
        if option.startswith("--")
    )


def test_option_tables_match_the_owning_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefix resolution is only right if every competing long option is known."""
    import argparse

    from agent_worktrees import finalize_cli, pr_merge_cli

    root = argparse.ArgumentParser()
    finalize_cli.add_parsers(root.add_subparsers())
    choices: dict[str, Any] = root._subparsers._group_actions[0].choices  # type: ignore[union-attr,assignment]
    for verb in ("create-pr", "pr-create", "push-changes", "finalize"):
        assert consumer_telemetry._LONG_OPTIONS[verb] == _parser_options(choices[verb]), verb

    captured: list[frozenset[str]] = []

    def capture(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        captured.append(_parser_options(self))
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    assert pr_merge_cli.cmd_pr_merge_dispatch(["1"]) == 0
    assert captured == [consumer_telemetry._LONG_OPTIONS["pr-merge"]]


class _LabelProvider:
    def __init__(self) -> None:
        self.labels: list[str] = []

    def get_publication_labels(self, repo: str, number: int, **_: Any) -> list[str]:
        return list(self.labels)

    def add_publication_labels(
        self, repo: str, number: int, labels: tuple[str, ...], **_: Any,
    ) -> None:
        self.labels.extend(labels)


def test_direct_reconciliation_reports_its_label_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreign-repository creation reconciles labels directly, not via refresh."""
    root = _admit(tmp_path, monkeypatch)
    origin = pr_publication_labels.parse_origin({"id": "origin", "labels": ["tag"]})

    def main() -> int:
        status = pr_publication_labels.reconcile(
            _LabelProvider(), "owner/secret-repo", 7, origin,
        )
        assert status is not None and status["ok"] is True
        return 0

    assert _run(monkeypatch, ["pr", "create"], main) == 0
    (record,) = _readback(root)
    assert record["event"]["action"] == "create"
    assert record["event"]["label_result"] == "applied"
    assert "secret" not in json.dumps(record)


def test_records_reach_a_real_receiver_without_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _admit(tmp_path, monkeypatch)
    assert _run(monkeypatch, ["-p", "secret-project", "create"], lambda: 0) == 0

    def conflict() -> int:
        raise SystemExit(3)

    with pytest.raises(SystemExit):
        _run(monkeypatch, ["lease", "acquire", "secret-resource"], conflict)

    def interrupted() -> int:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, ["finalize", "--abandon"], interrupted)

    records = _readback(root)
    assert [r["event"] for r in records] == [
        {"kind": "stage", "stage": "worktree_create", "outcome": "success",
         "reason": "none", "duration_ms": records[0]["event"]["duration_ms"], "attempt": 1},
        {"kind": "stage", "stage": "lease", "outcome": "failure",
         "reason": "lease_conflict", "duration_ms": records[1]["event"]["duration_ms"],
         "attempt": 1},
        {"kind": "pr", "action": "abandon", "outcome": "failure", "reason": "interrupted",
         "duration_ms": records[2]["event"]["duration_ms"]},
    ]
    for record in records:
        assert (record["source"], record["source_schema"]) == (
            "agent-worktrees", "worktrees.lifecycle.v1",
        )
    text = json.dumps(records)
    assert "secret" not in text and str(tmp_path) not in text


@pytest.mark.parametrize("result, code, expected", [
    ({"ok": True, "added": ["label"]}, 0, ("success", "none", "repaired")),
    ({"ok": True, "added": []}, 0, ("success", "none", "already_present")),
    ({"skipped": True}, 0, ("success", "none", "not_requested")),
    ({"ok": False, "error": "provider does not support publication labels"}, 1,
     ("failure", "unavailable", "unsupported")),
    ({"ok": False, "error": "HTTP 403 for https://example.invalid/secret"}, 1,
     ("failure", "provider_rejected", "failed")),
])
def test_label_activity_reports_only_the_closed_label_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    result: dict[str, Any], code: int, expected: tuple[str, str, str],
) -> None:
    root = _admit(tmp_path, monkeypatch)

    def main() -> int:
        pr_publication_labels.last_result.set(result)
        return code

    assert _run(monkeypatch, ["pr-publication-labels"], main) == code
    (record,) = _readback(root)
    event = record["event"]
    assert (event["outcome"], event["reason"], event["label_result"]) == expected
    assert "example.invalid" not in json.dumps(record)


def test_receiver_failure_never_changes_the_product_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _admit(tmp_path, monkeypatch)
    (root / ".agent-telemetry" / "config.json").write_text(json.dumps({
        "schema_version": 1, "mode": "command",
        "command": {"argv": [str(tmp_path / "missing-receiver")]},
    }), encoding="utf-8")
    assert _run(monkeypatch, ["push-changes"], lambda: 0) == 0


def test_console_entry_is_instrumented(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import __main__ as cli, current_roster_entry

    seen: list[Any] = []
    monkeypatch.setattr(current_roster_entry, "run_legacy_console", seen.append)
    cli.console_entry()
    assert seen and seen[0].__qualname__ == "instrument.<locals>.run"


def test_packaged_descriptor_names_only_the_worktrees_schema() -> None:
    path = Path(consumer_telemetry.__file__).parent / "metadata" / "consumer-telemetry-contract.json"
    assert json.loads(path.read_text(encoding="utf-8")) == capability([
        consumer_telemetry.SOURCE_SCHEMA,
    ])
