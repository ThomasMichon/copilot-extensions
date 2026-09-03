"""Tests for agent_worktrees.reconcile -- repo-configured plugin reconciliation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import reconcile

MKT = reconcile.MARKETPLACE
REPO = Path(__file__).resolve().parents[3]
INSTALLATION_CONTEXT = (
    REPO / "libs" / "installation-context" / "installation_context.py"
)
INSTALLATION_CONTEXT_PS1 = (
    REPO / "libs" / "installation-context" / "installation-context.ps1"
)


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
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)

    monkeypatch.setattr(reconcile, "_home", lambda: home)
    monkeypatch.setattr(
        reconcile.cfg, "install_dir", lambda: home / ".agent-worktrees"
    )
    # Isolate the runtime gate: load_runtime_gate() falls back to the real
    # test-chamber external-repos.yaml (resolved via the repos registry) when
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
    e.cell_homes = []

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
        if name in {"agent-machines", "agent-index", "agent-worktrees"}:
            helper_py = (
                pdir
                / "scripts"
                / "installation-context"
                / "installation_context.py"
            )
            helper_py.parent.mkdir(parents=True, exist_ok=True)
            if not helper_py.is_file():
                shutil.copyfile(INSTALLATION_CONTEXT, helper_py)
            helper_ps1 = helper_py.with_name("installation-context.ps1")
            if not helper_ps1.is_file():
                shutil.copyfile(INSTALLATION_CONTEXT_PS1, helper_ps1)
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

    def select_context(
        name: str,
        payload_dir: Path,
        payload_version: str,
        deployed_version: str | None,
        *,
        marketplace_key: str = MKT,
    ) -> tuple[Path, Path]:
        helper = (
            payload_dir
            / "scripts"
            / "installation-context"
            / "installation_context.py"
        )
        helper.parent.mkdir(parents=True, exist_ok=True)
        if not helper.is_file():
            shutil.copyfile(INSTALLATION_CONTEXT, helper)
        helper_ps1 = helper.with_name("installation-context.ps1")
        if not helper_ps1.is_file():
            shutil.copyfile(INSTALLATION_CONTEXT_PS1, helper_ps1)
        durable = e.home / ".copilot-extensions"
        namespace_generation = 0
        if marketplace_key == MKT:
            namespace = (
                durable / "marketplaces" / reconcile.CORE_MARKETPLACE_ID
                / "namespace.json"
            )
            if namespace.is_file():
                namespace_generation = json.loads(
                    namespace.read_text(encoding="utf-8")
                )["generation"]
        if os.name == "nt":
            command = [
                "pwsh", "-NoProfile", "-File", str(helper_ps1), "stamp",
                "-SourceJson", json.dumps({
                    "source": "github",
                    "repo": (
                        "ThomasMichon/copilot-extensions"
                        if marketplace_key == MKT
                        else "Example-Org/Example-Marketplace.git"
                    ),
                }),
                "-MarketplaceKey", marketplace_key,
                "-PluginId", name,
                "-PayloadRoot", str(payload_dir),
                "-PayloadVersion", payload_version,
                "-PayloadOrigin", "explicit",
                "-ExpectedNamespaceGeneration", str(namespace_generation),
                "-ExpectedInstallGeneration", "0",
                "-DurableHome", str(durable),
            ]
        else:
            command = [
                sys.executable,
                str(helper),
                "stamp",
                "--source-json",
                json.dumps({
                    "source": "github",
                    "repo": (
                        "ThomasMichon/copilot-extensions"
                        if marketplace_key == MKT
                        else "Example-Org/Example-Marketplace.git"
                    ),
                }),
                "--marketplace-key",
                marketplace_key,
                "--plugin-id",
                name,
                "--payload-root",
                str(payload_dir),
                "--payload-version",
                payload_version,
                "--payload-origin",
                "explicit",
                "--expected-namespace-generation",
                str(namespace_generation),
                "--expected-install-generation",
                "0",
                "--durable-home",
                str(durable),
            ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise AssertionError(
                f"installation-context stamp failed: {result.stderr or result.stdout}"
            )
        context = json.loads(result.stdout)
        plugin_root = Path(context["pluginRoot"])
        if deployed_version is not None:
            (plugin_root / "deploy-manifest.json").write_text(
                json.dumps({
                    "schema_version": 3,
                    "source": {"version": deployed_version},
                }),
                encoding="utf-8",
            )
        monkeypatch.setenv(
            "COPILOT_EXTENSIONS_CONTEXT",
            str(context["installReceipt"]),
        )
        return Path(context["installReceipt"]), plugin_root

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
    e.select_context = select_context
    e.write_gate = write_gate
    e.write_gate_services = write_gate_services
    yield e
    for cell_home in e.cell_homes:
        shutil.rmtree(cell_home, ignore_errors=True)


def _services(plan: dict, phase: str | None = None) -> set[str]:
    ups = plan.get("updates", [])
    if phase:
        ups = [u for u in ups if u.get("phase") == phase]
    return {u["service"] for u in ups}


def _mode_resolution(
    env,
    name: str,
    *,
    status: str,
    reason: str,
    actual_mode: str | None,
    desired_mode: str | None,
    allow_mutation: bool,
    context: Path | None = None,
    runtime_root: Path | None = None,
    marketplace_id: str | None = None,
) -> dict:
    if runtime_root is None and actual_mode == "legacy":
        runtime_root = env.home / f".{name}"
    if marketplace_id is None and status != "provenance-blocked":
        marketplace_id = reconcile.CORE_MARKETPLACE_ID
    return {
        "schema": "copilot-extensions.installation-resolution",
        "version": 1,
        "marketplaceId": marketplace_id,
        "pluginId": name,
        "desiredMode": desired_mode,
        "actualMode": actual_mode,
        "status": status,
        "runtimeRoot": str(runtime_root) if runtime_root is not None else None,
        "context": str(context) if context is not None else None,
        "reason": reason,
        "allowMutation": allow_mutation,
        "probeReason": reason,
        "policy": {
            "state": "missing",
            "enabled": desired_mode == "namespaced",
            "reason": (
                "policy-default-false"
                if desired_mode == "legacy"
                else "policy-global-true"
            ),
        },
        "legacy": {
            "root": str(env.home / f".{name}"),
            "tombstone": None,
            "disposition": "active",
        },
    }


def test_same_path_resolves_symlink_alias(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    assert reconcile._same_path(alias, target)


def test_installation_mode_helper_failure_includes_bounded_detail(
    env, monkeypatch
):
    monkeypatch.setattr(
        reconcile.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=7,
            stdout="fallback detail",
            stderr="specific policy failure\nwith context",
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"installation-mode resolver failed for agent-machines "
            r"\(exit 7\): specific policy failure with context"
        ),
    ):
        reconcile._run_installation_mode_helper(
            "agent-machines",
            env.repo / "payload",
            INSTALLATION_CONTEXT,
            env.home / ".agent-machines",
            context=None,
        )


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
# read_user_enabled_plugins (#653)
# ---------------------------------------------------------------------------

def test_read_user_enabled_filters_and_local_override(tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    home.mkdir()
    monkeypatch.setattr(reconcile, "_copilot_home", lambda: home)
    (home / "settings.json").write_text(json.dumps({"enabledPlugins": {
        f"efforts@{MKT}": True,
        f"visions@{MKT}": True,
        f"agent-worktrees@{MKT}": True,      # self -> excluded
        f"context-handoff@{MKT}": False,     # disabled -> excluded
        "other@some-marketplace": True,      # foreign marketplace -> excluded
        "bare-name": True,                   # no marketplace -> excluded
    }}), encoding="utf-8")
    # settings.local.json overrides base within the convention (disables visions).
    (home / "settings.local.json").write_text(json.dumps({"enabledPlugins": {
        f"visions@{MKT}": False,
    }}), encoding="utf-8")
    assert reconcile.read_user_enabled_plugins() == ["efforts"]


def test_read_user_enabled_no_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "_copilot_home", lambda: tmp_path / "absent")
    assert reconcile.read_user_enabled_plugins() == []


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        '{"enabledPlugins":[]}',
        f'{{"enabledPlugins":{{"efforts@{MKT}":"true"}}}}',
    ],
)
def test_read_user_enabled_malformed_settings_fail_closed(
    tmp_path,
    monkeypatch,
    content,
):
    home = tmp_path / "copilot-home"
    home.mkdir()
    (home / "settings.json").write_text(content, encoding="utf-8")
    monkeypatch.setattr(reconcile, "_copilot_home", lambda: home)

    with pytest.raises(ValueError):
        reconcile.read_user_enabled_plugins()


def test_read_installed_plugins_uses_inventory_not_activation(tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    home.mkdir()
    monkeypatch.setattr(reconcile, "_copilot_home", lambda: home)
    (home / "config.json").write_text(
        json.dumps(
            {
                "installedPlugins": [
                    {
                        "name": "context-handoff",
                        "marketplace": MKT,
                        "enabled": False,
                    },
                    {"name": f"efforts@{MKT}", "enabled": False},
                    {"name": "other", "marketplace": "elsewhere"},
                    {"name": f"agent-worktrees@{MKT}", "enabled": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert reconcile.read_installed_plugins() == ["context-handoff", "efforts"]


# ---------------------------------------------------------------------------
# Payload presence
# ---------------------------------------------------------------------------

def test_missing_payload_emits_install(env):
    env.write_settings({f"agent-bridge@{MKT}": True})
    plan = reconcile.build_plan(env.repo, machine="m1", cache={}, save=False,
                                include_payload_refresh=True)
    assert plan["action"] == "reconcile"
    pay = [u for u in plan["updates"] if u["service"] == "agent-bridge"]
    assert pay and pay[0]["argv"] == [
        "copilot", "plugin", "install", f"agent-bridge@{MKT}"
    ]
    assert pay[0]["phase"] == "payload"


def test_missing_payload_suppressed_by_default(env):
    """Programmatic default is pull-free: a missing payload emits NO install
    (installing is a marketplace pull -- Picker/operator update flow only, #1393)."""
    env.write_settings({f"agent-bridge@{MKT}": True})
    plan = reconcile.build_plan(env.repo, machine="m1", cache={}, save=False)
    assert _services(plan, phase="payload") == set()


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
    env.write_gate({"agent-bridge": ["anomalous-potato", "emancipation-cube"]})
    plan = reconcile.build_plan(
        env.repo, machine="anomalous-potato", cache={}, save=False
    )
    assert _services(plan, phase="runtime") == {"agent-bridge"}


def test_machine_gated_disallowed_machine(env):
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "3.0.0", scope="machine-gated")
    env.deploy_runtime("agent-bridge", "2.0.0")  # drift, but wrong machine
    env.write_gate({"agent-bridge": ["anomalous-potato", "emancipation-cube"]})
    plan = reconcile.build_plan(
        env.repo, machine="host-book2", cache={}, save=False
    )
    assert _services(plan, phase="runtime") == set()


