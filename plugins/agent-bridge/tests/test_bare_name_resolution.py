"""Bare-name resolution across namespaces + collision balk (#50)."""

from __future__ import annotations

import pytest

from agent_bridge.agent_registry import (
    AgentConfig,
    AgentResolver,
    AmbiguousAgentError,
    NamespaceAgentInfo,
)
from agent_bridge.transport import SpawnTarget


class _NsResolver:
    """Configurable mock namespace resolver."""

    def __init__(self, prefix: str, infos: list[NamespaceAgentInfo]):
        self._prefix = prefix
        self._infos = infos
        self.resolved: list[str] = []

    @property
    def prefix(self) -> str:
        return self._prefix

    async def resolve(self, name: str) -> SpawnTarget:
        self.resolved.append(name)
        return SpawnTarget(
            type="command", spawn_command=["echo", f"{self._prefix}:{name}"]
        )

    async def list(self):
        return list(self._infos)

    async def ensure_ready(self, name: str) -> None:
        pass


def test_namespace_agent_info_aliases_default():
    assert NamespaceAgentInfo(name="x").aliases == []


@pytest.mark.asyncio
async def test_bare_friendly_name_resolves_to_raw():
    r = AgentResolver({}, {})
    cs = _NsResolver("codespace", [
        NamespaceAgentInfo(name="type-filters-adoption-7qv",
                           aliases=["type-filters-adoption"]),
    ])
    r.register_namespace_resolver(cs)
    target = await r.resolve_async("type-filters-adoption")  # friendly, no prefix
    # Spawns against the RAW name.
    assert target.spawn_command == ["echo", "codespace:type-filters-adoption-7qv"]
    assert cs.resolved == ["type-filters-adoption-7qv"]


@pytest.mark.asyncio
async def test_bare_name_collision_across_namespaces_balks():
    r = AgentResolver({}, {})
    r.register_namespace_resolver(
        _NsResolver("codespace", [NamespaceAgentInfo(name="foo-aaa", aliases=["foo"])])
    )
    r.register_namespace_resolver(
        _NsResolver("container", [NamespaceAgentInfo(name="foo")])
    )
    with pytest.raises(AmbiguousAgentError) as ei:
        await r.resolve_async("foo")
    msg = str(ei.value)
    assert "codespace:foo-aaa" in msg
    assert "container:foo" in msg
    assert len(ei.value.candidates) == 2


@pytest.mark.asyncio
async def test_bare_name_collision_static_vs_namespace_balks():
    r = AgentResolver({"foo": AgentConfig(name="foo", project="p")}, {})
    r.register_namespace_resolver(
        _NsResolver("codespace", [NamespaceAgentInfo(name="foo-aaa", aliases=["foo"])])
    )
    with pytest.raises(AmbiguousAgentError) as ei:
        await r.resolve_async("foo")
    assert "foo" in ei.value.candidates  # bare static label
    assert "codespace:foo-aaa" in str(ei.value)


@pytest.mark.asyncio
async def test_explicit_prefix_bypasses_collision():
    r = AgentResolver({}, {})
    r.register_namespace_resolver(
        _NsResolver("codespace", [NamespaceAgentInfo(name="foo-aaa", aliases=["foo"])])
    )
    r.register_namespace_resolver(
        _NsResolver("container", [NamespaceAgentInfo(name="foo")])
    )
    # `codespace:` constrains resolution -- no ambiguity even though `foo`
    # collides across namespaces.
    target = await r.resolve_async("codespace:foo-aaa")
    assert target.spawn_command == ["echo", "codespace:foo-aaa"]


@pytest.mark.asyncio
async def test_bare_static_agent_still_resolves():
    r = AgentResolver({"local-agent": AgentConfig(name="local-agent", project="p")}, {})
    r.register_namespace_resolver(
        _NsResolver("codespace", [NamespaceAgentInfo(name="other-xxx", aliases=["other"])])
    )
    target = await r.resolve_async("local-agent")
    assert target.type == "local"


@pytest.mark.asyncio
async def test_failing_resolver_does_not_break_resolution():
    class _Boom(_NsResolver):
        async def list(self):
            raise RuntimeError("gh down")

    r = AgentResolver({}, {})
    r.register_namespace_resolver(_Boom("codespace", []))
    r.register_namespace_resolver(
        _NsResolver("container", [NamespaceAgentInfo(name="foo")])
    )
    # The codespace resolver's list() blows up but resolution still finds foo.
    target = await r.resolve_async("foo")
    assert target.spawn_command == ["echo", "container:foo"]
