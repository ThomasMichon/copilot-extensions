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
    # Isolate the runtime gate: load_runtime_gate() falls back to the real
    # aperture-labs external-repos.yaml (resolved via the repos registry) when
    # the test repo lacks one. Pin resolve_path to None so the gate is derived
    # solely from the test repo -- otherwise these tests are non-hermetic and
    # fail on any host where the real manifest gates a tested plugin.
    from agent_worktrees import repos as _repos_mod
    monkeypatch.setattr(_repos_mod, "resolve_path", lambda name: None)
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

    def deploy_running(name: str, version: str, pid: int = 4321):
        rdir = home / f".{name}"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "running-version.json").write_text(
            json.dumps({
                "version": version, "pid": pid,
                "started_at": "2026-01-01T00:00:00+00:00",
            }),
            encoding="utf-8",
        )

    def write_gate(mapping: dict[str, list[str]]):
        services = [{"name": n, "deploy_machines": m} for n, m in mapping.items()]
        doc = {"repos": {"copilot-extensions": {"services": services}}}
        import yaml
        (repo / "external-repos.yaml").write_text(
            yaml.safe_dump(doc), encoding="utf-8"
        )

    def write_gate_services(mapping: dict[str, list[str]]):
        """Write the native ``services.yaml`` gate schema (top-level ``plugins:``)."""
        plugins = [{"name": n, "deploy_machines": m} for n, m in mapping.items()]
        import yaml
        (repo / "services.yaml").write_text(
            yaml.safe_dump({"plugins": plugins}), encoding="utf-8"
        )

    e.write_settings = write_settings
    e.install_payload = install_payload
    e.deploy_runtime = deploy_runtime
    e.deploy_running = deploy_running
    e.write_gate = write_gate
    e.write_gate_services = write_gate_services
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
        env.repo, machine="host-book2", cache={}, save=False
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
        "agent-codespaces": ["host-book2"],
    })
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-bridge"] == {"lambda-core", "borealis"}
    assert gate["agent-codespaces"] == {"host-book2"}


def test_load_runtime_gate_parses_plugins_schema(env):
    # Native services.yaml shape: a top-level ``plugins:`` list.
    env.write_gate_services({
        "agent-mcp": ["lambda-core", "borealis"],
        "agent-dispatch": ["host-book2"],
    })
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-mcp"] == {"lambda-core", "borealis"}
    assert gate["agent-dispatch"] == {"host-book2"}


def test_load_runtime_gate_prefers_services_over_external(env):
    # Both files present (migration window): services.yaml must win.
    env.write_gate({"agent-mcp": ["legacy-host"]})           # external-repos.yaml
    env.write_gate_services({"agent-mcp": ["new-host"]})     # services.yaml
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-mcp"] == {"new-host"}


def test_load_runtime_gate_falls_back_to_external_when_no_services(env):
    # Only the legacy file exists -> still parsed (back-compat).
    env.write_gate({"agent-mcp": ["legacy-host"]})
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-mcp"] == {"legacy-host"}


def test_gate_manifest_override_pins_single_filename(env, monkeypatch):
    # An explicit WORKTREE_GATE_MANIFEST pins one name; the other is ignored.
    monkeypatch.setattr(reconcile, "GATE_MANIFESTS", ("services.yaml",))
    env.write_gate({"agent-mcp": ["legacy-host"]})       # external-repos.yaml: ignored
    env.write_gate_services({"agent-mcp": ["new-host"]})  # services.yaml: read
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-mcp"] == {"new-host"}

    monkeypatch.setattr(reconcile, "GATE_MANIFESTS", ("external-repos.yaml",))
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-mcp"] == {"legacy-host"}


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


# ---------------------------------------------------------------------------
# Running-version awareness (dotfiles #533): a live daemon can lag its installed
# plugin even when the on-disk deploy-manifest already matches the payload.
# ---------------------------------------------------------------------------

def _runtime_updates(plan):
    return [u for u in plan.get("updates", []) if u.get("phase") == "runtime"]


