from __future__ import annotations

import json
from collections.abc import MutableMapping
from operator import setitem
from pathlib import Path
from typing import Any, cast

import pytest
from configuration_context import context_from_owner
from configuration_layers import ConfigurationError, read_source

from process_telemetry.command_config import (
    CAPABILITY,
    ProviderCapabilities,
    read_policy_capabilities,
    supports_policy,
)
from process_telemetry.policy import (
    POLICY_VERSION,
    PolicyOrigin,
    compose_policy,
)


def _context(tmp_path: Path, *, binding: str = "bound") -> dict[str, Any]:
    base = tmp_path / "harness"
    knowledge = tmp_path / "knowledge"
    base.mkdir(parents=True, exist_ok=True)
    knowledge.mkdir(parents=True, exist_ok=True)
    context: dict[str, Any] = {
        "repository_id": "harness-repository",
        "execution_machine": "logical-machine",
        "installation_cell": "installation-cell",
        "binding": binding,
        "base_root": str(base),
    }
    if binding == "bound":
        context["knowledge_root"] = str(knowledge)
    return context


def _layer(
    *,
    layer_id: str,
    tier: str,
    origin: str,
    values: dict[str, Any],
    repository_id: str | None = None,
    execution_machine: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    layer: dict[str, Any] = {
        "id": layer_id,
        "tier": tier,
        "origin": origin,
        "values": values,
    }
    if repository_id is not None:
        layer["repository_id"] = repository_id
    if execution_machine is not None:
        layer["execution_machine"] = execution_machine
    if root is not None:
        layer["source"] = {"root": str(root), "path": "config/telemetry.json"}
    return layer


def _source_layer(
    *,
    root: Path,
    layer_id: str,
    tier: str,
    origin: str,
    values: dict[str, Any],
    repository_id: str | None = None,
    execution_machine: str | None = None,
) -> dict[str, Any]:
    path = root / "config" / "telemetry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    source = read_source(str(root), "config/telemetry.json")
    assert source is not None
    layer = _layer(
        layer_id=layer_id,
        tier=tier,
        origin=origin,
        values=source["values"],
        repository_id=repository_id,
        execution_machine=execution_machine,
    )
    layer["source"] = source["source"]
    return layer


def _document(**fields: Any) -> dict[str, Any]:
    return {"telemetry": {"schema_version": POLICY_VERSION, **fields}}


def _base_layer(tmp_path: Path, *, scopes: dict[str, Any] | None = None) -> dict[str, Any]:
    return _layer(
        layer_id="harness-policy",
        tier="repository",
        origin="base",
        repository_id="harness-repository",
        root=tmp_path / "harness",
        values=_document(
            sinks={
                "local-a": {
                    "transport": "command-jsonl-v1",
                    "argv": ["receiver-a"],
                    "source_schemas": ["bridge.lifecycle.v1", "dispatch.lifecycle.v1"],
                },
                "local-b": {
                    "transport": "command-jsonl-v1",
                    "argv": ["receiver-b"],
                    "source_schemas": ["bridge.lifecycle.v1"],
                },
            },
            defaults={"lifecycle": "local-a"},
            scopes=scopes
            or {
                "lifecycle": {
                    "repositories": ["repo-a", "repo-b"],
                    "worktrees": ["worktree-a", "worktree-b"],
                },
            },
        ),
    )


def _provider() -> ProviderCapabilities:
    return ProviderCapabilities(
        policy_versions=frozenset({POLICY_VERSION}),
        transports=frozenset({"command-jsonl-v1"}),
        source_schemas=frozenset({"bridge.lifecycle.v1"}),
    )


def test_absent_optional_policy_remains_unconfigured(tmp_path: Path) -> None:
    result = compose_policy(_context(tmp_path, binding="unbound"), [])
    assert result.configured is False
    assert (
        result.resolve(
            "lifecycle",
            PolicyOrigin("repo-a", "worktree-a"),
            "bridge.lifecycle.v1",
            _provider(),
        ).state
        == "unconfigured"
    )


def test_policy_composes_all_native_tiers_with_scope_and_provenance(tmp_path: Path) -> None:
    context = _context(tmp_path)
    knowledge_root = Path(context["knowledge_root"])
    exact_root = tmp_path / "exact-machine"
    exact_root.mkdir()
    layers = [
        _base_layer(tmp_path),
        _layer(
            layer_id="knowledge-preferences",
            tier="repository",
            origin="knowledge",
            repository_id="harness-repository",
            root=knowledge_root,
            values=_document(usages={"lifecycle": "local-b"}),
        ),
        _layer(
            layer_id="machine-preferences",
            tier="machine",
            origin="local_machine",
            execution_machine="logical-machine",
            values=_document(
                usages={"lifecycle": "local-a"},
                scopes={"lifecycle": {"worktrees": ["worktree-a", "worktree-b"]}},
            ),
        ),
        _layer(
            layer_id="exact-preferences",
            tier="repository_machine",
            origin="repository_machine",
            repository_id="harness-repository",
            execution_machine="logical-machine",
            root=exact_root,
            values=_document(
                usages={"lifecycle": "local-b"},
                scopes={"lifecycle": {"repositories": ["repo-a", "repo-b"]}},
                exclusions={"lifecycle": {"repositories": ["repo-b"]}},
            ),
        ),
    ]

    policy = compose_policy(context, layers)
    decision = policy.resolve(
        "lifecycle",
        PolicyOrigin("repo-a", "worktree-a"),
        "bridge.lifecycle.v1",
        _provider(),
    )
    assert decision.state == "active"
    assert decision.sink is not None
    assert decision.sink.name == "local-b"
    assert decision.provenance is not None
    assert decision.provenance["id"] == "exact-preferences"
    assert decision.provenance["source"] == {
        "root": str(exact_root),
        "path": str(Path("config") / "telemetry.json"),
    }
    with pytest.raises(TypeError):
        setitem(cast(MutableMapping[str, Any], decision.provenance), "id", "changed")
    with pytest.raises(TypeError):
        setitem(
            cast(MutableMapping[str, Any], decision.provenance["source"]),
            "root",
            "changed",
        )
    assert policy.layers == (
        "harness-policy",
        "knowledge-preferences",
        "machine-preferences",
        "exact-preferences",
    )
    assert tuple(source["id"] for source in policy.scope_sources["lifecycle"]) == (
        "harness-policy",
        "machine-preferences",
        "exact-preferences",
    )
    assert policy.exclusion_sources["lifecycle"][0]["id"] == "exact-preferences"
    assert policy.exclusion_sources["lifecycle"][0]["source"] == {
        "root": str(exact_root),
        "path": str(Path("config") / "telemetry.json"),
    }
    assert (
        policy.resolve(
            "lifecycle",
            PolicyOrigin("repo-b", "worktree-b"),
            "bridge.lifecycle.v1",
            _provider(),
        ).state
        == "excluded"
    )


def test_native_context_and_file_reader_compose_exact_machine_policy(
    tmp_path: Path,
) -> None:
    native_context = _context(tmp_path)
    exact_root = tmp_path / "exact-machine"
    report = {
        "schema": "agent-worktrees.configuration",
        "version": 1,
        "context": native_context,
        "repository_machine_root": str(exact_root),
    }
    context = context_from_owner(report)
    base_root = Path(context["base_root"])
    knowledge_root = Path(context["knowledge_root"])
    layers = [
        _source_layer(
            root=base_root,
            layer_id="harness-policy",
            tier="repository",
            origin="base",
            repository_id="harness-repository",
            values=_document(
                sinks={
                    "local": {
                        "transport": "command-jsonl-v1",
                        "argv": ["receiver"],
                        "source_schemas": ["bridge.lifecycle.v1"],
                    },
                },
                defaults={"lifecycle": "local"},
                scopes={"lifecycle": {"repositories": ["repo-a"]}},
            ),
        ),
        _source_layer(
            root=knowledge_root,
            layer_id="knowledge-preferences",
            tier="repository",
            origin="knowledge",
            repository_id="harness-repository",
            values=_document(usages={"lifecycle": "local"}),
        ),
        _source_layer(
            root=tmp_path / "machine",
            layer_id="machine-preferences",
            tier="machine",
            origin="local_machine",
            execution_machine="logical-machine",
            values=_document(usages={"lifecycle": "local"}),
        ),
        _source_layer(
            root=exact_root,
            layer_id="exact-preferences",
            tier="repository_machine",
            origin="repository_machine",
            repository_id="harness-repository",
            execution_machine="logical-machine",
            values=_document(usages={"lifecycle": "local"}),
        ),
    ]
    policy = compose_policy(context, layers)
    decision = policy.resolve(
        "lifecycle",
        PolicyOrigin("repo-a", "worktree-a"),
        "bridge.lifecycle.v1",
        _provider(),
    )
    assert decision.state == "active"
    assert decision.provenance is not None
    assert decision.provenance["id"] == "exact-preferences"
    assert decision.provenance["source"] == {
        "root": str(exact_root),
        "path": str(Path("config") / "telemetry.json"),
    }
    assert policy.layers == (
        "harness-policy",
        "knowledge-preferences",
        "machine-preferences",
        "exact-preferences",
    )


def test_higher_layers_cannot_expand_harness_scope(tmp_path: Path) -> None:
    context = _context(tmp_path)
    layers = [
        _base_layer(
            tmp_path,
            scopes={
                "lifecycle": {"repositories": ["repo-a"], "worktrees": ["worktree-a"]},
            },
        ),
        _layer(
            layer_id="machine-preferences",
            tier="machine",
            origin="local_machine",
            execution_machine="logical-machine",
            values=_document(
                scopes={"lifecycle": {"repositories": ["repo-a", "repo-b"]}},
            ),
        ),
    ]
    policy = compose_policy(context, layers)
    assert (
        policy.resolve(
            "lifecycle",
            PolicyOrigin("repo-b", "worktree-b"),
            "bridge.lifecycle.v1",
            _provider(),
        ).state
        == "out_of_scope"
    )

    denied = compose_policy(
        _context(tmp_path),
        [
            _base_layer(tmp_path),
            _layer(
                layer_id="machine-opt-out",
                tier="machine",
                origin="local_machine",
                execution_machine="logical-machine",
                values=_document(
                    scopes={"lifecycle": {"repositories": []}},
                ),
            ),
        ],
    )
    assert (
        denied.resolve(
            "lifecycle",
            PolicyOrigin("repo-a", "worktree-a"),
            "bridge.lifecycle.v1",
            _provider(),
        ).state
        == "out_of_scope"
    )


def test_duplicate_sink_identity_across_layers_refuses(tmp_path: Path) -> None:
    root = Path(_context(tmp_path)["base_root"])
    duplicate = _layer(
        layer_id="second-catalog",
        tier="repository",
        origin="base",
        repository_id="harness-repository",
        root=root,
        values=_document(
            sinks={
                "local-a": {
                    "transport": "command-jsonl-v1",
                    "argv": ["replacement"],
                    "source_schemas": ["bridge.lifecycle.v1"],
                },
            }
        ),
    )
    with pytest.raises(ConfigurationError, match="duplicate telemetry sink"):
        compose_policy(_context(tmp_path), [_base_layer(tmp_path), duplicate])


@pytest.mark.parametrize(
    "overlay",
    [
        {"opt_out": {"lifecycle": True}},
        {"opt_out": {"lifecycle": False}},
    ],
)
def test_opt_out_is_monotonic_and_blocks_selection(
    tmp_path: Path,
    overlay: dict[str, Any],
) -> None:
    context = _context(tmp_path)
    layers = [
        _base_layer(tmp_path),
        _layer(
            layer_id="knowledge-opt-out",
            tier="repository",
            origin="knowledge",
            repository_id="harness-repository",
            root=Path(context["knowledge_root"]),
            values=_document(opt_out={"lifecycle": True}),
        ),
        _layer(
            layer_id="machine-preferences",
            tier="machine",
            origin="local_machine",
            execution_machine="logical-machine",
            values=_document(**overlay),
        ),
    ]
    policy = compose_policy(context, layers)
    assert (
        policy.resolve(
            "lifecycle",
            PolicyOrigin("repo-a", "worktree-a"),
            "bridge.lifecycle.v1",
            _provider(),
        ).state
        == "disabled"
    )
    expected_sources = (
        ("knowledge-opt-out", "machine-preferences")
        if overlay["opt_out"]["lifecycle"]
        else ("knowledge-opt-out",)
    )
    assert (
        tuple(source["id"] for source in policy.opt_out_sources["lifecycle"]) == expected_sources
    )


def test_explicit_null_disables_and_unknown_origin_never_selects(tmp_path: Path) -> None:
    context = _context(tmp_path)
    layers = [
        _base_layer(tmp_path),
        _layer(
            layer_id="machine-preferences",
            tier="machine",
            origin="local_machine",
            execution_machine="logical-machine",
            values=_document(usages={"lifecycle": None}),
        ),
    ]
    policy = compose_policy(context, layers)
    assert (
        policy.resolve("lifecycle", None, "bridge.lifecycle.v1", _provider()).state == "disabled"
    )

    active = compose_policy(_context(tmp_path / "other"), [_base_layer(tmp_path / "other")])
    assert (
        active.resolve(
            "lifecycle",
            None,
            "bridge.lifecycle.v1",
            _provider(),
        ).state
        == "origin_unavailable"
    )


def test_malformed_explicit_policy_and_unresolved_binding_refuse(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="unresolved"):
        compose_policy(_context(tmp_path, binding="unresolved"), [])

    invalid = _base_layer(tmp_path)
    invalid["values"]["telemetry"]["extra"] = True
    with pytest.raises(ConfigurationError, match="unsupported telemetry policy"):
        compose_policy(_context(tmp_path), [invalid])

    knowledge = _layer(
        layer_id="knowledge-sink",
        tier="repository",
        origin="knowledge",
        repository_id="harness-repository",
        root=Path(_context(tmp_path)["knowledge_root"]),
        values=_document(sinks={}),
    )
    with pytest.raises(ConfigurationError, match="only the harness repository"):
        compose_policy(_context(tmp_path), [_base_layer(tmp_path), knowledge])

    builtins = _layer(
        layer_id="builtins",
        tier="defaults",
        origin="defaults",
        values=_document(usages={"lifecycle": "local-a"}),
    )
    with pytest.raises(ConfigurationError, match="built-in defaults"):
        compose_policy(_context(tmp_path), [_base_layer(tmp_path), builtins])

    invalid_default = _base_layer(tmp_path)
    invalid_default["values"]["telemetry"]["defaults"]["lifecycle"] = "missing"
    machine_disable = _layer(
        layer_id="machine-disable",
        tier="machine",
        origin="local_machine",
        execution_machine="logical-machine",
        values=_document(usages={"lifecycle": None}),
    )
    with pytest.raises(ConfigurationError, match="undefined sink"):
        compose_policy(_context(tmp_path), [invalid_default, machine_disable])


def test_old_provider_is_not_credited_with_new_policy_support(tmp_path: Path) -> None:
    descriptor = tmp_path / "provider"
    descriptor.mkdir()
    path = descriptor / "consumer-telemetry-contract.json"
    path.write_text(json.dumps(CAPABILITY), encoding="utf-8")
    assert (
        supports_policy(
            descriptor,
            policy_version=POLICY_VERSION,
            source_schema="bridge.lifecycle.v1",
        )
        is False
    )

    path.write_text(
        json.dumps(
            {
                **CAPABILITY,
                "policy_versions": [POLICY_VERSION],
            }
        ),
        encoding="utf-8",
    )
    assert read_policy_capabilities(descriptor) == ProviderCapabilities(
        frozenset({POLICY_VERSION}),
        frozenset({"command-jsonl-v1"}),
        frozenset({"bridge.lifecycle.v1"}),
    )
    assert (
        supports_policy(
            descriptor,
            policy_version=POLICY_VERSION,
            source_schema="bridge.lifecycle.v1",
        )
        is True
    )
    assert (
        supports_policy(
            descriptor,
            policy_version=POLICY_VERSION,
            source_schema="dispatch.lifecycle.v1",
        )
        is False
    )
    assert (
        supports_policy(
            descriptor,
            policy_version=POLICY_VERSION,
            source_schema="bridge.lifecycle.v1",
            transport="unsupported",
        )
        is False
    )


def test_policy_refuses_provider_without_explicit_capability(tmp_path: Path) -> None:
    policy = compose_policy(_context(tmp_path), [_base_layer(tmp_path)])
    old_provider = ProviderCapabilities(
        policy_versions=frozenset({1}),
        transports=frozenset({"command-jsonl-v1"}),
        source_schemas=frozenset({"bridge.lifecycle.v1"}),
    )
    assert (
        policy.resolve(
            "lifecycle",
            PolicyOrigin("repo-a", "worktree-a"),
            "bridge.lifecycle.v1",
            old_provider,
        ).state
        == "unsupported_provider"
    )
