"""Tests for copilot_launch_prefs: translating the persisted model/effort/
context-tier preference into explicit CLI flags at launch time."""

from __future__ import annotations

import json

from agent_worktrees import copilot_launch_prefs as prefs


def _write_settings(tmp_path, monkeypatch, data: dict) -> None:
    home = tmp_path / "home"
    (home / ".copilot").mkdir(parents=True)
    (home / ".copilot" / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(prefs.Path, "home", classmethod(lambda cls: home))


def test_no_settings_file_yields_no_flags(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(prefs.Path, "home", classmethod(lambda cls: home))
    assert prefs.resolve_launch_pref_flags([]) == []


def test_malformed_settings_file_yields_no_flags(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".copilot").mkdir(parents=True)
    (home / ".copilot" / "settings.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(prefs.Path, "home", classmethod(lambda cls: home))
    assert prefs.resolve_launch_pref_flags([]) == []


def test_full_preference_translates_to_cli_flags(tmp_path, monkeypatch):
    _write_settings(
        tmp_path,
        monkeypatch,
        {"model": "claude-sonnet-5", "effortLevel": "medium", "contextTier": "long_context"},
    )
    assert prefs.resolve_launch_pref_flags([]) == [
        "--model", "claude-sonnet-5",
        "--reasoning-effort", "medium",
        "--context", "long_context",
    ]


def test_partial_preference_only_emits_known_keys(tmp_path, monkeypatch):
    _write_settings(tmp_path, monkeypatch, {"model": "gpt-5.4"})
    assert prefs.resolve_launch_pref_flags([]) == ["--model", "gpt-5.4"]


def test_non_string_or_empty_values_are_skipped(tmp_path, monkeypatch):
    _write_settings(
        tmp_path,
        monkeypatch,
        {"model": "", "effortLevel": None, "contextTier": 5},
    )
    assert prefs.resolve_launch_pref_flags([]) == []


def test_caller_supplied_bare_flag_is_never_duplicated(tmp_path, monkeypatch):
    _write_settings(tmp_path, monkeypatch, {"model": "claude-sonnet-5"})
    assert prefs.resolve_launch_pref_flags(["--model", "gpt-5.4"]) == []


def test_caller_supplied_equals_flag_is_never_duplicated(tmp_path, monkeypatch):
    _write_settings(tmp_path, monkeypatch, {"model": "claude-sonnet-5"})
    assert prefs.resolve_launch_pref_flags(["--model=gpt-5.4"]) == []


def test_only_overridden_keys_are_skipped_others_still_injected(tmp_path, monkeypatch):
    _write_settings(
        tmp_path,
        monkeypatch,
        {"model": "claude-sonnet-5", "effortLevel": "medium"},
    )
    assert prefs.resolve_launch_pref_flags(["--model", "gpt-5.4"]) == [
        "--reasoning-effort", "medium",
    ]