def test_machine_gated_no_manifest_provisions_locally(env):
    # #693 Phase 3: with NO gate manifest anywhere, an explicitly-enabled
    # machine-gated runtime provisions on the local machine (enabling it is the
    # intent; there is no gate to defer to). The env fixture pins the anchor
    # resolve to None, so the test repo having no manifest means none exists.
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "3.0.0", scope="machine-gated")
    env.deploy_runtime("agent-bridge", "2.0.0")  # drift
    plan = reconcile.build_plan(env.repo, machine="anomalous-potato", cache={}, save=False)
    assert _services(plan, phase="runtime") == {"agent-bridge"}


def test_machine_gated_manifest_present_but_omits_plugin_skips(env):
    # A gate manifest that exists but does not name this plugin is authoritative
    # and conservative: skip (the harness configured gating and left it out).
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "3.0.0", scope="machine-gated")
    env.deploy_runtime("agent-bridge", "2.0.0")  # drift
    env.write_gate({"some-other-plugin": ["anomalous-potato"]})
    plan = reconcile.build_plan(env.repo, machine="anomalous-potato", cache={}, save=False)
    assert _services(plan, phase="runtime") == set()


def test_invalid_scope_treated_as_none(env):
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "3.0.0", scope="bogus")
    env.deploy_runtime("agent-bridge", "2.0.0")
    env.write_gate({"agent-bridge": ["anomalous-potato"]})
    plan = reconcile.build_plan(env.repo, machine="anomalous-potato", cache={}, save=False)
    assert _services(plan, phase="runtime") == set()


def test_runtime_allowed_gate_present_semantics():
    # Pure-function semantics of the #693 Phase 3 gate_present refinement.
    gate = {"a": {"m1"}}
    # Listed plugin: strict machine check regardless of gate_present.
    assert reconcile.runtime_allowed("machine-gated", "a", "m1", gate) is True
    assert reconcile.runtime_allowed("machine-gated", "a", "m2", gate) is False
    # Unlisted plugin, manifest PRESENT -> conservative skip.
    assert reconcile.runtime_allowed(
        "machine-gated", "b", "m1", gate, gate_present=True
    ) is False
    # Unlisted plugin, NO manifest -> provision locally.
    assert reconcile.runtime_allowed(
        "machine-gated", "b", "m1", {}, gate_present=False
    ) is True
    # universal always; none never.
    assert reconcile.runtime_allowed("universal", "b", "m1", {}, gate_present=False) is True
    assert reconcile.runtime_allowed("none", "b", "m1", {}, gate_present=False) is False


def test_gate_manifest_present_detects_repo_file(env):
    assert reconcile.gate_manifest_present(env.repo) is False
    env.write_gate({"agent-bridge": ["anomalous-potato"]})
    assert reconcile.gate_manifest_present(env.repo) is True


# ---------------------------------------------------------------------------
# Payload-refresh throttle
# ---------------------------------------------------------------------------

def test_payload_refresh_throttled_when_recent(env):
    env.write_settings({f"agent-mcp@{MKT}": True})
    env.install_payload("agent-mcp", "1.0.0", scope="none")
    cache = {"plugins": {"agent-mcp": {"last_payload_update": 1_000_000.0}}}
    plan = reconcile.build_plan(
        env.repo, machine="m1", now=1_000_100.0, cache=cache, save=False,
        include_payload_refresh=True,
    )
    assert plan["action"] == "continue"


