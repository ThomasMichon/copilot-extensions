"""Selected tracked consumers composed with the existing workspace owner."""

from __future__ import annotations

import asyncio
import copy

from .review_intent import intent
from .review_workspace import _scope
from .workspace_operations import PROTOCOL, supports_prepared_review
from .workspace_selection import supports_selection
from .workspace_tools import WorkspaceTools, WorkspaceToolUnavailable


class TrackedPublication:
    """Trusted in-process host input, not a loader, grant or tracking ledger.

    The host supplies the exact tracked alias -> native scope/account mapping
    from its authenticated binding, never from a caller's intent or role label.
    No normal CLI/host is enrolled by constructing this source adapter.
    """

    protocol = "agent-worktrees.selected-publication/v1"

    def __init__(
        self, host: WorkspaceTools, *, phase: str, worktree_id: str,
        target: dict, native_scope: dict, request: dict,
    ) -> None:
        if (
            not isinstance(host, WorkspaceTools) or phase not in ("preview", "submit", "recover")
            or not isinstance(worktree_id, str) or not worktree_id
            or type(target) is not dict
            or set(target) != {"provider", "api_base", "repo", "number"}
            or any(not isinstance(target[name], str) or not target[name]
                   for name in ("provider", "api_base", "repo"))
            or type(request) is not dict
        ):
            raise ValueError("actual tracked publication owner and mapping required")
        target = {**target, "api_base": target["api_base"].rstrip("/")}
        scope = _scope(native_scope)
        if (
            target["provider"] != scope["provider"] or target["api_base"] != scope["endpoint"]
            or type(target["number"]) is not int or target["number"] != scope["number"]
        ):
            raise ValueError("tracked mapping differs from the native scope")
        self.host, self.phase, self.worktree_id = host, phase, worktree_id
        self.target, self.scope = copy.deepcopy(target), scope
        self.request = copy.deepcopy(request)

    @staticmethod
    def _surface(selected: dict, command: str, body: str, event: str, *, recover: bool) -> None:
        actions = selected["actions"]
        kinds = [action["kind"] for action in actions]
        if command == "pr-comment":
            expected = ["comment"]
        elif command == "pr-review":
            if not recover and event not in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
                raise ValueError("unsupported tracked review verdict")
            expected = (["comment"] if body else []) + (
                ["verdict"] if event in ("APPROVED", "CHANGES_REQUESTED") else []
            )
            if recover:
                expected = kinds if kinds in (["comment"], ["verdict"], ["comment", "verdict"]) else []
        else:
            raise ValueError("unsupported tracked publication surface")
        if kinds != expected or not expected:
            raise ValueError("selected actions differ from the tracked native surface")
        if not recover:
            text = next((action["body"] for action in actions if action["kind"] == "comment"), "")
            vote = next((action["verdict"] for action in actions if action["kind"] == "verdict"), "")
            if text != body or vote != (event if event in ("APPROVED", "CHANGES_REQUESTED") else ""):
                raise ValueError("tracked input differs from the exact selected body or verdict")

    def _retained(self, response: dict, operation_id: str) -> dict:
        if (
            not isinstance(response, dict) or response.get("protocol") != PROTOCOL
            or response.get("scope") != self.scope or type(response.get("result")) is not dict
        ):
            raise ValueError("retained tracked workspace response differs")
        operation = response["result"]
        selected = intent(operation.get("intent"), versions=(2,))
        if (
            selected["scope"] != self.scope or selected["operation_id"] != operation_id
            or not isinstance(operation.get("selection"), dict)
            or operation["selection"].get("stage") not in ("prepared", "admitted")
        ):
            raise ValueError("retained tracked operation differs")
        return selected

    async def _invoke(self, *, command: str, body: str, event: str, request: dict) -> dict:
        host = self.host.for_one_shot()
        capabilities = await host.capabilities()
        backend = capabilities.get("backend_contract") or {}
        operation = {"preview": "allocate", "submit": "submit", "recover": "inspect"}[self.phase]
        if (
            not supports_prepared_review(backend.get("prepared_review_contract"))
            or operation not in capabilities["operations"]
            or self.phase == "submit" and "inspect" not in capabilities["operations"]
            or self.phase == "preview" and not supports_selection(backend.get("selection_contract"))
        ):
            raise WorkspaceToolUnavailable("tracked selected publication contract unavailable")
        if request.get("number") != self.scope["number"] or type(request.get("number")) is not int:
            raise ValueError("tracked request target differs")
        if self.phase != "recover":
            selected = intent(request.get("intent"), versions=(1,) if self.phase == "preview" else (2,))
            if selected["scope"] != self.scope:
                raise ValueError("tracked intent differs from the bound native scope")
            self._surface(selected, command, body, event, recover=False)
            if self.phase == "submit":
                retained = await host.invoke("inspect", {
                    "number": self.scope["number"], "operation_id": selected["operation_id"],
                })
                if self._retained(retained, selected["operation_id"]) != selected:
                    raise ValueError("submission differs from the original committed tracked preview")
        response = await host.invoke(operation, request)
        if (
            not isinstance(response, dict) or response.get("protocol") != PROTOCOL
            or response.get("scope") != self.scope or type(response.get("result")) is not dict
        ):
            raise ValueError("tracked workspace response differs from the selected owner")
        result = response["result"]
        if self.phase == "recover":
            selected = self._retained(response, request["operation_id"])
            self._surface(selected, command, body, event, recover=True)
        out = {
            "has_pr": True, "number": self.scope["number"], "repo": self.target["repo"],
            "publication": {"phase": self.phase, "response": response},
        }
        if self.phase == "submit" and result.get("status") != "complete":
            out["error"] = "selected publication incomplete; retain original action outcomes"
        return out

    def execute(
        self, *, worktree_id: str, target: dict | None,
        command: str, body: str, event: str,
    ) -> dict:
        if target is not None:
            target = {**target, "api_base": target["api_base"].rstrip("/")}
        if worktree_id != self.worktree_id or (
            self.phase != "recover" and target != self.target
        ) or (self.phase == "recover" and target is not None):
            raise ValueError("tracked target changed from the original publication binding")
        # A synchronous CLI boundary must not take over a resident's running loop.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise WorkspaceToolUnavailable("tracked publication requires its synchronous CLI boundary")
        return asyncio.run(self._invoke(
            command=command, body=body, event=event, request=copy.deepcopy(self.request),
        ))
