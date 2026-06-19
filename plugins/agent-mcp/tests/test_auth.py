from __future__ import annotations

import sys

from agent_mcp.auth import (
    CommandInjector,
    EntraInjector,
    EnvInjector,
    GitCredentialInjector,
    NoneInjector,
    build_injector,
    parse_response,
)
from agent_mcp.config import parse_config


def _cfg(auth, server=None):
    doc = {"server": server or {"type": "http", "url": "https://mcp.example/o"}, "auth": auth}
    return parse_config(doc)


def test_parse_response_keyvalue():
    fields = parse_response("protocol=https\nhost=h\ntoken=abc\n\n")
    assert fields["token"] == "abc"
    assert fields["host"] == "h"


def test_parse_response_empty():
    assert parse_response(None) == {}
    assert parse_response("") == {}


def test_build_none():
    inj = build_injector(_cfg({"kind": "none"}))
    assert isinstance(inj, NoneInjector)


async def test_none_injects_nothing():
    inj = NoneInjector()
    assert await inj.headers() == {}
    assert await inj.child_env() == {}


async def test_env_injector_header(monkeypatch):
    monkeypatch.setenv("MY_TOK", "s3cret")
    inj = build_injector(_cfg({"kind": "env", "source_env": "MY_TOK"}))
    assert isinstance(inj, EnvInjector)
    assert await inj.headers() == {"Authorization": "Bearer s3cret"}


async def test_env_injector_static_value_and_child_env():
    inj = build_injector(_cfg(
        {"kind": "static", "value": "lit", "target_env": "API_KEY", "format": "{token}"},
        server={"type": "stdio", "command": "npx"},
    ))
    assert await inj.headers() == {"Authorization": "lit"}
    assert await inj.child_env() == {"API_KEY": "lit"}


async def test_env_injector_missing_token_is_empty():
    inj = build_injector(_cfg({"kind": "env", "source_env": "DEFINITELY_UNSET_VAR_XYZ"}))
    assert await inj.headers() == {}


async def test_token_injector_caches_and_invalidates(monkeypatch):
    calls = {"n": 0}

    monkeypatch.setenv("ROT", "v1")
    inj = build_injector(_cfg({"kind": "env", "source_env": "ROT"}))

    orig = inj._acquire

    async def counting():
        calls["n"] += 1
        return await orig()

    inj._acquire = counting
    await inj.headers()
    await inj.headers()
    assert calls["n"] == 1  # cached
    await inj.invalidate()
    await inj.headers()
    assert calls["n"] == 2


async def test_entra_injector_wraps_source(monkeypatch):
    inj = build_injector(_cfg({"kind": "entra", "resource": "res"}))
    assert isinstance(inj, EntraInjector)

    class FakeSource:
        async def resolve(self, action, fields, *, timeout=30.0):
            assert action == "get-azure-token"
            assert fields["resource"] == "res"
            return "protocol=https\nhost=h\ntoken=AZTOKEN\n\n"

    inj._source = FakeSource()
    assert await inj.headers() == {"Authorization": "Bearer AZTOKEN"}


def test_build_git_credential_derives_host():
    inj = build_injector(_cfg(
        {"kind": "git-credential"},
        server={"type": "http", "url": "https://dev.azure.com/org"},
    ))
    assert isinstance(inj, GitCredentialInjector)
    assert inj._host == "dev.azure.com"


# -- command injector -------------------------------------------------------

def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _command_cfg(auth_over, *, stdio=True):
    auth = {"kind": "command"}
    auth.update(auth_over)
    server = {"type": "stdio", "command": "npx"} if stdio else \
        {"type": "http", "url": "https://mcp.example/o"}
    return _cfg(auth, server=server)


def test_build_command_injector():
    inj = build_injector(_command_cfg({"command": "vault"}))
    assert isinstance(inj, CommandInjector)


async def test_command_raw_mode_child_env():
    inj = build_injector(_command_cfg({
        "command": _py("import sys; sys.stdout.write('rawtok\\n')"),
        "parse": "raw",
        "target_env": "API_KEY",
    }))
    assert await inj.child_env() == {"API_KEY": "rawtok"}


async def test_command_keyvalue_default_token():
    inj = build_injector(_command_cfg({
        "command": _py("print('password=pw123')"),
        "target_env": "API_KEY",
    }))
    assert await inj.child_env() == {"API_KEY": "pw123"}


async def test_command_keyvalue_field_selects_key():
    inj = build_injector(_command_cfg({
        "command": _py("print('token=t'); print('password=p')"),
        "field": "password",
        "target_env": "API_KEY",
    }))
    assert await inj.child_env() == {"API_KEY": "p"}


