"""Opt-in prepared-loadout consumer of the existing authenticated service Gateway."""

from __future__ import annotations

import asyncio
from asyncio import sleep
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
import subprocess
import time
from time import monotonic

from .gateway_service_channel import CONTROL_RATE, REQUEST_RATE, TransportError
from .gateway_service_handler import CallerContext, HandlerBinding, ServiceRequest, ServiceResponse
from .loadout_preparation import (
    PLAN_CHUNK, InvalidPlanRange, PreparationStore, PreparedPlanChanged,
)
from .loadout_sources import GitSource
from .loadouts import design_loadout
from .observation_store import ObservationStore
from .observations import CurrentUpdate, ObservationCursor, OriginBinding
from .planning import (
    MAX_PLAN_BYTES, PlanError, canonical, configuration_capacity, decode, identifier, integer,
)

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewScope:
    account: str
    policy_revision: str
    can_confirm: bool

    def __post_init__(self) -> None:
        identifier(self.account, "review account")
        identifier(self.policy_revision, "review policy revision")
        if type(self.can_confirm) is not bool:
            raise PlanError("review confirmation permission must be explicit")


async def _settled(function):
    """Cancellation joins the finite, owner-contained source worker before returning."""
    task = asyncio.create_task(asyncio.to_thread(function))
    cancelled = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as error:
            cancelled = error
            if task.done():
                # A worker-side cancellation is not a reason to spin forever.
                result = task.result()
                break
    if cancelled is not None:
        raise cancelled
    return result


