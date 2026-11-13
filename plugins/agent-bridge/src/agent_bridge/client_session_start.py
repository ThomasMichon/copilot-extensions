"""BridgeClient session-start request construction and capability negotiation."""

from __future__ import annotations

from typing import Any

from .caller_session import gate_caller_session_id
from .client_worktree_restart import _Base


class SessionStartClientMixin(_Base):
    def inspect_retained_recovery(self, session_id: str, operation_id: str) -> dict[str, Any]:
        from urllib.parse import quote
        from .client_protocol_floor import protocol_floored_request
        from .retained_session_recovery import CAPABILITY
        from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

        send = protocol_floored_request(
            self, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities={CAPABILITY: 1},
        )
        return send(
            "GET", f"/api/v1/sessions/{quote(session_id, safe='')}/retained-recovery/"
            f"{quote(operation_id, safe='')}",
        ) or {}

    def resume_session(
        self, session_id: str, *, request_timeout: float | None = None,
        worktree_writer: dict[str, Any] | None = None,
        retained_recovery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .startup_observation import current

        send = self._request
        body = ()
        if worktree_writer is not None:
            from .acquisition_contract import WorktreeWriterRequirement
            from .client_protocol_floor import protocol_floored_request
            from .creation_worktree_writer import RESUME_CAPABILITY
            from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

            body = ({"worktree_writer": WorktreeWriterRequirement.model_validate(
                worktree_writer,
            ).model_dump()},)
            send = protocol_floored_request(
                self, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities={RESUME_CAPABILITY: 1},
            )
        if retained_recovery is not None:
            from .models import RetainedRecoveryRequest
            from .client_protocol_floor import protocol_floored_request
            from .retained_session_recovery import CAPABILITY
            from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

            if worktree_writer is None:
                raise ValueError("Retained recovery requires its original Worktrees writer")
            body = ({**body[0], "retained_recovery": RetainedRecoveryRequest.model_validate(
                retained_recovery,
            ).model_dump()},)
            send = protocol_floored_request(
                self, PLUGIN_READINESS_PROTOCOL_VERSION,
                capabilities={RESUME_CAPABILITY: 1, CAPABILITY: 1},
            )
        observation = current()
        if observation is not None and not observation.clock.ready:
            return observation.request(
                self, session_id,
                lambda: send(
                    "POST", f"/api/v1/sessions/{session_id}/resume", *body,
                    request_timeout=observation.clock.process_remaining(),
                    allow_retry=False,
                ) or {},
            )
        return send(
            "POST", f"/api/v1/sessions/{session_id}/resume", *body,
            request_timeout=request_timeout,
            **({"allow_retry": False} if retained_recovery is not None else {}),
        ) or {}

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
        worktree_writer: dict[str, Any] | None = None,
        dispatch_origin: dict[str, Any] | None = None,
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
        from .remote_acp_config import is_remote_address

        if dispatch_origin is not None:
            from .dispatch_origin import DispatchSessionOrigin

            if (
                not agent or not creation_key or not worktree_id or provider_instance
                or target_dir is not None or remote_session_id is not None or parity_fault is not None
                or is_remote_address(agent) or "://" in agent
            ):
                raise ValueError("dispatch origin requires keyed creation into a prepared local worktree")
            body["dispatch_origin"] = DispatchSessionOrigin.model_validate(dispatch_origin).model_dump(mode="json")
        if worktree_writer is not None:
            from .acquisition_contract import WorktreeWriterRequirement

            if (
                creation_key is None or not worktree_id or provider_instance
                or target_dir is not None or remote_session_id is not None or parity_fault is not None
                or is_remote_address(agent or "") or "://" in (agent or "")
            ):
                raise ValueError("worktree_writer requires keyed creation into a prepared local worktree")
            body["worktree_writer"] = WorktreeWriterRequirement.model_validate(worktree_writer).model_dump()
        if "://" in (agent or ""):
            from .protocol import REMOTE_ACP_TARGETS_PROTOCOL_VERSION

            if not self.daemon_supports(REMOTE_ACP_TARGETS_PROTOCOL_VERSION):
                raise BridgeClientError(426, "daemon does not support remote ACP endpoint targets")
        if is_remote_address(agent or "") or remote_session_id is not None:
            if caller_owner_ref is not None:
                from .relationship_authority import canonical_owner
                from .protocol import REMOTE_ACP_OWNERSHIP_PROTOCOL_VERSION

                canonical_owner(caller_owner_ref)
                if not self.daemon_supports(REMOTE_ACP_OWNERSHIP_PROTOCOL_VERSION):
                    raise BridgeClientError(426, "daemon does not support retained remote host ownership")
            from .protocol import REMOTE_ACP_PROTOCOL_VERSION
            if not self.daemon_supports(REMOTE_ACP_PROTOCOL_VERSION):
                raise BridgeClientError(426, "daemon does not support qualified remote ACP")
            if remote_session_id is not None:
                if not is_remote_address(agent or ""):
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
        send = self._request
        capabilities = {}
        if worktree_writer is not None:
            from .creation_worktree_writer import CAPABILITY

            capabilities[CAPABILITY] = 1
        if dispatch_origin is not None:
            from .dispatch_origin import CAPABILITY as ORIGIN_CAPABILITY

            capabilities[ORIGIN_CAPABILITY] = 1
        if capabilities:
            from .client_protocol_floor import protocol_floored_request
            from .protocol import PLUGIN_READINESS_PROTOCOL_VERSION

            send = protocol_floored_request(
                self, PLUGIN_READINESS_PROTOCOL_VERSION, capabilities=capabilities,
            )
        return send(
            "POST",
            "/api/v1/sessions/workspace-creations" if creation_key is not None else "/api/v1/sessions",
            body,
            request_timeout=request_timeout,
        ) or {}
