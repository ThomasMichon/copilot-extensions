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

import os
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("isolated_service_environment")


@pytest.fixture
def daemon_calls(monkeypatch):
    calls = []

    def launch(argv, **options):
        calls.append((argv, options))
        return SimpleNamespace(pid=99999999)

    monkeypatch.setattr("agent_procutil.spawn_daemon", launch)
    return calls


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


@pytest.mark.parametrize("windows_keys", [False, True])
def test_service_env_overlay_matches_native_key_semantics(tmp_path, monkeypatch, windows_keys):
    from agent_dispatch import install_paths

    monkeypatch.setattr(install_paths, "os", SimpleNamespace(
        **{**vars(os), "name": "nt" if windows_keys else "posix"},
    ))
    (tmp_path / "service.env").write_text(
        "AGENT_DISPATCH_HOST=127.0.0.1\n"
        "Agent_Dispatch_Host=127.0.0.2\n",
    )
    env = {"AGENT_DISPATCH_HOST": "127.0.0.3"}
    assert install_paths.apply_service_env_overlay(env, tmp_path) is env
    assert env["AGENT_DISPATCH_HOST"] == ("127.0.0.2" if windows_keys else "127.0.0.1")
    assert ("Agent_Dispatch_Host" in env) is not windows_keys
    if not windows_keys:
        assert env["Agent_Dispatch_Host"] == "127.0.0.2"


@pytest.mark.parametrize("windows_keys", [False, True])
@pytest.mark.parametrize("reference", ["$FIXTURE_ROOT", "${FIXTURE_ROOT}", "%FIXTURE_ROOT%"])
def test_service_env_expands_queue_path_from_its_captured_mapping(
    tmp_path, monkeypatch, windows_keys, reference,
):
    from agent_dispatch import install_paths

    monkeypatch.setattr(install_paths, "os", SimpleNamespace(
        **{**vars(os), "name": "nt" if windows_keys else "posix"},
    ))
    monkeypatch.setenv("FIXTURE_ROOT", str(tmp_path / "unrelated-caller"))
    selected = str(tmp_path / "durable-root")
    (tmp_path / "service.env").write_text(
        f"FIXTURE_ROOT={selected}\nAGENT_DISPATCH_DB={reference}/tasks.db\n",
    )
    env = {"FIXTURE_ROOT": str(tmp_path / "captured-before-overlay")}
    install_paths.apply_service_env_overlay(env, tmp_path)
    expected = selected if windows_keys or reference != "%FIXTURE_ROOT%" else reference
    assert env["AGENT_DISPATCH_DB"] == f"{expected}/tasks.db"
    assert os.environ["FIXTURE_ROOT"] == str(tmp_path / "unrelated-caller")


@pytest.mark.parametrize("windows_keys", [False, True])
@pytest.mark.parametrize("value", [
    "$FIXTURE_ROOT", "${FIXTURE_ROOT}", "%FIXTURE_ROOT%",
    "$$FIXTURE_ROOT", "%%FIXTURE_ROOT%%", "'$FIXTURE_ROOT'",
    "'unclosed $FIXTURE_ROOT", '"$FIXTURE_ROOT"', "${FIXTURE_UNKNOWN}",
    "%FIXTURE_UNKNOWN%", "$FIXTURE_ROOT-tail", "${FIXTURE_ROOT}/$FIXTURE_UNKNOWN",
    "$FIXTURE_ROOT/${FIXTURE_ROOT}", "$FIXTURE_ROOT$", "${FIXTURE_ROOT", "%FIXTURE_ROOT",
    "$FIXTURE_ROOT\u00e9/tasks.db", "$FIXTURE_ROOT\u4e00/tasks.db",
    "${FIXTURE_ROOT}\u00e9/tasks.db",
    "${FIXTURE_UNKNOWN/$FIXTURE_ROOT", "%FIXTURE_UNKNOWN/$FIXTURE_ROOT",
    "prefix${FIXTURE_UNKNOWN/%FIXTURE_ROOT%", "prefix%FIXTURE_UNKNOWN/${FIXTURE_ROOT}",
])
def test_captured_environment_expansion_preserves_native_syntax(
    monkeypatch, windows_keys, value,
):
    import ntpath
    import posixpath

    from agent_dispatch import install_paths

    monkeypatch.setenv("FIXTURE_ROOT", "expanded with $FIXTURE_UNKNOWN")
    monkeypatch.delenv("FIXTURE_UNKNOWN", raising=False)
    env = dict(os.environ)
    expected = (ntpath if windows_keys else posixpath).expandvars(value)
    monkeypatch.setattr(install_paths, "os", SimpleNamespace(
        **{**vars(os), "name": "nt" if windows_keys else "posix"},
    ))
    assert install_paths._expand_service_env_value(value, env) == expected


