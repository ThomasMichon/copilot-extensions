from __future__ import annotations

import argparse
import json

from agent_mcp import tool_cache_maintenance as tcm
from agent_mcp.__main__ import _cmd_clean_tool_cache


def _write_entry(directory, name: str, schema_version: int | None, extra: str = "") -> None:
    path = directory / f"{name}.json"
    if schema_version is None:
        path.write_text("{not valid json" + extra, encoding="utf-8")
        return
    path.write_text(
        json.dumps({"schemaVersion": schema_version, "serverName": name}) + extra,
        encoding="utf-8",
    )


def test_resolve_cache_dir_honors_explicit_override(tmp_path):
    override = tmp_path / "custom"
    assert tcm.resolve_cache_dir(str(override)) == override


def test_resolve_cache_dir_honors_copilot_cache_home_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_CACHE_HOME", str(tmp_path))
    assert tcm.resolve_cache_dir() == tmp_path / "mcp-tools"


def test_scan_uses_maximum_schema_version_not_most_common(tmp_path):
    # Regression test: an earlier version of this logic used the *most common*
    # schema version as authoritative, which silently picks the WRONG version
    # (and deletes good, current-schema entries) whenever stale entries
    # outnumber current ones -- confirmed live on a facility machine where
    # 1,840 stale v1 entries outnumbered 1,374 current v3 entries.
    for i in range(5):
        _write_entry(tmp_path, f"stale-{i}", schema_version=1)
    for i in range(2):
        _write_entry(tmp_path, f"current-{i}", schema_version=3)

    result = tcm.scan(tmp_path)

    assert result.current_version == 3
    stale_names = {e.path.stem for e in result.stale_entries}
    assert stale_names == {"stale-0", "stale-1", "stale-2", "stale-3", "stale-4"}


def test_scan_flags_unparseable_entries_as_stale(tmp_path):
    _write_entry(tmp_path, "current", schema_version=3)
    _write_entry(tmp_path, "broken", schema_version=None)

    result = tcm.scan(tmp_path)

    assert result.current_version == 3
    stale_names = {e.path.stem for e in result.stale_entries}
    assert stale_names == {"broken"}


def test_scan_reports_nothing_stale_when_only_one_version_present(tmp_path):
    for i in range(3):
        _write_entry(tmp_path, f"entry-{i}", schema_version=3)

    result = tcm.scan(tmp_path)

    assert result.stale_entries == []


def test_clean_dry_run_does_not_delete(tmp_path):
    _write_entry(tmp_path, "stale", schema_version=1)
    _write_entry(tmp_path, "current", schema_version=3)

    result = tcm.clean(tmp_path, apply=False)

    assert result["stale_entries"] == 1
    assert result["deleted"] == 0
    assert (tmp_path / "stale.json").exists()


def test_clean_apply_deletes_only_stale_entries(tmp_path):
    _write_entry(tmp_path, "stale", schema_version=1)
    _write_entry(tmp_path, "current", schema_version=3)

    result = tcm.clean(tmp_path, apply=True)

    assert result["deleted"] == 1
    assert not (tmp_path / "stale.json").exists()
    assert (tmp_path / "current.json").exists()


def test_cmd_clean_tool_cache_missing_dir_returns_2(tmp_path, capsys):
    args = argparse.Namespace(
        apply=False, cache_dir=str(tmp_path / "does-not-exist"), json=False, quiet=False,
    )
    rc = _cmd_clean_tool_cache(args)
    assert rc == 2
    assert "nothing to do" in capsys.readouterr().out


def test_cmd_clean_tool_cache_dry_run_reports_stale_and_returns_1(tmp_path, capsys):
    _write_entry(tmp_path, "stale", schema_version=1)
    _write_entry(tmp_path, "current", schema_version=3)
    args = argparse.Namespace(apply=False, cache_dir=str(tmp_path), json=False, quiet=False)

    rc = _cmd_clean_tool_cache(args)

    output = capsys.readouterr().out
    assert rc == 1
    assert "1 stale entry" in output
    assert "Dry run" in output
    assert (tmp_path / "stale.json").exists()


def test_cmd_clean_tool_cache_apply_deletes_and_returns_0(tmp_path, capsys):
    _write_entry(tmp_path, "stale", schema_version=1)
    _write_entry(tmp_path, "current", schema_version=3)
    args = argparse.Namespace(apply=True, cache_dir=str(tmp_path), json=False, quiet=False)

    rc = _cmd_clean_tool_cache(args)

    assert rc == 0
    assert not (tmp_path / "stale.json").exists()
    assert (tmp_path / "current.json").exists()


def test_cmd_clean_tool_cache_json_output(tmp_path, capsys):
    _write_entry(tmp_path, "stale", schema_version=1)
    _write_entry(tmp_path, "current", schema_version=3)
    args = argparse.Namespace(apply=False, cache_dir=str(tmp_path), json=True, quiet=True)

    rc = _cmd_clean_tool_cache(args)

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["stale_entries"] == 1
    assert payload["current_schema_version"] == 3
    assert payload["applied"] is False
