"""Run the coordinator with uvicorn (the ``agent-dispatch serve`` command)."""

from __future__ import annotations

from pathlib import Path

from .config import Config, load_config
from .coordinator import create_app
from .queue import TaskQueue


def build_app(cfg: Config | None = None):
    """Construct the coordinator app, ensuring the queue DB directory exists."""
    cfg = cfg or load_config()
    Path(cfg.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    queue = TaskQueue(Path(cfg.db_path).expanduser())
    return create_app(queue, token=cfg.token, sweep_interval=cfg.sweep_interval)


def serve(cfg: Config | None = None) -> None:
    """Bind and serve the coordinator (blocking)."""
    import uvicorn

    cfg = cfg or load_config()
    uvicorn.run(build_app(cfg), host=cfg.host, port=cfg.port, log_level="info")