def test_payload_refresh_due_after_interval(env):
    env.write_settings({f"agent-mcp@{MKT}": True})
    env.install_payload("agent-mcp", "1.0.0", scope="none")
    cache = {"plugins": {"agent-mcp": {"last_payload_update": 0.0}}}
    now = 10 * 24 * 3600.0
    plan = reconcile.build_plan(
        env.repo, machine="m1", now=now, cache=cache, save=False,
        include_payload_refresh=True,
    )
    assert _services(plan, phase="payload") == {"agent-mcp"}
    assert cache["plugins"]["agent-mcp"]["last_payload_update"] == now


def test_payload_refresh_suppressed_by_default(env):
    """Default (programmatic) path never refreshes payloads, even when due, and
    does NOT advance the throttle clock (#1393)."""
    env.write_settings({f"agent-mcp@{MKT}": True})
    env.install_payload("agent-mcp", "1.0.0", scope="none")
    cache = {"plugins": {"agent-mcp": {"last_payload_update": 0.0}}}
    now = 10 * 24 * 3600.0
    plan = reconcile.build_plan(
        env.repo, machine="m1", now=now, cache=cache, save=False,
    )
    assert _services(plan, phase="payload") == set()
    assert cache["plugins"]["agent-mcp"]["last_payload_update"] == 0.0


# ---------------------------------------------------------------------------
# Explicit installation-context manifest selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("status", "reason", "desired_mode"),
    [
        ("ready", "policy-default-false", "legacy"),
        ("migration-required", "migration-required", "namespaced"),
    ],
)
def test_cell_aware_legacy_modes_reconcile_without_receipt(
    env,
    monkeypatch,
    status,
    reason,
    desired_mode,
):
    payload = env.install_payload(
        "agent-machines",
        "2.0.0",
        scope="universal",
    )
    receipt = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-machines"
        / "install.json"
    )
    assert not receipt.exists()
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-machines",
            status=status,
            reason=reason,
            actual_mode="legacy",
            desired_mode=desired_mode,
            allow_mutation=True,
        ),
    )

    selected = reconcile.resolve_runtime_installation(
        "agent-machines",
        payload,
    )

    assert selected.runtime_root == env.home / ".agent-machines"
    assert selected.context is None
    assert selected.actual_mode == "legacy"


def test_missing_policy_active_legacy_runtime_reconciles_without_receipt(
    env,
    monkeypatch,
):
    env.write_settings({f"agent-machines@{MKT}": True})
    payload = env.install_payload(
        "agent-machines",
        "2.0.0",
        scope="universal",
    )
    env.deploy_runtime("agent-machines", "1.0.0")
    receipt = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-machines"
        / "install.json"
    )
    assert not receipt.exists()
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-machines",
            status="ready",
            reason="policy-default-false",
            actual_mode="legacy",
            desired_mode="legacy",
            allow_mutation=True,
        ),
    )

    plan = reconcile.build_plan(
        env.repo,
        machine="m1",
        cache={},
        save=False,
    )

    update = next(u for u in plan["updates"] if u["phase"] == "runtime")
    assert update["service"] == "agent-machines"
    assert update["from_version"] == "1.0.0"
    assert update["to_version"] == "2.0.0"
    assert "COPILOT_EXTENSIONS_CONTEXT" not in update["environment"]
    assert plan.get("diagnostics") is None
    assert payload.is_dir()


@pytest.mark.parametrize(
    ("status", "reason", "actual_mode", "desired_mode"),
    [
        ("ready", "activation-required", "legacy", "namespaced"),
        ("maintenance-blocked", "maintenance-active", "legacy", "legacy"),
        ("invalid", "policy-invalid", None, None),
        ("orphaned-transfer", "orphaned-transfer", "legacy", "legacy"),
        ("foreign-environment", "foreign-environment", None, None),
        ("revalidation-required", "revalidation-required", "namespaced", "namespaced"),
        ("provenance-blocked", "provenance-blocked", None, None),
    ],
)
def test_unsafe_installation_modes_block_runtime_reconcile(
    env,
    monkeypatch,
    status,
    reason,
    actual_mode,
    desired_mode,
):
    payload = env.install_payload(
        "agent-machines",
        "2.0.0",
        scope="universal",
    )
    namespaced_context = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-machines"
        / "install.json"
        if actual_mode == "namespaced"
        else None
    )
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-machines",
            status=status,
            reason=reason,
            actual_mode=actual_mode,
            desired_mode=desired_mode,
            allow_mutation=False,
            context=namespaced_context,
            runtime_root=(
                namespaced_context.parent
                if namespaced_context is not None
                else None
            ),
        ),
    )

    with pytest.raises(ValueError, match="blocks runtime reconciliation"):
        reconcile.resolve_runtime_installation("agent-machines", payload)


@pytest.mark.parametrize(
    ("status", "reason", "desired_mode"),
    [
        ("ready", "namespaced-active", "namespaced"),
        ("deactivation-required", "deactivation-required", "legacy"),
    ],
)
def test_namespaced_modes_require_exact_validated_receipt(
    env,
    monkeypatch,
    status,
    reason,
    desired_mode,
):
    payload = env.install_payload(
        "agent-machines",
        "2.0.0",
        scope="universal",
    )
    receipt = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-machines"
        / "install.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-machines",
            status=status,
            reason=reason,
            actual_mode="namespaced",
            desired_mode=desired_mode,
            allow_mutation=False,
            context=receipt,
            runtime_root=receipt.parent,
        ),
    )

    selected = reconcile.resolve_runtime_installation(
        "agent-machines",
        payload,
    )

    assert selected.context == receipt
    assert selected.runtime_root == receipt.parent


