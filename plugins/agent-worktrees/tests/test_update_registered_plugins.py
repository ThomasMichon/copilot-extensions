"""Tests for the ``update`` registered-plugin payload refresh.

``_update_registered_plugins`` closes the "phantom deploy" gap (test-chamber
#2554): ``update`` must ``copilot plugin update`` (or install) EVERY
copilot-extensions plugin registered for the managed repo -- including
payload-only plugins (``runtimeScope: none``) like ``context-handoff`` -- and
do so BEFORE the service payload / runtime steps.
"""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import activation_preservation, reconcile


_READ_USER_ENABLED_PLUGINS = reconcile.read_user_enabled_plugins


@pytest.fixture(autouse=True)
def _pin_copilot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Copilot resolution to the literal ``copilot`` so the argv assertions
    in these logic tests are environment-independent. (The real resolver --
    which may return an absolute path when a bare ``copilot`` is not on PATH --
    is unit-tested in ``test_reconcile.py``.)"""
    monkeypatch.setattr(m, "_resolve_copilot", lambda: "copilot")


@pytest.fixture(autouse=True)
def _no_user_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the USER-GLOBAL enabled set to empty so per-test repo mocks stay
    deterministic (the real reader would pick up this machine's
    ``~/.copilot/settings.json``). The dedicated user-global tests override it."""
    monkeypatch.setattr(reconcile, "read_user_enabled_plugins", lambda: [])
    monkeypatch.setattr(reconcile, "read_installed_plugins", lambda: [])
    monkeypatch.setattr(
        activation_preservation,
        "run_install_preserving_activation",
        lambda argv, identity, **kwargs: subprocess.run(argv, **kwargs),
    )


@pytest.fixture(autouse=True)
def _no_invocation_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep existing tests focused on configured repository contexts."""
    monkeypatch.setattr(m, "_INVOCATION_CWD", Path("/missing/invocation"))


def _install_config(monkeypatch: pytest.MonkeyPatch, anchor: str) -> None:
    repo = cfg.RepoConfig(
        anchor=anchor,
        worktree_root=str(Path(anchor).parent / "wt"),
        default_branch="master",
        remote="origin",
    )
    config = cfg.Config(
        srcroot=str(Path(anchor).parent),
        machine="test",
        platform="linux",
        repo_name="anchor",
        repos={"anchor": repo},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)


def _ok(stdout: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail(code: int = 1, stderr: str = "boom") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=code, stdout="", stderr=stderr)


def test_loop_covers_all_registered_including_payload_only(monkeypatch):
    """Every registered plugin (incl. a payload-only one) gets updated."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins",
        lambda repo_dir: ["agent-bridge", "context-handoff", "efforts"],
    )
    # All are already installed -> "update" verb.
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )

    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins()

    # Marketplace refreshed once.
    assert ["copilot", "plugin", "marketplace", "update", reconcile.MARKETPLACE] in calls
    # Each registered plugin updated (payload-only context-handoff included).
    for name in ("agent-bridge", "context-handoff", "efforts"):
        assert [
            "copilot", "plugin", "update", f"{name}@{reconcile.MARKETPLACE}"
        ] in calls


def test_installed_but_inactive_plugin_is_updated(monkeypatch):
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: [])
    monkeypatch.setattr(
        reconcile, "read_installed_plugins", lambda: ["context-handoff"]
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or _ok(),
    )

    assert m._update_registered_plugins()
    assert [
        "copilot",
        "plugin",
        "update",
        "context-handoff@copilot-extensions",
    ] in calls


def test_retired_inactive_plugin_is_purged(monkeypatch, capsys):
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: [])
    monkeypatch.setattr(
        reconcile,
        "read_installed_plugins",
        lambda: ["context-handoff", "retired-plugin"],
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1:4] == ["plugin", "marketplace", "browse"]:
            return _ok(
                'Plugins in "copilot-extensions":\n'
                "  \u2022 context-handoff - Current plugin\n"
            )
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins()
    assert [
        "copilot",
        "plugin",
        "uninstall",
        "retired-plugin@copilot-extensions",
    ] in calls
    assert [
        "copilot",
        "plugin",
        "update",
        "retired-plugin@copilot-extensions",
    ] not in calls
    assert "retired-plugin (OK (purged))" in capsys.readouterr().out


def test_retired_active_plugin_is_not_purged(monkeypatch):
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile,
        "read_enabled_plugins",
        lambda repo_dir: ["retired-plugin"],
    )
    monkeypatch.setattr(
        reconcile, "read_installed_plugins", lambda: ["retired-plugin"]
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1:4] == ["plugin", "marketplace", "browse"]:
            return _ok(
                'Plugins in "copilot-extensions":\n'
                "  \u2022 context-handoff - Current plugin\n"
            )
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins()
    assert not any("uninstall" in call for call in calls)
    assert [
        "copilot",
        "plugin",
        "update",
        "retired-plugin@copilot-extensions",
    ] in calls


