"""A caller-owned Bridge frontend with isolated state and real session hosts."""

import json
import logging
import os
import socket
from pathlib import Path

import uvicorn

from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )
    logging.getLogger("agent-bridge").setLevel(logging.DEBUG)
    root = Path(os.environ["AGENT_BRIDGE_CONFIG_DIR"])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config = ServiceConfig(
            port=port, db_path=str(root / "sessions.db"),
            enable_credential_relay=False, idle_reap_ttl_seconds=0,
        )
        app = create_app(config=config, token=os.environ["AGENT_BRIDGE_TOKEN"])
        (root / "listener.json").write_text(json.dumps({"port": port}), encoding="utf-8")
        uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[listener])