@pytest.mark.parametrize(
    ("status", "reason", "desired_mode"),
    [
        ("ready", "namespaced-active", "namespaced"),
        ("deactivation-required", "deactivation-required", "legacy"),
    ],
)
def test_namespaced_agent_machines_plan_uses_cell_transaction(
    env,
    monkeypatch,
    status,
    reason,
    desired_mode,
):
    env.write_settings({f"agent-machines@{MKT}": True})
    env.install_payload(
        "agent-machines",
        "2.0.0",
        scope="universal",
        installer="init.sh",
    )
    receipt = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-machines"
        / "install.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")
    (receipt.parent / "deploy-manifest.json").write_text(
        json.dumps({"source": {"version": "1.0.0"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-machines",
            status=status,
            reason=reason,
            actual_mode="namespaced",
            desired_mode=desired_mode,
            allow_mutation=False,
            context=receipt,
            runtime_root=receipt.parent,
        ),
    )

    plan = reconcile.build_plan(
        env.repo,
        machine="m1",
        cache={},
        save=False,
    )

    update = next(u for u in plan["updates"] if u["phase"] == "runtime")
    assert update["argv"][2] == "cell-provision"
    assert update["environment"] == {
        "COPILOT_EXTENSIONS_CONTEXT": str(receipt)
    }
    assert update["from_version"] == "1.0.0"
    assert update["to_version"] == "2.0.0"


def test_namespaced_runtime_without_transaction_reports_diagnostic(
    env,
    monkeypatch,
):
    env.write_settings({f"agent-index@{MKT}": True})
    env.install_payload(
        "agent-index",
        "2.0.0",
        scope="universal",
        installer="install.sh",
    )
    receipt = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-index"
        / "install.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")
    (receipt.parent / "deploy-manifest.json").write_text(
        json.dumps({"source": {"version": "1.0.0"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-index",
            status="ready",
            reason="namespaced-active",
            actual_mode="namespaced",
            desired_mode="namespaced",
            allow_mutation=False,
            context=receipt,
            runtime_root=receipt.parent,
        ),
    )

    plan = reconcile.build_plan(
        env.repo,
        machine="m1",
        cache={},
        save=False,
    )

    assert _services(plan, phase="runtime") == set()
    assert plan["diagnostics"][0]["reason"] == (
        "installation-context-unsupported"
    )


@pytest.mark.parametrize(
    ("context_value", "root_value"),
    [
        (None, "expected"),
        ("expected", "legacy"),
        ("foreign", "expected"),
    ],
)
def test_namespaced_mode_blocks_missing_or_mismatched_receipt(
    env,
    monkeypatch,
    context_value,
    root_value,
):
    payload = env.install_payload(
        "agent-machines",
        "2.0.0",
        scope="universal",
    )
    expected = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-machines"
        / "install.json"
    )
    expected.parent.mkdir(parents=True)
    expected.write_text("{}", encoding="utf-8")
    context = {
        None: None,
        "expected": expected,
        "foreign": env.home / "foreign" / "install.json",
    }[context_value]
    root = {
        "expected": expected.parent,
        "legacy": env.home / ".agent-machines",
    }[root_value]
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-machines",
            status="ready",
            reason="namespaced-active",
            actual_mode="namespaced",
            desired_mode="namespaced",
            allow_mutation=False,
            context=context,
            runtime_root=root,
        ),
    )

    with pytest.raises(ValueError):
        reconcile.resolve_runtime_installation("agent-machines", payload)


def test_governed_current_legacy_runtime_avoids_reinstall(
    env, monkeypatch
):
    env.write_settings({f"agent-index@{MKT}": True})
    payload = env.install_payload("agent-index", "2.0.0", scope="universal")
    _context, plugin_root = env.select_context(
        "agent-index", payload, "2.0.0", "2.0.0"
    )
    env.deploy_runtime("agent-index", "2.0.0")
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-index",
            status="ready",
            reason="policy-default-false",
            actual_mode="legacy",
            desired_mode="legacy",
            allow_mutation=True,
        ),
    )

    plan = reconcile.build_plan(
        env.repo, machine="m1", cache={}, save=False
    )

    assert plan["action"] == "continue"
    assert _services(plan, phase="runtime") == set()
    assert plan.get("diagnostics") is None
    assert (env.home / ".agent-index" / "deploy-manifest.json").is_file()
    assert (plugin_root / "deploy-manifest.json").is_file()


def test_governed_reconcile_compares_authoritative_legacy_runtime(env, monkeypatch):
    env.write_settings({f"agent-index@{MKT}": True})
    payload = env.install_payload("agent-index", "2.0.0", scope="universal")
    _context, plugin_root = env.select_context(
        "agent-index", payload, "2.0.0", "2.0.0"
    )
    env.deploy_runtime("agent-index", "1.0.0")
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda *args, **kwargs: _mode_resolution(
            env,
            "agent-index",
            status="ready",
            reason="policy-default-false",
            actual_mode="legacy",
            desired_mode="legacy",
            allow_mutation=True,
        ),
    )

    plan = reconcile.build_plan(
        env.repo, machine="m1", cache={}, save=False
    )

    assert plan["action"] == "reconcile", plan
    assert _services(plan, phase="runtime") == {"agent-index"}
    update = next(u for u in plan["updates"] if u["phase"] == "runtime")
    assert "COPILOT_EXTENSIONS_CONTEXT" not in update["environment"]
    assert "COPILOT_PLUGIN_ROOT" in update["unset_environment"]
    assert plugin_root.is_dir()
    candidate = reconcile.runtime_installation_candidate("agent-index", payload)
    assert candidate is None
    assert update["from_version"] == "1.0.0"


def test_payload_only_context_is_ignored_by_agent_runtime_reconcile(env):
    runtime = env.install_payload("agent-index", "2.0.0", scope="universal")
    payload_only = env.install_payload("context-handoff", "1.0.0", scope="none")
    env.select_context("context-handoff", payload_only, "1.0.0", None)

    selected_root, context_selected = reconcile._selected_runtime_root(
        "agent-index",
        runtime,
    )

    assert selected_root == env.home / ".agent-index"
    assert not context_selected


def test_payload_only_context_does_not_abort_build_plan(env):
    env.write_settings({f"agent-codespaces@{MKT}": True})
    env.install_payload("agent-codespaces", "2.0.0", scope="universal")
    payload_only = env.install_payload("context-handoff", "1.0.0", scope="none")
    env.select_context("context-handoff", payload_only, "1.0.0", None)
    env.deploy_runtime("agent-codespaces", "1.0.0")

    plan = reconcile.build_plan(
        env.repo,
        machine="m1",
        cache={},
        save=False,
    )

    assert plan["action"] == "reconcile"
    assert _services(plan, phase="runtime") == {"agent-codespaces"}
    assert plan.get("diagnostics") is None


def test_non_agent_runtime_never_uses_installation_cell(env):
    runtime = env.install_payload("runtime-helper", "2.0.0", scope="universal")
    context, _plugin_root = env.select_context(
        "runtime-helper",
        runtime,
        "2.0.0",
        "1.0.0",
    )

    selected_root, context_selected = reconcile._selected_runtime_root(
        "runtime-helper",
        runtime,
    )

    assert context.is_file()
    assert selected_root == env.home / ".runtime-helper"
    assert not context_selected


def test_direct_agent_runtime_never_uses_installation_cell(env):
    direct = (
        env.home
        / ".copilot"
        / "installed-plugins"
        / "_direct"
        / "downstream-agent-helper"
    )
    (direct / "scripts").mkdir(parents=True)
    (direct / "plugin.json").write_text(
        json.dumps(
            {
                "name": "agent-helper",
                "version": "2.0.0",
                "runtimeScope": "universal",
            }
        ),
        encoding="utf-8",
    )

    assert reconcile.installation_cell_eligibility("agent-helper", direct) is False


def _direct_agent_payload(env):
    direct = (
        env.home
        / ".copilot"
        / "installed-plugins"
        / "_direct"
        / "downstream-agent-helper"
    )
    (direct / "scripts").mkdir(parents=True, exist_ok=True)
    (direct / "plugin.json").write_text(
        json.dumps(
            {
                "name": "agent-helper",
                "version": "2.0.0",
                "runtimeScope": "universal",
            }
        ),
        encoding="utf-8",
    )
    return direct


