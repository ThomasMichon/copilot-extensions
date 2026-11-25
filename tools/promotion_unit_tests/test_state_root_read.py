"""Exercise read-only context handlers without unrelated CLI integrations."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-worktrees/src/agent_worktrees/context_cli.py"


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    name = "promotion_state_root_reader"
    package = types.ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)
    for child in (
        "config", "output", "git_ops", "sessions", "state_root", "tracking",
        "installer", "installation_cli", "machine_instructions", "pr_config",
        "session_binding_cli", "status_updater_cli", "worktree_identity",
        "context_get_keys", "context_capabilities", "cli_pr_reminders",
        "execution_spaces",
    ):
        module = types.ModuleType(f"{name}.{child}")
        setattr(package, child, module)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    package.context_get_keys.GET_KEYS = {}
    package.context_get_keys.PR_GRAFT_DEPENDENT_KEYS = frozenset()
    def unrelated_pr_call(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("state-root reading must not invoke PR reminders")

    package.cli_pr_reminders.reminder_for = unrelated_pr_call
    package.cli_pr_reminders.emit = unrelated_pr_call
    procutil = types.ModuleType("agent_procutil")
    setattr(procutil, "no_window_kwargs", lambda: {})
    monkeypatch.setitem(sys.modules, "agent_procutil", procutil)
    specification = importlib.util.spec_from_file_location(f"{name}.context_cli", SOURCE)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    monkeypatch.setitem(sys.modules, specification.name, module)
    specification.loader.exec_module(module)
    return module


def _config(reader: types.ModuleType, *, conduct: bool = False) -> types.SimpleNamespace:
    config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor="/synthetic/harness")
    )

    def load_config(
        *, include_control_plane_related_pr: bool = True, include_knowledge_preferences: bool = True
    ) -> types.SimpleNamespace:
        assert include_control_plane_related_pr is False
        assert include_knowledge_preferences is conduct
        return config

    reader.cfg.load_config = load_config
    return config


@pytest.mark.parametrize(
    "arguments,bound,override",
    [
        (["--json"], True, None),
        ([], True, None),
        (["--repo", "alternate", "--json"], True, "alternate"),
        (["--json"], False, None),
        ([], False, None),
    ],
    ids=["json-bound", "plain-bound", "repo-override", "json-unbound", "plain-unbound"],
)
def test_state_root_read_preserves_response_without_pr_overlays(
    reader: types.ModuleType, capsys: pytest.CaptureFixture[str],
    arguments: list[str], bound: bool, override: str | None,
) -> None:
    config = _config(reader)
    payload = {
        "state_root": "/synthetic/knowledge" if bound else None,
        "source": "knowledge_repo",
        "repo": "knowledge",
        "stateless": True,
        "requires_external": True,
        "bound": bound,
        "error": None if bound else "No knowledge repository is bound",
    }
    result = types.SimpleNamespace(
        path=payload["state_root"],
        error=payload["error"],
        as_dict=lambda: payload.copy(),
    )

    def resolve_state_root(
        actual: types.SimpleNamespace, *, repo_override: str | None,
    ) -> types.SimpleNamespace:
        assert actual is config
        assert repo_override == override
        return result

    reader.state_root_mod.resolve_state_root = resolve_state_root
    assert reader.cmd_state_root_dispatch(arguments) == (0 if bound else 3)
    captured = capsys.readouterr()
    if "--json" in arguments:
        assert json.loads(captured.out) == payload
        assert captured.err == ""
    elif bound:
        assert captured.out == "/synthetic/knowledge\n"
        assert captured.err == ""
    else:
        assert captured.out == ""
        assert captured.err == "No knowledge repository is bound\n"


def test_state_root_conduct_preserves_pair_and_launch_context(
    reader: types.ModuleType, capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(reader, conduct=True)
    result = object()
    pair = object()
    reader.state_root_mod.resolve_state_root = lambda actual, **kw: result
    reader.state_root_mod._git_toplevel = lambda cwd: "/synthetic/checkout"
    setattr(reader, "os", types.SimpleNamespace(getcwd=lambda: "/synthetic/launch"))

    def resolve_pair(actual: types.SimpleNamespace, *, cwd: str) -> object:
        assert actual is config
        assert cwd == "/synthetic/launch"
        return pair

    def definition(actual: object, **values: object) -> str:
        assert actual is result
        assert values == {
            "pair": pair,
            "launch_path": "/synthetic/checkout",
            "launch_anchor": "/synthetic/harness",
        }
        return "State repository definition"

    reader.state_root_mod.resolve_pair = resolve_pair
    reader.state_root_mod.state_repo_definition = definition
    assert reader.cmd_state_root_dispatch(["--conduct"]) == 0
    assert capsys.readouterr().out == "State repository definition\n"


def test_state_root_keeps_config_refusal_visible(reader: types.ModuleType) -> None:
    def refused(
        *, include_control_plane_related_pr: bool = True, include_knowledge_preferences: bool = True
    ) -> NoReturn:
        assert include_control_plane_related_pr is False
        assert include_knowledge_preferences is False
        raise ValueError("invalid state-root binding")

    reader.cfg.load_config = refused
    with pytest.raises(ValueError, match="invalid state-root binding"):
        reader.cmd_state_root_dispatch(["--json"])


def test_state_root_pair_keeps_its_existing_dispatch(reader: types.ModuleType) -> None:
    calls: list[bool] = []
    def pair_dispatch(as_json: bool) -> int:
        calls.append(as_json)
        return 3

    setattr(reader, "_state_root_pair", pair_dispatch)
    assert reader.cmd_state_root_dispatch(["--pair", "--json"]) == 3
    assert calls == [True]


@pytest.fixture
def get_reader(reader: types.ModuleType) -> types.ModuleType:
    config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(
            anchor="/synthetic/repo", remote="origin", worktree_root="/synthetic/worktrees",
            pr=types.SimpleNamespace(enabled=True, required=False, provider="github"),
        ),
        srcroot="/synthetic", machine="host", platform="linux", repo_name="repo",
    )
    setattr(reader, "_PR_GRAFT_DEPENDENT_KEYS", frozenset({
        "pr-enabled", "pr-required", "pr-provider", "pr-profile",
    }))
    reader.cfg.load_config = lambda **kwargs: config
    reader.cfg.project_dir = lambda: Path("/synthetic/state")
    reader.cfg.active_project = lambda: "repo"
    reader.context_capabilities.emit = lambda key: False
    reader.pr_config._resolve_repo_remote = lambda actual, repo: "https://example.com/repo.git"
    reader.pr_config._pr_flow_profile = lambda repo: types.SimpleNamespace(profile="pr-required")
    setattr(reader, "_resolve_lease_origin", lambda: "local")
    core = types.ModuleType("synthetic_core")
    setattr(reader, "_core", lambda: core)
    return reader


@pytest.mark.parametrize("key,expected", [
    ("repo-remote", "https://example.com/repo.git"),
    ("lease-origin", "local"),
    ("pr-enabled", "true"),
    ("pr-required", "false"),
    ("pr-provider", "github"),
    ("pr-profile", "pr-required"),
])
def test_get_repository_values_skip_workspace_work(
    get_reader: types.ModuleType, capsys: pytest.CaptureFixture[str], key: str, expected: str,
) -> None:
    reader = get_reader
    calls: list[dict[str, object]] = []
    load: Callable[..., types.SimpleNamespace] = reader.cfg.load_config

    def observed_load(**kwargs: object) -> types.SimpleNamespace:
        calls.append(kwargs)
        return load(**kwargs)

    reader.cfg.load_config = observed_load

    def unexpected(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("repository value must not infer or adopt a workspace")

    core = getattr(reader, "_core")()
    core._infer_worktree_id_from_cwd = unexpected
    core._worktree_path_for_id = unexpected
    core._cwd_is_inside_project = unexpected
    assert reader.cmd_get(types.SimpleNamespace(key=key)) == 0
    assert capsys.readouterr().out == expected + "\n"
    assert calls == [{"include_control_plane_related_pr": key.startswith("pr-")}]


@pytest.mark.parametrize("key", ["worktree-id", "session-scope-id", "owner-ref"])
def test_get_identity_values_do_not_resolve_checkout_path(
    get_reader: types.ModuleType, capsys: pytest.CaptureFixture[str], key: str,
) -> None:
    reader = get_reader
    calls: list[str] = []
    core = getattr(reader, "_core")()

    def infer(config: object) -> str:
        calls.append("identity")
        return "worktree"

    core._infer_worktree_id_from_cwd = infer
    core._cwd_is_inside_project = lambda anchor: False

    def unexpected(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("identity value must not resolve an unused checkout path")

    core._worktree_path_for_id = unexpected
    spaces = sys.modules[f"{reader.__package__}.execution_spaces"]
    setattr(spaces, "ExecutionSpaceError", ValueError)
    setattr(spaces, "require_current_execution_space", lambda config: calls.append("execution-space"))
    reader.worktree_identity.reference_owner = lambda *args: "host/repo/worktree"
    assert reader.cmd_get(types.SimpleNamespace(key=key)) == 0
    assert capsys.readouterr().out == (
        "host/repo/worktree\n" if key == "owner-ref" else "worktree\n"
    )
    assert calls == (["execution-space", "identity"] if key == "owner-ref" else ["identity"])


def test_get_repo_remote_keeps_session_activation_and_config_refusal(
    get_reader: types.ModuleType, capsys: pytest.CaptureFixture[str],
) -> None:
    reader = get_reader
    calls: list[str] = []

    def activate(sid: str) -> str:
        calls.append(sid)
        return "wt"

    reader.session_binding_cli._activate_session_binding = activate

    def refused(**kwargs: object) -> NoReturn:
        raise ValueError("invalid repository configuration")

    reader.cfg.load_config = refused
    reader.output.err = lambda message: print(message, file=sys.stderr)
    assert reader.cmd_get(types.SimpleNamespace(key="repo-remote", session_id="target")) == 1
    assert calls == ["target"]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Cannot load config: invalid repository configuration\n"


def test_get_owner_ref_keeps_execution_space_refusal(
    get_reader: types.ModuleType, capsys: pytest.CaptureFixture[str],
) -> None:
    reader = get_reader
    spaces = sys.modules[f"{reader.__package__}.execution_spaces"]
    setattr(spaces, "ExecutionSpaceError", ValueError)

    def refused(config: object) -> NoReturn:
        raise ValueError("execution owner is unavailable")

    setattr(spaces, "require_current_execution_space", refused)
    reader.output.err = lambda message: print(message, file=sys.stderr)
    assert reader.cmd_get(types.SimpleNamespace(key="owner-ref")) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "execution owner is unavailable\n"