def test_unreadable_marketplace_inventory_skips_purge(monkeypatch):
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: [])
    monkeypatch.setattr(
        reconcile, "read_installed_plugins", lambda: ["retired-plugin"]
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1:4] == ["plugin", "marketplace", "browse"]:
            return _fail()
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins()
    assert not any("uninstall" in call for call in calls)
    assert [
        "copilot",
        "plugin",
        "update",
        "retired-plugin@copilot-extensions",
    ] in calls


def test_inactive_inventory_failure_is_advisory(monkeypatch, capsys):
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: [])
    monkeypatch.setattr(
        reconcile, "read_installed_plugins", lambda: ["context-handoff"]
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )

    def fake_run(argv, **kwargs):
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        return _fail()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins() is True
    assert "inactive installed inventory advisory" in capsys.readouterr().out


def test_active_plugin_failure_remains_fatal(monkeypatch):
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile,
        "read_enabled_plugins",
        lambda repo_dir: ["context-handoff"],
    )
    monkeypatch.setattr(
        reconcile,
        "read_installed_plugins",
        lambda: ["context-handoff"],
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )

    def fake_run(argv, **kwargs):
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        return _fail()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins() is False


@pytest.mark.parametrize(
    "failed_scope",
    ["invocation-discovery", "invocation", "repo", "config"],
)
def test_activation_read_failure_keeps_installed_payload_hard(
    monkeypatch,
    tmp_path,
    failed_scope,
):
    anchor = Path("/repo/anchor")
    _install_config(monkeypatch, str(anchor))
    invocation = tmp_path / "invocation"
    if failed_scope == "invocation-discovery":
        monkeypatch.setattr(
            m,
            "_invocation_update_context",
            lambda: (_ for _ in ()).throw(
                OSError("invocation settings unavailable")
            ),
        )
    elif failed_scope == "invocation":
        settings = invocation / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(m, "_INVOCATION_CWD", invocation)
    elif failed_scope == "config":
        monkeypatch.setattr(
            cfg,
            "load_config",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("project registry unavailable")
            ),
        )

    def read_enabled(repo_dir):
        if (
            failed_scope == "invocation"
            and repo_dir == invocation
        ) or (
            failed_scope == "repo"
            and repo_dir == anchor
        ):
            raise OSError("activation settings unavailable")
        return []

    monkeypatch.setattr(reconcile, "read_enabled_plugins", read_enabled)
    monkeypatch.setattr(
        reconcile,
        "read_installed_plugins",
        lambda: ["agent-codespaces"],
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )
    targets = m._registered_plugin_targets()

    assert targets["agent-codespaces"].activation is m._PluginActivation.UNKNOWN
    assert targets["agent-codespaces"].required

    def fail_payload(argv, **kwargs):
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        return _fail()

    monkeypatch.setattr(subprocess, "run", fail_payload)
    assert m._update_registered_plugins(targets) is False


def test_invocation_context_detects_local_only_settings(monkeypatch, tmp_path):
    invocation = tmp_path / "repo" / "subdir"
    settings = (
        invocation.parent
        / ".github"
        / "copilot"
        / "settings.local.json"
    )
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "_INVOCATION_CWD", invocation)

    assert m._invocation_update_context() == invocation.parent


def test_malformed_user_global_settings_keeps_installed_payload_hard(
    monkeypatch,
    tmp_path,
):
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: [])
    copilot_home = tmp_path / ".copilot"
    copilot_home.mkdir()
    (copilot_home / "settings.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(reconcile, "_copilot_home", lambda: copilot_home)
    monkeypatch.setattr(
        reconcile,
        "read_user_enabled_plugins",
        _READ_USER_ENABLED_PLUGINS,
    )
    monkeypatch.setattr(
        reconcile,
        "read_installed_plugins",
        lambda: ["agent-codespaces"],
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )
    targets = m._registered_plugin_targets()

    assert targets["agent-codespaces"].activation is m._PluginActivation.UNKNOWN
    assert targets["agent-codespaces"].required

    def fail_payload(argv, **kwargs):
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        return _fail()

    monkeypatch.setattr(subprocess, "run", fail_payload)
    assert m._update_registered_plugins(targets) is False


def test_active_and_inactive_success_preserves_overall_success(monkeypatch):
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile,
        "read_enabled_plugins",
        lambda repo_dir: ["context-handoff"],
    )
    monkeypatch.setattr(
        reconcile,
        "read_installed_plugins",
        lambda: ["context-handoff", "visions"],
    )
    monkeypatch.setattr(
        reconcile,
        "core_installed_payload_dir",
        lambda name: Path(f"/inst/{name}"),
    )
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: _ok())

    assert m._update_registered_plugins() is True


