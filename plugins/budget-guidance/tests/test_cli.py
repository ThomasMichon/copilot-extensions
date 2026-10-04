from __future__ import annotations

import json

from budget_guidance.cli import main


def _write_config(path):
    path.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.budget-guidance-config",
                "version": 1,
                "adapters": [
                    {
                        "type": "static",
                        "id": "manual",
                        "authority": 1,
                        "reading": {
                            "schema": "copilot-extensions.budget-reading",
                            "version": 1,
                            "source": "manual-example",
                            "captured_at": "2026-09-05T19:00:00Z",
                            "freshness_seconds": 86400,
                            "availability": "available",
                            "allowance": 100,
                            "consumption": 25,
                            "reset_at": "2026-09-08T19:00:00Z",
                            "trailing_rates": [
                                {"window_days": 7, "rate_per_day": 20}
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_json_and_human_status_share_one_posture(tmp_path, capsys):
    config = tmp_path / "config.json"
    _write_config(config)
    args = [
        "status",
        "--config",
        str(config),
        "--at",
        "2026-09-05T19:00:00Z",
    ]
    assert main([*args, "--json"]) == 0
    posture = json.loads(capsys.readouterr().out)
    assert posture["calculated"]["remaining"] == "75"

    assert main(args) == 0
    human = capsys.readouterr().out
    assert "remaining 75" in human
    assert posture["calculated"]["warning_band"] in human
    assert "manual-example" in human


def test_missing_config_is_explicitly_unavailable(tmp_path, capsys):
    assert main(["status", "--config", str(tmp_path / "missing.json"), "--json"]) == 0
    posture = json.loads(capsys.readouterr().out)
    assert posture["availability"] == "unavailable"
    assert posture["calculated"] is None
    assert "consumption" in posture["missing_fields"]


def test_invalid_config_is_a_machine_readable_error(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text('{"schema":"wrong","version":1,"adapters":[]}', encoding="utf-8")
    assert main(["status", "--config", str(config), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["schema"] == "copilot-extensions.budget-posture-error"


def test_invalid_at_is_a_machine_readable_error(capsys):
    assert main(["status", "--at", "not-a-timestamp", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["schema"] == "copilot-extensions.budget-posture-error"
    assert "--at must be a valid RFC 3339 instant" in error["error"]


def test_empty_at_is_rejected_not_silently_defaulted(capsys):
    # An explicitly supplied empty string is a malformed --at, not an omitted
    # one -- it must not silently fall back to "now".
    assert main(["status", "--at", "", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["schema"] == "copilot-extensions.budget-posture-error"
    assert "--at must be a valid RFC 3339 instant" in error["error"]


def test_invalid_at_human_error_is_not_mislabeled_as_configuration(capsys):
    assert main(["status", "--at", "not-a-timestamp"]) == 2
    err = capsys.readouterr().err
    assert "invalid configuration" not in err
    assert "--at must be a valid RFC 3339 instant" in err


def test_huge_freshness_returns_modeled_configuration_error(tmp_path, capsys):
    config = tmp_path / "config.json"
    _write_config(config)
    value = json.loads(config.read_text(encoding="utf-8"))
    value["adapters"][0]["reading"]["freshness_seconds"] = 10**100
    config.write_text(json.dumps(value), encoding="utf-8")

    assert main(
        [
            "status",
            "--config",
            str(config),
            "--at",
            "2026-09-05T19:00:00Z",
            "--json",
        ]
    ) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["schema"] == "copilot-extensions.budget-posture-error"
    assert "freshness_seconds must not exceed" in error["error"]


def test_near_max_timestamp_returns_modeled_posture_without_overflow(tmp_path, capsys):
    config = tmp_path / "config.json"
    _write_config(config)
    value = json.loads(config.read_text(encoding="utf-8"))
    reading = value["adapters"][0]["reading"]
    reading["captured_at"] = "9999-12-31T23:59:58Z"
    reading["reset_at"] = "9999-12-31T23:59:59Z"
    reading["freshness_seconds"] = 315576000
    config.write_text(json.dumps(value), encoding="utf-8")

    assert main(
        [
            "status",
            "--config",
            str(config),
            "--at",
            "9999-12-31T23:59:58Z",
            "--json",
        ]
    ) == 0
    posture = json.loads(capsys.readouterr().out)
    assert posture["schema"] == "copilot-extensions.budget-posture"
    assert posture["calculated"]["seconds_remaining"] == "1"


def test_extreme_allowance_returns_machine_readable_configuration_error(
    tmp_path,
    capsys,
):
    config = tmp_path / "config.json"
    _write_config(config)
    text = config.read_text(encoding="utf-8").replace(
        '"allowance": 100',
        '"allowance": 1e999999999',
    )
    config.write_text(text, encoding="utf-8")

    assert main(["status", "--config", str(config), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["schema"] == "copilot-extensions.budget-posture-error"
    assert "must not exceed" in error["error"]


def test_offset_normalization_overflow_returns_machine_readable_error(
    tmp_path,
    capsys,
):
    config = tmp_path / "config.json"
    _write_config(config)
    value = json.loads(config.read_text(encoding="utf-8"))
    value["adapters"][0]["reading"]["captured_at"] = (
        "9999-12-31T23:59:59-01:00"
    )
    config.write_text(json.dumps(value), encoding="utf-8")

    assert main(["status", "--config", str(config), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["schema"] == "copilot-extensions.budget-posture-error"
    assert "representable UTC instant" in error["error"]
