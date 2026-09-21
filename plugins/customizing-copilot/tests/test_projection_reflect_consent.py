"""Tests for projection-reflect's adopter consent (opt-in) module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "reviewing-customizations"
    / "scripts"
)
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import projection_reflect_consent as consent  # noqa: E402


def _write(repo: Path, payload: dict) -> Path:
    path = repo.joinpath(*consent.CONSENT_PATH_PARTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_payload(**overrides) -> dict:
    payload = {
        "schema": consent.CONSENT_SCHEMA,
        "version": consent.CONSENT_VERSION,
        "enabled": True,
        "reconcilerAgent": "projection-reconciler",
        "dispatchLabel": "projection-conflict",
        "trustedMarketplaces": ["copilot-extensions"],
    }
    payload.update(overrides)
    return payload


def test_no_file_is_no_consent(tmp_path: Path) -> None:
    assert consent.load_consent(tmp_path) is None


def test_valid_consent_loads(tmp_path: Path) -> None:
    _write(tmp_path, _valid_payload())

    result = consent.load_consent(tmp_path)

    assert result == consent.Consent(
        reconciler_agent="projection-reconciler",
        trusted_marketplaces=("copilot-extensions",),
        dispatch_label="projection-conflict",
    )


def test_setup_declines_without_opt_in_file(tmp_path: Path) -> None:
    # The negative-proof case the effort's Validation Plan requires: no
    # opt-in file present -> refuse, never fall back to any other signal.
    assert consent.load_consent(tmp_path) is None


def test_live_revocation_fails_closed_without_a_second_setup(tmp_path: Path) -> None:
    # opt-in present at setup time...
    _write(tmp_path, _valid_payload())
    assert consent.load_consent(tmp_path) is not None

    # ...then withdrawn (deleted) -- the very next call must fail closed on
    # its own; no second `setup` invocation is needed or consulted.
    tmp_path.joinpath(*consent.CONSENT_PATH_PARTS).unlink()
    assert consent.load_consent(tmp_path) is None


def test_explicit_enabled_false_is_not_consent(tmp_path: Path) -> None:
    _write(tmp_path, _valid_payload(enabled=False))
    assert consent.load_consent(tmp_path) is None


def test_missing_enabled_key_is_not_consent(tmp_path: Path) -> None:
    payload = _valid_payload()
    del payload["enabled"]
    _write(tmp_path, payload)
    assert consent.load_consent(tmp_path) is None


def test_wrong_schema_is_not_consent(tmp_path: Path) -> None:
    _write(tmp_path, _valid_payload(schema="something-else"))
    assert consent.load_consent(tmp_path) is None


def test_wrong_version_is_not_consent(tmp_path: Path) -> None:
    _write(tmp_path, _valid_payload(version=2))
    assert consent.load_consent(tmp_path) is None


def test_malformed_json_is_not_consent(tmp_path: Path) -> None:
    path = tmp_path.joinpath(*consent.CONSENT_PATH_PARTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    assert consent.load_consent(tmp_path) is None


def test_missing_reconciler_agent_is_not_consent(tmp_path: Path) -> None:
    payload = _valid_payload()
    del payload["reconcilerAgent"]
    _write(tmp_path, payload)
    assert consent.load_consent(tmp_path) is None


def test_empty_trusted_marketplaces_is_not_consent(tmp_path: Path) -> None:
    _write(tmp_path, _valid_payload(trustedMarketplaces=[]))
    assert consent.load_consent(tmp_path) is None


def test_non_string_trusted_marketplace_entry_is_not_consent(tmp_path: Path) -> None:
    _write(tmp_path, _valid_payload(trustedMarketplaces=["ok", 5]))
    assert consent.load_consent(tmp_path) is None


def test_non_dict_json_is_not_consent(tmp_path: Path) -> None:
    path = tmp_path.joinpath(*consent.CONSENT_PATH_PARTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    assert consent.load_consent(tmp_path) is None


def test_symlinked_consent_file_is_not_consent(tmp_path: Path) -> None:
    # A symlinked consent file could source an opt-in from outside the repo
    # the committed file appears to live in -- reject it outright.
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_valid_payload()), encoding="utf-8")
    path = tmp_path.joinpath(*consent.CONSENT_PATH_PARTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    assert consent.load_consent(tmp_path) is None


def test_symlinked_parent_directory_is_not_consent(tmp_path: Path) -> None:
    # A symlinked *parent* directory is exactly as much of an escape as a
    # symlinked file -- also reject it.
    outside = tmp_path / "outside-copilot"
    outside.mkdir()
    (outside / "projection-reflect.json").write_text(
        json.dumps(_valid_payload()), encoding="utf-8"
    )
    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    linked = github_dir / "copilot"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    assert consent.load_consent(tmp_path) is None
