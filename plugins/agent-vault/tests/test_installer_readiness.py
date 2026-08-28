from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_vault import cli, rendezvous
from agent_vault.config import ResolvedVault
from agent_vault.installer_readiness import emit, evaluate
from agent_vault.service import VaultService


def _context(kpdb: str | None) -> ResolvedVault:
    return ResolvedVault(
        vault_name="example" if kpdb else None,
        kpdb=kpdb,
        group=None,
        port=19999,
        sources={},
    )


def test_healthy_unconfigured_runtime_is_configuration_empty(capsys):
    result = evaluate(_context(None), {"ok": True, "cli": "locked"})

    assert result["state"] == "configuration-empty"
    assert "did not create or unlock vault state" in result["detail"]
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_locked_configured_vault_remains_ready_without_unlock():
    result = evaluate(_context("example.kdbx"), {"ok": True, "cli": "locked"})

    assert result["state"] == "ready"
    assert "unlock it explicitly" in result["detail"]


def test_unlocked_configured_vault_is_ready():
    result = evaluate(_context("example.kdbx"), {"ok": True, "cli": "unlocked"})

    assert result["state"] == "ready"
    assert "currently unlocked" in result["detail"]


def test_ping_reports_selected_vault_locked_when_another_is_unlocked():
    service = VaultService()
    service.cli._cli_path = "keepassxc-cli"
    service.cli.set_password("other.kdbx", "secret")

    response = service.handle_request(
        {"action": "ping", "kpdb": "selected.kdbx", "vault": "selected"}
    )

    assert response["cli"] == "locked"
    assert response["unlocked_vaults"] == ["other.kdbx"]


def test_corrupt_config_or_service_failure_is_failed():
    invalid = evaluate(None, None, ["config.json: malformed JSON"])
    unavailable = evaluate(_context("example.kdbx"), None)
    missing_cli = evaluate(
        _context("example.kdbx"),
        {"ok": True, "cli": "not_found"},
    )

    assert invalid["state"] == "failed"
    assert unavailable["state"] == "failed"
    assert missing_cli["state"] == "failed"


def test_payload_command_only_pings_and_never_starts_or_unlocks(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        "agent_vault.config.resolve_context",
        lambda **_kwargs: _context("example.kdbx"),
    )
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda request, **_kwargs: {"ok": True, "cli": "locked"}
        if request == {"action": "ping"}
        else None,
    )
    monkeypatch.setattr(
        cli,
        "ensure_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("readiness must not start the service")
        ),
    )
    monkeypatch.setattr(
        cli,
        "gui_prompt_password",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("readiness must not unlock the vault")
        ),
    )

    assert cli.main(["installer-readiness"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_payload_command_rejects_corrupt_config_without_contacting_service(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(path))
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda _request: (_ for _ in ()).throw(
            AssertionError("invalid config must fail before service contact")
        ),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "invalid configuration" in result["detail"]


def test_payload_command_rejects_invalid_named_vault_shape(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "config.json"
    path.write_text(
        '{"default_vault":"example","vaults":{"example":[]}}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(path))
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda _request: (_ for _ in ()).throw(
            AssertionError("invalid config must fail before service contact")
        ),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "must be an object" in result["detail"]


def test_payload_command_rejects_invalid_endpoint_and_port(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        "agent_vault.config.resolve_context",
        lambda **_kwargs: _context("example.kdbx"),
    )
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda _request, **_kwargs: (_ for _ in ()).throw(
            ValueError("malformed endpoint")
        ),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "malformed endpoint" in result["detail"]
    assert "service probe failed" in result["detail"]


def test_strict_context_rejects_invalid_environment_port(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_PORT", "not-a-port")

    try:
        from agent_vault import config

        config.resolve_context(strict=True)
    except RuntimeError as exc:
        assert "port must be an integer" in str(exc)
    else:
        raise AssertionError("strict resolution accepted an invalid port")


def test_strict_context_treats_null_port_as_default(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"port": null}\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(path))

    from agent_vault import config

    assert config.resolve_context(strict=True).port == config.DEFAULT_TCP_PORT
    assert config.resolve_context().port == config.DEFAULT_TCP_PORT


@pytest.mark.parametrize(
    "port",
    (
        True,
        False,
        1.0,
        1.5,
        0,
        -1,
        65536,
        "",
        " 19999 ",
        "+19999",
        "1.0",
        "not-a-port",
        [],
        {},
    ),
)
def test_strict_context_still_rejects_invalid_non_null_port(
    port, tmp_path, monkeypatch
):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"port": port}), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(path))

    from agent_vault import config

    with pytest.raises(RuntimeError, match="port must be an integer"):
        config.resolve_context(strict=True)


@pytest.mark.parametrize(
    ("configured", "expected"),
    ((1, 1), (65535, 65535), ("1", 1), ("19999", 19999), ("65535", 65535)),
)
def test_strict_context_accepts_integer_and_digit_string_ports(
    configured, expected, tmp_path, monkeypatch
):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"port": configured}), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(path))

    from agent_vault import config

    assert config.resolve_context(strict=True).port == expected


