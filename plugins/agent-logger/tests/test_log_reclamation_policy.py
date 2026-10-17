"""Reserved reclamation settings cannot silently enable an incomplete feature."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_logger.config import Config
from agent_logger.sync.log_archives import LogArchiveSettings
from agent_logger.sync.log_reclamation_policy import (
    LogReclamationSettings, resolve_log_reclamation_settings,
)
from agent_logger.sync.log_reclamation_targets import resolve_required_log_targets

pytestmark = [pytest.mark.guard, pytest.mark.contract("agent_logger.process_logs.reclamation")]
ARCHIVES = LogArchiveSettings(True, True, 600)
POLICY = {"enabled": True, "min_age_seconds": 86400, "required_targets": ["local", "onedrive"]}


def _settings(raw: object) -> LogReclamationSettings:
    return resolve_log_reclamation_settings(
        raw, process_logs_enabled=True, archives=ARCHIVES, active_target="local",
    )


def _config(tmp_path: Path) -> Config:
    source = tmp_path / "source"
    (source / "logs").mkdir(parents=True)
    return Config({"sync": {
        "source": str(source), "targets": {
            "local": {"path": str(tmp_path / "first")},
            "onedrive": {"root": str(tmp_path / "second"), "subfolder": "sessions"},
        },
    }}, tmp_path / "home")


def test_reclamation_defaults_have_no_retention_or_target_inference(tmp_path: Path) -> None:
    assert _settings(None) == LogReclamationSettings()
    cfg = _config(tmp_path)
    assert cfg.process_log_reclamation == LogReclamationSettings()
    assert resolve_required_log_targets(cfg, cfg.process_log_reclamation, "example") == ()


@pytest.mark.parametrize("raw", [
    [], True, {"enabled": 1}, {"enabled": "true"}, {"unknown": 1},
    {"min_age_seconds": True}, {"min_age_seconds": 0}, {"min_age_seconds": -1},
    {"min_age_seconds": 1.5}, {"min_age_seconds": "86400"},
    {"required_targets": "local"}, {"required_targets": [None]},
    {"required_targets": [""]}, {"required_targets": [" local"]},
    {"required_targets": ["local", "local"]},
    {"enabled": True, "required_targets": ["local"]},
    {"enabled": True, "min_age_seconds": 86400},
    {"enabled": True, "min_age_seconds": 86400, "required_targets": ["onedrive"]},
])
def test_reclamation_malformed_policy_is_an_error(raw: object) -> None:
    with pytest.raises(ValueError):
        _settings(raw)


@pytest.mark.parametrize("logs,archives", [
    (False, ARCHIVES), (True, LogArchiveSettings()), (True, LogArchiveSettings(True, False)),
])
def test_reclamation_requires_all_preservation_opt_ins(
    logs: bool, archives: LogArchiveSettings,
) -> None:
    with pytest.raises(ValueError, match="local process logs and ZIP snapshots"):
        resolve_log_reclamation_settings(
            POLICY, process_logs_enabled=logs, archives=archives, active_target="local",
        )


def test_reclamation_enabled_runtime_is_refused_before_any_sync(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not available.*post-publication"):
        Config({"sync": {"process_logs": {
            "enabled": True, "zip_snapshots": {
                "enabled": True, "local_source": True,
            }, "reclamation": POLICY,
        }}}, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_reclamation_resolves_only_explicit_targets_once(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    settings = _settings(POLICY)
    resolved = resolve_required_log_targets(cfg, settings, "example")
    assert [target.target_name for target in resolved] == ["local", "onedrive"]
    assert [target.directory for target in resolved] == [
        tmp_path / "first" / "example" / "logs",
        tmp_path / "second" / "sessions" / "example" / "logs",
    ]
    cfg._data["sync"]["targets"]["local"]["path"] = str(tmp_path / "changed")
    cfg._data["sync"]["targets"]["onedrive"]["root"] = str(tmp_path / "changed")
    assert resolved[0].directory == tmp_path / "first" / "example" / "logs"
    assert resolved[1].directory == tmp_path / "second" / "sessions" / "example" / "logs"
    assert not (tmp_path / "first").exists()


@pytest.mark.parametrize("name", ["ssh", "ssh-tunnel", "ingest", "unknown"])
def test_reclamation_never_omits_unsupported_or_unresolved_targets(
    tmp_path: Path, name: str,
) -> None:
    cfg = _config(tmp_path)
    settings = _settings({**POLICY, "required_targets": ["local", name]})
    with pytest.raises(ValueError, match="support.*readback|unknown sync target"):
        resolve_required_log_targets(cfg, settings, "example")


@pytest.mark.parametrize("alias", ["source", "duplicate"])
def test_reclamation_rejects_source_and_duplicate_destinations(
    tmp_path: Path, alias: str,
) -> None:
    cfg = _config(tmp_path)
    first = cfg.target_options("local")["path"]
    if alias == "duplicate":
        cfg._data["sync"]["targets"]["onedrive"] = {"root": first, "subfolder": ""}
    else:
        # Overlapping source ancestry is rejected even before directories exist.
        cfg._data["sync"]["source"] = str(Path(first) / "example")
        (Path(first) / "example" / "logs").mkdir(parents=True)
    with pytest.raises(ValueError, match="same destination|aliases"):
        resolve_required_log_targets(cfg, _settings(POLICY), "example")


@pytest.mark.parametrize("machine", ["", ".", "..", "namespace/example", "x\\y", "x:y"])
def test_reclamation_namespace_is_not_a_local_machine(tmp_path: Path, machine: str) -> None:
    with pytest.raises(ValueError, match="local machine name"):
        resolve_required_log_targets(_config(tmp_path), _settings(POLICY), machine)
