"""Separate actual CLI processes cross the real write owner and observation control sockets."""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from _native_receipt_fixtures import environment, page, publish, setup
from native_shell_support import run_headless

from agent_worktrees import locks, status_monitor_cutover, tracking, tracking_write
from agent_worktrees import owner_observation_transport as wire

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]


def cli(home, argv, *, endpoint=None, timeout=20):
    prelude = (
        "import sys;from pathlib import Path;"
        "from agent_worktrees import config as cfg;"
        f"home=Path({str(home)!r});"
        "cfg.install_dir=lambda:home/'.agent-worktrees';"
        "cfg.project_dir=lambda name=None:home/('.'+(name or 'native-test'));"
    )
    if endpoint is not None:
        prelude += (
            "from agent_worktrees import owner_observation_cli as read_cli;"
            "from agent_worktrees.status_monitor_cutover import ControlClient;"
            f"read_cli._control_client=lambda:ControlClient({endpoint!r},token='native-fixture',timeout=1);"
        )
    prelude += (
        "import agent_worktrees.__main__ as main_cli;"
        "from agent_worktrees import status_monitor_runtime as monitor;"
        "def_forbidden=lambda:(_ for _ in ()).throw(AssertionError('unexpected bootstrap'));"
        "main_cli._ensure_status_monitor=def_forbidden;monitor._ensure_status_monitor=def_forbidden;"
        f"raise SystemExit(main_cli.main({argv!r}))"
    )
    return run_headless(
        [sys.executable, "-B", "-c", prelude],
        cwd=home,
        env=environment(home),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_actual_cli_write_save_owner_intake_index_and_actual_cli_history(tmp_path, monkeypatch):
    owner, path, home = setup(tmp_path, monkeypatch)
    writer = tracking_write.start_server(owner.native_compute)
    writer.start()
    native_delivery = threading.Event()

    def handle(action, payload):
        if action == "health":
            return {
                **owner.health(),
                "published": owner._admitting,
                "status": "ready" if owner._admitting else "draining",
            }
        if action == wire.READ_ACTION:
            return owner.observation_read(payload)
        if action == wire.HISTORY_ACTION:
            return owner.observation_history(payload)
        raise AssertionError(action)

    def stream(action, payload, disconnected):
        for value in owner.stream(action, payload, disconnected):
            if any(
                entry["evidence_kind"] == "persisted-mutation"
                for entry in value.get("entries", [])
            ):
                native_delivery.set()
            yield value

    control = status_monitor_cutover.ControlServer(
        handle,
        token="native-fixture",
        stream_handle=stream,
    )
    control.start()
    endpoint = f"http://127.0.0.1:{control.port}"
    try:
        locks.write_lock(
            home / ".agent-worktrees" / "status-monitor.lock",
            extra=tracking_write.rendezvous_fields(writer),
        )
        result = cli(
            home,
            [
                "--project",
                "native-test",
                "status",
                "--worktree-id",
                "native-one",
                "--summary",
                "actual separate CLI save",
                "--json",
            ],
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert tracking.load_record(path).summary == "actual separate CLI save"
        assert owner.native_observations.counts()["native-receipt-queued"] > 0
        publish(owner, path)
        current = cli(
            home,
            [
                "--project",
                "native-test",
                "worktree-observations",
                "--worktree",
                "native-one",
                "--session",
                "head",
                "--json",
            ],
            endpoint=endpoint,
        )
        assert current.returncode == 0, (current.stdout, current.stderr)
        snapshot = json.loads(current.stdout)
        history = cli(
            home,
            [
                "--project",
                "native-test",
                "worktree-observation-history",
                "--worktree",
                "native-one",
                "--session",
                "head",
                "--json",
                "--source-generation",
                snapshot["source_generation"],
            ],
            endpoint=endpoint,
        )
        assert history.returncode == 0, (history.stdout, history.stderr)
        entries = json.loads(history.stdout)["entries"]
        native = [entry for entry in entries if entry["evidence_kind"] == "persisted-mutation"]
        assert native[-1]["native_kind"] == "disposition-saved"
        assert native[-1]["payload"]["after"]["summary"] == "actual separate CLI save"
        assert native[-1]["actor"]["id"] is None
        assert len({entry["event_id"] for entry in page(owner)["entries"]}) == len(entries)
        with ThreadPoolExecutor(max_workers=1) as pool:
            followed = pool.submit(
                cli,
                home,
                [
                    "--project",
                    "native-test",
                    "worktree-observations",
                    "--worktree",
                    "native-one",
                    "--session",
                    "head",
                    "--json",
                    "--follow",
                ],
                endpoint=endpoint,
            )
            deadline = time.monotonic() + 8
            while owner.observations.counts()["streams"] == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert owner.observations.counts()["streams"] == 1
            second = cli(
                home,
                [
                    "--project",
                    "native-test",
                    "status",
                    "--worktree-id",
                    "native-one",
                    "--summary",
                    "live native update",
                    "--json",
                ],
            )
            assert second.returncode == 0, (second.stdout, second.stderr)
            publish(owner, path)
            assert native_delivery.wait(5)
            owner.close_admission("draining")
            streamed = followed.result(timeout=20)
            assert streamed.returncode == 0, (streamed.stdout, streamed.stderr)
            frames = [json.loads(line) for line in streamed.stdout.splitlines()]
            assert frames[0]["type"] == "snapshot"
            assert any(
                entry["evidence_kind"] == "persisted-mutation"
                for frame in frames
                for entry in frame.get("entries", [])
            )
            assert frames[-1]["type"] == "going-away"
    finally:
        owner.close_admission("closed")
        writer.close()
        control.close()
        owner.shutdown()
