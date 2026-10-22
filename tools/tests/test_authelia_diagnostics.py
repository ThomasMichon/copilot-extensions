"""Useful failure evidence survives cleanup without credential-bearing logs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_authelia_profile_qualification import CID, MODULES, NETWORK, PREP, ROOT, RUN

DIAG = MODULES["diagnostics"]


def test_useful_nested_cause_is_retained_and_actual_fixture_secrets_are_redacted(
    tmp_path: Path,
) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(mode=0o700)
    PREP.prepare(fixtures)
    inputs = json.loads((fixtures / "client/inputs.json").read_bytes())
    config = json.loads((fixtures / "provider/configuration.yml").read_bytes())
    client = config["identity_providers"]["oidc"]["clients"][0]
    secret_values = [
        inputs["client_secret"], inputs["proof_key"]["d"], client["client_secret"],
        config["session"]["secret"], config["storage"]["encryption_key"],
        config["identity_providers"]["oidc"]["hmac_secret"],
        config["identity_validation"]["reset_password"]["jwt_secret"],
        config["identity_providers"]["oidc"]["jwks"][0]["key"],
        (fixtures / "provider/key.pem").read_text(),
    ]
    raw = json.dumps({
        "level": "fatal", "msg": "Provider startup failed",
        "error": {"cause": "opening /state/authelia.sqlite3: permission denied",
                  "debug": secret_values},
        "stack": "fixture/server.go:42",
        "arbitrary_field": "must-not-be-retained",
    }).encode()
    engine = RUN.QualifierEngine(tmp_path, {})
    log = engine.logs / "provider.log"
    engine.write_log(log, raw)
    stored = json.loads(log.read_bytes())
    record = stored["diagnostics"][0]
    assert record["msg"] == "Provider startup failed"
    assert "opening /state/authelia.sqlite3: permission denied" in record["error"]
    assert record["stack"] == "fixture/server.go:42"
    text = log.read_text()
    none_leaked = all(value.strip() not in text and json.dumps(value)[1:-1] not in text
                      for value in secret_values)
    assert none_leaked
    assert "must-not-be-retained" not in text
    assert "[redacted]" in record["error"]


@pytest.mark.parametrize("text,forbidden", [
    ('"password": "unknown-password-value"', "unknown-password-value"),
    ("access_token=unknown-access-value", "unknown-access-value"),
    ("DP" + "oP " + "unknown-access-value", "unknown-access-value"),
    ("a" * 12 + "." + "b" * 12 + "." + "c" * 12, "a" * 12),
    ("-----BEGIN PRIVATE KEY-----\nprivate-body\n-----END PRIVATE KEY-----", "private-body"),
    ("$argon2id$v=19$m=8192,t=1,p=1$fixture-salt$fixture-digest", "fixture-digest"),
])
def test_unknown_credential_shapes_are_removed(text: str, forbidden: str) -> None:
    assert forbidden not in DIAG.redact(text, ())


def test_redaction_precedes_truncation_and_omissions_are_counted(tmp_path: Path) -> None:
    secret = "fixture-secret-" + "s" * 100
    text = "x" * (DIAG.MAX_TEXT - 5) + secret
    redacted = DIAG.redact(text, (secret,))
    assert "fixture-secret" not in redacted and redacted.endswith("[truncated]")
    assert len(redacted) <= DIAG.MAX_TEXT + len(" [truncated]")
    records = [
        json.dumps({"level": "error", "error": "precise nested cause"}).encode()
        for _ in range(DIAG.MAX_RECORDS + 3)
    ]
    result = DIAG.diagnostic_records(b"\n".join(records), tmp_path)
    assert len(result["diagnostics"]) == DIAG.MAX_RECORDS
    assert result["omitted_diagnostic_records"] == 3


def test_structured_vendor_stack_and_provider_names_survive_capture(tmp_path: Path) -> None:
    record = {"level": "fatal", "msg": "Startup checks failed",
              "stack": ["fixture/server.go:42", "fixture/health.go:17"],
              "providers": ["storage", "notification"]}
    result = DIAG.diagnostic_records(json.dumps(record).encode(), tmp_path)["diagnostics"][0]
    assert json.loads(result["stack"]) == record["stack"]
    assert json.loads(result["providers"]) == record["providers"]


def test_text_configuration_error_preserves_cause_and_escaped_values(tmp_path: Path) -> None:
    message = 'Configuration: invalid value "quoted" at C:\\fixture\\config\nsecond line'
    error = 'client_secret="unknown-fixture-secret"'
    data = (
        'time="2026-10-22T00:00:00Z" level=error msg=' + json.dumps(message)
        + " error=" + json.dumps(error) + " provider=configuration"
    ).encode()
    value = DIAG.diagnostic_records(data, tmp_path)["diagnostics"][0]
    assert value["msg"] == message and value["provider"] == "configuration"
    assert "unknown-fixture-secret" not in value["error"]
    assert "[redacted]" in value["error"]


def test_text_fatal_configuration_error_redacts_actual_fixture_secret(tmp_path: Path) -> None:
    provider = tmp_path / "provider"
    provider.mkdir()
    secret = "unique-throwaway-client-secret"
    (provider / "configuration.yml").write_text(json.dumps({"client_secret": secret}))
    data = ("time=fixture level=fatal msg=" + json.dumps(
        "Configuration: rejected value " + secret)).encode()
    result = DIAG.diagnostic_records(data, tmp_path)
    assert result["diagnostics"][0]["msg"] == "Configuration: rejected value [redacted]"
    assert secret not in json.dumps(result)


@pytest.mark.parametrize("line", [
    b"unrestricted unstructured output",
    b'level=error msg="unterminated',
    b"level=error level=fatal msg=ambiguous",
    b"prefix level=error msg=foreign",
])
def test_text_parser_has_no_arbitrary_output_fallback(tmp_path: Path, line: bytes) -> None:
    assert DIAG.diagnostic_records(line, tmp_path)["diagnostics"] == []


def test_unreadable_redaction_input_withholds_text_and_surfaces_failure(tmp_path: Path) -> None:
    provider = tmp_path / "fixtures/provider"
    provider.mkdir(parents=True, mode=0o700)
    (provider / "configuration.yml").write_text("not valid JSON")
    engine = RUN.QualifierEngine(tmp_path, {})
    log = engine.logs / "provider.log"
    engine.write_log(log, b'{"level":"fatal","error":"sensitive raw detail"}')
    stored = json.loads(log.read_bytes())
    assert stored["diagnostic_capture"] == "unavailable"
    assert "sensitive raw detail" not in log.read_text()
    assert engine.receipt["diagnostic_capture_error"] == stored["capture_error"]


def test_symlinked_redaction_input_is_not_followed(tmp_path: Path) -> None:
    provider = tmp_path / "provider"
    provider.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("must not read")
    (provider / "configuration.yml").symlink_to(outside)
    with pytest.raises(DIAG.CaptureError):
        DIAG.fixture_secrets(tmp_path)


def test_state_and_redacted_cause_are_persisted_before_exact_cleanup(tmp_path: Path) -> None:
    engine = RUN.QualifierEngine(tmp_path, {})
    engine.containers = [CID]
    sequence = []
    log = engine.logs / "provider.log"

    def call(*args: str, **kwargs: object) -> str:
        sequence.append(args[0])
        if args[0] == "inspect":
            return json.dumps({
                "Status": "exited", "ExitCode": 1, "OOMKilled": False, "Error": "",
            })
        if args[0] == "logs":
            engine.write_log(log, json.dumps({
                "level": "fatal", "msg": "Provider startup failed",
                "error": "opening /state/authelia.sqlite3: permission denied",
            }).encode())
        if args[0] == "stop":
            assert log.exists()
            receipt = json.loads((tmp_path / "receipt.json").read_bytes())
            assert receipt["states_before_cleanup"][0]["status_before_cleanup"] == "exited"
        return ""

    with patch.object(engine, "call", call):
        assert engine.cleanup() == []
    assert sequence == ["inspect", "logs", "stop", "rm"]
    assert "permission denied" in json.loads(log.read_bytes())["diagnostics"][0]["error"]
    state = engine.receipt["states_before_cleanup"][0]
    assert state["exit_code"] == 1 and state["oom_killed"] is False


def test_journal_failure_still_stops_removes_and_removes_network(tmp_path: Path) -> None:
    engine = RUN.QualifierEngine(tmp_path, {})
    engine.containers = [CID]
    engine.network = NETWORK
    calls = []

    def call(*args: str, **kwargs: object) -> str:
        calls.append(args)
        if args[0] == "inspect":
            return json.dumps({
                "Status": "running", "ExitCode": 0, "OOMKilled": False, "Error": "",
            })
        return ""

    with (patch.object(engine, "call", call),
          patch.object(engine, "journal", side_effect=OSError("synthetic write failure"))):
        errors = engine.cleanup()
    assert ("stop", "--time", "5", CID) in calls
    assert ("rm", CID) in calls
    assert ("network", "rm", NETWORK) in calls
    assert errors == ["pre-cleanup-receipt-write-failed", "cleanup-receipt-write-failed"]


def test_final_receipt_failure_returns_explicit_failure_and_restores_signals(tmp_path: Path) -> None:
    previous = {number: RUN.signal.getsignal(number) for number in RUN.SIGNALS}
    with (patch.object(RUN, "materialize_probe", side_effect=RUN.ProofError("synthetic-stop")),
          patch.object(RUN, "save", side_effect=OSError("synthetic write failure"))):
        result = RUN.run(ROOT, "a" * 40, tmp_path, 900)
    assert result["result"] == "fail" and result["error"] == "final-receipt-write-failed"
    assert result["receipt_persistence"] == "failed"
    assert result["cleanup"] == "complete"
    assert {number: RUN.signal.getsignal(number) for number in RUN.SIGNALS} == previous