def test_non_cell_agent_runtime_without_context_remains_legacy(env, monkeypatch):
    direct = _direct_agent_payload(env)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)

    resolution = reconcile.resolve_runtime_installation(
        "agent-helper",
        direct,
        home=env.home,
    )

    assert resolution.runtime_root == env.home / ".agent-helper"
    assert resolution.context is None
    assert resolution.actual_mode == "legacy"


@pytest.mark.parametrize("kind", ["relative", "malformed", "unsafe"])
def test_non_cell_agent_runtime_rejects_invalid_explicit_context(
    env,
    monkeypatch,
    kind,
):
    direct = _direct_agent_payload(env)
    if kind == "relative":
        pointer = Path("relative/install.json")
    else:
        pointer = env.home / ".copilot-extensions" / f"{kind}.json"
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(
            "{"
            if kind == "malformed"
            else json.dumps(
                {
                    "marketplaceId": reconcile.CORE_MARKETPLACE_ID,
                    "pluginId": "../unsafe",
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(pointer))

    with pytest.raises(ValueError):
        reconcile.resolve_runtime_installation(
            "agent-helper",
            direct,
            home=env.home,
        )


def test_non_cell_agent_runtime_rejects_exact_core_context(env, monkeypatch):
    direct = _direct_agent_payload(env)
    pointer = env.home / ".copilot-extensions" / "core.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {
                "marketplaceId": reconcile.CORE_MARKETPLACE_ID,
                "pluginId": "agent-machines",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(pointer))

    with pytest.raises(ValueError, match="cannot authorize non-cell runtime"):
        reconcile.resolve_runtime_installation(
            "agent-helper",
            direct,
            home=env.home,
        )


def test_helperless_agent_runtime_rejects_exact_core_context(env, monkeypatch):
    payload = env.install_payload(
        "agent-codespaces",
        "2.0.0",
        scope="universal",
    )
    pointer = env.home / ".copilot-extensions" / "core.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {
                "marketplaceId": reconcile.CORE_MARKETPLACE_ID,
                "pluginId": "agent-codespaces",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(pointer))

    with pytest.raises(ValueError, match="no trusted installation-context"):
        reconcile.resolve_runtime_installation(
            "agent-codespaces",
            payload,
            home=env.home,
        )


def test_non_cell_agent_runtime_ignores_foreign_context(env, monkeypatch):
    direct = _direct_agent_payload(env)
    pointer = env.home / ".copilot-extensions" / "foreign.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {
                "marketplaceId": "foreign--0000000000000000",
                "pluginId": "../unsafe",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(pointer))

    resolution = reconcile.resolve_runtime_installation(
        "agent-helper",
        direct,
        home=env.home,
    )

    assert resolution.runtime_root == env.home / ".agent-helper"
    assert resolution.context is None


def test_foreign_same_name_context_does_not_capture_core_runtime(env):
    core = env.install_payload("agent-index", "2.0.0", scope="universal")
    foreign = env.home / "foreign-agent-index"
    (foreign / "scripts").mkdir(parents=True)
    (foreign / "plugin.json").write_text(
        json.dumps(
            {
                "name": "agent-index",
                "version": "9.0.0",
                "runtimeScope": "universal",
            }
        ),
        encoding="utf-8",
    )
    env.select_context(
        "agent-index",
        foreign,
        "9.0.0",
        None,
        marketplace_key="downstream",
    )

    selected_root, context_selected = reconcile._selected_runtime_root(
        "agent-index",
        core,
    )

    assert selected_root == env.home / ".agent-index"
    assert not context_selected


def test_foreign_context_with_unsafe_plugin_id_is_ignored(env, monkeypatch):
    runtime = env.install_payload("agent-index", "2.0.0", scope="universal")
    foreign = env.home / ".copilot-extensions" / "foreign.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text(
        json.dumps(
            {
                "marketplaceId": "downstream--0000000000000000",
                "pluginId": "../unsafe",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(foreign))

    selected_root, context_selected = reconcile._selected_runtime_root(
        "agent-index",
        runtime,
    )

    assert selected_root == env.home / ".agent-index"
    assert not context_selected


def test_same_prefix_foreign_marketplace_is_ignored(env, monkeypatch):
    runtime = env.install_payload("agent-index", "2.0.0", scope="universal")
    foreign = env.home / ".copilot-extensions" / "foreign.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text(
        json.dumps(
            {
                "marketplaceId": "copilot-extensions--0000000000000000",
                "pluginId": "agent-index",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(foreign))

    selected_root, context_selected = reconcile._selected_runtime_root(
        "agent-index",
        runtime,
    )

    assert selected_root == env.home / ".agent-index"
    assert not context_selected


def test_ambient_core_context_blocks_unconfigured_payload(env, monkeypatch):
    direct = (
        env.home
        / ".copilot"
        / "installed-plugins"
        / "_direct"
        / "agent-index-direct"
    )
    (direct / "scripts").mkdir(parents=True)
    (direct / "plugin.json").write_text(
        json.dumps(
            {
                "name": "agent-index",
                "version": "9.0.0",
                "runtimeScope": "universal",
            }
        ),
        encoding="utf-8",
    )
    receipt = env.home / ".copilot-extensions" / "core.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "marketplaceId": reconcile.CORE_MARKETPLACE_ID,
                "pluginId": "agent-index",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(receipt))

    with pytest.raises(ValueError, match="cannot authorize non-cell runtime"):
        reconcile.resolve_runtime_installation(
            "agent-index",
            direct,
            home=env.home,
        )


def test_explicit_context_preserves_governed_reconcile_for_other_plugins(
    env, monkeypatch
):
    env.write_settings({
        f"agent-index@{MKT}": True,
        f"agent-machines@{MKT}": True,
    })
    selected = env.install_payload("agent-index", "2.0.0", scope="universal")
    env.select_context("agent-index", selected, "2.0.0", "2.0.0")
    other = env.install_payload("agent-machines", "2.0.0", scope="universal")
    env.select_context("agent-machines", other, "2.0.0", "1.0.0")
    env.deploy_runtime("agent-index", "2.0.0")
    env.deploy_runtime("agent-machines", "1.0.0")
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        str(
            env.home / ".copilot-extensions" / "marketplaces"
            / reconcile.CORE_MARKETPLACE_ID / "plugins" / "agent-index"
            / "install.json"
        ),
    )
    selections = []
    original_select = reconcile._selected_runtime_root

    def track_selection(name, plugin_dir, **kwargs):
        selections.append(name)
        return original_select(name, plugin_dir, **kwargs)

    monkeypatch.setattr(reconcile, "_selected_runtime_root", track_selection)
    monkeypatch.setattr(
        reconcile,
        "_run_installation_mode_helper",
        lambda name, *args, **kwargs: _mode_resolution(
            env,
            name,
            status="ready",
            reason="policy-default-false",
            actual_mode="legacy",
            desired_mode="legacy",
            allow_mutation=True,
        ),
    )

    plan = reconcile.build_plan(
        env.repo, machine="m1", cache={}, save=False
    )

    assert plan["action"] == "reconcile"
    assert _services(plan, phase="runtime") == {"agent-machines"}
    assert plan.get("diagnostics") is None
    assert selections == ["agent-machines"]
    update = next(u for u in plan["updates"] if u["service"] == "agent-machines")
    assert "COPILOT_EXTENSIONS_CONTEXT" not in update["environment"]


