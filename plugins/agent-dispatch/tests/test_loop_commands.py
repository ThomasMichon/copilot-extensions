"""Import guard for the split-out reviewer-loop / repository-issue-loop CLI
command implementations.

Every command flow here is already thoroughly exercised through the
``agent_dispatch.__main__`` facade (``test_cli.py``,
``test_repository_issue_loop_cli.py``) -- this file only guards that
``agent_dispatch.loop_commands`` remains directly importable with its own
stable public names, independent of the facade, and that its ``_client``/
``_emit``/etc. proxies still resolve dynamically against a (possibly
monkeypatched) ``agent_dispatch.__main__`` at call time.
"""

from __future__ import annotations

from agent_dispatch import loop_commands
from agent_dispatch.loop_commands import (
    _cmd_repository_issue_loop,
    _cmd_reviewer_loop,
    _repository_issue_loop_declarations,
    _repository_issue_loop_health_path,
    _repository_issue_loop_registrations,
    _repository_issue_loop_setup,
    _repository_issue_loop_status,
    _reviewer_loop_declarations,
    _reviewer_loop_registrations,
    _reviewer_loop_setup,
    _reviewer_loop_status,
    _spawn_attempt_projection,
)


def test_command_entry_points_are_directly_importable():
    assert callable(_cmd_reviewer_loop)
    assert callable(_cmd_repository_issue_loop)


def test_declaration_and_status_helpers_are_directly_importable():
    assert callable(_reviewer_loop_declarations)
    assert callable(_reviewer_loop_registrations)
    assert callable(_reviewer_loop_setup)
    assert callable(_reviewer_loop_status)
    assert callable(_repository_issue_loop_declarations)
    assert callable(_repository_issue_loop_registrations)
    assert callable(_repository_issue_loop_setup)
    assert callable(_repository_issue_loop_health_path)
    assert callable(_repository_issue_loop_status)


def test_spawn_attempt_projection_alias_matches_dedicated_module():
    from agent_dispatch.spawn_attempt_projection import spawn_attempt_projection

    assert _spawn_attempt_projection is spawn_attempt_projection


def test_client_proxy_picks_up_a_live_main_monkeypatch(monkeypatch):
    from agent_dispatch import __main__ as cli

    sentinel = object()
    monkeypatch.setattr(cli, "_client", lambda *a, **k: sentinel)
    assert loop_commands._client("ignored") is sentinel


def test_emit_proxy_picks_up_a_live_main_monkeypatch(monkeypatch, capsys):
    from agent_dispatch import __main__ as cli

    monkeypatch.setattr(cli, "_emit", lambda value: value)
    assert loop_commands._emit({"ok": True}) == {"ok": True}
