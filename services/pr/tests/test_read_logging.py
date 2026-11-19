"""Nested foreground embeddings share their sink and restore caller logging."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from agent_pull_requests_service.read_logging import LOGGER, reviewer_reads
from agent_pull_requests_service import hosting
from agent_pull_requests_service.local_config import LocalHostConfig


def test_foreground_trace_sink_is_shared_and_restored_after_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = logging.getLogger(LOGGER)
    before = logger.level, logger.propagate, list(logger.handlers)
    with pytest.raises(RuntimeError, match="embedding stopped"):
        with reviewer_reads():
            with reviewer_reads():
                logger.info('{"outcome":"projected"}')
            logger.info('{"outcome":"failed"}')
            raise RuntimeError("embedding stopped")
    assert [json.loads(line)["outcome"] for line in capsys.readouterr().err.splitlines()] == [
        "projected", "failed",
    ]
    assert (logger.level, logger.propagate, logger.handlers) == before


def test_selected_embedding_captures_to_stderr_without_changing_root_logging(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = logging.getLogger()
    before = root.level, list(root.handlers)
    config = LocalHostConfig(tmp_path, "test", cast(Any, None), reviewer_actions=True)

    async def serve(selected: Any, **_kwargs: Any) -> None:
        assert selected is config
        logging.getLogger(LOGGER).info('{"outcome":"projected","action_count":0}')

    monkeypatch.setattr(hosting, "_run_service", serve)
    asyncio.run(hosting.run(config))
    assert json.loads(capsys.readouterr().err)["action_count"] == 0
    assert (root.level, root.handlers) == before
