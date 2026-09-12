"""Import guard for the split-out reviewer-loop / repository-issue-loop CLI
command implementations.

Every command flow here is already thoroughly exercised through the
``agent_dispatch.__main__`` facade (``test_cli.py``,
``test_repository_issue_loop_cli.py``) -- this file only guards that
``agent_dispatch.loop_commands`` remains directly importable with its own
stable public names, independent of the facade, and that its ``_client``/
``_emit``/etc. proxies still resolve dynamically against a (possibly
monkeypatched) ``agent_dispatch.__main__`` at call time -- including under
``python -m agent_dispatch``, where that module is loaded as
``sys.modules["__main__"]`` rather than the dotted name.
"""

from __future__ import annotations

import types

from agent_dispatch import loop_commands
from agent_dispatch.loop_commands import (
    _cmd_repository_issue_loop,
    _cmd_reviewer_loop,
    _repository_issue_loop_declarations,
    _repository_issue_loop_health_path,
    _repository_issue_loop_registrations,
    _repository_issue_loop_setup,
    _repository_issue_loop_status,
    _resolve_cli_module,
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


def test_resolve_cli_module_prefers_the_live_python_dash_m_module(monkeypatch):
    """Under ``python -m agent_dispatch``, ``__main__.py`` runs as
    ``sys.modules["__main__"]`` with ``__spec__.name ==
    "agent_dispatch.__main__"`` -- not under the dotted module name. Fabricate
    that exact shape and confirm the resolver returns the live fake, not a
    second, independently-imported copy of the real module.
    """
    import sys

    fake_main = types.ModuleType("__main__")
    fake_main.__spec__ = types.SimpleNamespace(name="agent_dispatch.__main__")
    fake_main._client = lambda *a, **k: "from-live-dash-m-main"
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    monkeypatch.delitem(sys.modules, "agent_dispatch.__main__", raising=False)

    assert _resolve_cli_module() is fake_main
    assert loop_commands._client() == "from-live-dash-m-main"


def test_resolve_cli_module_falls_back_to_dotted_import_for_a_normal_importer(
    monkeypatch,
):
    """When ``sys.modules["__main__"]`` belongs to some unrelated entry point
    (the ordinary case for tests and any library importer), the resolver
    must use the real dotted ``agent_dispatch.__main__`` module instead.
    """
    import sys

    from agent_dispatch import __main__ as real_cli

    unrelated_main = types.ModuleType("__main__")
    unrelated_main.__spec__ = None
    monkeypatch.setitem(sys.modules, "__main__", unrelated_main)

    assert _resolve_cli_module() is real_cli
