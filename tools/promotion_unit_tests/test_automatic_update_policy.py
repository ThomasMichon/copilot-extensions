"""Automatic policy smoke controls without loading native execution owners."""

from __future__ import annotations

import ast
import importlib
from io import StringIO
import json
from pathlib import Path, PureWindowsPath
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worktree-manager/src"))
from worktree_manager import bootstrap_policy, maintenance_policy


@pytest.fixture
def policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    source = ROOT / "libs/update-driver/src/update_driver/core.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name in {
            "automatic_update_refusal", "_automatic_update_inhibited",
        }
    ]
    assert len(selected) == 3
    namespace = {
        "__name__": "unit_automatic_update_policy", "Path": Path, "json": json,
        "importlib": importlib, "sys": SimpleNamespace(
            version_info=sys.version_info, stderr=StringIO(),
        ),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(home / "manager"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    return SimpleNamespace(home=home, owner=SimpleNamespace(**namespace))


def spec(*arguments: str, **context: str) -> SimpleNamespace:
    return SimpleNamespace(
        backend="engine", argv=arguments, context=tuple(context.items()), killswitch_path="", cwd="",
    )


@pytest.fixture
def manager_policy_boundary(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "update_driver", SimpleNamespace(
        RequestSpec=lambda backend, argv, **kwargs: SimpleNamespace(backend=backend, argv=argv, **kwargs),
    ))
    monkeypatch.setitem(sys.modules, "update_driver.core", SimpleNamespace(
        automatic_update_refusal=policy.owner.automatic_update_refusal,
    ))


def test_relative_manager_policy_uses_retained_request_directory(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = policy.home / "caller"
    manager = caller / "manager"
    manager.mkdir(parents=True)
    path = manager / "config.toml"
    path.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    request = spec("--auto", WORKTREE_MANAGER_ROOT="manager")
    request.cwd = str(caller)
    monkeypatch.chdir(policy.home)
    assert policy.owner.automatic_update_refusal(request) is None
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    assert "maintenance.automatic is false" in policy.owner.automatic_update_refusal(request)
    request.argv = ()
    assert policy.owner.automatic_update_refusal(request) is None


@pytest.mark.parametrize("cwd", ["", "relative-caller"])
def test_relative_manager_policy_requires_absolute_request_directory(
    policy: SimpleNamespace, cwd: str,
) -> None:
    request = spec("--auto", WORKTREE_MANAGER_ROOT="manager")
    request.cwd = cwd
    assert "absolute request cwd" in policy.owner.automatic_update_refusal(request)
    request.argv = ()
    assert policy.owner.automatic_update_refusal(request) is None


def test_hold_reader_normalizes_missing_parent_segments(policy: SimpleNamespace) -> None:
    path = policy.home / "hold.json"
    path.write_text('{"active":true}', encoding="utf-8")
    request = spec("--auto")
    request.killswitch_path = str(policy.home / "missing/../hold.json")
    assert "KillSwitch active" in policy.owner.automatic_update_refusal(request)
    request.argv = ()
    assert policy.owner.automatic_update_refusal(request) is None


def test_drive_relative_manager_root_never_borrows_worker_drive(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WindowsPolicyPath(PureWindowsPath):
        def expanduser(self) -> WindowsPolicyPath:
            return self

        def read_text(self, **kwargs: object) -> str:
            pytest.fail("unresolved drive-relative policy read")

    monkeypatch.setitem(policy.owner.automatic_update_refusal.__globals__, "Path", WindowsPolicyPath)
    request = spec("--auto", WORKTREE_MANAGER_ROOT="C:manager")
    request.cwd = "D:/caller"
    assert "cannot resolve against request cwd" in policy.owner.automatic_update_refusal(request)
    request.argv = ()
    assert policy.owner.automatic_update_refusal(request) is None


def test_plain_update_never_reads_automation_policy(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("manual policy read"))
    assert policy.owner.automatic_update_refusal(spec()) is None
    assert policy.owner.automatic_update_refusal(spec("--force")) is None


@pytest.mark.parametrize("value", ["false", "'false'", "false\nunknown = true"])
def test_current_automatic_request_rechecks_strict_manager_policy(
    policy: SimpleNamespace, value: str,
) -> None:
    root = policy.home / "manager"
    root.mkdir()
    path = root / "config.toml"
    path.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    request = spec("--auto", WORKTREE_MANAGER_ROOT=str(root))
    assert policy.owner.automatic_update_refusal(request) is None
    path.write_text("[maintenance]\nautomatic = " + value, encoding="utf-8")
    assert policy.owner.automatic_update_refusal(request)
    assert policy.owner._automatic_update_inhibited(request)
    assert policy.owner.automatic_update_refusal(spec(WORKTREE_MANAGER_ROOT=str(root))) is None


@pytest.mark.parametrize("software_update", [False, True])
def test_launch_rechecks_maintenance_but_no_update_ignores_only_software_hold(
    policy: SimpleNamespace, software_update: bool,
) -> None:
    root = policy.home / "manager"
    root.mkdir()
    path = root / "config.toml"
    path.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    hold = policy.home / ".copilot-extensions/killSwitches/bootstrap-killswitch.json"
    hold.parent.mkdir(parents=True)
    hold.write_text('{"active":true}', encoding="utf-8")
    flags = [] if software_update else ["--no-update"]
    request = spec("--auto", *flags, "--with-reconcile", WORKTREE_MANAGER_ROOT=str(root))
    request.backend = "engine-launch-update"
    initial = policy.owner.automatic_update_refusal(request)
    assert (initial is not None) is software_update
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    assert "maintenance.automatic is false" in policy.owner.automatic_update_refusal(request)
    request.argv = tuple(flags)
    assert policy.owner.automatic_update_refusal(request) is None


def test_legacy_parser_dispatch_and_missing_parser_are_explicit(policy: SimpleNamespace) -> None:
    root = policy.home / "manager"
    root.mkdir()
    (root / "config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    request = spec("--auto", WORKTREE_MANAGER_ROOT=str(root))
    requested = []
    parser = importlib.import_module("tomllib")
    namespace = policy.owner.automatic_update_refusal.__globals__
    namespace["sys"] = SimpleNamespace(version_info=(3, 10), stderr=StringIO())
    namespace["importlib"] = SimpleNamespace(
        import_module=lambda name: requested.append(name) or parser,
    )
    assert "maintenance.automatic is false" in policy.owner.automatic_update_refusal(request)
    assert requested == ["tomli"]

    def missing(name: str) -> None:
        raise ModuleNotFoundError(name)

    namespace["importlib"] = SimpleNamespace(import_module=missing)
    assert "unsupported" in policy.owner.automatic_update_refusal(request)
    assert policy.owner.automatic_update_refusal(spec(WORKTREE_MANAGER_ROOT=str(root))) is None


def test_independent_cell_keeps_default_installation_scope(policy: SimpleNamespace) -> None:
    root = policy.home / ".worktree-manager"
    root.mkdir()
    (root / "config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    cell = {"COPILOT_EXTENSIONS_CONTEXT": str(policy.home / "cell/install.json")}
    assert policy.owner.automatic_update_refusal(spec("--auto", **cell)) is None
    assert policy.owner.automatic_update_refusal(spec(
        "--auto", **cell, WORKTREE_MANAGER_ROOT=str(root),
    ))


def test_bootstrap_freezes_addressed_root_and_keeps_legacy_reader_veto(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import update_driver_cli

    source = ROOT / "worktree-manager/scripts/runtime_install.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name in {
            "_automatic_bootstrap_refusal", "_automatic_bootstrap_inhibited",
        }
    ]
    assert len(selected) == 3
    root = policy.home / "addressed-manager"
    root.mkdir()
    (root / "config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    monkeypatch.setattr(
        update_driver_cli, "request_context",
        lambda: {"WORKTREE_MANAGER_ROOT": str(policy.home / "different-default")},
    )
    driver = SimpleNamespace(
        RequestSpec=lambda backend, argv, **kwargs: SimpleNamespace(backend=backend, argv=argv, **kwargs),
        automatic_update_refusal=policy.owner.automatic_update_refusal,
    )
    scripts = policy.home / "current-controller" / "scripts"

    def execution_driver(payload):
        assert payload == scripts.parent
        return driver

    namespace = {
        "_execution_driver": execution_driver, "_bootstrap_hold_path": lambda: "",
        "SCRIPTS": scripts,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    args = SimpleNamespace(root=root, payload=root, command=["update", "--auto"])
    assert "maintenance.automatic is false" in namespace["_automatic_bootstrap_refusal"](args)
    driver = SimpleNamespace(
        RequestSpec=driver.RequestSpec, _automatic_update_inhibited=lambda request: False,
    )
    assert namespace["_automatic_bootstrap_inhibited"](args)
    args.command = ["update"]
    namespace["_execution_driver"] = lambda payload: pytest.fail("manual consulted automation owner")
    assert namespace["_automatic_bootstrap_refusal"](args) is None


def test_read_only_controls_preserve_defaults_without_creating_state(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bootstrap_policy, "_guard_command", lambda: ["inert-controller"])
    monkeypatch.setattr(bootstrap_policy, "update_refusal", lambda args: None)
    assert maintenance_policy.inspect_policy().enabled is True
    row = bootstrap_policy.configuration_row()
    assert row["enabled"] == "ON" and row["bootstrap_control"]
    assert not list(policy.home.iterdir())


def test_direct_manager_recheck_keeps_its_invocation_directory(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, manager_policy_boundary: None,
) -> None:
    root = policy.home / "manager"
    root.mkdir()
    path = root / "config.toml"
    path.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    monkeypatch.chdir(policy.home)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", "manager")
    assert maintenance_policy.update_refusal(["--auto"]) is None
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    assert "maintenance.automatic is false" in maintenance_policy.update_refusal(["--auto"])
    assert maintenance_policy.update_refusal([]) is None


@pytest.mark.parametrize("root_form", ["absolute", "relative", "home", "missing-parent"])
def test_configuration_inspection_reads_the_same_policy_as_admission(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, root_form: str,
    manager_policy_boundary: None,
) -> None:
    root = policy.home / "manager"
    root.mkdir()
    path = root / "config.toml"
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    caller = policy.home / "caller"
    shadow = caller / "~" / "manager" / "config.toml"
    shadow.parent.mkdir(parents=True)
    shadow.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    monkeypatch.chdir(caller)
    roots = {
        "absolute": str(root), "relative": "../manager", "home": "~/manager",
        "missing-parent": str(policy.home / "missing/../manager"),
    }
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", roots[root_form])
    state = maintenance_policy.inspect_policy()
    row = maintenance_policy.configuration_row()
    assert state.path == path.resolve() and state.enabled is False
    assert row["enabled"] == "OFF" and row["activity"] == "blocked"
    request = spec("--auto", WORKTREE_MANAGER_ROOT=roots[root_form])
    request.cwd = str(caller)
    assert "maintenance.automatic is false" in policy.owner.automatic_update_refusal(request)
    assert shadow.read_text(encoding="utf-8") == "[maintenance]\nautomatic = true\n"
    assert not list(root.glob(".*.lock"))


@pytest.mark.parametrize("root_form", ["absolute", "relative", "home"])
def test_submission_context_freezes_the_same_selected_policy(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, root_form: str,
) -> None:
    from worktree_manager import update_driver_cli

    root = policy.home / "manager"
    root.mkdir()
    path = root / "config.toml"
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    caller = policy.home / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    roots = {"absolute": str(root), "relative": "../manager", "home": "~/manager"}
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", roots[root_form])
    context = update_driver_cli.request_context()
    assert context["WORKTREE_MANAGER_ROOT"] == str(root.resolve())
    request = spec("--auto", **context)
    request.cwd = str(caller)
    assert "maintenance.automatic is false" in policy.owner.automatic_update_refusal(request)
    request.argv = ()
    assert policy.owner.automatic_update_refusal(request) is None
    path.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    request.argv = ("--auto",)
    assert policy.owner.automatic_update_refusal(request) is None
    assert not list(caller.iterdir())
    assert update_driver_cli.os.environ["WORKTREE_MANAGER_ROOT"] == roots[root_form]


@pytest.mark.parametrize("root_form", ["absolute", "relative", "home"])
def test_borrowed_manager_context_freezes_root_and_restores_worker(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, root_form: str,
    manager_policy_boundary: None,
) -> None:
    from worktree_manager import update_driver_cli

    root = policy.home / "manager"
    root.mkdir()
    (root / "config.toml").write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    worker = policy.home / "worker"
    (worker / "manager").mkdir(parents=True)
    (worker / "manager/config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    monkeypatch.chdir(worker)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", "worker-original")
    roots = {"absolute": str(root), "relative": "manager", "home": "~/manager"}
    with update_driver_cli._request_environment(
        (("WORKTREE_MANAGER_ROOT", roots[root_form]),), cwd=str(policy.home),
    ):
        assert maintenance_policy.update_refusal(["--auto"]) is None
        assert maintenance_policy.inspect_policy().path == root / "config.toml"
    assert maintenance_policy.inspect_policy().path == worker / "worker-original/config.toml"
    assert update_driver_cli.os.environ["WORKTREE_MANAGER_ROOT"] == "worker-original"


def test_invalid_local_policy_is_unknown_not_an_enabled_default(policy: SimpleNamespace) -> None:
    root = policy.home / "manager"
    root.mkdir()
    (root / "config.toml").write_text("[maintenance]\nautomatic = 'false'\n", encoding="utf-8")
    state = maintenance_policy.inspect_policy()
    assert state.enabled is None and state.error


def test_cell_control_never_reads_or_edits_global_switch(
    policy: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(policy.home / "cell/install.json"))
    monkeypatch.setattr(bootstrap_policy, "_read", lambda *args: pytest.fail("global switch read"))
    row = bootstrap_policy.configuration_row()
    assert row["enabled"] == "not applicable" and not row["bootstrap_control"]
    assert not list(policy.home.iterdir())