def test_missing_payload_install_uses_activation_preserving_wrapper(monkeypatch):
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: None)
    called: list[tuple[list[str], str]] = []

    def preserving(argv, identity, **kwargs):
        called.append((list(argv), identity))
        return _ok()

    monkeypatch.setattr(
        activation_preservation, "run_install_preserving_activation", preserving
    )

    assert (
        m._update_one_plugin_payload("efforts", "copilot-extensions")
        == "OK (installed)"
    )
    assert called == [
        (
            [
                "copilot",
                "plugin",
                "install",
                "efforts@copilot-extensions",
            ],
            "efforts@copilot-extensions",
        )
    ]


def test_malformed_activation_state_is_reported_without_raising(monkeypatch):
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: None)

    def fail_preservation(argv, identity, **kwargs):
        raise activation_preservation.PluginStateError("malformed settings")

    monkeypatch.setattr(
        activation_preservation,
        "run_install_preserving_activation",
        fail_preservation,
    )
    assert m._update_one_plugin_payload(
        "efforts", "copilot-extensions"
    ) == "plugin state error: malformed settings"


def test_repo_declared_marketplace_operations_run_from_declaring_anchor(monkeypatch):
    """Copilot children inherit the repo settings that declare the marketplace."""
    anchor = Path("/repo/anchor")
    _install_config(monkeypatch, str(anchor))
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins", lambda repo_dir: ["context-handoff"]
    )
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )
    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(argv, **kw):
        calls.append((list(argv), kw.get("cwd")))
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins() is True
    assert calls
    assert all(cwd == anchor for _argv, cwd in calls)


def test_trusted_invocation_context_wins_over_untrusted_anchor(monkeypatch, tmp_path):
    """A linked invocation worktree supplies settings instead of its main anchor."""
    anchor = Path("/repo/anchor")
    invocation = tmp_path / "worktree"
    settings = invocation / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}")
    _install_config(monkeypatch, str(anchor))
    monkeypatch.setattr(m, "_INVOCATION_CWD", invocation)
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins", lambda repo_dir: ["context-handoff"]
    )
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )
    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(argv, **kw):
        calls.append((list(argv), kw.get("cwd")))
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins() is True
    assert calls
    assert all(cwd == invocation for _argv, cwd in calls)


def test_manager_transport_context_wins_after_project_chdir(monkeypatch, tmp_path):
    invocation = tmp_path / "invocation"
    settings = invocation / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}")
    monkeypatch.setattr(m, "_INVOCATION_CWD", Path("/repo/anchor"))
    monkeypatch.setenv(m._UPDATE_CONTEXT_ENV, str(invocation))

    assert m._invocation_update_context() == invocation


def test_missing_plugin_uses_install_path(monkeypatch):
    """A plugin whose payload is not installed is installed, not updated."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins", lambda repo_dir: ["context-handoff"]
    )
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: None)

    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    m._update_registered_plugins()

    assert [
        "copilot", "plugin", "install", f"context-handoff@{reconcile.MARKETPLACE}"
    ] in calls
    # It must NOT try the update verb for an uninstalled plugin.
    assert not any(c[:3] == ["copilot", "plugin", "update"] for c in calls)


def test_single_failure_warns_and_continues(monkeypatch):
    """One plugin failing does not abort the rest of the loop."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins",
        lambda repo_dir: ["aaa", "bbb", "ccc"],
    )
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )

    updated: list[str] = []

    def fake_run(argv, **kw):
        # marketplace refresh
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        name = argv[3].split("@")[0]
        updated.append(name)
        if name == "bbb":
            return _fail()  # middle plugin fails
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Must not raise despite bbb failing.
    assert m._update_registered_plugins() is False

    # All three were attempted (loop continued past the failure).
    assert updated == ["aaa", "bbb", "ccc"]


def test_timeout_on_one_plugin_does_not_abort(monkeypatch):
    """A marketplace/plugin timeout warns and continues."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins", lambda repo_dir: ["aaa", "bbb"]
    )
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )

    attempted: list[str] = []

    def fake_run(argv, **kw):
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        name = argv[3].split("@")[0]
        attempted.append(name)
        if name == "aaa":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=120)
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert m._update_registered_plugins() is False

    assert attempted == ["aaa", "bbb"]


def test_no_config_is_non_fatal(monkeypatch):
    """No resolvable project config -> silent no-op, no subprocess calls."""
    def _boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr(cfg, "load_config", _boom)

    def fake_run(argv, **kw):  # pragma: no cover - must not be called
        raise AssertionError("subprocess.run should not run without config")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert m._update_registered_plugins() is True


def test_no_registered_plugins_skips_marketplace(monkeypatch):
    """Empty registered set -> no marketplace refresh, no plugin calls."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: [])

    def fake_run(argv, **kw):  # pragma: no cover - must not be called
        raise AssertionError("no subprocess calls expected")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert m._update_registered_plugins() is True


