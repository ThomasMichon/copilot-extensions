"""Jump planning and launch ownership without running SSH on the test host."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ssh_manager import proxy, proxy_jump
from ssh_manager.config_sources import SSHConfig
from ssh_manager.forward import build_forward_ssh_args
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


@pytest.mark.parametrize("specification,host,user", [
    ("%r@jump-%h-%n-%p", "jump-endpoint.example-target-2200", "alice"),
    ("ssh://alice%40realm@jump-%25h:2300", "jump-endpoint.example", "alice@realm"),
    ("jump-%%n", "jump-%n", None),
])
async def test_jump_tokens_expand_against_destination_identity(
    monkeypatch, specification, host, user,
):
    calls = _profiles(monkeypatch, {host: _profile(host)})
    route = await proxy_jump.resolve_jump_route(
        _profile("target", specification, hostname="endpoint.example", user="alice", port=2200),
        "ssh",
    )
    assert route is not None and route.argv[-1] == host
    assert calls[0][1]["user"] == user
    assert route.argv[-2] == "endpoint.example:2200"


async def test_jump_chain_tokens_expand_before_passing_earlier_hops(monkeypatch):
    calls = _profiles(monkeypatch, {
        "last-endpoint.example": _profile("last-endpoint.example"),
        "first-endpoint.example": _profile("first-endpoint.example"),
    })
    route = await proxy_jump.resolve_jump_route(
        _profile("target", "first-%h,last-%h", hostname="endpoint.example"), "ssh",
    )
    assert route is not None and route.upstream is not None
    assert [host for host, _options in calls] == [
        "last-endpoint.example", "first-endpoint.example",
    ]


@pytest.mark.parametrize("value", ["jump-%Z", "jump-%"])
async def test_unsupported_jump_tokens_refuse_before_metadata_lookup(monkeypatch, value):
    calls = _profiles(monkeypatch, {})
    with pytest.raises(ValueError, match="Unsupported SSH proxy token"):
        await proxy_jump.resolve_jump_route(_profile("target", value), "ssh")
    assert calls == []


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("command", ["owned-proxy %Z", "owned-proxy %"])
async def test_invalid_terminal_proxy_tokens_refuse_before_transport(
    monkeypatch, nested, command,
):
    root = _profile("target", "jump" if nested else None,
                    proxy_command=None if nested else command)
    _profiles(monkeypatch, {"target": root, "jump": _profile("jump", proxy_command=command)})
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    with pytest.raises(ValueError, match="Unsupported SSH proxy token"):
        await proxy.create_ssh_subprocess("ssh", "target", config=root)
    prepared.assert_not_awaited()


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
    calls = _profiles(monkeypatch, {"target": config, "jump": _profile("jump")})
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
    _profiles(monkeypatch, {"target": _profile("target")})
    try:
        await proxy.create_ssh_subprocess("ssh", "target", config=_profile("target"))
        assert spawn.await_args.args[:3] == ("ssh", "-o", "ProxyCommand=none")
    finally:
        await run_process_cleanup(process)


@pytest.mark.parametrize("options", [
    ("-J", "jump"), ("-Jjump",), ("-vJjump",),
    ("-o", "ProxyJump=jump"), ("-oProxyJump=jump",),
    ("-o", "ProxyJump jump"),
])
async def test_windows_consumes_explicit_jump_before_installing_its_proxy(monkeypatch, options):
    calls = _profiles(monkeypatch, {
        "target": _profile("target", "jump", port=2200), "jump": _profile("jump"),
    })
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    await proxy.create_ssh_subprocess(
        "ssh", "-p", "2200", *options, "target", "remote-command", "-J", "remote-argument",
        config=_profile("target", "profile-jump"),
    )
    arguments, config, route, _kwargs = prepared.await_args.args
    expected_options = ("-v",) if options == ("-vJjump",) else ()
    assert arguments == (
        "ssh", "-p", "2200", *expected_options, "target", "remote-command", "-J", "remote-argument",
    )
    assert proxy_jump.ssh_option(config, "proxyjump") == "jump"
    assert route is not None and route.argv[-1] == "jump"
    assert route.argv[-2] == "target:2200"
    assert [host for host, _options in calls] == ["target", "jump"]


def test_jump_projection_preserves_other_options_and_explicit_disable():
    args = ("ssh", "-Fconfig", "-o", "BatchMode=yes", "-Jnone", "--", "-target", "-Jremote")
    projected, config = proxy_jump.prepare_jump_arguments(args, _profile("target", "profile-jump"))
    assert projected == ("ssh", "-Fconfig", "-o", "BatchMode=yes", "--", "-target", "-Jremote")
    assert proxy_jump.ssh_option(config, "proxyjump") == "none"


def test_jump_options_after_destination_precede_remote_command_arguments():
    args, config = proxy_jump.prepare_jump_arguments(
        ("ssh", "target", "-p2200", "-Jjump", "remote-command", "-J", "literal"),
        _profile("target"),
    )
    assert args == ("ssh", "target", "-p2200", "remote-command", "-J", "literal")
    assert config.port == 2200 and proxy_jump.ssh_option(config, "proxyjump") == "jump"


@pytest.mark.parametrize("options,endpoint", [
    (("-p2200",), "target:2200"),
    (("-o", "Port=2200"), "target:2200"),
    (("-oPort=2200", "-p2300"), "target:2200"),
    (("-p2200", "-oPort=2300"), "target:2200"),
    (("-o", "HostName=endpoint.example"), "endpoint.example:22"),
    (("-oHostName=%h.example",), "target.example:22"),
    (("-oHostName=%h.%%.example",), "target.%.example:22"),
    (("-oHostName=2001:db8::1", "-o", "Port 2200"), "[2001:db8::1]:2200"),
])
async def test_cli_endpoint_overrides_bind_the_owned_forward(monkeypatch, options, endpoint):
    _profiles(monkeypatch, {"jump": _profile("jump")})
    args, config = proxy_jump.prepare_jump_arguments(
        ("ssh", *options, "-Jjump", "target"), _profile("target"),
    )
    assert args == ("ssh", *options, "target")
    route = await proxy_jump.resolve_jump_route(config, "ssh")
    assert route is not None and route.argv[-2] == endpoint


@pytest.mark.parametrize("options", [("-Jnone",), ("-o", "ProxyJump=none")])
async def test_plain_config_cannot_reenable_an_explicitly_disabled_jump(monkeypatch, options):
    _profiles(monkeypatch, {"target": _profile("target")})
    process = SimpleNamespace(wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=(process, None))
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "spawn_in_kill_on_close_job", spawn)
    try:
        await proxy.create_ssh_subprocess(
            "ssh", *options, "target", config=SSHConfig("target"),
        )
        assert spawn.await_args.args == ("ssh", "-o", "ProxyCommand=none", "target")
    finally:
        await run_process_cleanup(process)


@pytest.mark.parametrize("options", [(), ("-p2200",), ("-lprobe",), ("-oHostName=endpoint",)])
@pytest.mark.parametrize("inherited", ["jump", "proxy"])
async def test_plain_config_overrides_preserve_inherited_native_routes(
    monkeypatch, options, inherited,
):
    root = _profile(
        "target", "gateway" if inherited == "jump" else None,
        proxy_command="owned-proxy --stdio" if inherited == "proxy" else None,
    )
    calls = _profiles(monkeypatch, {"target": root, "gateway": _profile("gateway")})
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    await proxy.create_ssh_subprocess(
        "ssh", *options, "target", config=SSHConfig("target"),
    )
    _args, selected, route, _kwargs = prepared.await_args.args
    assert selected == root
    assert (route is not None) is (inherited == "jump")
    assert selected.proxy_command == root.proxy_command
    assert [host for host, _options in calls] == (
        ["target", "gateway"] if inherited == "jump" else ["target"]
    )
    assert calls[0][1]["resolve_args"] == (*options, "target")


@pytest.mark.parametrize("command", ["owned-proxy --stdio", "none"])
async def test_cli_proxy_command_overrides_profile_jump(monkeypatch, command):
    _profiles(monkeypatch, {"target": _profile("target", proxy_command=command)})
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    config = _profile("target", "profile-jump")
    args = build_forward_ssh_args(
        config, 31001, 31002, extra_options={"ProxyCommand": command},
    )
    await proxy.create_ssh_subprocess(*args, config=config)
    _args, selected, route, _kwargs = prepared.await_args.args
    assert selected.proxy_command == command
    assert proxy_jump.ssh_option(selected, "proxyjump") is None
    assert route is None


async def test_cli_proxy_command_is_not_lost_from_a_direct_profile(monkeypatch):
    _profiles(monkeypatch, {"target": _profile("target", proxy_command="owned-proxy --stdio")})
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    await proxy.create_ssh_subprocess(
        "ssh", "-oProxyCommand=owned-proxy --stdio", "target", config=_profile("target"),
    )
    assert prepared.await_args.args[1].proxy_command == "owned-proxy --stdio"


@pytest.mark.parametrize("options,command,jump", [
    (("-oProxyCommand=first", "-oProxyCommand=second"), "first", None),
    (("-oProxyCommand=first", "-oProxyJump=second"), "first", None),
    (("-oProxyJump=first", "-oProxyCommand=second"), None, "first"),
])
def test_cli_proxy_selection_preserves_first_selected_route(options, command, jump):
    _args, config = proxy_jump.prepare_jump_arguments(
        ("ssh", *options, "target"), _profile("target"),
    )
    assert config.proxy_command == command
    assert proxy_jump.ssh_option(config, "proxyjump") == jump


@pytest.mark.parametrize("options", [("-F", "selected.conf"), ("-Fselected.conf",)])
async def test_cli_config_selection_rebinds_root_and_every_jump(monkeypatch, options):
    selected_root = _profile(
        "target", "gateway", config_file="selected.conf", hostname="selected-target.example", port=2200,
    )
    gateway = _profile("gateway", config_file="selected.conf", identity_file="selected-key")
    calls = _profiles(monkeypatch, {"target": selected_root, "gateway": gateway})
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    arguments = ("selected-ssh", *options, "-Jgateway", "target")
    environment = {"EXAMPLE_ACCOUNT": "selected"}
    await proxy.create_ssh_subprocess(
        *arguments, config=_profile("target", config_file="fallback.conf"),
        cwd="caller-work", env=environment,
    )
    _args, selected, route, _kwargs = prepared.await_args.args
    assert selected.config_file == "selected.conf"
    assert selected.hostname == "selected-target.example"
    assert route is not None and route.argv[1:3] == ("-F", "selected.conf")
    assert route.argv[-2] == "selected-target.example:2200"
    assert route.config.identity_file == "selected-key"
    assert [host for host, _kwargs in calls] == ["target", "gateway"]
    assert calls[0][1] == {
        "config_file": "selected.conf", "resolve_timeout": 15,
        "ssh_executable": "selected-ssh", "resolve_cwd": "caller-work",
        "process_env": environment, "resolve_args": ("-F", "selected.conf", *arguments[1:]),
    }
    assert calls[1][1]["config_file"] == "selected.conf"


@pytest.mark.parametrize("setting", [
    "ProxyCommand proxy --endpoint=value",
    "ProxyCommand = proxy --endpoint=value",
    "ProxyCommand=proxy --endpoint=value",
])
def test_proxy_setting_uses_its_first_name_separator_not_command_equals(setting):
    _args, config = proxy_jump.prepare_jump_arguments(
        ("ssh", "-o", setting, "target"), SSHConfig("target"),
    )
    assert config.proxy_command == "proxy --endpoint=value"


def test_tag_option_does_not_consume_the_destination_position():
    args, config = proxy_jump.prepare_jump_arguments(
        ("ssh", "-P", "scope", "target", "-J", "jump"), SSHConfig("target"),
    )
    assert args == ("ssh", "-P", "scope", "target")
    assert proxy_jump.ssh_option(config, "proxyjump") == "jump"


@pytest.mark.parametrize("filename", [None, "same.conf"])
async def test_profile_routing_refreshes_on_relaunch_with_the_same_cached_config(
    monkeypatch, filename,
):
    cached = _profile("target", config_file=filename)
    profiles = {"target": cached, "gateway": _profile("gateway", config_file=filename)}
    calls = _profiles(monkeypatch, profiles)
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    await proxy.create_ssh_subprocess("selected-ssh", "target", config=cached)
    assert prepared.await_args.args[2] is None
    profiles["target"] = _profile("target", "gateway", config_file=filename)
    await proxy.create_ssh_subprocess("selected-ssh", "target", config=cached)
    route = prepared.await_args.args[2]
    assert route is not None and route.argv[-1] == "gateway"
    assert proxy_jump.ssh_option(cached, "proxyjump") is None
    assert [host for host, _options in calls] == ["target", "target", "gateway"]


@pytest.mark.parametrize("command", ["owned-proxy --stdio", "none"])
async def test_profile_refresh_preserves_explicit_programmatic_proxy(monkeypatch, command):
    _profiles(monkeypatch, {"target": _profile("target", config_file="same.conf")})
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    await proxy.create_ssh_subprocess(
        "ssh", "-F", "same.conf", "target",
        config=SSHConfig("target", config_file="same.conf", proxy_command=command),
    )
    assert prepared.await_args.args[1].proxy_command == command
    assert prepared.await_args.args[2] is None


@pytest.mark.parametrize("jump", [False, True])
async def test_cli_proxy_choice_beats_programmatic_proxy_after_refresh(monkeypatch, jump):
    fresh = _profile("target", "gateway" if jump else None, config_file="same.conf")
    if not jump:
        fresh = SSHConfig("target", config_file="same.conf", proxy_command="none")
    _profiles(monkeypatch, {"target": fresh, "gateway": _profile("gateway")})
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    option = "-Jgateway" if jump else "-oProxyCommand=none"
    await proxy.create_ssh_subprocess(
        "ssh", "-F", "same.conf", option, "target",
        config=SSHConfig("target", config_file="same.conf", proxy_command="old-proxy"),
    )
    selected, route = prepared.await_args.args[1:3]
    assert selected.proxy_command != "old-proxy"
    assert (route is not None) is jump


async def test_cli_proxy_disable_survives_native_none_normalization(monkeypatch):
    _profiles(monkeypatch, {"target": _profile("target", config_file="same.conf")})
    args, selected = await proxy_jump.prepare_windows_launch(
        ("ssh", "-Fsame.conf", "-oProxyCommand=none", "target"),
        SSHConfig("target", config_file="same.conf", proxy_command="old-proxy"), "ssh",
    )
    assert "-oProxyCommand=none" in args
    assert selected.proxy_command is None
    assert proxy_jump.ssh_option(selected, "proxycommand") is None
    assert proxy_jump.ssh_option(selected, "proxyjump") is None


async def test_changed_file_does_not_restore_the_previous_programmatic_proxy(monkeypatch):
    _profiles(monkeypatch, {
        "target": _profile("target", "gateway", config_file="selected.conf"),
        "gateway": _profile("gateway", config_file="selected.conf"),
    })
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    await proxy.create_ssh_subprocess(
        "ssh", "-Fselected.conf", "target",
        config=SSHConfig("target", config_file="fallback.conf", proxy_command="old-proxy"),
    )
    selected, route = prepared.await_args.args[1:3]
    assert selected.proxy_command is None
    assert route is not None and route.argv[-1] == "gateway"


@pytest.mark.parametrize("key,value,jump", [
    ("ProxyCommand", "owned-proxy --stdio", False),
    ("ProxyCommand", "none", False),
    ("ProxyJump", "gateway", True),
    ("ProxyJump", "none", False),
])
async def test_profile_refresh_preserves_explicit_programmatic_proxy_options(
    monkeypatch, key, value, jump,
):
    _profiles(monkeypatch, {
        "target": _profile("target", config_file="same.conf"),
        "gateway": _profile("gateway", config_file="same.conf"),
    })
    prepared = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(proxy, "_create_prepared_ssh", prepared)
    await proxy.create_ssh_subprocess(
        "ssh", "-Fsame.conf", "target",
        config=SSHConfig("target", config_file="same.conf", extra_options={key: value}),
    )
    selected, route = prepared.await_args.args[1:3]
    assert (route is not None) is jump
    assert proxy_jump.ssh_option(selected, key) == value


async def test_profile_refresh_preserves_explicit_fields_but_not_cli_replaced_values(monkeypatch):
    fresh = SSHConfig("target", config_file="same.conf", effective_config=(
        ("hostname", "target"), ("user", "cli-user"), ("port", "2300"),
    ))
    _profiles(monkeypatch, {"target": fresh})
    original = SSHConfig(
        "target", config_file="same.conf", hostname="programmatic.example",
        user="old-user", port=2200, proxy_command="owned-proxy --stdio",
        extra_options={"User": "old-user", "Port": "2200"},
    )
    _args, selected = await proxy_jump.prepare_windows_launch(
        ("ssh", "-p2300", "cli-user@target"), original, "ssh",
    )
    assert selected.hostname == "programmatic.example"
    assert proxy_jump._endpoint(selected)[1:] == ("cli-user", 2300)
    assert "User" not in selected.extra_options and "Port" not in selected.extra_options
    assert selected.proxy_command == "owned-proxy --stdio"


@pytest.mark.parametrize("destination", [
    ("ssh://cli-user@target:2300",), ("--", "ssh://cli-user@target:2300"),
])
async def test_uri_user_and_port_override_programmatic_fields_after_refresh(monkeypatch, destination):
    _profiles(monkeypatch, {
        "target": SSHConfig("target", config_file="same.conf", effective_config=(
            ("hostname", "target"), ("user", "cli-user"), ("port", "2300"),
        )),
    })
    _args, selected = await proxy_jump.prepare_windows_launch(
        ("ssh", *destination),
        SSHConfig("target", config_file="same.conf", user="old-user", port=2200), "ssh",
    )
    assert proxy_jump._endpoint(selected)[1:] == ("cli-user", 2300)


@pytest.mark.parametrize("arguments,user,port", [
    (("ssh://cli-user@target:2300",), "cli-user", 2300),
    (("--", "ssh://cli-user@target:2300"), "cli-user", 2300),
    (("-p2400", "-lfirst-user", "ssh://cli-user@target:2300"), "first-user", 2400),
    (("ssh://cli-user@target:2300", "-p2400", "-llater-user"), "cli-user", 2300),
    (("-oPort=2400", "-oUser=first-user", "ssh://cli-user@target:2300"), "first-user", 2400),
    (("ssh://cli-user@target:2300", "-oPort=2400", "-oUser=later-user"), "cli-user", 2300),
    (("cli-user@target",), "cli-user", 2200),
])
async def test_programmatic_destination_identity_binds_owned_forward(
    monkeypatch, arguments, user, port,
):
    _profiles(monkeypatch, {"jump": _profile("jump")})
    original = SSHConfig(
        "target", user="old-user", port=2200, extra_options={"ProxyJump": "jump"},
    )
    args, selected = proxy_jump.prepare_jump_arguments(("ssh", *arguments), original)
    route = await proxy_jump.resolve_jump_route(selected, "ssh")
    assert args == ("ssh", *arguments)
    assert proxy_jump._endpoint(selected)[1:] == (user, port)
    assert route is not None and route.argv[-2] == f"target:{port}"
    assert original.user == "old-user" and original.port == 2200


@pytest.mark.parametrize("args", [
    ("ssh", "-J"), ("ssh", "-J", ""), ("ssh", "-o", ""),
    ("ssh", "-Jfirst", "-Jsecond", "target"),
    ("ssh", "-oHostName=%p.example", "target"),
    ("ssh", "-oHostName=target%", "target"),
    ("ssh", "-oProxyCommand=first", "-Jsecond", "target"),
])
def test_malformed_jump_options_refuse_before_native_launch(args):
    with pytest.raises(ValueError):
        proxy_jump.prepare_jump_arguments(args, _profile("target"))


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
