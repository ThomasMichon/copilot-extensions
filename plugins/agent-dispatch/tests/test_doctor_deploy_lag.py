from pathlib import Path
from typing import Any

from agent_dispatch import doctor_deploy_lag as lag


def test_running_older_than_installed_reports_restart_pending() -> None:
    block = lag.deploy_lag(running="0.21.9-dev1", installed="0.21.10-dev1", channel_reader=lambda: "0.21.10-dev1")
    assert block["restart_pending"]["status"] == "behind"
    assert block["update_pending"]["status"] == "current"
    assert len(block["findings"]) == 1


def test_equal_versions_yield_no_finding() -> None:
    block = lag.deploy_lag(running="0.21.66-dev1", installed="0.21.66-dev1", channel_reader=lambda: "0.21.66-dev1")
    assert block["restart_pending"]["status"] == "current"
    assert block["update_pending"]["status"] == "current"
    assert block["findings"] == []


def test_installed_older_than_channel_reports_update_pending() -> None:
    block = lag.deploy_lag(running="0.21.66-dev1", installed="0.21.66-dev1", channel_reader=lambda: "0.21.70-dev1")
    assert block["update_pending"]["status"] == "behind"
    assert block["findings"] == ["installed payload is older than the release channel; an update is pending"]


def test_channel_read_failure_is_unknown_not_current() -> None:
    def boom() -> str | None:
        raise OSError("offline")

    readers: list[lag.ChannelReader | None] = [boom, lambda: None, None]
    for reader in readers:
        block = lag.deploy_lag(running="1.0.0", installed="1.0.0", channel_reader=reader)
        assert block["update_pending"]["status"] == "unknown"
        assert block["findings"] == []


def test_gather_treats_unreachable_coordinator_as_unknown(tmp_path: Path) -> None:
    (tmp_path / "current-version").write_text("1.2.3\n", encoding="utf-8")

    class _Down:
        def health(self) -> dict[str, Any]:
            raise ConnectionError("refused")

    block = lag.gather(_Down(), install_root=tmp_path)
    assert block["versions"]["running"] is None
    assert block["versions"]["installed"] == "1.2.3"
    assert block["restart_pending"]["status"] == "unknown"
