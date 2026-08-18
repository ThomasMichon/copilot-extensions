"""Fake `copilot --acp --stdio` for the elevated-launch repro.

A minimal but *real* ACP agent (built on the ``acp`` library the bridge already
depends on), so the agent-bridge frontend's ACP handshake -- initialize +
session/new + prompt -- completes exactly as it would against real copilot. No
network, credentials, or model calls. Stands in for the child a Session Host
owns, for both the singleton (base_repo) and worktree class launch shapes.
"""

from __future__ import annotations

import asyncio

import acp
from acp.schema import AgentCapabilities, InitializeResponse, NewSessionResponse


class Agent:
    async def initialize(self, protocol_version, **kw):
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, cwd, **kw):
        return NewSessionResponse(session_id="repro-sess")

    def __getattr__(self, name):
        if name.startswith("_") or name == "on_connect":
            raise AttributeError(name)

        async def _noop(*a, **k):
            return None

        return _noop


if __name__ == "__main__":
    asyncio.run(acp.run_agent(Agent()))
