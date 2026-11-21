"""Explicit collection stays on the executing runtime's qualified owner."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import python_cache_command as command


def arguments(*values: Any) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    command.add_parser(parser.add_subparsers(dest="action", required=True))
    return parser.parse_args(["collect", *map(str, values)])


def test_missing_capability_refuses_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(command, "retained_owner", lambda: SimpleNamespace())
    assert command.run(arguments("--root", tmp_path, "--apply")) == 2
    assert "does not support" in capsys.readouterr().err


def test_contract_reports_old_owner_without_claiming_support(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(command, "retained_owner", lambda: SimpleNamespace())
    assert command.run(arguments("--contract")) == 2
    assert not json.loads(capsys.readouterr().out)["supported"]


@pytest.mark.parametrize("roots", [[], ["--root", "relative"], ["--root", "a", "--root", "b"]])
def test_invalid_root_never_reaches_collection(monkeypatch: pytest.MonkeyPatch, roots: list[str]) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid root reached owner")

    monkeypatch.setattr(command, "retained_owner", lambda: SimpleNamespace(
        PYTHON_CACHE_RECLAMATION_CAPABILITY=command.CAPABILITY, collect_python_cache=unexpected,
    ))
    assert command.run(arguments(*roots)) == 2


def test_explicit_dry_run_default_and_all_budgets_reach_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[Path, dict[str, Any]]] = []

    def collect(root: Path, **options: Any) -> dict[str, Any]:
        calls.append((root, options))
        return {"summary": {}, "pending": True}

    monkeypatch.setattr(command, "retained_owner", lambda: SimpleNamespace(
        PYTHON_CACHE_RECLAMATION_CAPABILITY=command.CAPABILITY, collect_python_cache=collect,
    ))
    assert command.run(arguments("--root", tmp_path, "--drain", "--max-seconds", 42, "--json")) == 0
    assert calls[0][0] == Path(tmp_path)
    assert calls[0][1]["apply"] is False
    assert calls[0][1]["drain"] is True and calls[0][1]["seconds"] == 42
    assert json.loads(capsys.readouterr().out)["pending"]


def test_status_json_reads_only_cached_metadata_without_claiming_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_procutil import python_cache_store
    from agent_worktrees import config, python_cache_prune

    root = tmp_path / "runtime"
    cache = tmp_path / "os-cache"
    monkeypatch.setattr(config, "install_dir", lambda: root)
    monkeypatch.setattr(python_cache_store, "user_cache", lambda: cache)
    parser = argparse.ArgumentParser()
    python_cache_prune.add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["python-cache", "status", "--json"])
    assert python_cache_prune.cmd_python_cache(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observation"] == "not-measured"
    assert "summary" not in result
    assert "collect" in result["inspect_command"] and str(root) in result["inspect_command"]
    assert not cache.exists()


def test_unknown_inventory_is_rendered_as_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    from agent_worktrees import python_cache_diagnostics

    python_cache_diagnostics.render({
        "observation": "cached", "inventory_complete": False, "scan_complete": False,
        "summary": {"measured_bytes": 0, "eligible_bytes": 0, "held_bytes": 0,
                    "unmeasured_prefixes": None},
        "observation_age_seconds": 1, "inspect_command": "cache-tool",
    })
    text = capsys.readouterr().out
    assert "unknown unmeasured prefixes" in text
    assert "None" not in text
