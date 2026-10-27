"""Single-request registry bindings, never persistent installation authority."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import registry_paths, state_root_request


def test_shared_reader_preserves_lenient_and_strict_parser_contract(tmp_path):
    from agent_worktrees.projects_registry import read_projects_registry

    path = tmp_path / "projects.yaml"
    path.write_text("projects: [invalid]\n", encoding="utf-8")
    assert read_projects_registry(path) == {"projects": {}}
    with pytest.raises(ValueError):
        read_projects_registry(path, strict=True)


def test_shared_lookup_preserves_adoption_priority_and_normalization(tmp_path):
    from agent_worktrees.projects_registry import project_for_anchor

    anchor = tmp_path / "CALLER"
    result = project_for_anchor(
        anchor,
        read_projects=lambda: {"projects": {"caller": {"anchor": str(anchor).lower()}}},
        read_repos=lambda: pytest.fail("identity fallback overrode adoption"),
        platform=lambda: "windows",
        normalize=lambda path: path.casefold(),
    )
    assert result == "caller"


def test_shared_lookup_retains_identity_fallback_after_unreadable_adoption(tmp_path):
    from agent_worktrees.projects_registry import project_for_anchor

    def unreadable():
        raise ValueError("invalid adoption metadata")

    result = project_for_anchor(
        tmp_path,
        read_projects=unreadable,
        read_repos=lambda: SimpleNamespace(
            repos={
                "caller": SimpleNamespace(local_path=lambda platform: str(tmp_path)),
            }
        ),
        platform=lambda: "windows",
        normalize=lambda path: path,
    )
    assert result == "caller"


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "install.json"))
    context = {
        "pluginRoot": str(tmp_path / "selected"),
        "generation": 1,
        "namespaceGeneration": 1,
        "source": {"fingerprint": "first"},
    }
    calls = []

    class Helper:
        def resolve_registry_context(self, **kwargs):
            calls.append(kwargs)
            return deepcopy(context)

        def resolve_registry_root(self, **kwargs):
            return Path(self.resolve_registry_context(**kwargs)["pluginRoot"])

    monkeypatch.setattr(registry_paths, "_helper", lambda: Helper())
    return context, calls


def test_request_reuses_only_its_canonical_binding_and_revalidates(owner, tmp_path):
    context, calls = owner
    with registry_paths._state_root_request_scope():
        for _ in range(4):
            assert registry_paths.registry_root(tmp_path / "wrong") == Path(context["pluginRoot"])
            assert registry_paths.installation_context() == context
        assert len(calls) == 1
    assert len(calls) == 2
    registry_paths.registry_root()
    registry_paths.installation_context()
    assert len(calls) == 4
    assert registry_paths._STATE_ROOT_REQUEST.get() is None


def test_separate_requests_do_not_reuse_loaded_authority(owner, tmp_path):
    context, calls = owner
    for generation in (1, 2):
        context.update(generation=generation, pluginRoot=str(tmp_path / str(generation)))
        with registry_paths._state_root_request_scope():
            assert registry_paths.registry_root() == tmp_path / str(generation)
    assert len(calls) == 4


@pytest.mark.parametrize("field", ["generation", "namespaceGeneration", "source"])
def test_authority_change_rejects_before_result_can_escape(owner, field):
    context, calls = owner
    with pytest.raises(state_root_request.StateRootRequestRefused, match="authority changed"):
        with registry_paths._state_root_request_scope():
            assert registry_paths.installation_context() == context
            context[field] = 2 if field != "source" else {"fingerprint": "foreign"}
    assert len(calls) == 2
    assert registry_paths._STATE_ROOT_REQUEST.get() is None


@pytest.mark.parametrize(
    "variable",
    [
        "COPILOT_EXTENSIONS_CONTEXT",
        "COPILOT_PLUGIN_ROOT",
        "AGENT_WORKTREES_PAYLOAD_ROOT",
    ],
)
def test_routing_change_cannot_select_another_root_inside_request(owner, monkeypatch, variable):
    with pytest.raises(state_root_request.StateRootRequestRefused, match="routing changed"):
        with registry_paths._state_root_request_scope():
            monkeypatch.setenv(variable, "another-owner")
            registry_paths.registry_root()
    assert registry_paths._STATE_ROOT_REQUEST.get() is None


def test_returned_context_cannot_mutate_the_binding(owner):
    context, _ = owner
    with registry_paths._state_root_request_scope():
        returned = registry_paths.installation_context()
        returned["source"]["fingerprint"] = "mutated"
        returned["pluginRoot"] = "foreign"
        assert registry_paths.installation_context() == context


def test_failed_read_drops_its_request_binding(owner):
    _, calls = owner
    with pytest.raises(RuntimeError, match="read failed"):
        with registry_paths._state_root_request_scope():
            raise RuntimeError("read failed")
    assert len(calls) == 2
    assert registry_paths._STATE_ROOT_REQUEST.get() is None


def test_config_error_is_not_reclassified_as_authority_refusal(owner):
    with pytest.raises(ValueError, match="invalid config") as error:
        with registry_paths._state_root_request_scope():
            raise ValueError("invalid config")
    assert type(error.value) is ValueError
    assert registry_paths._STATE_ROOT_REQUEST.get() is None


def test_nested_read_uses_outer_guard_not_a_new_authority_cache(owner):
    _, calls = owner
    with registry_paths._state_root_request_scope():
        with registry_paths._state_root_request_scope():
            registry_paths.installation_context()
    assert len(calls) == 2


def test_initial_refusal_never_enters_a_request(owner, monkeypatch):
    def refused():
        raise ValueError("context refused")

    monkeypatch.setattr(registry_paths, "_fresh_context", refused)
    with pytest.raises(state_root_request.StateRootRequestRefused, match="context refused"):
        with registry_paths._state_root_request_scope():
            pytest.fail("refused authority entered request")
    assert registry_paths._STATE_ROOT_REQUEST.get() is None


def test_resolve_uses_normal_owner_project_config_and_state_semantics(tmp_path, monkeypatch):
    from agent_worktrees import config, invocation_project, state_root

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    selected = config.Config(
        repo_name="caller",
        knowledge_repo="state",
        srcroot=str(tmp_path),
        machine="fixture",
        platform="windows",
        repos={
            "caller": config.RepoConfig(
                anchor=str(tmp_path / "caller"),
                worktree_root=str(tmp_path / "worktrees"),
                stateless=True,
            )
        },
    )
    project_calls, config_calls = [], []
    monkeypatch.setattr(
        invocation_project, "resolve_invocation_project", lambda *args: project_calls.append(args)
    )
    monkeypatch.setattr(
        config, "load_config", lambda **kwargs: config_calls.append(kwargs) or selected
    )
    monkeypatch.setattr(state_root, "_checkout_path", lambda name: str(tmp_path / name))
    value = state_root_request.resolve()
    assert value.bound and value.requires_external
    assert value.path == str(tmp_path / "state")
    assert project_calls[0][0:2] == (["state-root"], None)
    assert config_calls == [
        {
            "include_control_plane_related_pr": False,
            "include_knowledge_preferences": False,
        }
    ]
    assert registry_paths._STATE_ROOT_REQUEST.get() is None


def test_request_discovers_project_fresh_and_restores_callers_state(monkeypatch):
    from agent_worktrees import config, invocation_project, state_root

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(config, "_ACTIVE_PROJECT", "previous")

    def discover(*_args):
        assert config.active_project() is None
        config.set_active_project("selected")

    def unreadable(**_kwargs):
        assert config.active_project() == "selected"
        raise ValueError("fixture config error")

    monkeypatch.setattr(invocation_project, "resolve_invocation_project", discover)
    monkeypatch.setattr(config, "load_config", unreadable)
    monkeypatch.setattr(
        state_root, "resolve_state_root", lambda _: pytest.fail("invalid config used")
    )
    with pytest.raises(ValueError, match="fixture config error"):
        state_root_request.resolve()
    assert config.active_project() == "previous"
    assert registry_paths._STATE_ROOT_REQUEST.get() is None