def test_invalid_cross_plugin_context_does_not_enable_legacy_reconcile(
    env, monkeypatch
):
    env.write_settings({f"agent-machines@{MKT}": True})
    env.install_payload("agent-index", "2.0.0", scope="universal")
    env.install_payload("agent-machines", "2.0.0", scope="universal")
    env.deploy_runtime("agent-machines", "1.0.0")
    forged = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / "forged--0000000000000000"
        / "plugins"
        / "agent-index"
        / "install.json"
    )
    forged.parent.mkdir(parents=True)
    forged.write_text('{"pluginId":"agent-index"}', encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(forged))

    plan = reconcile.build_plan(
        env.repo, machine="m1", cache={}, save=False
    )

    assert plan["action"] == "continue"
    assert _services(plan, phase="runtime") == set()
    assert plan["diagnostics"][0]["reason"] == "installation-context-invalid"


@pytest.mark.parametrize(
    "plugin_id",
    ("/tmp/untrusted", "../untrusted", r"name\child", "CON"),
)
def test_untrusted_plugin_id_does_not_select_validator_path(
    tmp_path, monkeypatch, plugin_id
):
    payload = tmp_path / "payload"
    helper = (
        payload
        / "scripts"
        / "installation-context"
        / "installation_context.py"
    )
    helper.parent.mkdir(parents=True)
    shutil.copyfile(INSTALLATION_CONTEXT, helper)
    context = (
        tmp_path
        / "durable"
        / "marketplaces"
        / "forged--0000000000000000"
        / "plugins"
        / "agent-index"
        / "install.json"
    )
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({"pluginId": plugin_id}), encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))

    def installed_payload(name):
        assert name == reconcile.SELF_PLUGIN
        return None

    monkeypatch.setattr(reconcile, "installed_payload_dir", installed_payload)

    with pytest.raises(ValueError):
        reconcile._selected_runtime_root("agent-index", payload)


def test_invalid_context_does_not_fall_through_to_legacy_runtime(env, monkeypatch):
    env.write_settings({f"agent-index@{MKT}": True})
    env.install_payload("agent-index", "2.0.0", scope="universal")
    env.deploy_runtime("agent-index", "1.0.0")
    invalid = env.home / ".copilot-extensions" / "invalid.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(invalid))

    plan = reconcile.build_plan(
        env.repo, machine="m1", cache={}, save=False
    )

    assert plan["action"] == "continue"
    assert _services(plan, phase="runtime") == set()
    assert plan["diagnostics"][0]["reason"] == "installation-context-invalid"


def test_duplicate_context_plugin_id_fails_closed(env, monkeypatch):
    env.write_settings({f"agent-index@{MKT}": True})
    env.install_payload("agent-index", "2.0.0", scope="universal")
    env.deploy_runtime("agent-index", "1.0.0")
    invalid = env.home / ".copilot-extensions" / "duplicate.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text(
        '{"pluginId":"agent-index","pluginId":"other"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(invalid))

    plan = reconcile.build_plan(
        env.repo, machine="m1", cache={}, save=False
    )

    assert plan["action"] == "continue"
    assert _services(plan, phase="runtime") == set()
    assert plan["diagnostics"][0]["reason"] == "installation-context-invalid"


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


def test_namespaced_agent_machines_uses_cell_provision_transaction(
    env,
    monkeypatch,
):
    monkeypatch.setattr(reconcile.platform, "system", lambda: "Linux")
    payload = env.install_payload(
        "agent-machines",
        "1.0.0",
        installer="init.sh",
    )
    context = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-machines"
        / "install.json"
    )

    _cmd, argv = reconcile.runtime_installer_argv(
        payload,
        context=context,
    )

    assert argv == [
        "bash",
        str(payload / "scripts" / "init.sh"),
        "cell-provision",
        "--context",
        str(context),
        "--expected-marketplace-id",
        reconcile.CORE_MARKETPLACE_ID,
        "--durable-home",
        str(env.home / ".copilot-extensions"),
    ]


def test_namespaced_agent_machines_windows_uses_powershell_fallback(
    env,
    monkeypatch,
):
    payload = env.install_payload(
        "agent-machines",
        "1.0.0",
        installer="init.ps1",
    )
    context = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-machines"
        / "install.json"
    )
    monkeypatch.setattr(reconcile.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        reconcile.shutil,
        "which",
        lambda name: "powershell" if name == "powershell" else None,
    )

    _cmd, argv = reconcile.runtime_installer_argv(
        payload,
        context=context,
    )

    assert argv[0] == "powershell"
    assert argv[4] == "cell-provision"


def test_namespaced_runtime_without_transaction_fails_closed(env):
    payload = env.install_payload(
        "agent-index",
        "1.0.0",
        installer="install.sh",
    )
    context = (
        env.home
        / ".copilot-extensions"
        / "marketplaces"
        / reconcile.CORE_MARKETPLACE_ID
        / "plugins"
        / "agent-index"
        / "install.json"
    )

    with pytest.raises(ValueError, match="transaction is unavailable"):
        reconcile.runtime_installer_argv(payload, context=context)


# ---------------------------------------------------------------------------
# Gate parsing
# ---------------------------------------------------------------------------

def test_load_runtime_gate_parses_deploy_machines(env):
    env.write_gate({
        "agent-bridge": ["anomalous-potato", "emancipation-cube"],
        "agent-codespaces": ["host-book2"],
    })
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-bridge"] == {"anomalous-potato", "emancipation-cube"}
    assert gate["agent-codespaces"] == {"host-book2"}


def test_load_runtime_gate_parses_plugins_schema(env):
    # Native services.yaml shape: a top-level ``plugins:`` list.
    env.write_gate_services({
        "agent-mcp": ["anomalous-potato", "emancipation-cube"],
        "agent-dispatch": ["host-book2"],
    })
    gate = reconcile.load_runtime_gate(env.repo)
    assert gate["agent-mcp"] == {"anomalous-potato", "emancipation-cube"}
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
        env.repo, machine="m1", now=now, cache=cache, save=False,
        include_payload_refresh=True,
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


def test_version_lt_orders_only_confidently():
    """_version_lt is True only for a confidently-older `a`; ambiguity -> False."""
    assert reconcile._version_lt("1.0.0", "2.0.0")
    assert reconcile._version_lt("0.4.0-dev260", "0.4.0-dev262")
    # PEP440 spelling tolerated (importlib vs plugin.json)
    assert reconcile._version_lt("0.4.0.dev260", "0.4.0-dev262")
    # equal / newer / missing -> not confidently older
    assert not reconcile._version_lt("2.0.0", "2.0.0")
    assert not reconcile._version_lt("2.0.0-dev1", "2.0.0.dev1")
    assert not reconcile._version_lt("2.0.0", "1.0.0")
    assert not reconcile._version_lt(None, "1.0.0")
    assert not reconcile._version_lt("1.0.0", None)


