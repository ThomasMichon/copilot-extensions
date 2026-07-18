"""Tests for the agent-vault extension seam.

Each of the hook categories is exercised at its wiring point:
- unlock-source provider   -> service.VaultService.ensure_unlocked
- registrable action       -> service.VaultService.handle_request
- client transport         -> cli.send_command
- config source            -> config.resolve_context
- CLI command              -> cli.main() subparser wiring

Plus the registry ordering and the env-var loader.
"""

from __future__ import annotations

import sys

import pytest

from agent_vault import cli, config
from agent_vault import extensions as ext
from agent_vault.extensions import (
    ActionContext,
    ExtensionRegistry,
    TransportContext,
    UnlockContext,
    load_extensions,
    reset_registry,
)
from agent_vault.service import VaultService


@pytest.fixture
def registry():
    """Install a fresh, pre-loaded registry as the process singleton."""
    reg = ExtensionRegistry()
    reg._loaded = True  # prevent load_extensions from re-discovering
    ext._REGISTRY = reg
    yield reg
    reset_registry()


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Neutralize ambient vault configuration so resolve_context is deterministic."""
    for var in ("KPDB", "AGENT_VAULT", "VAULT_GROUP", "AGENT_VAULT_PORT", "AGENT_VAULT_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "no-config.json"))
    # Isolate endpoint discovery from the real machine's runtime dir, so tests
    # never read a live/deployed rendezvous file (which would tag transports
    # "discovered-*"). The dir is intentionally left empty/absent.
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(tmp_path / "run"))
    return tmp_path


# ---------------------------------------------------------------------------
# Registry basics
# ---------------------------------------------------------------------------


def test_registry_orders_by_priority_then_registration(registry):
    order = []
    registry.register_unlock_provider(lambda ctx: order.append("b") or None, priority=50, name="b")
    registry.register_unlock_provider(lambda ctx: order.append("a") or None, priority=10, name="a")
    registry.register_unlock_provider(lambda ctx: order.append("c") or None, priority=50, name="c")
    names = [r.name for r in registry.unlock_providers]
    assert names == ["a", "b", "c"]  # priority asc, then registration order


def test_register_action_requires_name(registry):
    with pytest.raises(ValueError):
        registry.register_action("", lambda s, r, c: {"ok": True})


def test_env_var_loader_discovers_module(monkeypatch, tmp_path):
    mod = tmp_path / "fake_vault_ext.py"
    mod.write_text(
        "def register(registry):\n"
        "    registry.register_action('probe', lambda s, r, c: {'ok': True})\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("AGENT_VAULT_EXTENSIONS", "fake_vault_ext")
    reset_registry()
    try:
        reg = load_extensions(ExtensionRegistry())
        assert reg.action("probe") is not None
    finally:
        reset_registry()
        sys.modules.pop("fake_vault_ext", None)


def test_broken_extension_is_skipped(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_EXTENSIONS", "definitely_not_a_real_module_xyz")
    reset_registry()
    try:
        reg = load_extensions(ExtensionRegistry())  # must not raise
        assert reg.action("probe") is None
    finally:
        reset_registry()


# ---------------------------------------------------------------------------
# Unlock-source provider -> ensure_unlocked
# ---------------------------------------------------------------------------


def test_unlock_provider_unlocks_before_prompt(registry, monkeypatch, tmp_path):
    kpdb = tmp_path / "vault.kdbx"
    kpdb.write_text("x", encoding="utf-8")
    kpdb = str(kpdb)

    svc = VaultService()
    stored = {}
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: db in stored)
    monkeypatch.setattr(svc.cli, "verify_password", lambda db, pw: pw == "s3cret")
    monkeypatch.setattr(svc.cli, "set_password", lambda db, pw: stored.__setitem__(db, pw))

    import agent_vault.service as service_mod

    def _fail_prompt(_msg):
        raise AssertionError("interactive prompt must not run when a provider unlocks")

    monkeypatch.setattr(service_mod, "prompt_password", _fail_prompt)

    seen = {}

    def broker(ctx):
        seen["ctx"] = ctx
        return "s3cret"

    registry.register_unlock_provider(broker, name="broker")

    assert svc.ensure_unlocked(kpdb, vault_name="work") is True
    assert stored[kpdb] == "s3cret"
    assert isinstance(seen["ctx"], UnlockContext)
    assert seen["ctx"].vault_name == "work"


def test_wrong_provider_falls_through_to_next(registry, monkeypatch, tmp_path):
    kpdb = tmp_path / "vault.kdbx"
    kpdb.write_text("x", encoding="utf-8")
    kpdb = str(kpdb)

    svc = VaultService()
    stored = {}
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: db in stored)
    monkeypatch.setattr(svc.cli, "verify_password", lambda db, pw: pw == "good")
    monkeypatch.setattr(svc.cli, "set_password", lambda db, pw: stored.__setitem__(db, pw))

    registry.register_unlock_provider(lambda ctx: "wrong", priority=10, name="bad")
    registry.register_unlock_provider(lambda ctx: "good", priority=20, name="ok")

    assert svc.ensure_unlocked(kpdb) is True
    assert stored[kpdb] == "good"


# ---------------------------------------------------------------------------
# Registrable protocol action -> handle_request
# ---------------------------------------------------------------------------


def test_registered_action_handles_request(registry, monkeypatch):
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: True)

    captured = {}

    def echo(service, request, ctx):
        captured["ctx"] = ctx
        return {"ok": True, "echo": request.get("msg")}

    registry.register_action("echo", echo)
    resp = svc.handle_request({"action": "echo", "msg": "hi", "kpdb": "x.kdbx"})
    assert resp == {"ok": True, "echo": "hi"}
    assert isinstance(captured["ctx"], ActionContext)


def test_unknown_action_still_reported(registry):
    svc = VaultService()
    resp = svc.handle_request({"action": "nope", "kpdb": "x.kdbx"})
    assert resp["ok"] is False
    assert "Unknown action" in resp["error"]


def test_action_exception_is_contained(registry, monkeypatch):
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: True)

    def boom(service, request, ctx):
        raise RuntimeError("kaboom")

    registry.register_action("boom", boom)
    resp = svc.handle_request({"action": "boom", "kpdb": "x.kdbx"})
    assert resp["ok"] is False
    assert "boom" in resp["error"]


# ---------------------------------------------------------------------------
# Client transport -> send_command
# ---------------------------------------------------------------------------


def test_transport_fallback_used_when_builtins_fail(registry, monkeypatch, clean_env):
    monkeypatch.setattr(cli, "_send_socket", lambda req, timeout=5.0: None)
    monkeypatch.setattr(cli, "_send_tcp", lambda req, host, port, timeout: None)

    def tunnel(request, timeout, ctx):
        assert isinstance(ctx, TransportContext)
        return {"ok": True, "value": "via-tunnel"}

    registry.register_transport(tunnel, name="tunnel")
    result = cli.send_command({"action": "get", "entry": "x"})
    assert result["ok"] is True
    assert result["value"] == "via-tunnel"
    assert result["_transport"] == "ext:tunnel"


def test_transport_not_consulted_when_builtin_succeeds(registry, monkeypatch, clean_env):
    monkeypatch.setattr(cli, "_send_tcp", lambda req, host, port, timeout: {"ok": True})
    called = {"n": 0}

    def never(request, timeout, ctx):
        called["n"] += 1
        return {"ok": True}

    registry.register_transport(never, name="never")
    result = cli.send_command({"action": "ping"})
    assert result["ok"] is True
    assert called["n"] == 0


def test_before_builtin_transport_wins_over_builtins(registry, monkeypatch, clean_env):
    # A built-in TCP that would succeed must NOT be reached when a before_builtin
    # transport handles the request.
    monkeypatch.setattr(cli, "_send_socket", lambda req, timeout=5.0: None)
    tcp_calls = {"n": 0}

    def _tcp(req, host, port, timeout):
        tcp_calls["n"] += 1
        return {"ok": True, "value": "via-local"}

    monkeypatch.setattr(cli, "_send_tcp", _tcp)
    registry.register_transport(
        lambda request, timeout, ctx: {"ok": True, "value": "via-tunnel"},
        name="tunnel", before_builtin=True,
    )
    result = cli.send_command({"action": "get", "entry": "x"})
    assert result["value"] == "via-tunnel"
    assert result["_transport"] == "ext:tunnel"
    assert tcp_calls["n"] == 0  # built-in TCP never consulted


def test_before_builtin_none_falls_through_to_builtins(registry, monkeypatch, clean_env):
    monkeypatch.setattr(cli, "_send_socket", lambda req, timeout=5.0: None)
    monkeypatch.setattr(
        cli, "_send_tcp",
        lambda req, host, port, timeout: {"ok": True, "value": "local"},
    )
    # before_builtin transport declines (not applicable) -> built-ins run.
    registry.register_transport(
        lambda request, timeout, ctx: None, name="inactive", before_builtin=True,
    )
    result = cli.send_command({"action": "get", "entry": "x"})
    assert result["value"] == "local"
    assert result["_transport"] == "tcp"


# ---------------------------------------------------------------------------
# Config source -> resolve_context
# ---------------------------------------------------------------------------


def test_config_source_contributes_kpdb(registry, clean_env):
    registry.register_config_source(
        lambda cwd: {"kpdb": "machine/vault.kdbx", "group": "Machine"}, name="machine-map"
    )
    ctx = config.resolve_context(cwd=str(clean_env))
    assert ctx.sources["kpdb"] == "ext"
    assert ctx.kpdb.replace("\\", "/").endswith("machine/vault.kdbx")
    assert ctx.group == "Machine"
    assert ctx.sources["group"] == "ext"


def test_repo_config_outranks_config_source(registry, clean_env):
    (clean_env / config.REPO_CONFIG_NAME).write_text(
        '{"kpdb": "repo-vault.kdbx"}', encoding="utf-8"
    )
    registry.register_config_source(lambda cwd: {"kpdb": "machine-vault.kdbx"}, name="m")
    ctx = config.resolve_context(cwd=str(clean_env))
    assert ctx.sources["kpdb"] == "repo"
    assert ctx.kpdb.replace("\\", "/").endswith("repo-vault.kdbx")


# ---------------------------------------------------------------------------
# CLI command -> main() subparser wiring
# ---------------------------------------------------------------------------


def test_registered_cli_command_adds_verb(registry, monkeypatch):
    ran = {}

    def build(subparsers):
        p = subparsers.add_parser("probe", help="facility probe")
        p.add_argument("target")

        def handler(args):
            ran["target"] = args.target
            return 7

        p.set_defaults(func=handler)

    registry.register_cli_command(build, name="probe")
    monkeypatch.setattr(sys, "argv", ["agent-vault", "probe", "db1"])
    rc = cli.main()
    assert rc == 7
    assert ran["target"] == "db1"


def test_cli_commands_apply_in_priority_order(registry):
    import argparse

    built = []
    registry.register_cli_command(
        lambda sp: built.append("b") or sp.add_parser("vb"), priority=50, name="b"
    )
    registry.register_cli_command(
        lambda sp: built.append("a") or sp.add_parser("va"), priority=10, name="a"
    )
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    registry.apply_cli_commands(sub)
    assert built == ["a", "b"]  # priority asc


def test_broken_cli_command_builder_is_skipped(registry):
    import argparse

    good = {}

    def boom(subparsers):
        raise RuntimeError("bad builder")

    def build_good(subparsers):
        p = subparsers.add_parser("probe2")
        p.set_defaults(func=lambda args: 0)
        good["built"] = True

    registry.register_cli_command(boom, name="boom")
    registry.register_cli_command(build_good, name="good")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    registry.apply_cli_commands(sub)  # must not raise
    assert good["built"] is True
    args = parser.parse_args(["probe2"])
    assert args.func(args) == 0