async def test_command_header_injection():
    inj = build_injector(_command_cfg(
        {"command": _py("print('password=h3y')"), "parse": "keyvalue"},
        stdio=False,
    ))
    assert await inj.headers() == {"Authorization": "Bearer h3y"}


async def test_command_receives_request_on_stdin():
    # Echo back the host field from the git-credential request as the token.
    code = (
        "import sys\n"
        "f=dict(l.split('=',1) for l in sys.stdin.read().splitlines() if '=' in l)\n"
        "print('password=' + f.get('host',''))"
    )
    inj = build_injector(_command_cfg({
        "command": _py(code),
        "request": {"protocol": "https", "host": "vault.example"},
        "target_env": "API_KEY",
    }))
    assert await inj.child_env() == {"API_KEY": "vault.example"}


async def test_command_nonzero_exit_is_empty():
    inj = build_injector(_command_cfg({
        "command": _py("import sys; sys.exit(3)"),
        "target_env": "API_KEY",
    }))
    assert await inj.child_env() == {}


async def test_command_not_found_is_empty():
    inj = build_injector(_command_cfg({
        "command": ["definitely-not-a-real-cmd-xyz"],
        "target_env": "API_KEY",
    }))
    assert await inj.child_env() == {}


async def test_command_timeout_kills_child():
    # A command that outlives the timeout must be reaped, not leaked.
    inj = build_injector(_command_cfg({
        "command": [sys.executable, "-c", "import time; time.sleep(30)"],
        "target_env": "API_KEY",
    }))
    inj._timeout = 0.5
    captured: dict = {}
    orig_term = inj._terminate

    async def spy(proc):
        captured["proc"] = proc
        await orig_term(proc)

    inj._terminate = spy
    assert await inj.child_env() == {}  # timed out -> no token
    proc = captured.get("proc")
    assert proc is not None
    assert proc.returncode is not None  # reaped (not left running)


async def test_command_source_env_first(monkeypatch):
    monkeypatch.setenv("PRESET_TOK", "from-env")
    inj = build_injector(_command_cfg({
        "command": _py("print('password=from-cmd')"),
        "source_env": "PRESET_TOK",
        "target_env": "API_KEY",
    }))
    assert await inj.child_env() == {"API_KEY": "from-env"}  # env wins; cmd not run


async def test_command_source_env_absent_runs_command(monkeypatch):
    monkeypatch.delenv("PRESET_TOK_ABSENT", raising=False)
    inj = build_injector(_command_cfg({
        "command": _py("print('password=from-cmd')"),
        "source_env": "PRESET_TOK_ABSENT",
        "target_env": "API_KEY",
    }))
    assert await inj.child_env() == {"API_KEY": "from-cmd"}


async def test_command_caches_until_invalidate():
    inj = build_injector(_command_cfg({
        "command": _py("print('password=cached')"),
        "target_env": "API_KEY",
    }))
    calls = {"n": 0}
    orig = inj._acquire

    async def counting():
        calls["n"] += 1
        return await orig()

    inj._acquire = counting
    await inj.child_env()
    await inj.child_env()
    assert calls["n"] == 1
    await inj.invalidate()
    await inj.child_env()
    assert calls["n"] == 2


# -- composite (multi-secret) injector --------------------------------------

def _multi_cfg(specs):
    return _cfg(specs, server={"type": "stdio", "command": "npx"})


async def test_composite_merges_two_command_secrets():
    from agent_mcp.auth import CompositeInjector
    inj = build_injector(_multi_cfg([
        {"kind": "command", "command": _py("print('password=pw1')"),
         "parse": "keyvalue", "target_env": "PASSWORD_VAR"},
        {"kind": "command", "command": _py("import sys; sys.stdout.write('rawkey\\n')"),
         "parse": "raw", "target_env": "KEY_VAR"},
    ]))
    assert isinstance(inj, CompositeInjector)
    assert await inj.child_env() == {"PASSWORD_VAR": "pw1", "KEY_VAR": "rawkey"}


async def test_composite_invalidate_fans_out():
    inj = build_injector(_multi_cfg([
        {"kind": "command", "command": _py("print('password=a')"),
         "parse": "keyvalue", "target_env": "A"},
        {"kind": "command", "command": _py("print('password=b')"),
         "parse": "keyvalue", "target_env": "B"},
    ]))
    counts = {"a": 0, "b": 0}
    for sub, key in zip(inj.injectors, ("a", "b"), strict=True):
        orig = sub._acquire

        def make(o, k):
            async def c():
                counts[k] += 1
                return await o()
            return c
        sub._acquire = make(orig, key)
    await inj.child_env()
    await inj.child_env()
    assert counts == {"a": 1, "b": 1}  # both cached
    await inj.invalidate()
    await inj.child_env()
    assert counts == {"a": 2, "b": 2}  # both refreshed


