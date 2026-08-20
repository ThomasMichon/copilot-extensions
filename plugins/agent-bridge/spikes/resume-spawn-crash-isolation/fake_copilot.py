"""Fake ``copilot --acp --stdio`` for the resume-spawn crash-isolation repro.

A minimal but *real* ACP agent (built on the ``acp`` library the bridge already
depends on) so the frontend's ACP handshake -- initialize + session/new +
session/load (resume) -- completes exactly as it would against real copilot. No
network, credentials, model calls, or elevation. Stands in for the child a
``command`` (process-owned) session owns, across an initial start, a stop, and a
resume.
"""

from __future__ import annotations

import asyncio

import acp
from acp.schema import (
    AgentCapabilities,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
)


class Agent:
    async def initialize(self, protocol_version, **kw):
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, cwd, **kw):
        return NewSessionResponse(session_id="repro-sess")

    async def load_session(self, *a, **kw):
        # Resume path: reattach to the persisted session with no history replay.
        return LoadSessionResponse()

    def __getattr__(self, name):
        if name.startswith("_") or name == "on_connect":
            raise AttributeError(name)

        async def _noop(*a, **k):
            return None

        return _noop


if __name__ == "__main__":
    asyncio.run(acp.run_agent(Agent()))