class PreparedLoadoutService:
    """One source selection, target snapshot owner and current proposal per installation.

    ``authorize`` maps channel-derived identity through current adopter policy;
    it must refuse withdrawn/missing scope, never infer it from request fields.
    ``inventory`` returns the existing admitted provider's fresh snapshot.
    Neither callback deploys. Connector credentials and resident lifetime remain
    with the existing Gateway embedding, not a new issuer, daemon or installer.
    """

    def __init__(
        self, *, fleet: str, gateway: str, slot: str, config: GitSource,
        catalog: GitSource | None, store: PreparationStore,
        observations: ObservationStore, origin: OriginBinding, project: str,
        authorize: Callable[[CallerContext], ReviewScope],
        inventory: Callable[[], Awaitable[dict]], acquisition_seconds: float = 15,
    ) -> None:
        for name in (fleet, gateway, slot, project):
            identifier(name, "prepared service scope")
        if (store.fleet != fleet or type(config) is not GitSource
                or (catalog is not None and type(catalog) is not GitSource)
                or type(origin) is not OriginBinding or origin.installation != store.installation
                or not callable(authorize) or not callable(inventory)
                or type(acquisition_seconds) not in (int, float)
                or not 0 < acquisition_seconds <= 30):
            raise PlanError("invalid prepared loadout service binding")
        self.fleet, self.gateway, self.slot, self.project = fleet, gateway, slot, project
        self.config, self.catalog, self.store = config, catalog, store
        self.observations, self.origin = observations, origin
        self.authorize, self.inventory, self.acquisition_seconds = authorize, inventory, acquisition_seconds
        self._busy = asyncio.Lock()
        self.repository_failure: subprocess.SubprocessError | None = None

    def binding(self) -> HandlerBinding:
        return HandlerBinding(self.fleet, self.gateway, self.handle)

    def _scope(self, request: ServiceRequest) -> ReviewScope:
        request.lifetime.check()
        context = request.context
        if ((context.fleet, context.gateway, context.slot) != (self.fleet, self.gateway, self.slot)
                or context.expires_at <= time.time()):
            raise PermissionError("prepared service caller scope refused")
        selected = self.authorize(context)
        if type(selected) is not ReviewScope:
            raise PermissionError("prepared service current policy unavailable")
        return selected

    def _checkpoint(self, request: ServiceRequest, scope: ReviewScope) -> None:
        if self._scope(request) != scope:
            raise PermissionError("prepared service caller policy changed")

    def _notify(self, current: dict) -> None:
        point = self.observations.read(
            origin=self.origin.origin, project=self.project, entity=self.store.installation,
        )
        if point["binding"] != self.origin:
            raise PlanError("prepared notification producer binding changed")
        previous = (point["row"]["update"] if point["row"]
                    and point["row"]["coverage"] == "point-observation" else None)
        if previous is not None and previous["source_revision"] > current["sequence"]:
            raise PlanError("prepared notification revision is ahead of its owner")
        candidate = current["candidate"]
        update = CurrentUpdate.from_dict({
            "schema": "fleet-manager.current-update", "version": 1,
            **asdict(self.origin), "project": self.project, "entity": self.store.installation,
            "sequence": (
                previous["sequence"] + (previous["source_revision"] != current["sequence"])
                if previous is not None else 1
            ),
            "source_revision": current["sequence"], "observed_at": current["observed_at"],
            "availability": "advisory", "reason": "prepared-review-not-deployment",
            "state": {
                "status": "review-confirmed" if current["decision"] else candidate["status"],
                "summary": json.dumps({
                    "generation": current["generation"], "digest": current["digest"],
                    "plan_digest": candidate["plan_digest"], "activation_authorized": False,
                }, sort_keys=True, separators=(",", ":")),
            },
        })
        if previous is not None and previous["source_revision"] == current["sequence"]:
            if previous != update.to_dict():
                raise PlanError("prepared notification conflicts with its durable proposal")
            return
        self.observations.apply(update)

    @staticmethod
    def _summary(current: dict | None) -> dict:
        if current is None:
            return {"schema": "fleet-manager.prepared-loadout", "version": 1, "status": "absent"}
        return {
            "schema": "fleet-manager.prepared-loadout", "version": 1,
            **{key: current[key] for key in (
                "generation", "sequence", "observed_at", "candidate", "digest", "decision",
            )},
            "activation_authorized": False, "live_readiness_verified": False,
            "source_revalidation_required": True,
            "inventory_fresh": time.time() < current["candidate"]["inventory_expires_at"],
        }

    def _notice(self, update: dict) -> dict | None:
        if update["entity"] != self.store.installation:
            return None
        try:
            value = json.loads(update["state"]["summary"])
            if (set(value) != {"generation", "digest", "plan_digest", "activation_authorized"}
                    or value["activation_authorized"] is not False):
                raise PlanError("invalid prepared notification")
            integer(value["generation"], "notification generation", minimum=1)
            identifier(value["digest"], "notification digest")
            if value["plan_digest"] is not None:
                identifier(value["plan_digest"], "notification plan digest")
            status = update["state"]["status"]
            if status not in ("prepared", "blocked", "review-confirmed"):
                raise PlanError("invalid prepared notification status")
        except (ValueError, TypeError, KeyError):
            raise PlanError("invalid prepared notification") from None
        return {"source_revision": update["source_revision"], "status": status, **value}

    def _notifications(self, target: str) -> dict:
        point = self.observations.read(
            origin=self.origin.origin, project=self.project, entity=self.store.installation,
        )
        if point["binding"] != self.origin:
            raise PlanError("prepared notification producer binding changed")
        if target == "/notifications":
            notice = (self._notice(point["row"]["update"]) if point["row"]
                      and point["row"]["coverage"] == "point-observation" else None)
            return {"cursor": asdict(point["cursor"]), "current": notice, "events": []}
        parts = target.split("/")
        if (len(parts) != 6 or parts[1] != "notifications" or len(parts[5]) > 19
                or not parts[5].isascii() or not parts[5].isdecimal()
                or (parts[3], parts[4]) != (self.origin.origin, self.project)):
            raise PlanError("invalid prepared notification cursor")
        cursor = ObservationCursor(parts[2], parts[3], parts[4], int(parts[5]))
        replay = self.observations.replay(cursor, limit=8)
        events = []
        for event in replay["events"]:
            if event["kind"] == "origin-bound":
                events.append({"position": event["position"], "status": "origin-resync-required"})
            else:
                notice = self._notice(event["update"])
                if notice is not None:
                    events.append({"position": event["position"], **notice})
        return {"cursor": asdict(replay["cursor"]), "events": events}

    async def _refresh(self, request: ServiceRequest, scope: ReviewScope) -> dict:
        previous = await _settled(self.store.read_metadata)
        deadline = time.monotonic() + self.acquisition_seconds
        candidate = {
            "schema": "fleet-manager.loadout-candidate", "version": 1,
            "fleet": self.fleet, "installation": self.store.installation,
            "status": "blocked", "reason": "published-source-unavailable",
            "sources": {}, "plan_digest": None, "plan_bytes": 0,
            "inventory_expires_at": 0, "execution_admission": "not-implemented",
        }
        plan = None
        try:
            config = await _settled(lambda: self.config.pull(deadline=deadline))
            candidate["sources"]["config"] = config.provenance()
            catalog = None
            if self.catalog is not None:
                catalog = await _settled(lambda: self.catalog.pull(deadline=deadline))
                candidate["sources"]["catalog"] = catalog.provenance()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PlanError("published source acquisition deadline exceeded")
            candidate["reason"] = "inventory-unavailable"
            inventory = decode(canonical(await asyncio.wait_for(self.inventory(), timeout=remaining)))
            candidate["reason"] = "published-loadout-invalid"
            configuration = config.document()
            configuration_capacity(configuration, fleet_id=self.fleet)
            plan = await _settled(lambda: design_loadout(
                configuration, inventory, catalog.document() if catalog else None,
                now=time.time(),
            ))
            encoded = canonical(plan, output=True)
            candidate.update(
                status="blocked" if plan["blockers"] else "prepared",
                reason="design-blocked" if plan["blockers"] else None,
                plan_digest=hashlib.sha256(encoded).hexdigest(), plan_bytes=len(encoded),
                inventory_expires_at=plan["inventory_expires_at"],
            )
        except (PlanError, OSError, asyncio.TimeoutError):
            LOG.warning("loadout-preparation-blocked code=%s", candidate["reason"])
            plan = None
        except subprocess.SubprocessError as error:
            # Preserve native process/Job custody carried by an unsettled cleanup.
            self.repository_failure = error
            LOG.error("loadout-preparation-refused code=repository-owner-required")
            raise
        self._checkpoint(request, scope)
        if time.monotonic() >= deadline:
            candidate.update(status="blocked", reason="preparation-deadline-exceeded",
                             plan_digest=None, plan_bytes=0, inventory_expires_at=0)
            plan = None
        current = await _settled(lambda: self.store.publish(
            candidate, plan, expected=previous["generation"] if previous else 0,
            now=int(time.time()),
        ))
        self._checkpoint(request, scope)
        await _settled(lambda: self._notify(current))
        return current

    async def handle(self, request: ServiceRequest) -> ServiceResponse:
        """Only source refresh, bounded inspection and an exact review decision."""
        try:
            scope = self._scope(request)
            if self.repository_failure is not None:
                return self._json(503, {"error": "repository-owner-required"})
            if self._busy.locked():
                return self._json(429, {"error": "preparation-busy"})
            async with self._busy:
                if ((request.target == "/notifications"
                     or request.target.startswith("/notifications/"))
                        and request.method == "GET" and not request.body):
                    current = await _settled(self.store.read_metadata)
                    if current:
                        await _settled(lambda: self._notify(current))
                    result = await _settled(lambda: self._notifications(request.target))
                    self._checkpoint(request, scope)
                    return self._json(200, result)
                if request.target == "/prepared" and request.method == "GET" and not request.body:
                    current = await _settled(self.store.read_metadata)
                    if current:
                        await _settled(lambda: self._notify(current))
                    self._checkpoint(request, scope)
                    return self._json(200, self._summary(current))
                if request.target == "/refresh" and request.method == "POST" and not request.body:
                    return self._json(200, self._summary(await self._refresh(request, scope)))
                if request.target == "/confirm" and request.method == "POST":
                    value = decode(request.body)
                    if (not isinstance(value, dict) or set(value) != {"generation", "digest"}
                            or not scope.can_confirm):
                        return self._json(403, {"error": "exact-review-decision-required"})
                    integer(value["generation"], "selected generation", minimum=1)
                    identifier(value["digest"], "selected digest")
                    await self._refresh(request, scope)
                    self._checkpoint(request, scope)
                    current = await _settled(lambda: self.store.confirm(
                        generation=value["generation"], selected_digest=value["digest"],
                        reviewer={"principal": request.context.principal,
                                  "account": scope.account, "policy_revision": scope.policy_revision},
                        now=int(time.time()),
                    ))
                    self._checkpoint(request, scope)
                    await _settled(lambda: self._notify(current))
                    return self._json(200, self._summary(current))
                if request.target.startswith("/plan/") and request.method == "GET" and not request.body:
                    parts = request.target.split("/")
                    if (len(parts) != 5 or not parts[2].isascii() or not parts[2].isdecimal()
                            or not parts[4].isascii() or not parts[4].isdecimal()
                            or len(parts[2]) > 19 or len(parts[4]) > 10):
                        return self._json(400, {"error": "invalid-plan-range"})
                    raw = await _settled(lambda: self.store.read_plan_range(
                        generation=int(parts[2]), selected_digest=parts[3], offset=int(parts[4]),
                    ))
                    self._checkpoint(request, scope)
                    return ServiceResponse(200, (("content-type", "application/octet-stream"),),
                                           raw)
                return self._json(404, {"error": "prepared-route-unavailable"})
        except PermissionError:
            return self._json(403, {"error": "prepared-scope-refused"})
        except InvalidPlanRange:
            return self._json(400, {"error": "invalid-plan-range"})
        except PreparedPlanChanged:
            return self._json(409, {"error": "prepared-generation-changed"})
        except PlanError:
            LOG.warning("loadout-preparation-refused code=prepared-state-conflict")
            return self._json(409, {"error": "prepared-state-conflict"})

    @staticmethod
    def _json(status: int, value: dict) -> ServiceResponse:
        return ServiceResponse(status, (("content-type", "application/json"),), canonical(value))