def test_monotonic_guard_suppresses_payload_downgrade(env, monkeypatch):
    """Payload OLDER than the running build must NOT be redeployed (#1366).

    A fresh `source: local` deploy that is newer than the (throttled/stale)
    marketplace payload would otherwise be reverted on the next pass."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "0.4.0-dev260", scope="universal")  # stale payload
    env.deploy_runtime("context-handoff", "0.4.0-dev262")   # newer local deploy
    env.deploy_running("context-handoff", "0.4.0-dev262")   # newer live process
    plan = reconcile.build_plan(env.repo, machine="anywhere", cache={}, save=False)
    assert _runtime_updates(plan) == [], "must not downgrade a newer build to a stale payload"


def test_monotonic_guard_still_deploys_forward_upgrade(env, monkeypatch):
    """A payload NEWER than the running build is still deployed (guard is one-way)."""
    monkeypatch.setattr(reconcile, "_pid_alive", lambda pid: True)
    env.write_settings({f"context-handoff@{MKT}": True})
    env.install_payload("context-handoff", "0.4.0-dev262", scope="universal")  # newer payload
    env.deploy_runtime("context-handoff", "0.4.0-dev260")   # older local deploy
    env.deploy_running("context-handoff", "0.4.0-dev260")
    plan = reconcile.build_plan(env.repo, machine="anywhere", cache={}, save=False)
    rt = _runtime_updates(plan)
    assert len(rt) == 1 and rt[0]["to_version"] == "0.4.0-dev262"


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


# ---------------------------------------------------------------------------
# apply_plan -- in-process 2-pass execution (session-start self-provisioning)
# ---------------------------------------------------------------------------

def test_apply_plan_noop_when_current(env):
    """Nothing to do -> action 'continue', runner never called."""
    import time
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "1.0.0", scope="universal")
    env.deploy_runtime("agent-bridge", "1.0.0")
    # Seed the persisted reconcile cache so the throttled payload refresh
    # (copilot plugin update) is suppressed -- isolating the runtime decision,
    # exactly as a steady-state (non-first) launch behaves.
    reconcile.save_cache({"plugins": {"agent-bridge": {"last_payload_update": time.time()}}})

    calls: list = []
    summary = reconcile.apply_plan(
        env.repo, machine="anywhere", passes=1,
        runner=lambda argv: calls.append(list(argv)) or 0,
    )
    assert summary["action"] == "continue"
    assert summary["executed"] == []
    assert calls == []


def test_apply_plan_runs_runtime_drift(env, monkeypatch):
    """A drifted runtime -> the installer argv is executed and recorded."""
    import time
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "2.0.0", scope="universal")
    env.deploy_runtime("agent-bridge", "1.0.0")  # drift
    reconcile.save_cache({"plugins": {"agent-bridge": {"last_payload_update": time.time()}}})

    foreign_context = env.home / ".copilot-extensions" / "foreign.json"
    foreign_context.parent.mkdir(parents=True, exist_ok=True)
    foreign_context.write_text(
        json.dumps(
            {
                "marketplaceId": "foreign--0000000000000000",
                "pluginId": "agent-bridge",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(foreign_context))
    calls: list = []
    child_contexts: list[str | None] = []

    def runner(argv):
        calls.append(list(argv))
        child_contexts.append(os.environ.get("COPILOT_EXTENSIONS_CONTEXT"))
        return 0

    summary = reconcile.apply_plan(
        env.repo, machine="anywhere", passes=1,
        runner=runner,
    )
    assert summary["action"] == "reconcile"
    assert len(calls) == 1
    assert calls[0][0] == "bash"  # install.sh runtime installer (POSIX-pinned)
    assert child_contexts == [None]
    assert os.environ["COPILOT_EXTENSIONS_CONTEXT"] == str(foreign_context)
    assert summary["executed"][0]["service"] == "agent-bridge"
    assert summary["executed"][0]["ok"] is True


def test_apply_plan_strips_caller_payload_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/caller/payload")
    monkeypatch.setenv("PYTHONPATH", "/caller/python")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "/caller/install.json")
    monkeypatch.setenv("RECONCILE_KEEP", "present")
    monkeypatch.setattr(
        reconcile,
        "build_plan",
        lambda *_args, **_kwargs: {
            "action": "reconcile",
            "updates": [
                {
                    "service": "target-plugin",
                    "reason": "runtime-drift",
                    "argv": ["target-installer", "update"],
                    "environment": {
                        "COPILOT_EXTENSIONS_CONTEXT": "/target/install.json",
                    },
                    "unset_environment": list(reconcile._RUNTIME_ENV_UNSET),
                }
            ],
        },
    )
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(reconcile.subprocess, "run", fake_run)

    summary = reconcile.apply_plan(tmp_path, passes=1)

    assert summary["executed"][0]["ok"] is True
    assert captured["argv"] == ["target-installer", "update"]
    assert "COPILOT_PLUGIN_ROOT" not in captured["env"]
    assert "PYTHONPATH" not in captured["env"]
    assert captured["env"]["COPILOT_EXTENSIONS_CONTEXT"] == "/target/install.json"
    assert captured["env"]["RECONCILE_KEEP"] == "present"


def test_launch_adapters_apply_planned_runtime_environment():
    powershell = (
        REPO / "plugins" / "agent-worktrees" / "bin" / "launch-session.ps1"
    ).read_text(encoding="utf-8")
    posix = (
        REPO / "plugins" / "agent-worktrees" / "bin" / "launch-session.sh"
    ).read_text(encoding="utf-8")

    assert "$u.PSObject.Properties['unset_environment']" in powershell
    assert "$u.PSObject.Properties['environment'] -and $u.environment" in powershell
    assert "$u.unset_environment" in powershell
    assert "$u.environment.PSObject.Properties" in powershell
    assert "update.get('unset_environment', [])" in posix
    assert "update.get('environment', {}).items()" in posix
    assert "status.get('unset_environment', [])" in posix
    assert '--install-dir "$runtime_root"' in posix
    assert "Push-UpdateEnvironment $status" in powershell
    assert "Push-UpdateEnvironment $update" in powershell
    assert "'-InstallDir'" in powershell


def test_apply_plan_skips_copilot_when_absent(env, monkeypatch):
    """A 'copilot ...' payload step is skipped when copilot is not on PATH."""
    env.write_settings({f"agent-bridge@{MKT}": True})
    # No installed payload -> the plan emits a `copilot plugin install` step.
    monkeypatch.setattr(reconcile.shutil, "which", lambda _c: None)
    # ...and no fallback copilot binary exists either, so resolution -> None.
    monkeypatch.setattr(reconcile, "_COPILOT_FALLBACK_PATHS", ())

    calls: list = []
    summary = reconcile.apply_plan(
        env.repo, machine="anywhere", passes=1, include_payload_refresh=True,
        runner=lambda argv: calls.append(list(argv)) or 0,
    )
    # The copilot step was planned but skipped (not executed).
    assert calls == []
    assert summary["executed"] == []


def test_apply_plan_default_is_runtime_only(env):
    """The provision-check path (apply_plan default) never emits a marketplace
    pull: a missing payload yields no copilot step (#1393)."""
    env.write_settings({f"agent-bridge@{MKT}": True})  # no installed payload
    calls: list = []
    summary = reconcile.apply_plan(
        env.repo, machine="anywhere", passes=1,
        runner=lambda argv: calls.append(list(argv)) or 0,
    )
    assert calls == []
    assert summary["executed"] == []
    """A non-zero runner exit is recorded as ok=False, never raised."""
    import time
    env.write_settings({f"agent-bridge@{MKT}": True})
    env.install_payload("agent-bridge", "2.0.0", scope="universal")
    env.deploy_runtime("agent-bridge", "1.0.0")
    reconcile.save_cache({"plugins": {"agent-bridge": {"last_payload_update": time.time()}}})

    summary = reconcile.apply_plan(
        env.repo, machine="anywhere", passes=1,
        runner=lambda _argv: 7,
    )
    assert summary["executed"][0]["ok"] is False
    assert summary["executed"][0]["returncode"] == 7




# ---------------------------------------------------------------------------
# resolve_copilot -- find (never install) the Copilot CLI executable
# ---------------------------------------------------------------------------

def test_resolve_copilot_prefers_path(monkeypatch, tmp_path):
    """A bare ``copilot`` on PATH (via ``shutil.which``) wins."""
    on_path = tmp_path / "path-copilot"
    on_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(reconcile.shutil, "which", lambda name: str(on_path))
    monkeypatch.setattr(reconcile, "_COPILOT_FALLBACK_PATHS", ())
    assert reconcile.resolve_copilot() == str(on_path)


def test_resolve_copilot_falls_back_to_autoinstall_location(monkeypatch, tmp_path):
    """When bare ``copilot`` is not on PATH, use the first executable fallback
    (the WSL stub's auto-install target)."""
    monkeypatch.setattr(reconcile.shutil, "which", lambda name: None)
    missing = tmp_path / "nope" / "copilot"
    autoinstall = tmp_path / "share" / "gh" / "copilot" / "copilot"
    autoinstall.parent.mkdir(parents=True)
    autoinstall.write_text("#!/bin/sh\n")
    autoinstall.chmod(0o755)
    monkeypatch.setattr(reconcile, "_COPILOT_FALLBACK_PATHS", (missing, autoinstall))
    assert reconcile.resolve_copilot() == str(autoinstall)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX execute-bit semantics: Windows ignores chmod(0o644), so an "
           "existing fallback still reads as executable and is selected.",
)
def test_resolve_copilot_skips_non_executable_fallback(monkeypatch, tmp_path):
    """A fallback that exists but is not executable is not selected."""
    monkeypatch.setattr(reconcile.shutil, "which", lambda name: None)
    non_exec = tmp_path / "copilot"
    non_exec.write_text("x")
    non_exec.chmod(0o644)
    monkeypatch.setattr(reconcile, "_COPILOT_FALLBACK_PATHS", (non_exec,))
    assert reconcile.resolve_copilot() is None


