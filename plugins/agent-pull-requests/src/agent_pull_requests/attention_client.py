"""Finite negotiated attention peer over the existing authenticated caller."""

from __future__ import annotations

import copy
from dataclasses import asdict

from fleet_manager.gateway_service_channel import TransportError
from fleet_manager.gateway_service_caller import ServiceCaller

from .attention import Attention, Candidate, Cursor, PRKey
from .attention_read import PROTOCOL, MAX_BATCH, MAX_BATCH_BYTES, validate_after
from .watch_replay_binding import ReplayBinding
from .watch_replay_client import ReplayClient, ReplayClientError
from .watch_replay_store import json_object
from .workspace_operations import encoded


class AttentionClient:
    """No fallback, local persistence, retries, acquisition or feedback ingestion."""

    def __init__(
        self, caller: ServiceCaller, *, route_prefix: str, binding: ReplayBinding, key: PRKey,
    ) -> None:
        ReplayClient(caller, route_prefix=route_prefix, target=binding.target,
                     repo=binding.repo, binding=binding)
        if type(key) is not PRKey or key.namespace != binding.namespace:
            raise ValueError("expected scoped attention identity required")
        self.caller, self.binding, self.key = caller, binding, key
        self.path = route_prefix + "/v1/attention/" + binding.target
        self._capabilities: dict | None = None
        self._epoch = 0

    async def _exchange(self, operation: str, payload: dict) -> dict:
        try:
            response = await self.caller.request(
                "POST", self.path + "/" + operation,
                request_headers=(("Content-Type", "application/json"),),
                body=encoded({"binding": self.binding.document(), "request": payload}),
                response_limit=MAX_BATCH_BYTES,
            )
        except TransportError as error:
            raise ReplayClientError(error.code, http_status=error.status) from None
        if response.status != 200:
            raise ReplayClientError(
                "attention_unsupported" if response.status == 404 else "attention_unavailable",
                http_status=response.status,
            )
        try:
            types = [value for name, value in response.headers if name.lower() == "content-type"]
            if (
                len(response.body) > MAX_BATCH_BYTES or len(types) != 1
                or types[0].split(";")[0].strip().lower() != "application/json"
            ):
                raise ValueError
            value = json_object(response.body)
            if value.pop("binding", None) != self.binding.document() or value.get("protocol") != PROTOCOL:
                raise ValueError
            return value
        except (ValueError, UnicodeError, RecursionError):
            raise ReplayClientError("invalid_attention_response") from None

    async def capabilities(self) -> dict:
        self._epoch += 1
        epoch = self._epoch
        self._capabilities = None
        value = await self._exchange("capabilities", {})
        if (
            value.get("operations") != ["capabilities", "read"]
            or value.get("owner_store_versions") != [5]
            or value.get("max_batch") != MAX_BATCH or type(value.get("max_batch")) is not int
            or value.get("max_batch_bytes") != MAX_BATCH_BYTES
            or value.get("effects") != "none" or value.get("adopts") is not False
            or value.get("acknowledges") is not False
            or value.get("feedback") != "retained_original_capture"
            or any(type(value.get(name)) is not int or not 1 <= value[name] <= 86400
                   for name in ("max_age_seconds", "aging_seconds"))
        ):
            raise ReplayClientError("unsupported_attention_contract")
        if epoch != self._epoch:
            raise ReplayClientError("attention_discovery_superseded")
        self._capabilities = copy.deepcopy(value)
        return value

    async def read(self, *, limit: int, after: dict | None) -> dict:
        if self._capabilities is None:
            raise ReplayClientError("attention_capability_required")
        if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
            raise ValueError("invalid attention batch limit")
        validate_after(after)
        value = await self._exchange("read", {"limit": limit, "after": after})
        try:
            if (
                set(value) != {"protocol", "items", "snapshot", "next", "effects"}
                or value["effects"] != "none" or not isinstance(value["items"], list)
                or len(value["items"]) > limit
            ):
                raise ValueError
            from .review_intent import identifier
            identifier(value["snapshot"])
            if after is not None and value["snapshot"] != after["snapshot"]:
                raise ValueError
            seen = set()
            for raw in value["items"]:
                if not isinstance(raw, dict) or set(raw) != set(Attention.__dataclass_fields__):
                    raise ValueError
                item = Attention(**{
                    **raw, "key": PRKey(**raw["key"]), "candidate": Candidate(**raw["candidate"]),
                    "cursor": Cursor(**raw["cursor"]), "reasons": tuple(raw["reasons"]),
                })
                if (
                    any(asdict(item.key)[name] != expected for name, expected in asdict(self.key).items()
                        if name != "number")
                    or item.consumer != self.binding.consumer
                    or item.role not in ("maintainer", "codeowner", "subject-matter-expert")
                    or item.status not in ("actionable", "waiting", "excluded", "unavailable", "terminal")
                    or (item.key.number, item.role) in seen
                    or not isinstance(raw["reasons"], list)
                    or any(not isinstance(reason, str) or not 1 <= len(reason) <= 128 for reason in raw["reasons"])
                    or any(type(raw[name]) is not int or raw[name] < 0
                           for name in ("priority", "pending_since", "observed_at", "expires_at"))
                    or item.pending_since > item.observed_at
                    or item.expires_at != item.observed_at + self._capabilities["max_age_seconds"]
                ):
                    raise ValueError
                seen.add((item.key.number, item.role))
            next_page = value["next"]
            if next_page is not None and (
                not isinstance(next_page, dict) or set(next_page) != {"snapshot", "offset"}
                or next_page["snapshot"] != value["snapshot"]
                or type(next_page["offset"]) is not int
                or next_page["offset"] != (after["offset"] if after else 0) + len(value["items"])
                or not value["items"]
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ReplayClientError("invalid_attention_response") from None
        return value
