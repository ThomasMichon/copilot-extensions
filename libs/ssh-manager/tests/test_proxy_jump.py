"""Jump planning and launch ownership without running SSH on the test host."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ssh_manager import proxy, proxy_jump
from ssh_manager.config_sources import SSHConfig
from ssh_manager.process import run_process_cleanup


def _profile(host: str, jump: str | None = None, **kwargs: object) -> SSHConfig:
    values = [("hostname", host), ("user", "probe"), ("port", "22")]
    if jump is not None:
        values.append(("proxyjump", jump))
    return SSHConfig(host, effective_config=tuple(values), **kwargs)


def _profiles(monkeypatch, profiles: dict[str, SSHConfig]) -> list[tuple]:
    calls = []

    def source(host, **kwargs):
        calls.append((host, kwargs))
        return SimpleNamespace(get_ssh_config=lambda: profiles[host])

    monkeypatch.setattr(proxy_jump, "SSHProfileSource", source)
    return calls


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("jump.example", ("jump.example", None, None)),
        ("alice@jump.example:2200", ("jump.example", "alice", 2200)),
        ("ssh://alice@jump.example:2200", ("jump.example", "alice", 2200)),
        ("Alice@Jump.Example:2200", ("Jump.Example", "Alice", 2200)),
        ("ssh://alice%40realm@Jump.Example", ("Jump.Example", "alice@realm", None)),
        ("[2001:db8::1]:2200", ("2001:db8::1", None, 2200)),
    ],
)
def test_jump_specification(value, expected):
    assert proxy_jump._parse_jump(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "-jump", "alice:secret@jump", "jump/path", "jump?query", "jump#fragment",
     "jump:0", "jump:65536", "jump:not-a-port", "https://jump",
     " jump", "jump\nhost", "ssh://alice%00@jump", "ssh://%2djump", "@jump"],
)
def test_invalid_jump_specification_refuses(value):
    with pytest.raises(ValueError):
        proxy_jump._parse_jump(value)


async def test_jump_uses_selected_client_and_independent_profile(monkeypatch):
    child = _profile(
        "gateway.example", proxy_command="native-proxy --stdio", config_file="ssh.config",
    )
    calls = _profiles(monkeypatch, {"gateway.example": child})
    target = _profile("target.example", "alice@gateway.example:2200", config_file="ssh.config")
    route = await proxy_jump.resolve_jump_route(target, "selected-ssh")
    assert route is not None
    assert route.argv == (
        "selected-ssh", "-F", "ssh.config", "-p", "2200", "-l", "alice",
        "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
        "-o", "ClearAllForwardings=yes", "-W", "target.example:22", "gateway.example",
    )
    assert route.config is child
    assert route.upstream is None
    assert calls == [("gateway.example", {
        "user": "alice", "port": 2200, "config_file": "ssh.config",
        "resolve_timeout": 15,
        "ssh_executable": "selected-ssh",
        "resolve_cwd": None,
        "process_env": None,
    })]


async def test_multiple_hops_are_planned_in_reverse_transport_order(monkeypatch):
    first = _profile("first.example")
    last = _profile("last.example", proxy_command="must-not-override-explicit-jump")
    _profiles(monkeypatch, {"first.example": first, "last.example": last})
    route = await proxy_jump.resolve_jump_route(
        _profile("target.example", "first.example,last.example"), "ssh",
    )
    assert route is not None and route.argv[-1] == "last.example"
    assert route.upstream is not None
    assert route.upstream.argv[-3:] == ("-W", "last.example:22", "first.example")
    assert route.upstream.upstream is None
    assert route.config.proxy_command is None
    assert last.proxy_command == "must-not-override-explicit-jump"


async def test_profile_derived_nested_jump_is_owned(monkeypatch):
    _profiles(monkeypatch, {
        "outer": _profile("outer", "inner"),
        "inner": _profile("inner"),
    })
    route = await proxy_jump.resolve_jump_route(_profile("target", "outer"), "ssh")
    assert route is not None and route.upstream is not None
    assert route.upstream.config.host_alias == "inner"


async def test_distinct_aliases_sharing_an_endpoint_are_not_a_cycle(monkeypatch):
    endpoint = "192.0.2.1"
    _profiles(monkeypatch, {
        "inner": _profile("inner", "outer", hostname=endpoint),
        "outer": _profile("outer", hostname=endpoint),
    })
    route = await proxy_jump.resolve_jump_route(
        _profile("target", "inner", hostname=endpoint), "ssh",
    )
    assert route is not None and route.config.host_alias == "inner"
    assert route.argv[-2:] == (f"{endpoint}:22", "inner")
    assert route.upstream is not None and route.upstream.config.host_alias == "outer"
    assert route.upstream.argv[-2:] == (f"{endpoint}:22", "outer")
    assert route.upstream.upstream is None


async def test_repeated_alias_is_cyclic_even_when_resolved_endpoints_change(monkeypatch):
    _profiles(monkeypatch, {
        "Inner": _profile("Inner", "outer", hostname="192.0.2.1"),
        "outer": _profile("outer", "inner", hostname="192.0.2.2"),
        "inner": _profile("inner", "outer", hostname="192.0.2.3"),
    })
    with pytest.raises(ValueError, match="Cyclic"):
        await proxy_jump.resolve_jump_route(_profile("target", "Inner"), "ssh")


async def test_ipv6_forward_endpoint_retains_its_brackets(monkeypatch):
    _profiles(monkeypatch, {"jump": _profile("jump")})
    route = await proxy_jump.resolve_jump_route(_profile("2001:db8::2", "jump"), "ssh")
    assert route is not None and route.argv[-2] == "[2001:db8::2]:22"


async def test_jump_profile_query_keeps_the_transport_working_directory(monkeypatch):
    calls = _profiles(monkeypatch, {"jump": _profile("jump", config_file="relative.config")})
    route = await proxy_jump.resolve_jump_route(
        _profile("target", "jump", config_file="relative.config"), "ssh", cwd="caller-work",
    )
    assert route is not None and route.argv[1:3] == ("-F", "relative.config")
    assert calls[0][1]["resolve_cwd"] == "caller-work"


async def test_jump_profile_queries_preserve_an_explicit_empty_environment(monkeypatch):
    calls = _profiles(monkeypatch, {"jump": _profile("jump")})
    await proxy_jump.resolve_jump_route(_profile("target", "jump"), "ssh", environment={})
    assert calls[0][1]["process_env"] == {}


async def test_jump_planning_uses_the_pinned_account_environment(monkeypatch):
    key = "c28fc6f98a2c44abbbd89d6a3037d0d9_POSIX_FD_STATE"
    environment = {"EXAMPLE_ACCOUNT": "selected", key: "ancestor"}
    config = _profile("target", "jump", process_env=environment)
    calls = _profiles(monkeypatch, {"jump": _profile("jump")})
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", AsyncMock(return_value=SimpleNamespace()))
    await proxy.create_ssh_subprocess("ssh", "target", config=config)
    assert calls[0][1]["process_env"] == {"EXAMPLE_ACCOUNT": "selected"}
    assert environment == {"EXAMPLE_ACCOUNT": "selected", key: "ancestor"}


@pytest.mark.parametrize("jump", ["target", "first,first", ""])
async def test_cycles_and_empty_hops_refuse_before_transport(monkeypatch, jump):
    _profiles(monkeypatch, {
        "target": _profile("target"),
        "first": _profile("first"),
    })
    if not jump:
        jump = "first,,first"
    with pytest.raises(ValueError):
        await proxy_jump.resolve_jump_route(_profile("target", jump), "ssh")


async def test_over_budget_profile_chain_refuses(monkeypatch):
    _profiles(monkeypatch, {
        f"hop{i}": _profile(f"hop{i}", f"hop{i + 1}") for i in range(10)
    })
    with pytest.raises(ValueError, match="budget"):
        await proxy_jump.resolve_jump_route(_profile("target", "hop0"), "ssh")


@pytest.mark.parametrize("command", ["proxy --stdio", "none"])
async def test_explicit_proxy_selection_does_not_resolve_a_jump(monkeypatch, command):
    def unavailable(*args, **kwargs):
        raise AssertionError("An explicit proxy must not resolve a jump")

    monkeypatch.setattr(proxy_jump, "SSHProfileSource", unavailable)
    config = _profile("target", "jump", proxy_command=command)
    assert await proxy_jump.resolve_jump_route(config, "ssh") is None


async def test_jump_broker_uses_prepared_ssh_owner_not_generic_spawn(monkeypatch):
    route = proxy_jump.JumpRoute(("selected-ssh", "jump"), _profile("jump"), None)
    process = SimpleNamespace()
    prepared = AsyncMock(return_value=process)
    generic = AsyncMock()
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    monkeypatch.setattr(proxy, "spawn_in_kill_on_close_job", generic)
    broker = proxy._ProxyBroker(list(route.argv), {"EXAMPLE": "value"}, ssh_route=route)
    assert await broker._spawn_child() == (process, None)
    assert prepared.await_args.args[:3] == (list(route.argv), route.config, None)
    assert prepared.await_args.args[3]["env"] == {"EXAMPLE": "value"}
    generic.assert_not_awaited()


async def test_resolved_direct_route_cannot_reenable_native_jump(monkeypatch):
    process = SimpleNamespace(wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=(process, None))
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "spawn_in_kill_on_close_job", spawn)
    try:
        await proxy.create_ssh_subprocess("ssh", "target", config=_profile("target"))
        assert spawn.await_args.args[:3] == ("ssh", "-o", "ProxyCommand=none")
    finally:
        await run_process_cleanup(process)


async def test_posix_keeps_its_native_jump_arguments(monkeypatch):
    process = SimpleNamespace(wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=(process, None))
    plan = AsyncMock(side_effect=AssertionError("POSIX must keep native jump handling"))
    monkeypatch.setattr(proxy, "_is_windows", lambda: False)
    monkeypatch.setattr(proxy, "spawn_in_kill_on_close_job", spawn)
    monkeypatch.setattr(proxy, "resolve_jump_route", plan)
    try:
        await proxy.create_ssh_subprocess(
            "ssh", "-J", "jump", "target", config=_profile("target", "jump"),
        )
        assert spawn.await_args.args == ("ssh", "-J", "jump", "target")
    finally:
        await run_process_cleanup(process)
