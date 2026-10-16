"""Session-start and keyed workspace-creation request fields."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .preference_models import PreferenceRequestFields


class StartSessionRequest(PreferenceRequestFields):
    """Request to start a new agent session."""

    agent: str | None = None
    remote_session_id: str | None = Field(default=None, min_length=1, max_length=1024)
    target_dir: str | None = None
    topology: str | None = None
    worktree_id: str | None = None  # agent-worktrees worktree ID for session roll
    creation_key: str | None = Field(default=None, min_length=1, max_length=1024)
    creation_prompt: str | None = None
    provider_instance: bool = False  # explicit keyed provider-native creation, never a worktree

    @model_validator(mode="after")
    def require_provider_creation_key(self) -> StartSessionRequest:
        if self.provider_instance and self.creation_key is None:
            raise ValueError("provider_instance requires creation_key")
        return self

    caller_id: str | None = None  # caller identity for session affinity
    caller_session_id: str | None = None  # creating Copilot session's id; invalid -> dropped
    sender_repo: str | None = None  # caller's repo (agent-worktrees `get project`
    #                                 in the CLI cwd) -- bare-venue default source
    caller_owner_ref: str | None = None  # resource-obligation-settlement Ph3c: the
    #                                      caller's qualified ClaimRef (agent-worktrees
    #                                      `get owner-ref`), stamped as the bridge
    #                                      worktree's owner_ref so its finalize
    #                                      settles the caller's obligation
    force_new: bool = False  # skip caller_id reuse and always create a fresh session
    # Harness-only remote launch fault. The sessions route accepts the sole
    # supported value only for an exclusive ``venue-parity:`` caller using
    # ``force_new``; ordinary callers cannot alter the far-side ACP command.
    parity_fault: str | None = None
    # Per-session model / reasoning-effort override (``agent-bridge create
    # --model/--effort``). Copilot ignores ``--model`` under ``--acp``, so these
    # feed ``session/set_config_option`` at highest precedence over the daemon
    # default (``AcpClient._apply_model_config``). None / omitted keeps the default.
    model: str | None = None
    effort: str | None = None
    # Per-session MCP servers mounted into the ACP session at session/new, giving
    # this session a bespoke, run-bound toolset (e.g. an AI code-review
    # toolset). Each entry is an ACP MCP server spec; ``type`` selects the
    # transport and defaults to ``stdio``:
    #   {"type": "stdio", "name": ..., "command": ..., "args": [...], "env": {...}}
    #   {"type": "http" | "sse", "name": ..., "url": ..., "headers": {...}}
    # None / omitted preserves the historic empty-toolset behavior.
    mcp_servers: list[dict[str, Any]] | None = None
    # Extra ``copilot`` CLI args APPENDED to the resolved agent's copilot_args
    # for THIS session only (e.g. a per-run ``--additional-mcp-config @<file>``
    # to mount a run-bound MCP toolset the argv way -- copilot honors this over
    # --acp, unlike the ACP session/new ``mcp_servers`` path). The registered
    # agent's own args are preserved; these are added after them. None / omitted
    # changes nothing.
    copilot_args: list[str] | None = None
    # Per-session environment overrides merged into the spawned Copilot CLI's
    # process env (on top of the resolved agent's declared ``env``). The primary
    # use is BYOK provider selection -- pointing a session's brain at a local
    # inference front (``COPILOT_PROVIDER_BASE_URL`` / ``COPILOT_MODEL`` /
    # ``COPILOT_OFFLINE``) without hand-editing provider config -- but any env a
    # caller wants set for this session only is honored. Merged after the agent's
    # own env so a per-session value wins; None / omitted changes nothing.
    env: dict[str, str] | None = None
    # The caller's HTTP wire-contract protocol version (dotfiles #632). A caller
    # predating negotiation omits it -> the receiving daemon treats it as
    # unversioned. Lets a (cross-host) receiver know which capabilities the
    # sender speaks; the response advertises the receiver's version in turn.
    protocol_version: int | None = None
