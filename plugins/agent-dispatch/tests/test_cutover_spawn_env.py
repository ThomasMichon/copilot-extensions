"""Regression: a cutover's replacement coordinator must inherit service.env.

A zero-downtime cutover's ``spawn_passive`` only ever inherited
``dict(os.environ)`` -- the triggering process's OWN ambient env -- and
never re-applied the installed ``service.env`` the way the first-use
bootstrap spawn (``_spawn_coordinator_process``) already does. A cutover
triggered from a process whose own environment lacked
``AGENT_DISPATCH_CONTROL_TOKEN_COMMAND`` (anything other than the
supervisor's own systemd/Scheduled-Task unit, which loads it via
``EnvironmentFile``) could silently produce a replacement coordinator with
no control token at all: evaluator/producer-scope registrations would then
fail with ``control_authority_not_configured`` even though the installed
``service.env`` already had the token command configured.
"""

from __future__ import annotations


def test_apply_service_env_overlay_adds_control_token_command(tmp_path):
    from agent_dispatch.install_paths import apply_service_env_overlay

    (tmp_path / "service.env").write_text(
        "# comment, skipped\n"
        "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND=vault get \"Entry Name\" password\n"
        "AGENT_DISPATCH_HOST=127.0.0.1\n"
    )
    env = {"PATH": "/usr/bin"}

    result = apply_service_env_overlay(env, tmp_path)

    assert result is env  # mutates + returns the same dict
    assert env["AGENT_DISPATCH_CONTROL_TOKEN_COMMAND"] == 'vault get "Entry Name" password'
    assert env["AGENT_DISPATCH_HOST"] == "127.0.0.1"
    assert env["PATH"] == "/usr/bin"  # untouched keys survive


def test_apply_service_env_overlay_is_a_noop_without_a_service_env_file(tmp_path):
    from agent_dispatch.install_paths import apply_service_env_overlay

    env = {"PATH": "/usr/bin"}
    result = apply_service_env_overlay(env, tmp_path)

    assert result == {"PATH": "/usr/bin"}


def test_cutover_spawn_passive_applies_service_env_overlay(tmp_path, monkeypatch):
    """The actual ``spawn_passive`` closure inside ``_cmd_cutover`` must call
    through to the shared overlay -- not just the extracted helper in
    isolation -- so a future refactor can't silently drop the call site."""
    import zdd.breadcrumb as zdd_breadcrumb
    import zdd.cutover as zdd_cutover

    from agent_dispatch import __main__ as main_mod

    install_dir = tmp_path / ".agent-dispatch"
    install_dir.mkdir()
    (install_dir / "service.env").write_text(
        'AGENT_DISPATCH_CONTROL_TOKEN_COMMAND=vault get "Agent-Dispatch Control Token" password\n'
    )
    monkeypatch.setattr(
        "agent_dispatch.install_paths.install_dir", lambda: install_dir
    )
    # Simulate a cutover triggered from a process whose own ambient env has
    # NOTHING control-token-shaped at all (the exact failure mode).
    monkeypatch.delenv("AGENT_DISPATCH_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_CONTROL_TOKEN_COMMAND", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.setattr("agent_dispatch.config.client_token", lambda: None)
    monkeypatch.setattr(zdd_breadcrumb, "recover_stale_cutover", lambda *a, **k: {"recovered": False})
    monkeypatch.setattr(zdd_breadcrumb, "read_breadcrumb", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_reap_abandoned_passive", lambda *_a, **_k: {"reaped": False})
    monkeypatch.setattr(main_mod, "_reap_superseded_coordinators", lambda *_a, **_k: None)

    captured: dict = {}

    class _FakeResult:
        ok = True
        new_port = 1234
        error = None
        rolled_back = False
        steps: list = []

        def to_dict(self):
            return {"ok": True}

    class _FakeOrchestrator:
        def __init__(self, _routing_dir, *, spawn_passive, **_k):
            captured["spawn_passive"] = spawn_passive

        def run(self, **_kwargs):
            return _FakeResult()

    monkeypatch.setattr(zdd_cutover, "CutoverOrchestrator", _FakeOrchestrator)
    popen_calls: list = []
    monkeypatch.setattr(
        "agent_procutil.windowless_python", lambda python: python
    )
    monkeypatch.setattr(
        "agent_procutil.windowless_python_env", lambda _python: {}
    )
    monkeypatch.setattr(
        "agent_procutil.detached_kwargs", lambda: {}
    )

    import subprocess as _subprocess

    def _fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))

        class _P:
            pass

        return _P()

    monkeypatch.setattr(_subprocess, "Popen", _fake_popen)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)
    assert exit_code == 0

    # Now actually invoke the captured spawn_passive closure, as the real
    # orchestrator would mid-cutover, and verify the spawned env carries the
    # control-token command even though this test process's own env was
    # scrubbed of it above.
    captured["spawn_passive"](9999)
    assert len(popen_calls) == 1
    _cmd, kwargs = popen_calls[0]
    assert (
        kwargs["env"]["AGENT_DISPATCH_CONTROL_TOKEN_COMMAND"]
        == 'vault get "Agent-Dispatch Control Token" password'
    )