def test_resolve_copilot_none_when_nothing_found(monkeypatch):
    """No PATH entry and no fallback -> None (caller degrades to skip)."""
    monkeypatch.setattr(reconcile.shutil, "which", lambda name: None)
    monkeypatch.setattr(reconcile, "_COPILOT_FALLBACK_PATHS", ())
    assert reconcile.resolve_copilot() is None


def test_apply_plan_substitutes_resolved_copilot_path(env, monkeypatch, tmp_path):
    """When ``copilot`` is not on PATH but the auto-install binary exists, the
    provision step runs with the resolved absolute path (not skipped)."""
    env.write_settings({f"agent-bridge@{MKT}": True})
    # No installed payload -> plan emits `copilot plugin install`.
    monkeypatch.setattr(reconcile.shutil, "which", lambda _c: None)
    resolved = tmp_path / "share" / "gh" / "copilot" / "copilot"
    resolved.parent.mkdir(parents=True)
    resolved.write_text("#!/bin/sh\n")
    resolved.chmod(0o755)
    monkeypatch.setattr(reconcile, "_COPILOT_FALLBACK_PATHS", (resolved,))

    calls: list = []
    summary = reconcile.apply_plan(
        env.repo, machine="anywhere", passes=1, include_payload_refresh=True,
        runner=lambda argv: calls.append(list(argv)) or 0,
    )
    assert len(calls) == 1
    assert calls[0][0] == str(resolved)
    assert calls[0][1:] == ["plugin", "install", f"agent-bridge@{MKT}"]
    assert summary["executed"][0]["ok"] is True


# ---------------------------------------------------------------------------
# hook_shims_drifted -- bin/ shim currency, independent of runtime version
# (dotfiles #1171: a version-match quick-skip must not leave bin/ shims stale)
# ---------------------------------------------------------------------------

def _make_hook_layout(home: Path, plugin_dir: Path, *, deploy: bool = True):
    """Create a payload scripts/ set and (optionally) the deployed bin/ copy."""
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    bin_dir = home / ".agent-worktrees" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in reconcile.HOOK_SHIM_FILES:
        (scripts / name).write_text(f"# payload {name}\n", encoding="utf-8")
        if deploy:
            (bin_dir / name).write_text(f"# payload {name}\n", encoding="utf-8")
    return scripts, bin_dir


def test_hook_shims_drifted_false_when_identical(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(reconcile, "_home", lambda: home)
    plugin_dir = tmp_path / "plugin"
    _make_hook_layout(home, plugin_dir, deploy=True)
    assert reconcile.hook_shims_drifted(plugin_dir) is False


def test_hook_shims_drifted_true_when_shim_missing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(reconcile, "_home", lambda: home)
    plugin_dir = tmp_path / "plugin"
    _, bin_dir = _make_hook_layout(home, plugin_dir, deploy=True)
    # Simulate a payload that added a new shim never deployed to bin/ (the
    # resolve-runtime.ps1 / #1106 case that broke the sessionStart reseed).
    (bin_dir / "resolve-runtime.ps1").unlink()
    assert reconcile.hook_shims_drifted(plugin_dir) is True


def test_hook_shims_drifted_true_when_content_differs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(reconcile, "_home", lambda: home)
    plugin_dir = tmp_path / "plugin"
    _, bin_dir = _make_hook_layout(home, plugin_dir, deploy=True)
    # Deployed register-session.ps1 still on the retired .venv path.
    (bin_dir / "register-session.ps1").write_text(
        "$python = \"$env:USERPROFILE\\.agent-worktrees\\.venv\\...\"\n",
        encoding="utf-8",
    )
    assert reconcile.hook_shims_drifted(plugin_dir) is True


def test_hook_shims_drifted_skips_payload_absent_shim(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(reconcile, "_home", lambda: home)
    plugin_dir = tmp_path / "plugin"
    scripts, _ = _make_hook_layout(home, plugin_dir, deploy=True)
    # A shim not shipped by this payload must not count as drift.
    (scripts / "provision-check.sh").unlink()
    assert reconcile.hook_shims_drifted(plugin_dir) is False


def test_hook_shims_drifted_false_when_dirs_absent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(reconcile, "_home", lambda: home)
    # No scripts/ and no bin/ -> nothing to compare, never force a redeploy.
    assert reconcile.hook_shims_drifted(tmp_path / "plugin") is False