class PreparedLoadoutClient:
    """Thin finite consumer of ServiceCaller; no Git, config upload or manager effects."""

    def __init__(self, caller, *, prefix: str) -> None:
        from .gateway_service_channel import target

        path, query = target(prefix)
        if query or path != prefix or prefix.endswith("/"):
            raise PlanError("prepared client requires an explicit routed path prefix")
        self.caller, self.prefix = caller, prefix

    async def _request(self, method: str, suffix: str, body: bytes = b""):
        response = await self.caller.request(method, self.prefix + suffix, body=body)
        if response.status != 200:
            raise PlanError("Gateway prepared loadout request refused")
        return response.body

    async def inspect(self) -> dict:
        return decode(await self._request("GET", "/prepared"))

    async def refresh(self) -> dict:
        return decode(await self._request("POST", "/refresh"))

    async def notifications(self, cursor: dict | None = None) -> dict:
        suffix = "/notifications"
        if cursor is not None:
            if (not isinstance(cursor, dict) or set(cursor) != {"store", "origin", "project", "position"}):
                raise PlanError("an explicit scoped notification cursor is required")
            for key in ("store", "origin", "project"):
                identifier(cursor[key], f"notification {key}")
            integer(cursor["position"], "notification position")
            suffix += f"/{cursor['store']}/{cursor['origin']}/{cursor['project']}/{cursor['position']}"
        return decode(await self._request("GET", suffix))

    async def confirm(self, *, generation: int, selected_digest: str) -> dict:
        return decode(await self._request("POST", "/confirm", canonical({
            "generation": generation, "digest": selected_digest,
        })))

    async def plan(self, prepared: dict) -> dict:
        prepared = decode(canonical(prepared))
        if (not isinstance(prepared, dict) or not isinstance(prepared.get("candidate"), dict)
                or not {"generation", "digest"} <= prepared.keys()
                or not {"plan_bytes", "plan_digest"} <= prepared["candidate"].keys()):
            raise PlanError("an exact prepared plan selection is required")
        integer(prepared["generation"], "prepared generation", minimum=1)
        identifier(prepared["digest"], "prepared digest")
        candidate = prepared["candidate"]
        identifier(candidate["plan_digest"], "prepared plan digest")
        size = candidate["plan_bytes"]
        integer(size, "prepared plan bytes", minimum=1, maximum=MAX_PLAN_BYTES)
        raw = bytearray()
        interval = 2 / min(CONTROL_RATE, REQUEST_RATE)
        next_request, refusals = monotonic(), 0
        while len(raw) < size:
            delay = next_request - monotonic()
            if delay > 0:
                await sleep(delay)
            next_request = monotonic() + interval
            try:
                chunk = await self._request(
                    "GET", f"/plan/{prepared['generation']}/{prepared['digest']}/{len(raw)}",
                )
            except TransportError as error:
                if (error.code != "gateway-challenge-refused" or error.status != 429
                        or refusals >= 2):
                    raise
                refusals += 1
                next_request = monotonic() + interval * 2**refusals
                continue
            refusals = 0
            if len(chunk) != min(PLAN_CHUNK, size - len(raw)):
                raise PlanError("Gateway prepared plan range is invalid")
            raw.extend(chunk)
        if hashlib.sha256(raw).hexdigest() != candidate["plan_digest"]:
            raise PlanError("Gateway prepared plan content changed")
        return PreparationStore._json(bytes(raw), output=True)
