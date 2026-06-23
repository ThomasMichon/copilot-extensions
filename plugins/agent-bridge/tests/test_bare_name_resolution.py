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


# -- Modifier namespaces (admin:) must not pollute bare-name resolution -------

from agent_bridge.admin_resolver import AdminResolver  # noqa: E402


class _ModifierNsResolver(_NsResolver):
    """A namespace resolver that opts out of bare-name resolution."""

    @property
    def bare_addressable(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_non_bare_addressable_resolver_excluded_from_candidates():
    # A modifier resolver mirrors the static agent's base name, but because it
    # is not bare-addressable it must not collide -- bare resolves to static.
    r = AgentResolver({"foo": AgentConfig(name="foo", project="p")}, {})
    r.register_namespace_resolver(
        _ModifierNsResolver("admin", [NamespaceAgentInfo(name="foo")])
    )
    target = await r.resolve_async("foo")
    assert target.type == "local"


@pytest.mark.asyncio
async def test_admin_resolver_does_not_shadow_bare_static_agent(monkeypatch):
    # The admin: twin must not make the bare name ambiguous. A requires_admin
    # agent's bare name auto-relays to the elevated sub-daemon (Cap 2 Slice 3)
    # when relay is applicable -- here we force it applicable so the assertion
    # is platform-independent.
    from agent_bridge import elevated

    monkeypatch.setattr(elevated, "relay_applicable", lambda req: bool(req))
    monkeypatch.setattr(elevated, "ensure_running", lambda: "subtok")

    r = AgentResolver(
        {"spo": AgentConfig(name="spo", project="p", requires_admin=True)}, {}
    )
    r.register_namespace_resolver(AdminResolver(r))
    target = await r.resolve_async("spo")
    assert target.type == "command"
    assert target.project == "p"
    assert target.spawn_command[-4:] == [
        "ws://127.0.0.1:9281/acp/spo", "--token", "subtok", "--stdio",
    ]


@pytest.mark.asyncio
async def test_bare_requires_admin_stays_local_when_relay_not_applicable(monkeypatch):
    # Off Windows / already elevated, relay is not applicable, so a bare
    # requires_admin agent falls through to normal (local) resolution.
    from agent_bridge import elevated

    monkeypatch.setattr(elevated, "relay_applicable", lambda req: False)
    r = AgentResolver(
        {"spo": AgentConfig(name="spo", project="p", requires_admin=True)}, {}
    )
    r.register_namespace_resolver(AdminResolver(r))
    target = await r.resolve_async("spo")
    assert target.type == "local"
    assert target.project == "p"


@pytest.mark.asyncio
async def test_admin_list_is_opt_in_only():
    # Only agents flagged requires_admin get an admin: twin; the rest don't.
    r = AgentResolver(
        {
            "spo": AgentConfig(name="spo", project="p", requires_admin=True),
            "dotfiles": AgentConfig(name="dotfiles", project="p"),
        },
        {},
    )
    admin = AdminResolver(r)
    names = {info.name for info in await admin.list()}
    assert names == {"spo"}


@pytest.mark.asyncio
async def test_admin_prefix_rejects_non_opted_in_agent():
    # admin:<name> on an agent that didn't opt in fails with clear guidance.
    r = AgentResolver(
        {"dotfiles": AgentConfig(name="dotfiles", spawn_command=["copilot"])}, {}
    )
    r.register_namespace_resolver(AdminResolver(r))
    with pytest.raises(RuntimeError, match="requires_admin"):
        await r.resolve_async("admin:dotfiles")


@pytest.mark.asyncio
async def test_admin_prefix_still_elevates_explicitly(monkeypatch):
    # admin: stays opt-in -- the explicit prefix resolves an opted-in agent and
    # routes it through the elevated sub-daemon relay (no gsudo / RunAs, no
    # CLI-side difference from a bare elevated agent).
    from agent_bridge import elevated

    monkeypatch.setattr(elevated, "relay_applicable", lambda req: True)
    monkeypatch.setattr(elevated, "ensure_running", lambda: "subtok")

    r = AgentResolver(
        {
            "spo": AgentConfig(
                name="spo", spawn_command=["copilot"], requires_admin=True
            )
        },
        {},
    )
    r.register_namespace_resolver(AdminResolver(r))
    target = await r.resolve_async("admin:spo")
    assert target.type == "command"
    assert target.spawn_command[-4:] == [
        "ws://127.0.0.1:9281/acp/spo", "--token", "subtok", "--stdio",
    ]


def test_admin_resolver_is_not_bare_addressable():
    assert AdminResolver(AgentResolver({}, {})).bare_addressable is False


from agent_bridge import __main__ as m  # noqa: E402


def _agent(name, aliases=None):
    return {"name": name, "aliases": aliases or []}


def test_match_prefixed_friendly_alias_to_canonical():
    agents = [_agent("codespace:type-filters-adoption-7qv",
                     aliases=["codespace:type-filters-adoption"])]
    # Prefixed friendly name resolves to the raw canonical name.
    assert m._match_agents("codespace:type-filters-adoption", agents) == [
        "codespace:type-filters-adoption-7qv"
    ]


def test_match_bare_friendly_via_alias_bare_form():
    agents = [_agent("codespace:type-filters-adoption-7qv",
                     aliases=["codespace:type-filters-adoption"])]
    assert m._match_agents("type-filters-adoption", agents) == [
        "codespace:type-filters-adoption-7qv"
    ]


def test_match_exact_raw_name():
    agents = [_agent("codespace:foo-aaa", aliases=["codespace:foo"])]
    assert m._match_agents("codespace:foo-aaa", agents) == ["codespace:foo-aaa"]


def test_match_bare_collision_returns_all():
    agents = [
        _agent("codespace:foo-aaa", aliases=["codespace:foo"]),
        _agent("container:foo"),
    ]
    matches = m._match_agents("foo", agents)
    assert set(matches) == {"codespace:foo-aaa", "container:foo"}


def test_match_none():
    agents = [_agent("codespace:foo-aaa", aliases=["codespace:foo"])]
    assert m._match_agents("nope", agents) == []


def test_match_bare_skips_non_bare_addressable_modifier():
    # admin: mirrors the static agent under the same base name but is flagged
    # not bare-addressable -- a bare name must resolve to the static agent only.
    agents = [
        {"name": "dotfiles", "aliases": []},
        {"name": "admin:dotfiles", "aliases": [], "bare_addressable": False},
    ]
    assert m._match_agents("dotfiles", agents) == ["dotfiles"]


def test_match_explicit_admin_prefix_still_matches():
    agents = [
        {"name": "dotfiles", "aliases": []},
        {"name": "admin:dotfiles", "aliases": [], "bare_addressable": False},
    ]
    # The explicit prefix is an exact-name match -- not gated by the flag.
    assert m._match_agents("admin:dotfiles", agents) == ["admin:dotfiles"]
