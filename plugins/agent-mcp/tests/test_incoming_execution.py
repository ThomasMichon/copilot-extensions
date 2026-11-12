"""Cold-path admission and caller-context regression controls."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_mcp import __main__ as cli
from agent_mcp import cutover, execution_context, incoming, ipc
from agent_mcp.config import AuthSpec, BridgeConfig, ServerSpec


async def test_unselected_cold_call_never_opens_upstream(monkeypatch):
    monkeypatch.setattr(incoming, "launch_generation", lambda: "old")

    def unselected():
        raise cutover.ControlError("selected generation changed")

    monkeypatch.setattr(cutover, "_installed_slot", unselected)
    opened = []
    monkeypatch.setattr("agent_mcp.client.OneShotSession", lambda cfg: opened.append(cfg))
    cfg = BridgeConfig(server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec())
    # Both unavailable-accelerator and explicit no-serve converge here.
    with pytest.raises(incoming.AdmissionRefused):
        await cli._run_call(cfg, "mutate", {})
    assert not opened


def test_warm_selection_refusal_is_not_optional_accelerator_failure(monkeypatch, capsys):
    monkeypatch.setattr(ipc, "serve_socket_if_available", lambda: "private-handle")

    async def refused(*args):
        raise incoming.AdmissionRefused("selection changed")

    monkeypatch.setattr(ipc, "call_via_socket", refused)
    assert cli._try_serve_call("bridge", "mutate", {}, no_serve=False) == 1
    assert "selection changed" in capsys.readouterr().err


def test_uncertain_warm_mutation_is_not_cold_replayed(monkeypatch, capsys):
    monkeypatch.setattr(ipc, "serve_socket_if_available", lambda: "private-handle")

    async def lost(*args):
        raise incoming.OutcomeUnknown("outcome unknown")

    monkeypatch.setattr(ipc, "call_via_socket", lost)
    assert cli._try_serve_call("bridge", "mutate", {}, no_serve=False) == 1
    assert "outcome unknown" in capsys.readouterr().err


@pytest.mark.parametrize("wire", [
    b"", b"null\n", b"17\n", b"true\n", b"[]\n", b"{}\n",
    b'{"ok":"yes"}\n', b'{"ok":true,"content":[]}\n', b"not-json\n",
    b'{"ok":true,"content":"mutation executed","shared_execution":false}\n',
    b'{"ok":true,"structured":{"executed":true},"shared_execution":false}\n',
    b'{"ok":false,"error":"refused","shared_execution":false,"content":"executed"}\n',
    b'{"ok":false,"error":"refused","shared_execution":false,"structured":null}\n',
    b'{"ok":false,"error":"refused","shared_execution":false,"isError":true}\n',
    b'{"ok":false,"error":"refused","shared_execution":false,"result":{"executed":true}}\n',
    b'{"ok":false,"error":"refused","shared_execution":false,"unknown":null}\n',
    b'{"ok":false,"error":"refused","shared_execution":0}\n',
])
def test_lost_reply_after_application_write_preserves_uncertainty(monkeypatch, capsys, wire):
    monkeypatch.setattr(incoming, "ensure_current", lambda handle: None)
    monkeypatch.setattr(execution_context, "request_fields", lambda *args: {})
    writes = []

    class Reader:
        def __init__(self):
            self.replies = iter([
                b'{"ok":true,"pong":true,"configuration_context":1}\n', wire,
            ])

        async def readline(self):
            return next(self.replies)

    class Writer:
        def write(self, data):
            writes.append(json.loads(data))

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def connect(handle):
        return Reader(), Writer(), None

    monkeypatch.setattr(ipc, "_connect", connect)
    monkeypatch.setattr(ipc, "serve_socket_if_available", lambda: "private")
    cold = []
    monkeypatch.setattr("agent_mcp.config.load_config", lambda ref: BridgeConfig(
        server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec(),
    ))

    async def replay(cfg, tool, arguments):
        cold.append(tool)
        return 0

    monkeypatch.setattr(cli, "_run_call", replay)
    assert cli.main(["call", "bridge", "mutate", "{}"]) == 1
    assert not cold
    assert [request["op"] for request in writes] == ["ping", "call"]
    assert writes[1]["tool"] == "mutate"
    assert "no cold replay" in capsys.readouterr().err


def test_unsuccessful_pre_effect_context_refusal_still_allows_direct_fallback(monkeypatch):
    async def refused(*args, **kwargs):
        return {"ok": False, "error": "context differs", "shared_execution": False}

    monkeypatch.setattr(ipc, "request_via_socket", refused)
    monkeypatch.setattr(ipc, "serve_socket_if_available", lambda: "private")
    assert cli._try_serve_call("bridge", "mutate", {}, no_serve=False) is None


def test_prewrite_unavailable_accelerator_still_allows_cold_execution(monkeypatch):
    monkeypatch.setattr(ipc, "serve_socket_if_available", lambda: "private-handle")

    async def unavailable(*args):
        raise ConnectionRefusedError("no request written")

    monkeypatch.setattr(ipc, "call_via_socket", unavailable)
    assert cli._try_serve_call("bridge", "mutate", {}, no_serve=False) is None


def test_daemon_bookkeeping_is_not_caller_authority_but_explicit_use_is(monkeypatch):
    cfg = BridgeConfig(server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec())
    key = execution_context.descriptor()["key"]
    monkeypatch.delenv("AGENT_MCP_CONTROL_TOKEN", raising=False)
    before = execution_context._identity(cfg, key)
    monkeypatch.setenv("AGENT_MCP_CONTROL_TOKEN", "daemon-only-fixture")
    assert execution_context._identity(cfg, key) == before
    assert "AGENT_MCP_CONTROL_TOKEN" not in execution_context.inherited_environment(cfg)
    cfg.auth = AuthSpec(kind="env", source_env="AGENT_MCP_CONTROL_TOKEN")
    explicit = execution_context._identity(cfg, key)
    assert execution_context.inherited_environment(cfg)["AGENT_MCP_CONTROL_TOKEN"] == "daemon-only-fixture"
    monkeypatch.delenv("AGENT_MCP_CONTROL_TOKEN")
    assert execution_context._identity(cfg, key) != explicit


@pytest.mark.parametrize("name", [
    "COPILOT_EXTENSIONS_BOOT_TRACE", "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN",
    "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS",
])
def test_trace_fields_remain_physical_but_only_explicit_use_changes_context(monkeypatch, name):
    cfg = BridgeConfig(server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec())
    key = execution_context.descriptor()["key"]
    monkeypatch.setenv(name, "first-launch")
    first = execution_context._identity(cfg, key)
    monkeypatch.setenv(name, "second-launch")
    assert execution_context.inherited_environment(cfg)[name] == "second-launch"
    assert execution_context._identity(cfg, key) == first
    cfg.server.env["EXPLICIT_TRACE"] = "${" + name + "}"
    explicit = execution_context._identity(cfg, key)
    monkeypatch.setenv(name, "third-launch")
    assert execution_context._identity(cfg, key) != explicit


@pytest.mark.parametrize("platform,reference,significant", [
    ("nt", "agent_mcp_install_dir", True),
    ("posix", "agent_mcp_install_dir", False),
    ("posix", "AGENT_MCP_INSTALL_DIR", True),
])
def test_install_root_is_semantic_bookkeeping_but_physically_inherited(
    tmp_path, monkeypatch, platform, reference, significant,
):
    environment = {"AGENT_MCP_HOME": str(tmp_path / "home")}
    monkeypatch.setattr(execution_context, "os", SimpleNamespace(
        name=platform, environ=environment, getcwd=lambda: str(tmp_path),
    ))
    cfg = BridgeConfig(server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec())
    key = execution_context.descriptor()["key"]
    caller = execution_context._identity(cfg, key)
    environment["AGENT_MCP_INSTALL_DIR"] = str(tmp_path / "installation")
    assert execution_context._identity(cfg, key) == caller
    assert execution_context.inherited_environment(cfg)["AGENT_MCP_INSTALL_DIR"] == environment["AGENT_MCP_INSTALL_DIR"]
    cfg.auth = AuthSpec(kind="env", source_env=reference)
    explicit = execution_context._identity(cfg, key)
    environment["AGENT_MCP_INSTALL_DIR"] = str(tmp_path / "other-installation")
    assert (execution_context._identity(cfg, key) != explicit) is significant


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_explicit_reference_matching_retains_platform_case_semantics(monkeypatch, platform):
    cfg = BridgeConfig(
        server=ServerSpec(type="stdio", command=["unused"]),
        auth=AuthSpec(kind="env", source_env="AgEnT_McP_CoNtRoL_ToKeN"),
    )
    monkeypatch.setattr(execution_context, "os", SimpleNamespace(name=platform))
    assert execution_context._references(cfg, "AGENT_MCP_CONTROL_TOKEN") is (platform == "nt")
    assert execution_context._references(cfg, "AgEnT_McP_CoNtRoL_ToKeN")
    assert not execution_context._references(cfg, "UNRELATED_CREDENTIAL")


@pytest.mark.skipif(os.name != "nt", reason="real case-insensitive Windows environment lookup")
def test_mixed_case_explicit_control_credential_is_part_of_windows_context(monkeypatch):
    cfg = BridgeConfig(
        server=ServerSpec(type="stdio", command=["unused"]),
        auth=AuthSpec(kind="env", source_env="agent_mcp_control_token"),
    )
    key = execution_context.descriptor()["key"]
    monkeypatch.setenv("AGENT_MCP_CONTROL_TOKEN", "fixture-account-a")
    assert os.environ[cfg.auth.source_env] == "fixture-account-a"
    first = execution_context._identity(cfg, key)
    assert execution_context.inherited_environment(cfg)["AGENT_MCP_CONTROL_TOKEN"] == "fixture-account-a"
    request = {"execution_context": {
        "contract": execution_context.descriptor()["contract"], "digest": first,
    }}
    assert execution_context.load_config(
        "bridge", request, lambda bridge, **kwargs: cfg, repository_root=Path.cwd(),
    ) is cfg
    monkeypatch.setenv("AGENT_MCP_CONTROL_TOKEN", "fixture-account-b")
    assert os.environ[cfg.auth.source_env] == "fixture-account-b"
    assert execution_context._identity(cfg, key) != first
    with pytest.raises(execution_context.SharingUnavailable):
        execution_context.load_config(
            "bridge", request, lambda bridge, **kwargs: cfg, repository_root=Path.cwd(),
        )


def test_caller_context_is_derived_in_each_callers_environment(monkeypatch):
    cfg = BridgeConfig(server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec())
    monkeypatch.setenv("FIXTURE_CALLER", "account-a")
    key = execution_context.descriptor()["key"]
    first = execution_context._identity(cfg, key)
    request = {"execution_context": {
        "contract": execution_context.descriptor()["contract"], "digest": first,
    }}
    assert execution_context.load_config(
        "bridge", request, lambda bridge, **kwargs: cfg, repository_root=Path.cwd(),
    ) is cfg
    monkeypatch.setenv("FIXTURE_CALLER", "account-b")
    with pytest.raises(execution_context.SharingUnavailable):
        execution_context.load_config(
            "bridge", request, lambda bridge, **kwargs: cfg, repository_root=Path.cwd(),
        )
    assert execution_context._identity(cfg, key) != first


@pytest.mark.parametrize("empty", [False, True], ids=["unset", "empty"])
def test_default_home_shares_but_custom_and_explicit_home_remain_significant(tmp_path, monkeypatch, empty):
    from agent_mcp.home import default_home_dir

    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    if empty:
        monkeypatch.setenv("AGENT_MCP_HOME", "")
    else:
        monkeypatch.delenv("AGENT_MCP_HOME", raising=False)
    cfg = BridgeConfig(server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec())
    key = execution_context.descriptor()["key"]
    default = str(default_home_dir().resolve())
    original = execution_context._identity(cfg, key)
    monkeypatch.setenv("AGENT_MCP_HOME", default)
    assert execution_context._identity(cfg, key) == original
    assert execution_context.load_config("bridge", {"execution_context": {
        "contract": execution_context.descriptor()["contract"], "digest": original,
    }}, lambda bridge, **kwargs: cfg, repository_root=Path.cwd()) is cfg
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path / "custom"))
    assert execution_context._identity(cfg, key) != original
    cfg.auth = AuthSpec(kind="env", source_env="AGENT_MCP_HOME")
    if empty:
        monkeypatch.setenv("AGENT_MCP_HOME", "")
    else:
        monkeypatch.delenv("AGENT_MCP_HOME")
    explicit = execution_context._identity(cfg, key)
    monkeypatch.setenv("AGENT_MCP_HOME", default)
    assert execution_context._identity(cfg, key) != explicit


def test_installed_context_request_does_not_transfer_credentials(monkeypatch):
    cfg = BridgeConfig(server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec())
    monkeypatch.setenv("FIXTURE_CALLER", "fixture-private-value")
    monkeypatch.setattr(incoming, "launch_generation", lambda: "installed")
    monkeypatch.setattr(incoming, "_ping", lambda handle: {
        "execution_context": execution_context.descriptor(),
    })
    monkeypatch.setattr("agent_mcp.config.load_config", lambda bridge, **kwargs: cfg)
    fields = execution_context.request_fields("private", "bridge")
    assert set(fields["execution_context"]) == {"contract", "digest"}
    assert "fixture-private-value" not in json.dumps(fields)


def test_materialized_stub_cold_execution_checks_physical_selection(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_resolve_stub", lambda *args: ("bridge", "mutate"))
    monkeypatch.setattr(incoming, "launch_generation", lambda: "old")

    def unselected():
        raise cutover.ControlError("selection changed")

    monkeypatch.setattr(cutover, "_installed_slot", unselected)
    cfg = BridgeConfig(server=ServerSpec(type="stdio", command=["unused"]), auth=AuthSpec())
    monkeypatch.setattr("agent_mcp.config.load_config", lambda bridge: cfg)
    opened = []
    monkeypatch.setattr("agent_mcp.client.OneShotSession", lambda cfg: opened.append(cfg))
    assert cli.main(["call", "{}", "--manifest", "manifest.json", "--stub", "mutate", "--no-serve"]) == 1
    assert not opened
    assert "physical generation" in capsys.readouterr().err
