from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_vault import cli
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
            "endpoint": "127.0.0.1:19999",
            "pid": None,
            "started_at": None,
            "alt": ["not-an-endpoint-object"],
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
