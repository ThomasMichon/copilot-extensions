"""Explicit single-Bridge-reader selection, retaining independent membership evidence."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import json
from typing import Any

from rich.text import Text
from textual.widgets import Select

from .companion_bridge_session_source import BridgeSessionSource
from .companion_bridge_source import BridgeMembershipSource
from .companion_timeline_attachment import PreparedTimelineSource, TimelineAttachment
from .companion_timeline_contract import Availability, Provider
from .companion_timeline_widget import SubwayTimeline


class BridgeTimelineAttachment(TimelineAttachment):
    def __init__(
        self, *args: Any, owner_ref: Callable[[], Any], project_root: Callable[[], str], **kwargs: Any,
    ) -> None:
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
        return BridgeSessionSource(*selection, **options, member=member, hosting_generation=generation)

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
        await self.set_selection(self.selection)

    def head_changed(self) -> bool:
        """Ignore paused historical streams when testing the active reader's head fence."""
        if self.controller is None:
            return False
        return any(
            state is not None and state.token == token and state.availability is Availability.HEAD_CHANGED
            for token in self.controller.selected_tokens
            for state in (self.panel.window.sources.get(token.identity.source_key),)
        )

    async def set_selection(
        self, selection: tuple[str, str, str] | None, *, reconcile: bool = False,
    ) -> None:
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
                    self.panel.source_error(state.token, "Bridge reader paused; retained evidence only.")
        await super().set_selection(selection, reconcile=reconcile)