def test_running_drift_emits_even_when_ondisk_matches(env, monkeypatch):
    """On-disk == payload but the *live* process lags -> redeploy anyway."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "2.0.0", scope="universal")
    env.deploy_runtime("context-handoff", "2.0.0")   # on-disk looks current
    env.deploy_running("context-handoff", "1.0.0")   # live process is stale
    plan = reconcile.build_plan(env.repo, machine="anywhere", cache={}, save=False)
    rt = _runtime_updates(plan)
    assert len(rt) == 1
    assert rt[0]["reason"] == "runtime-running-drift"
    assert rt[0]["from_version"] == "1.0.0"
    assert rt[0]["to_version"] == "2.0.0"


def test_running_current_suppresses_ondisk_drift(env, monkeypatch):
    """The live process is already current -> no redeploy even if on-disk is stale."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "2.0.0", scope="universal")
    env.deploy_runtime("context-handoff", "1.0.0")   # stale on-disk manifest
    env.deploy_running("context-handoff", "2.0.0")   # live process is current
    plan = reconcile.build_plan(env.repo, machine="anywhere", cache={}, save=False)
    assert _runtime_updates(plan) == []


def test_running_dead_pid_falls_back_to_ondisk(env, monkeypatch):
    """A stale running-version.json (dead pid) is ignored -> on-disk decides."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: False)
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "2.0.0", scope="universal")
    env.deploy_runtime("context-handoff", "2.0.0")   # on-disk current
    env.deploy_running("context-handoff", "1.0.0")   # but pid is dead -> ignored
    plan = reconcile.build_plan(env.repo, machine="anywhere", cache={}, save=False)
    assert _runtime_updates(plan) == []


def test_runtime_running_version_pid_and_content(tmp_path, monkeypatch):
    """runtime_running_version: live pid -> version; dead/absent/malformed -> None."""
    home = tmp_path / "home"
    (home / ".svc").mkdir(parents=True)
    monkeypatch.setattr(reconcile, "_home", lambda: home)
    rvf = home / ".svc" / "running-version.json"

    # absent
    assert reconcile.runtime_running_version("svc") is None
    # live pid -> version
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    rvf.write_text(json.dumps({"version": "9.9.9", "pid": 1234}), encoding="utf-8")
    assert reconcile.runtime_running_version("svc") == "9.9.9"
    # dead pid -> None
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: False)
    assert reconcile.runtime_running_version("svc") is None
    # malformed (no version) -> None even if alive
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    rvf.write_text(json.dumps({"pid": 1234}), encoding="utf-8")
    assert reconcile.runtime_running_version("svc") is None


def test_pid_alive_basic():
    """_pid_alive is truthy for our own live pid, falsy for invalid inputs.

    Runs the real per-OS branch (no platform pin): safe on both -- Windows uses
    OpenProcess, POSIX uses os.kill(pid, 0)."""
    import os

    assert reconcile._pid_alive(os.getpid()) is True
    assert reconcile._pid_alive(0) is False
    assert reconcile._pid_alive(-1) is False
    assert reconcile._pid_alive("nope") is False  # type: ignore[arg-type]


def test_versions_equal_tolerates_pep440_spelling():
    """importlib's `0.4.0.dev176` and plugin.json's `0.4.0-dev176` are equal."""
    assert reconcile._versions_equal("0.4.0.dev176", "0.4.0-dev176")
    assert reconcile._versions_equal("1.5.3-dev261", "1.5.3-dev261")
    assert not reconcile._versions_equal("0.4.0-dev176", "0.4.0-dev177")
    assert not reconcile._versions_equal(None, "1.0.0")
    assert not reconcile._versions_equal("1.0.0", None)


def test_running_normalized_spelling_is_not_false_drift(env, monkeypatch):
    """A daemon whose importlib version is PEP440-normalized must not thrash.

    Running `2.0.0.dev1` (importlib) vs payload `2.0.0-dev1` (plugin.json) is the
    same version -> no redeploy (regression for the agent-bridge marker, #533)."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "2.0.0-dev1", scope="universal")
    env.deploy_runtime("context-handoff", "2.0.0-dev1")
    env.deploy_running("context-handoff", "2.0.0.dev1")  # importlib spelling
    plan = reconcile.build_plan(env.repo, machine="anywhere", cache={}, save=False)
    assert _runtime_updates(plan) == []


def test_zero_downtime_appends_flag(tmp_path, monkeypatch):
    """A plugin declaring zeroDowntimeUpdate carries -ZeroDowntime into its
    reconcile-driven install.ps1 update (Windows); absence -> no flag (#533 B)."""
    monkeypatch.setattr(reconcile.platform, "system", lambda: "Windows")
    pdir = tmp_path / "plug"
    (pdir / "scripts").mkdir(parents=True)
    (pdir / "scripts" / "install.ps1").write_text("", encoding="utf-8")

    # No zeroDowntimeUpdate -> plain `update`, no flag.
    (pdir / "plugin.json").write_text(
        json.dumps({"name": "x", "version": "1"}), encoding="utf-8"
    )
    _, argv = reconcile.runtime_installer_argv(pdir)
    assert "-ZeroDowntime" not in argv
    assert argv[:3] == ["pwsh", "-File", str(pdir / "scripts" / "install.ps1")]

    # zeroDowntimeUpdate: true -> the flag is appended after `update`.
    (pdir / "plugin.json").write_text(
        json.dumps({"name": "x", "version": "1", "zeroDowntimeUpdate": True}),
        encoding="utf-8",
    )
    _, argv = reconcile.runtime_installer_argv(pdir)
    assert argv[-2:] == ["update", "-ZeroDowntime"]



# ---------------------------------------------------------------------------
# Part C (#533): running_version_lag -- read-only mid-session lag diagnostic.
# ---------------------------------------------------------------------------

def test_running_version_lag_reports_live_laggard(env, monkeypatch):
    """A live daemon serving older code than the installed payload is reported."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    env.write_settings({f"agent-bridge@{MKT}": True, f"agent-mcp@{MKT}": True})
    env.install_payload("agent-bridge", "0.4.0-dev10", scope="universal")
    env.install_payload("agent-mcp", "1.0.0", scope="universal")
    env.deploy_running("agent-bridge", "0.4.0-dev7")   # lagging
    env.deploy_running("agent-mcp", "1.0.0")           # current -> no lag
    lags = reconcile.running_version_lag(env.repo)
    assert len(lags) == 1
    assert lags[0]["service"] == "agent-bridge"
    assert lags[0]["running"] == "0.4.0-dev7"
    assert lags[0]["payload"] == "0.4.0-dev10"


def test_running_version_lag_ignores_dead_and_absent(env, monkeypatch):
    """No live process (dead pid or no marker) -> nothing to nudge about."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: False)
    env.write_settings({f"agent-bridge@{MKT}": True, f"agent-mcp@{MKT}": True})
    env.install_payload("agent-bridge", "0.4.0-dev10", scope="universal")
    env.install_payload("agent-mcp", "1.0.0", scope="universal")
    env.deploy_running("agent-bridge", "0.4.0-dev7")   # dead pid -> ignored
    # agent-mcp has no running-version.json at all -> ignored
    assert reconcile.running_version_lag(env.repo) == []


def test_running_version_lag_no_false_drift_on_pep440(env, monkeypatch):
    """importlib `0.4.0.dev9` vs payload `0.4.0-dev9` is not a lag (PEP 440)."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "0.4.0-dev9", scope="universal")
    env.deploy_running("agent-bridge", "0.4.0.dev9")   # importlib spelling
    assert reconcile.running_version_lag(env.repo) == []


def test_running_version_lag_empty_without_settings(env):
    """No enabled plugins -> empty, never raises."""
    assert reconcile.running_version_lag(env.repo) == []
