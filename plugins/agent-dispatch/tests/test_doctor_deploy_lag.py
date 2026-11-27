from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_dispatch import doctor_deploy_lag as lag


def _lag(running: str | None, runtime: str | None, payload: str | None, *, local: bool = True,
         channel: str | None = None) -> dict[str, Any]:
    return lag.deploy_lag(running=running, runtime=runtime, payload=payload,
                          coordinator_owned=local, channel_reader=lambda: channel)


def test_running_older_than_runtime_reports_restart_pending() -> None:
    block = _lag("0.21.9-dev1", "0.21.10-dev1", "0.21.10-dev1", channel="0.21.10-dev1")
    assert block["restart_pending"]["status"] == "behind"
    assert block["install_pending"]["status"] == "current"
    assert block["update_pending"]["status"] == "current"
    assert len(block["findings"]) == 1


def test_payload_only_update_reports_install_pending_not_current() -> None:
    block = _lag("0.21.66-dev1", "0.21.66-dev1", "0.21.67-dev1")
    assert block["restart_pending"]["status"] == "current"
    assert block["install_pending"]["status"] == "behind"
    assert block["findings"] == ["selected runtime is older than the installed payload; a runtime install is pending"]


def test_prerelease_to_final_orders_correctly() -> None:
    assert _lag("0.1.2-dev5", "0.1.2", "0.1.2")["restart_pending"]["status"] == "behind"
    assert _lag("0.1.2", "0.1.2-dev5", "0.1.2")["restart_pending"]["status"] == "ahead"


def test_mixed_dev_spellings_are_equal() -> None:
    assert _lag("0.1.2.dev5", "0.1.2-dev5", "0.1.2dev5")["restart_pending"]["status"] == "current"


def test_differing_source_generations_are_unknown() -> None:
    block = _lag("0.1.2+src.aaaa", "0.1.2+src.bbbb", "0.1.2+src.bbbb")
    assert block["restart_pending"]["status"] == "unknown"
    assert block["findings"] == []


def test_remote_coordinator_skips_runtime_comparison() -> None:
    block = _lag("0.1.0", "0.2.0", "0.2.0", local=False)
    assert block["restart_pending"]["status"] == "unknown"
    assert block["findings"] == []


def test_channel_read_failure_is_unknown_not_current() -> None:
    def boom() -> str | None:
        raise OSError("offline")

    readers: list[lag.ChannelReader | None] = [boom, lambda: None, None]
    for reader in readers:
        block = lag.deploy_lag(running="1.0.0", runtime="1.0.0", payload="1.0.0",
                               coordinator_owned=True, channel_reader=reader)
        assert block["update_pending"]["status"] == "unknown"
        assert block["findings"] == []


def test_gather_reads_runtime_and_payload_separately(tmp_path: Path) -> None:
    (tmp_path / "current-version").write_text("1.2.3\n", encoding="utf-8")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text(json.dumps({"version": "1.2.4"}), encoding="utf-8")

    class _Up:
        def health(self) -> dict[str, Any]:
            return {"version": "1.2.3", "slot": {"pid": 1}}

    block = lag.gather(_Up(), install_root=tmp_path, payload_root=payload, is_owned=lambda slot, root: True)
    assert block["versions"] == {"running": "1.2.3", "runtime": "1.2.3", "payload": "1.2.4", "channel": None}
    assert block["install_pending"]["status"] == "behind"


def test_gather_treats_unreachable_coordinator_and_missing_payload_as_unknown(tmp_path: Path) -> None:
    (tmp_path / "current-version").write_text("1.2.3\n", encoding="utf-8")

    class _Down:
        def health(self) -> dict[str, Any]:
            raise ConnectionError("refused")

    block = lag.gather(_Down(), install_root=tmp_path, payload_root=tmp_path / "absent")
    assert block["coordinator_owned"] is False
    assert block["restart_pending"]["status"] == "unknown"
    assert block["install_pending"]["status"] == "unknown"


def test_unverifiable_slot_is_not_local() -> None:
    assert lag._is_local_process(None) is False
    assert lag._is_local_process({"pid": 1}) is False


def test_identical_invalid_versions_are_unknown_not_current() -> None:
    block = _lag("unknown", "unknown", "unknown")
    assert block["restart_pending"]["status"] == "unknown"
    assert block["install_pending"]["status"] == "unknown"


def test_payload_root_prefers_dispatch_shim_root(monkeypatch: Any, tmp_path: Path) -> None:
    payload = tmp_path / "shim-payload"
    payload.mkdir()
    (payload / "plugin.json").write_text(json.dumps({"version": "2.0.0"}), encoding="utf-8")
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_PAYLOAD_ROOT", str(payload))
    assert lag.read_payload_version(lag._default_payload_root()) == "2.0.0"
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(tmp_path / "other"))
    assert lag._default_payload_root() == payload


def test_local_coordinator_of_another_installation_is_not_owned(monkeypatch: Any, tmp_path: Path) -> None:
    from zdd import routing

    slot = {"pid": 4242, "process_start_time": "100"}
    monkeypatch.setattr(lag, "_is_local_process", lambda s: True)
    install_a, install_b = tmp_path / "install-a", tmp_path / "install-b"
    install_a.mkdir()
    install_b.mkdir()
    tables = {install_a: {"active": {"pid": 1111, "process_start_time": "50"}},
              install_b: {"active": {"pid": 4242, "process_start_time": "100"}}}
    monkeypatch.setattr(routing, "read_table", lambda root, **_: tables.get(Path(root)))
    assert lag._is_owned_coordinator(slot, install_b) is True
    assert lag._is_owned_coordinator(slot, install_a) is False
    assert lag._is_owned_coordinator({**slot, "process_start_time": "999"}, install_b) is False

    (install_a / "current-version").write_text("1.2.4\n", encoding="utf-8")

    class _B:
        def health(self) -> dict[str, Any]:
            return {"version": "1.2.3", "slot": slot}

    block = lag.gather(_B(), install_root=install_a, routing_root=install_a, payload_root=tmp_path / "none")
    assert block["coordinator_owned"] is False
    assert block["restart_pending"]["status"] == "unknown"
