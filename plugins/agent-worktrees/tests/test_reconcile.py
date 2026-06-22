"""Tests for agent_worktrees.reconcile -- repo-configured plugin reconciliation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import reconcile

MKT = reconcile.MARKETPLACE


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """Isolate HOME and the cache dir into tmp_path.

    Returns a small namespace with ``home`` and ``repo`` paths plus helpers
    to populate installed payloads, runtime manifests, and repo settings.
    """
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    (repo / ".github" / "copilot").mkdir(parents=True)

    monkeypatch.setattr(reconcile, "_home", lambda: home)
    monkeypatch.setattr(
        reconcile.cfg, "install_dir", lambda: home / ".agent-worktrees"
    )
    # Pin POSIX semantics so the suite is deterministic regardless of the host
    # OS: these tests create scripts/install.sh payloads and assert bash argv.
    # On Windows, runtime_installer_argv() correctly prefers install.ps1 (absent
    # here), so without this pin the runtime-phase tests fail on a Windows dev
    # box while passing on Linux CI. Individual tests may still re-pin.
    monkeypatch.setattr(reconcile.platform, "system", lambda: "Linux")

    class Env:
        pass

    e = Env()
    e.home = home
    e.repo = repo

    def write_settings(enabled: dict, local: dict | None = None):
        base = repo / ".github" / "copilot"
        (base / "settings.json").write_text(
            json.dumps({"enabledPlugins": enabled}), encoding="utf-8"
        )
        if local is not None:
            (base / "settings.local.json").write_text(
                json.dumps({"enabledPlugins": local}), encoding="utf-8"
            )

    def install_payload(name: str, version: str, scope: str | None = None,
                        installer: str = "install.sh"):
        pdir = home / ".copilot" / "installed-plugins" / MKT / name
        (pdir / "scripts").mkdir(parents=True)
        manifest = {"name": name, "version": version}
        if scope is not None:
            manifest["runtimeScope"] = scope
        (pdir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        if installer:
            (pdir / "scripts" / installer).write_text("#!/bin/sh\n", encoding="utf-8")
        return pdir

    def deploy_runtime(name: str, version: str):
        rdir = home / f".{name}"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "deploy-manifest.json").write_text(
            json.dumps({"schema_version": 3, "source": {"version": version}}),
            encoding="utf-8",
        )

    def write_gate(mapping: dict[str, list[str]]):
        services = [{"name": n, "deploy_machines": m} for n, m in mapping.items()]
        doc = {"repos": {"copilot-extensions": {"services": services}}}
        import yaml
        (repo / "external-repos.yaml").write_text(
            yaml.safe_dump(doc), encoding="utf-8"
        )

    e.write_settings = write_settings
    e.install_payload = install_payload
    e.deploy_runtime = deploy_runtime
    e.write_gate = write_gate
    return e


def _services(plan: dict, phase: str | None = None) -> set[str]:
    ups = plan.get("updates", [])
    if phase:
        ups = [u for u in ups if u.get("phase") == phase]
    return {u["service"] for u in ups}


# ---------------------------------------------------------------------------
# read_enabled_plugins
# ---------------------------------------------------------------------------

def test_read_enabled_filters_marketplace_and_self(env):
    env.write_settings({
        f"agent-bridge@{MKT}": True,
        f"agent-mcp@{MKT}": True,
        f"agent-worktrees@{MKT}": True,       # self -> excluded
        f"context-handoff@{MKT}": False,      # disabled -> excluded
        "other@some-marketplace": True,       # foreign marketplace -> excluded
        "bare-name": True,                    # no marketplace -> excluded
    })
    assert reconcile.read_enabled_plugins(env.repo) == ["agent-bridge", "agent-mcp"]


def test_local_settings_override(env):
    env.write_settings(
        {f"agent-bridge@{MKT}": True, f"agent-mcp@{MKT}": True},
        local={f"agent-mcp@{MKT}": False},
    )
    assert reconcile.read_enabled_plugins(env.repo) == ["agent-bridge"]


def test_no_settings_returns_empty(env):
    assert reconcile.read_enabled_plugins(env.repo) == []


# ---------------------------------------------------------------------------
# Payload presence
# ---------------------------------------------------------------------------

def test_missing_payload_emits_install(env):
    env.write_settings({f"agent-bridge@{MKT}": True})
    plan = reconcile.build_plan(env.repo, machine="m1", cache={}, save=False)
    assert plan["action"] == "reconcile"
    pay = [u for u in plan["updates"] if u["service"] == "agent-bridge"]
    assert pay and pay[0]["argv"] == [
        "copilot", "plugin", "install", f"agent-bridge@{MKT}"
    ]
    assert pay[0]["phase"] == "payload"


# ---------------------------------------------------------------------------
# Runtime scope buckets
# ---------------------------------------------------------------------------

def test_scope_none_never_touches_runtime(env):
    env.write_settings({f"agent-mcp@{MKT}": True})
    env.install_payload("agent-mcp", "1.0.0", scope="none")
    # no runtime deployed at all
    plan = reconcile.build_plan(env.repo, machine="m1", cache={}, save=False)
    assert _services(plan, phase="runtime") == set()


def test_scope_universal_emits_runtime_on_drift(env):
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "2.0.0", scope="universal")
    env.deploy_runtime("context-handoff", "1.0.0")  # stale
    plan = reconcile.build_plan(env.repo, machine="anywhere", cache={}, save=False)
    rt = [u for u in plan["updates"]
          if u["service"] == "context-handoff" and u["phase"] == "runtime"]
    assert rt, "expected a runtime update on version drift"
    assert rt[0]["reason"] == "runtime-version-drift"
    assert rt[0]["argv"][0] == "bash" and rt[0]["argv"][-1] == "update"


def test_scope_universal_no_runtime_when_current(env):
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "2.0.0", scope="universal")
    env.deploy_runtime("context-handoff", "2.0.0")  # matches payload
    # cache marks payload recently refreshed so no payload-refresh either
    cache = {"plugins": {"context-handoff": {"last_payload_update": 1_000_000.0}}}
    plan = reconcile.build_plan(
        env.repo, machine="m1", now=1_000_100.0, cache=cache, save=False
    )
    assert plan["action"] == "continue"


def test_runtime_missing_emits_with_reason(env):
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "2.0.0", scope="universal")
    # no runtime manifest deployed
    plan = reconcile.build_plan(env.repo, machine="m1", cache={}, save=False)
    rt = [u for u in plan["updates"] if u["phase"] == "runtime"]
    assert rt and rt[0]["reason"] == "runtime-missing"


# ---------------------------------------------------------------------------
# Machine gating
# ---------------------------------------------------------------------------

def test_machine_gated_allowed_machine(env):
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "3.0.0", scope="machine-gated")
    env.deploy_runtime("agent-bridge", "2.0.0")  # drift
    env.write_gate({"agent-bridge": ["lambda-core", "borealis"]})
    plan = reconcile.build_plan(
        env.repo, machine="lambda-core", cache={}, save=False
    )
    assert _services(plan, phase="runtime") == {"agent-bridge"}


def test_machine_gated_disallowed_machine(env):
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "3.0.0", scope="machine-gated")
    env.deploy_runtime("agent-bridge", "2.0.0")  # drift, but wrong machine
    env.write_gate({"agent-bridge": ["lambda-core", "borealis"]})
    plan = reconcile.build_plan(
        env.repo, machine="tmichon-book2", cache={}, save=False
    )
    assert _services(plan, phase="runtime") == set()


def test_machine_gated_no_gate_info_skips_runtime(env):
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "3.0.0", scope="machine-gated")
    env.deploy_runtime("agent-bridge", "2.0.0")  # drift
    # no external-repos.yaml written -> empty gate -> safe skip
    plan = reconcile.build_plan(env.repo, machine="lambda-core", cache={}, save=False)
    assert _services(plan, phase="runtime") == set()


def test_invalid_scope_treated_as_none(env):
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "3.0.0", scope="bogus")
    env.deploy_runtime("agent-bridge", "2.0.0")
    env.write_gate({"agent-bridge": ["lambda-core"]})
    plan = reconcile.build_plan(env.repo, machine="lambda-core", cache={}, save=False)
    assert _services(plan, phase="runtime") == set()


# ---------------------------------------------------------------------------
# Payload-refresh throttle
# ---------------------------------------------------------------------------

def test_payload_refresh_throttled_when_recent(env):
    env.write_settings({f"agent-mcp@{MKT}": True})
    env.install_payload("agent-mcp", "1.0.0", scope="none")
    cache = {"plugins": {"agent-mcp": {"last_payload_update": 1_000_000.0}}}
    plan = reconcile.build_plan(
        env.repo, machine="m1", now=1_000_100.0, cache=cache, save=False
    )
    assert plan["action"] == "continue"


def test_payload_refresh_due_after_interval(env):
    env.write_settings({f"agent-mcp@{MKT}": True})
    env.install_payload("agent-mcp", "1.0.0", scope="none")
    cache = {"plugins": {"agent-mcp": {"last_payload_update": 0.0}}}
    now = 10 * 24 * 3600.0
    plan = reconcile.build_plan(
        env.repo, machine="m1", now=now, cache=cache, save=False
    )
    assert _services(plan, phase="payload") == {"agent-mcp"}
    assert cache["plugins"]["agent-mcp"]["last_payload_update"] == now


# ---------------------------------------------------------------------------
# runtime_installer_argv
# ---------------------------------------------------------------------------

def test_installer_argv_prefers_install_then_init(env, monkeypatch):
    monkeypatch.setattr(reconcile.platform, "system", lambda: "Linux")
    pdir = env.install_payload("agent-bridge", "1.0.0", installer="install.sh")
    _cmd, argv = reconcile.runtime_installer_argv(pdir)
    assert argv == ["bash", str(pdir / "scripts" / "install.sh"), "update"]

    pdir2 = env.install_payload("agent-mcp", "1.0.0", installer="init.sh")
    _cmd2, argv2 = reconcile.runtime_installer_argv(pdir2)
    assert argv2 == ["bash", str(pdir2 / "scripts" / "init.sh")]


# ---------------------------------------------------------------------------
# Gate parsing
# ---------------------------------------------------------------------------

def test_load_runtime_gate_parses_deploy_machines(env):
    env.write_gate({
        "agent-bridge": ["lambda-core", "borealis"],
        "agent-codespaces": ["tmichon-book2"],
    })
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-bridge"] == {"lambda-core", "borealis"}
    assert gate["agent-codespaces"] == {"tmichon-book2"}


# ---------------------------------------------------------------------------
# Plan ordering: payload precedes runtime for the same plugin
# ---------------------------------------------------------------------------

def test_payload_before_runtime_ordering(env):
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "2.0.0", scope="universal")
    env.deploy_runtime("context-handoff", "1.0.0")
    cache = {"plugins": {"context-handoff": {"last_payload_update": 0.0}}}
    now = 10 * 24 * 3600.0
    plan = reconcile.build_plan(
        env.repo, machine="m1", now=now, cache=cache, save=False
    )
    phases = [u["phase"] for u in plan["updates"]
              if u["service"] == "context-handoff"]
    assert phases == ["payload", "runtime"]