@pytest.mark.parametrize(("payload_result", "expected_rc"), [(True, 0), (False, 1)])
def test_ordering_plugins_before_services(monkeypatch, payload_result, expected_rc):
    """cmd_update refreshes ALL plugin payloads BEFORE service modules/runtimes."""
    _install_config(monkeypatch, "/repo/anchor")
    order: list[str] = []
    run_cwds: list[Path | None] = []
    collections: list[str] = []

    # Step 1: agent-worktrees payload update (subprocess).
    def fake_run(argv, **kw):
        order.append("aw-plugin-update")
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        m, "_update_registered_plugins",
        lambda targets: (order.append("registered-plugins"), payload_result)[1],
    )
    monkeypatch.setattr(
        m,
        "_registered_plugin_targets",
        lambda: (collections.append("collected") or {}),
    )
    monkeypatch.setattr(
        m, "_find_installed_plugin_dir", lambda: Path("/plugin/dir")
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")

    # The agent-worktrees installer (runtime) and module/runtime steps.
    real_exists = Path.exists

    def fake_exists(self):
        if str(self).endswith("install.sh"):
            order.append("aw-installer")
            return True
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)

    def fake_installer_run(argv, **kw):
        # cmd_update runs the installer via subprocess without capture.
        return _ok()

    # cmd_update calls subprocess.run again for the installer; distinguish by
    # capture_output kwarg (plugin update captures; installer does not).
    def routed_run(argv, **kw):
        if kw.get("capture_output"):
            order.append("aw-plugin-update")
        else:
            order.append("aw-installer-run")
        run_cwds.append(kw.get("cwd"))
        return _ok()

    monkeypatch.setattr(subprocess, "run", routed_run)
    monkeypatch.setattr(
        m, "_update_modules",
        lambda *a, **k: order.append("modules"),
    )
    monkeypatch.setattr(
        m, "_fast_forward_project_anchors",
        lambda: order.append("anchors"),
    )

    args = types.SimpleNamespace(
        recreate_venv=False,
        skip_modules=None,
        no_anchor_sync=False,
        no_manager=True,
    )
    rc = m.cmd_update(args)
    assert rc == expected_rc
    assert run_cwds[0] == Path("/repo/anchor")

    # Registered plugin payloads happen before modules (services/runtimes).
    assert order.index("registered-plugins") < order.index("modules")
    # And the agent-worktrees payload update precedes the registered loop.
    assert order.index("aw-plugin-update") < order.index("registered-plugins")
    assert collections == ["collected"]


# ── user-global enabled plugins (#653) ────────────────────────────────────────

def test_user_global_enabled_are_refreshed(monkeypatch):
    """A plugin enabled ONLY user-global (not in any repo settings) is updated."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: [])
    monkeypatch.setattr(
        reconcile, "read_user_enabled_plugins", lambda: ["efforts", "visions"]
    )
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kw: (calls.append(list(argv)) or _ok())
    )

    m._update_registered_plugins()

    for name in ("efforts", "visions"):
        assert [
            "copilot", "plugin", "update", f"{name}@{reconcile.MARKETPLACE}"
        ] in calls


def test_union_of_repo_and_user_global(monkeypatch):
    """The refreshed set is the UNION of repo-scoped and user-global (deduped)."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins",
        lambda repo_dir: ["agent-bridge", "efforts"],
    )
    monkeypatch.setattr(
        reconcile, "read_user_enabled_plugins", lambda: ["efforts", "visions"]
    )
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )
    updated: list[str] = []

    def fake_run(argv, **kw):
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        updated.append(argv[3].split("@")[0])
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    m._update_registered_plugins()

    # efforts appears once (deduped); all three covered.
    assert sorted(updated) == ["agent-bridge", "efforts", "visions"]


def test_user_global_updates_without_project_config(monkeypatch):
    """No resolvable project config still refreshes USER-GLOBAL plugins (#653).

    The old behavior returned early when no config resolved, skipping even
    user-global-enabled plugins. The user-global read is now independent."""
    def _boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr(cfg, "load_config", _boom)
    monkeypatch.setattr(reconcile, "read_user_enabled_plugins", lambda: ["visions"])
    monkeypatch.setattr(
        reconcile, "core_installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kw: (calls.append(list(argv)) or _ok())
    )

    m._update_registered_plugins()  # must not raise despite no config

    assert [
        "copilot", "plugin", "update", f"visions@{reconcile.MARKETPLACE}"
    ] in calls
