"""BridgeClient session-start request construction and capability negotiation."""

from __future__ import annotations

from typing import Any

from .caller_session import gate_caller_session_id
from .client_worktree_restart import _Base


class SessionStartClientMixin(_Base):
    def start_session(
        self,
        *,
        agent: str | None = None,
        charter: str | None = None,
        target_dir: str | None = None,
        caller_id: str | None = None,
        caller_session_id: str | None = None,
        sender_repo: str | None = None,
        caller_owner_ref: str | None = None,
        force_new: bool = False,
        parity_fault: str | None = None,
        worktree_id: str | None = None,
        env: dict[str, str] | None = None,
        model: str | None = None,
        effort: str | None = None,
        preference_source: str | None = None,
        context: str | None = None,
        copilot_args: list[str] | None = None,
        request_timeout: float | None = None,
        creation_key: str | None = None,
        creation_prompt: str | None = None,
        provider_instance: bool = False,
        remote_session_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions

        ``worktree_id`` targets an *existing* worktree (a session roll). When
        set, the server enforces the session-lifecycle head guard: a create
        into a worktree whose ground-layer head is active or whose numbered
        handoff is pending is refused (409 ``worktree_head_active`` /
        ``worktree_head_pending``) with no break-glass of its own (Phase 3)
        -- ``resume_worktree(reclaim=True)`` resumes-or-creates it instead.

        ``charter`` binds a ``.github/agents/<charter>.agent.md`` overlay via
        ``copilot_args`` (``--agent <charter>``). ``env`` adds per-session env
        overrides for the spawned Copilot CLI (e.g. BYOK provider selection).
        ``caller_session_id`` is sent only to a daemon that records it.
        """
        from .client import BridgeClientError

        body: dict[str, Any] = {}
        if (agent or "").startswith("remote-acp:") or remote_session_id is not None:
            from .protocol import REMOTE_ACP_PROTOCOL_VERSION
            if not self.daemon_supports(REMOTE_ACP_PROTOCOL_VERSION):
                raise BridgeClientError(426, "daemon does not support qualified remote ACP")
            if remote_session_id is not None:
                if not (agent or "").startswith("remote-acp:"):
                    raise ValueError("remote_session_id requires remote-acp target")
                body["remote_session_id"] = remote_session_id
        if provider_instance:
            from .protocol import PROVIDER_CREATION_PROTOCOL_VERSION

            if creation_key is None:
                raise ValueError("provider_instance requires creation_key")
            if not self.daemon_supports(PROVIDER_CREATION_PROTOCOL_VERSION):
                raise BridgeClientError(426, "daemon does not support replay-safe provider creation")
            body["provider_instance"] = True
        if creation_key is not None:
            from .protocol import WORKSPACE_CREATION_PROTOCOL_VERSION

            if not self.daemon_supports(WORKSPACE_CREATION_PROTOCOL_VERSION):
                raise BridgeClientError(426, "daemon does not support replay-safe workspace creation")
            body["creation_key"] = creation_key
            if creation_prompt is not None:
                body["creation_prompt"] = creation_prompt
        elif creation_prompt is not None:
            raise ValueError("creation_prompt requires creation_key")
        from .protocol import TARGET_PREFERENCES_PROTOCOL_VERSION
        from .session_preferences import SOURCE_ENV, CONTEXT_ENV, SOURCES

        selected_source = preference_source if preference_source is not None else (env or {}).get(SOURCE_ENV)
        if selected_source is not None and selected_source not in SOURCES:
            raise ValueError("unsupported preference_source")
        if selected_source is not None or context is not None or (env or {}).get(CONTEXT_ENV):
            if not self.daemon_supports(TARGET_PREFERENCES_PROTOCOL_VERSION):
                raise BridgeClientError(426, "The daemon does not support execution preference policy")
        if preference_source is not None:
            body["preference_source"] = preference_source
        if context is not None:
            body["context"] = context
        if agent:
            body["agent"] = agent
        if args := (["--agent", charter, *(copilot_args or [])] if charter else copilot_args):
            body["copilot_args"] = args
        for key, value in (
            ("target_dir", target_dir), ("caller_id", caller_id), ("sender_repo", sender_repo),
            ("caller_session_id", gate_caller_session_id(self, caller_session_id)),
            ("caller_owner_ref", caller_owner_ref),
        ):
            if value:
                body[key] = value
        if force_new:
            body["force_new"] = True
        if parity_fault:
            from .protocol import FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION

            if not self.daemon_supports(
                FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION
            ):
                raise BridgeClientError(
                    426,
                    "The active agent-bridge daemon does not support failed "
                    "ACP handshake parity injection. Update the agent-bridge "
                    "runtime before running this fault scenario.",
                )
            body["parity_fault"] = parity_fault
        if worktree_id:
            body["worktree_id"] = worktree_id
        if env:
            body["env"] = env
        if model:
            body["model"] = model
        if effort:
            body["effort"] = effort
        # Always declare this client's HTTP contract version so a (cross-host)
        # receiver can gate capability across version skew (dotfiles #632).
        from .protocol import HTTP_PROTOCOL_VERSION

        body["protocol_version"] = HTTP_PROTOCOL_VERSION
        return self._request(
            "POST",
            "/api/v1/sessions/workspace-creations" if creation_key is not None else "/api/v1/sessions",
            body,
            request_timeout=request_timeout,
        ) or {}