def test_cutover_spawn_passive_fresh_port_wins_over_service_env_port_pin(tmp_path, monkeypatch):
    """A stale ``AGENT_DISPATCH_PORT`` pin in ``service.env`` must never
    override the orchestrator-selected port the cutover is actually binding
    the replacement coordinator to -- the overlay has to apply *before* the
    fresh port is stamped onto the child environment, not after."""
    import zdd.breadcrumb as zdd_breadcrumb
    import zdd.cutover as zdd_cutover

    from agent_dispatch import __main__ as main_mod

    install_dir = tmp_path / ".agent-dispatch"
    install_dir.mkdir()
    (install_dir / "service.env").write_text(
        'AGENT_DISPATCH_CONTROL_TOKEN_COMMAND=vault get "Agent-Dispatch Control Token" password\n'
        "AGENT_DISPATCH_PORT=4444\n"
    )
    monkeypatch.setattr("agent_dispatch.install_paths.install_dir", lambda: install_dir)
    monkeypatch.delenv("AGENT_DISPATCH_PORT", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.setattr("agent_dispatch.config.client_token", lambda: None)
    monkeypatch.setattr(zdd_breadcrumb, "recover_stale_cutover", lambda *a, **k: {"recovered": False})
    monkeypatch.setattr(zdd_breadcrumb, "read_breadcrumb", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_reap_abandoned_passive", lambda *_a, **_k: {"reaped": False})
    monkeypatch.setattr(main_mod, "_reap_superseded_coordinators", lambda *_a, **_k: None)

    captured: dict = {}

    class _FakeResult:
        ok = True
        new_port = 9999
        error = None
        rolled_back = False
        steps: list = []

        def to_dict(self):
            return {"ok": True}

    class _FakeOrchestrator:
        def __init__(self, _routing_dir, *, spawn_passive, **_k):
            captured["spawn_passive"] = spawn_passive

        def run(self, **_kwargs):
            return _FakeResult()

    monkeypatch.setattr(zdd_cutover, "CutoverOrchestrator", _FakeOrchestrator)
    popen_calls: list = []
    monkeypatch.setattr("agent_procutil.windowless_python", lambda python: python)
    monkeypatch.setattr("agent_procutil.windowless_python_env", lambda _python: {})
    monkeypatch.setattr("agent_procutil.detached_kwargs", lambda: {})

    import subprocess as _subprocess

    def _fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))

        class _P:
            pass

        return _P()

    monkeypatch.setattr(_subprocess, "Popen", _fake_popen)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)
    assert exit_code == 0

    # The orchestrator picked a fresh free port (9999 here) for the passive
    # process to bind -- the service.env pin of 4444 must not win.
    captured["spawn_passive"](9999)
    assert len(popen_calls) == 1
    _cmd, kwargs = popen_calls[0]
    assert kwargs["env"]["AGENT_DISPATCH_PORT"] == "9999"


def test_cutover_honors_durable_host_from_service_env(tmp_path, monkeypatch):
    """A durable ``AGENT_DISPATCH_HOST`` pin in ``service.env`` must be
    honored by the cutover's own configuration (and thus its health-check /
    routing bind and the ``--host`` flag passed to the replacement process),
    even when the triggering process's own ambient environment lacks it."""
    import zdd.breadcrumb as zdd_breadcrumb
    import zdd.cutover as zdd_cutover

    from agent_dispatch import __main__ as main_mod

    install_dir = tmp_path / ".agent-dispatch"
    install_dir.mkdir()
    (install_dir / "service.env").write_text(
        'AGENT_DISPATCH_CONTROL_TOKEN_COMMAND=vault get "Agent-Dispatch Control Token" password\n'
        "AGENT_DISPATCH_HOST=10.0.0.5\n"
    )
    monkeypatch.setattr("agent_dispatch.install_paths.install_dir", lambda: install_dir)
    monkeypatch.delenv("AGENT_DISPATCH_HOST", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.setattr("agent_dispatch.config.client_token", lambda: None)
    monkeypatch.setattr(zdd_breadcrumb, "recover_stale_cutover", lambda *a, **k: {"recovered": False})
    monkeypatch.setattr(zdd_breadcrumb, "read_breadcrumb", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_reap_abandoned_passive", lambda *_a, **_k: {"reaped": False})
    monkeypatch.setattr(main_mod, "_reap_superseded_coordinators", lambda *_a, **_k: None)

    captured: dict = {}

    class _FakeResult:
        ok = True
        new_port = 1234
        error = None
        rolled_back = False
        steps: list = []

        def to_dict(self):
            return {"ok": True}

    class _FakeOrchestrator:
        def __init__(self, _routing_dir, *, bind, spawn_passive, **_k):
            captured["bind"] = bind
            captured["spawn_passive"] = spawn_passive

        def run(self, **_kwargs):
            return _FakeResult()

    monkeypatch.setattr(zdd_cutover, "CutoverOrchestrator", _FakeOrchestrator)
    popen_calls: list = []
    monkeypatch.setattr("agent_procutil.windowless_python", lambda python: python)
    monkeypatch.setattr("agent_procutil.windowless_python_env", lambda _python: {})
    monkeypatch.setattr("agent_procutil.detached_kwargs", lambda: {})

    import subprocess as _subprocess

    def _fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))

        class _P:
            pass

        return _P()

    monkeypatch.setattr(_subprocess, "Popen", _fake_popen)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)
    assert exit_code == 0

    assert captured["bind"] == "10.0.0.5"
    captured["spawn_passive"](4321)
    assert len(popen_calls) == 1
    cmd, _kwargs = popen_calls[0]
    assert "--host" in cmd
    assert cmd[cmd.index("--host") + 1] == "10.0.0.5"