@pytest.mark.parametrize("vaults", ("missing", None))
def test_strict_context_rejects_default_vault_without_registry(
    vaults, tmp_path, monkeypatch
):
    path = tmp_path / "config.json"
    data = {"default_vault": "example"}
    if vaults != "missing":
        data["vaults"] = vaults
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(path))

    from agent_vault import config

    with pytest.raises(RuntimeError, match="is not a configured vault"):
        config.resolve_context(strict=True)


def test_readiness_allows_absent_endpoint_state_to_use_legacy_fallback(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(
        "agent_vault.config.resolve_context",
        lambda **_kwargs: _context("example.kdbx"),
    )
    monkeypatch.setattr(cli, "_send_socket", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_send_tcp",
        lambda request, _host, _port, _timeout: {
            "ok": True,
            "cli": "locked",
        },
    )
    monkeypatch.setattr(
        "agent_vault.extensions.get_registry",
        lambda: type(
            "Registry",
            (),
            {
                "apply_cli_commands": lambda *_args, **_kwargs: None,
                "try_transports": lambda *_args, **_kwargs: None,
            },
        )(),
    )

    assert cli.main(["installer-readiness"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_readiness_rejects_malformed_present_endpoint_before_legacy_fallback(
    tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "endpoint.json").write_text("{", encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(
        cli,
        "_send_tcp",
        lambda *_args, **_kwargs: pytest.fail("must not use legacy fallback"),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "malformed endpoint state" in result["detail"]


def test_normal_endpoint_discovery_remains_tolerant_of_malformed_state(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "endpoint.json").write_text("{", encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(cli, "IS_WSL", False)

    assert cli._discover_endpoint(_context("example.kdbx")) is None


@pytest.mark.parametrize("record", (None, [], False, 0, ""))
def test_normal_endpoint_discovery_tolerates_wrong_top_level_shape(
    record, tmp_path, monkeypatch
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "endpoint.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(cli, "IS_WSL", False)

    assert cli._discover_endpoint(_context("example.kdbx")) is None


def test_normal_endpoint_discovery_tolerates_invalid_utf8(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "endpoint.json").write_bytes(b"\xff")
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(cli, "IS_WSL", False)

    assert cli._discover_endpoint(_context("example.kdbx")) is None


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema", float("inf")),
        ("schema", True),
        ("schema", 1.0),
        ("schema", 10**100),
        ("pid", float("inf")),
        ("pid", True),
        ("pid", 1.0),
        ("pid", -1),
        ("pid", 10**100),
    ),
)
def test_normal_endpoint_discovery_tolerates_invalid_numeric_fields(
    field, value, tmp_path, monkeypatch
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = {
        "schema": 1,
        "transport": "pipe",
        "endpoint": r"\\.\pipe\agent-vault",
        "pid": None,
        "started_at": None,
    }
    record[field] = value
    (run_dir / "endpoint.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(cli, "IS_WSL", False)

    assert cli._discover_endpoint(_context("example.kdbx")) is None


def test_normal_endpoint_discovery_accepts_valid_record(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    record = {
        "schema": 1,
        "transport": "pipe",
        "endpoint": r"\\.\pipe\agent-vault",
        "pid": None,
        "started_at": None,
    }
    (run_dir / "endpoint.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(cli, "IS_WSL", False)

    endpoint = cli._discover_endpoint(_context("example.kdbx"))

    assert endpoint == rendezvous.Endpoint(
        transport="pipe",
        address=r"\\.\pipe\agent-vault",
    )


def test_strict_windows_endpoint_accepts_unsigned_32_bit_pid(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "windows-run"
    run_dir.mkdir()
    rendezvous.write_endpoint(
        run_dir,
        "pipe",
        r"\\.\pipe\agent-vault",
        pid=2**31,
    )
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(run_dir))

    endpoint = cli._read_windows_endpoint(strict=True)

    assert endpoint is not None
    assert endpoint.pid == 2**31
    assert endpoint.source == "windows"


def test_pid_alive_rejects_values_outside_local_platform_range():
    assert rendezvous.pid_alive(rendezvous._MAX_LOCAL_PID + 1) is False


def test_normal_endpoint_discovery_ignores_malformed_alternates(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "endpoint.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "transport": "pipe",
                "endpoint": r"\\.\pipe\agent-vault",
                "pid": None,
                "started_at": None,
                "alt": [
                    "not-an-endpoint-object",
                    {"transport": "invalid", "endpoint": "ignored"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(cli, "IS_WSL", False)

    endpoint = cli._discover_endpoint(_context("example.kdbx"))

    assert endpoint is not None
    assert endpoint.transport == "pipe"
    assert endpoint.alt == ()


def test_readiness_rejects_endpoint_file_shape_before_legacy_fallback(
    tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "run"
    (run_dir / "endpoint.json").mkdir(parents=True)
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(
        cli,
        "_send_tcp",
        lambda *_args, **_kwargs: pytest.fail("must not use legacy fallback"),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "unreadable endpoint state" in result["detail"]


def test_readiness_rejects_unreadable_endpoint_before_legacy_fallback(
    tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    endpoint = run_dir / "endpoint.json"
    endpoint.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    original_read_text = Path.read_text

    def fail_endpoint_read(path, *args, **kwargs):
        if path == endpoint:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_endpoint_read)
    monkeypatch.setattr(
        cli,
        "_send_tcp",
        lambda *_args, **_kwargs: pytest.fail("must not use legacy fallback"),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "unreadable endpoint state" in result["detail"]


@pytest.mark.parametrize(
    "record",
    (
        None,
        [],
        {
            "schema": 1,
            "transport": "tcp",
            "endpoint": "not-a-host-port",
            "pid": None,
            "started_at": None,
        },
        {
            "schema": 1,
            "transport": "tcp",
            "endpoint": "127.0.0.1:0",
            "pid": None,
            "started_at": None,
        },
        {
            "schema": 1,
            "transport": "tcp",
            "endpoint": "127.0.0.1:65536",
            "pid": None,
            "started_at": None,
        },
        {
            "schema": 1,
            "transport": "invalid",
            "endpoint": "address",
            "pid": None,
            "started_at": None,
        },
        {
            "schema": 1,
            "transport": "unix",
            "endpoint": "",
            "pid": None,
            "started_at": None,
        },
        {
            "schema": 1,
            "transport": "tcp",
            "endpoint": "127.0.0.1:19999",
            "pid": None,
            "started_at": None,
            "alt": ["not-an-endpoint-object"],
        },
        {
            "schema": 1,
            "transport": "tcp",
            "endpoint": "127.0.0.1:19999",
            "pid": None,
            "started_at": None,
            "alt": [{"transport": "invalid", "endpoint": "address"}],
        },
        {
            "schema": 1,
            "transport": "tcp",
            "endpoint": "127.0.0.1:19999",
            "pid": None,
            "started_at": None,
            "alt": [{"transport": "unix", "endpoint": ""}],
        },
        {
            "schema": 1,
            "transport": "tcp",
            "endpoint": "127.0.0.1:19999",
            "pid": None,
            "started_at": None,
            "alt": [{"transport": "tcp", "endpoint": "not-a-host-port"}],
        },
    ),
)
def test_readiness_rejects_wrong_shaped_endpoint_before_legacy_fallback(
    record, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "endpoint.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(
        cli,
        "_send_tcp",
        lambda *_args, **_kwargs: pytest.fail("must not use legacy fallback"),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "malformed endpoint state" in result["detail"]


@pytest.mark.parametrize("schema", (True, 1.0, float("inf"), "1", None))
def test_readiness_requires_exact_integer_endpoint_schema(
    schema, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "endpoint.json").write_text(
        json.dumps(
            {
                "schema": schema,
                "transport": "tcp",
                "endpoint": "127.0.0.1:19999",
                "pid": None,
                "started_at": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(
        cli,
        "_send_tcp",
        lambda *_args, **_kwargs: pytest.fail("must not use legacy fallback"),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "endpoint schema must be an integer" in result["detail"]


@pytest.mark.parametrize(
    "pid",
    (True, 1.0, float("inf"), -1, 0, rendezvous.MAX_PID + 1, 10**100),
)
def test_readiness_rejects_invalid_endpoint_pid(
    pid, tmp_path, monkeypatch, capsys
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "endpoint.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "transport": "tcp",
                "endpoint": "127.0.0.1:19999",
                "pid": pid,
                "started_at": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(run_dir))
    monkeypatch.setattr(
        cli,
        "_send_tcp",
        lambda *_args, **_kwargs: pytest.fail("must not use legacy fallback"),
    )

    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "pid must be a positive integer" in result["detail"]