def test_windows_expansion_uses_case_insensitive_snapshot_names(tmp_path, monkeypatch):
    from agent_dispatch import install_paths

    monkeypatch.setattr(install_paths, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    (tmp_path / "service.env").write_text(
        "Fixture_Root=captured\n"
        "AGENT_DISPATCH_DB=%fixture_root%/${fixture_root}/$fixture_root/tasks.db\n",
    )
    env = {}
    install_paths.apply_service_env_overlay(env, tmp_path)
    assert env["AGENT_DISPATCH_DB"] == "captured/captured/captured/tasks.db"


@pytest.mark.parametrize("windows_keys", [False, True])
def test_snapshot_expansion_matches_native_compound_reference_boundaries(monkeypatch, windows_keys):
    import ntpath
    import posixpath
    import random

    from agent_dispatch import install_paths

    monkeypatch.setenv("A", "captured-root")
    monkeypatch.setenv("B", "$A/%A%")
    env = dict(os.environ)
    monkeypatch.setattr(install_paths, "os", SimpleNamespace(
        **{**vars(os), "name": "nt" if windows_keys else "posix"},
    ))
    native = ntpath if windows_keys else posixpath
    generator = random.Random(17)
    chunks = ["$A", "${A}", "%A%", "$B", "${B}", "%B%", "$", "%", "{", "}", "'", '"',
              "\u00e9", " ", "/", "A", "B"]
    for _ in range(1000):
        value = "".join(generator.choices(chunks, k=generator.randrange(1, 25)))
        assert install_paths._expand_service_env_value(value, env) == native.expandvars(value), value


def test_cutover_spawn_passive_applies_service_env_overlay(tmp_path, monkeypatch, daemon_calls):
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
    monkeypatch.setattr("agent_dispatch.cutover_process.process_start_time", lambda _pid: None)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)
    assert exit_code == 0

    # Now actually invoke the captured spawn_passive closure, as the real
    # orchestrator would mid-cutover, and verify the spawned env carries the
    # control-token command even though this test process's own env was
    # scrubbed of it above.
    captured["spawn_passive"](9999)
    assert len(daemon_calls) == 1
    _cmd, kwargs = daemon_calls[0]
    assert kwargs["console_children"] is True
    assert kwargs.get("breakaway", True) is True
    assert kwargs["instance"].lifecycle == "resident"
    assert (
        kwargs["env"]["AGENT_DISPATCH_CONTROL_TOKEN_COMMAND"]
        == 'vault get "Agent-Dispatch Control Token" password'
    )
    # The passive replacement runs from the runtime root, never this
    # process's own cwd.
    assert kwargs["cwd"] == install_dir


def test_cutover_spawn_passive_fresh_port_wins_over_service_env_port_pin(
    tmp_path, monkeypatch, daemon_calls,
):
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
    monkeypatch.setattr("agent_dispatch.cutover_process.process_start_time", lambda _pid: None)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)
    assert exit_code == 0

    # The orchestrator picked a fresh free port (9999 here) for the passive
    # process to bind -- the service.env pin of 4444 must not win.
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "later-caller-route"))
    captured["spawn_passive"](9999)
    assert len(daemon_calls) == 1
    _cmd, kwargs = daemon_calls[0]
    assert kwargs["env"]["AGENT_DISPATCH_PORT"] == "9999"
    assert kwargs["env"]["AGENT_DISPATCH_ROUTING_DIR"] == str(tmp_path / "routing")


def test_cutover_honors_durable_host_from_service_env(tmp_path, monkeypatch, daemon_calls):
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
        f"FIXTURE_QUEUE_ROOT={install_dir}\n"
        "AGENT_DISPATCH_DB=${FIXTURE_QUEUE_ROOT}/tasks.db\n"
    )
    monkeypatch.setenv("FIXTURE_QUEUE_ROOT", str(tmp_path / "unrelated-caller"))
    monkeypatch.setattr("agent_dispatch.install_paths.install_dir", lambda: install_dir)
    monkeypatch.delenv("AGENT_DISPATCH_HOST", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.setattr("agent_dispatch.config.client_token", lambda: None)
    monkeypatch.setattr(zdd_breadcrumb, "recover_stale_cutover", lambda *a, **k: {"recovered": False})
    monkeypatch.setattr(zdd_breadcrumb, "read_breadcrumb", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_reap_abandoned_passive", lambda *_a, **_k: {"reaped": False})
    monkeypatch.setattr(main_mod, "_reap_superseded_coordinators", lambda *_a, **_k: None)

    captured: dict = {}
    from agent_dispatch import config

    load_config = config.load_config

    def captured_config():
        cfg = load_config()
        captured["db_path"] = cfg.db_path
        return cfg

    monkeypatch.setattr(config, "load_config", captured_config)

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
    monkeypatch.setattr("agent_dispatch.cutover_process.process_start_time", lambda _pid: None)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)
    assert exit_code == 0

    assert captured["bind"] == "10.0.0.5"
    assert captured["db_path"] == f"{install_dir}/tasks.db"
    captured["spawn_passive"](4321)
    assert len(daemon_calls) == 1
    cmd, options = daemon_calls[0]
    assert options["env"]["AGENT_DISPATCH_DB"] == captured["db_path"]
    assert "--host" in cmd
    assert cmd[cmd.index("--host") + 1] == "10.0.0.5"
