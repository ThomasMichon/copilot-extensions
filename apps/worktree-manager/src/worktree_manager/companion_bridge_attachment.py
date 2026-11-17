"""Explicit single-Bridge-reader selection, retaining independent membership evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
import json
from threading import Lock
from typing import Any, TypedDict

from rich.text import Text
from textual.widgets import Select

from .companion_bridge_session_source import BridgeSessionSource
from .companion_bridge_current_requests import CurrentRequestState
from .companion_bridge_tool_statistics import ToolStatisticsState
from .companion_bridge_source import BridgeMembershipSource
from .companion_timeline_attachment import PreparedTimelineSource, TimelineAttachment
from .companion_timeline_contract import Availability, Provider, SourceToken
from .companion_timeline_widget import SubwayTimeline


class _PendingState(TypedDict, total=False):
    current_requests: CurrentRequestState | None
    tool_statistics: ToolStatisticsState | None


class BridgeTimelineAttachment(TimelineAttachment):
    def __init__(
        self, *args: Any, owner_ref: Callable[[], Any], project_root: Callable[[], str],
        current_status: Callable[[CurrentRequestState | None], None] | None = None, **kwargs: Any,
    ) -> None:
        self._current_status = current_status
        self.current_requests: CurrentRequestState | None = None
        self.tool_statistics: ToolStatisticsState | None = None
        self._current_revision = 0
        super().__init__(*args, provider=Provider.BRIDGE, source_factory=self._source, **kwargs)
        self._owner_ref, self._project_root = owner_ref, project_root
        self.selection: tuple[str, str, str] | None = None
        self.choice = "membership"
        self._members: dict[str, tuple[dict[str, Any], str]] = {}
        self._chosen: tuple[dict[str, Any], str] | None = None
        self._options: tuple[tuple[str, str], ...] = ()

    def _source(self, selection: tuple[str, str, str]) -> PreparedTimelineSource:
        options = {
            "owner_ref": self._owner_ref(), "cwd": self.cwd, "project_root": self._project_root(),
        }
        if self._chosen is None:
            return BridgeMembershipSource(*selection, **options)
        member, generation = self._chosen
        loop = asyncio.get_running_loop()
        revision, current_revision = self._revision, self._current_revision
        pending: _PendingState = {}
        scheduled = False
        lock = Lock()

        def accept() -> None:
            nonlocal pending, scheduled
            with lock:
                states, pending, scheduled = pending, {}, False
            if (
                revision == self._revision and current_revision == self._current_revision
                and not self._stop.is_set() and self.selection == selection and self._renderable()
                and (self._head_current is None or self._head_current(selection))
            ):
                if "current_requests" in states:
                    self.current_requests = states["current_requests"]
                if "tool_statistics" in states:
                    self.tool_statistics = states["tool_statistics"]
                if self._current_status is not None:
                    self._current_status(self.current_requests)

        def publish(state: _PendingState) -> None:
            nonlocal scheduled
            with lock:
                pending.update(state)
                if scheduled:
                    return
                scheduled = True
            loop.call_soon_threadsafe(accept)

        return BridgeSessionSource(
            *selection, **options, member=member, hosting_generation=generation,
            current=lambda state: publish({"current_requests": state}),
            statistics=lambda state: publish({"tool_statistics": state}),
        )

    def _invalidate_current(self) -> None:
        self._current_revision += 1
        self.current_requests = None
        self.tool_statistics = None
        if self._current_status is not None:
            self._current_status(None)

    def stop_now(self) -> None:
        self._invalidate_current()
        super().stop_now()

    def _error(self, token: SourceToken | None, message: str) -> None:
        self._invalidate_current()
        super()._error(token, message)

    def update_choices(self, control: Select[str]) -> None:
        """Only complete delivered membership states can offer exact local session bindings."""
        if self.choice == "membership":
            members: dict[str, tuple[dict[str, Any], str]] = {}
            for state in self.panel.window.sources.values():
                identity = state.token.identity
                if (
                    identity.provider is not Provider.BRIDGE
                    or not identity.source_key.startswith("bridge-membership:")
                    or state.availability is not Availability.OBSERVED or state.snapshot is None
                    or state.snapshot.facts_json is None
                ):
                    continue
                facts = json.loads(state.snapshot.facts_json)
                if "membership-replay-incomplete" in facts.get("limitations", []):
                    continue
                for member in facts.get("members", []):
                    if (
                        member["registry"] in {"owned", "represented"} and member["worktree_id"]
                        and member["retirement"] is None
                    ):
                        # The wire hosting generation is retained in the membership
                        # source adapter, not inferred from its normalized digest.
                        source = self.controller._sources.get(Provider.BRIDGE) if self.controller else None
                        if isinstance(source, BridgeMembershipSource) and source.codec.source is not None:
                            members[member["member_id"]] = (deepcopy(member), source.codec.source["hosting_generation"])
            self._members = members
        options = (
            ("Bridge feed: membership (discover delegates)", "membership"),
            *((f"Session: {member['bridge_session_id']} ({member['registry']}; reported link)", key)
              for key, (member, _generation) in sorted(self._members.items())),
        )
        if options != self._options:
            self._options = options
            with control.prevent(Select.Changed):
                control.set_options((Text(label), value) for label, value in options)
                control.value = self.choice

    async def choose(self, value: str) -> None:
        if (
            value == self.choice or self.selection is None or not self._renderable()
            or not self.panel.query_one(SubwayTimeline).demand_visible
        ):
            return
        if value != "membership" and value not in self._members:
            if self._status is not None:
                self._status("selected member is no longer available; membership reader unchanged")
            return
        self.choice = value
        self._chosen = deepcopy(self._members[value]) if value != "membership" else None
        await self.set_selection(self.selection, reconcile=True, reset_membership=False)

    def head_changed(self) -> bool:
        """Stop pending admission on head loss; ignore paused historical stream markers."""
        if (
            self.selection is not None and self._head_current is not None
            and not self._head_current(self.selection)
        ):
            if not self._stop.is_set():
                self.stop_now()
            return True
        if self.controller is None:
            return False
        return any(
            state is not None and state.token == token and state.availability is Availability.HEAD_CHANGED
            for token in self.controller.selected_tokens
            for state in (self.panel.window.sources.get(token.identity.source_key),)
        )

    async def set_selection(
        self, selection: tuple[str, str, str] | None, *, reconcile: bool = False,
        reset_membership: bool = True,
    ) -> None:
        if reconcile and reset_membership:
            self.choice, self._chosen, self._members = "membership", None, {}
        if selection is not None:
            if self.selection is not None and selection != self.selection:
                self.choice, self._chosen, self._members = "membership", None, {}
            self.selection = selection
        # Retain events and integrity proofs, but never present an inactive
        # stream's old membership/requests as fresh current state.
        self.stop_now()
        if self._renderable():
            for state in tuple(self.panel.window.sources.values()):
                if state.token.identity.provider is Provider.BRIDGE:
                    self.panel.pause_source(state.token)
        await super().set_selection(selection, reconcile=reconcile)
