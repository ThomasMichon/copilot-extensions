"""A running participant cannot wait for queued work that its own slot blocks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

import update_driver
from update_driver.participant import ParticipantError


def test_running_participant_waiting_on_queued_work_refuses(tmp_path: Path) -> None:
    root = tmp_path / "state"
    runner = update_driver.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path))
    owner = update_driver.enqueue(update_driver.RequestSpec("owner"), runner=runner, root=root)
    queued = update_driver.enqueue(update_driver.RequestSpec("queued"), runner=runner, root=root)
    refused: list[str] = []

    def execute(transaction: Any) -> int:
        if transaction.request.id != owner.id:
            return 0
        custody = update_driver.borrowed_custody(root=root, environ=transaction.participant_env())
        assert custody is not None
        with custody, pytest.raises(ParticipantError) as error:
            update_driver.wait(queued.id, root=root, timeout=30)
        refused.append(str(error.value))
        return 0

    assert update_driver.drain(root=root, executor=execute) == 0
    assert update_driver.status(owner.id, root=root).exit_code == 0
    assert len(refused) == 1 and owner.id in refused[0]
