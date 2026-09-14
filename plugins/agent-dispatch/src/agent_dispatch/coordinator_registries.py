"""Mechanical coordinator extraction for registry and lease routes.

This module exists only to keep ``coordinator.py`` under the module-size
baseline. The route bodies were moved verbatim from that file with no intended
behavior change.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .queue import TaskError, TaskQueue


class ScheduleLeaseBody(BaseModel):
    holder: str
    holder_session: str | None = None
    ttl: float | None = None


class ReleaseLeaseBody(BaseModel):
    holder: str
    force: bool = False


class AcquireResourceReservationBody(BaseModel):
    key: str
    owner: str
    ttl: float
    token: str | None = None


class BindResourceReservationBody(BaseModel):
    key: str
    owner: str
    token: str
    task_id: str


class ReleaseResourceReservationBody(BaseModel):
    key: str
    owner: str
    token: str


class RegistrationBody(BaseModel):
    kind: str
    spec: dict
    id: str | None = None
    machine: str | None = None
    env: str = "default"


class RegistrationStatusBody(BaseModel):
    status: str


def register_registry_routes(app: FastAPI, queue: TaskQueue) -> None:
    # -- schedule registry ---------------------------------------------------

    @app.post("/schedules")
    def register_schedule(entry: dict) -> dict:
        """Register (or upsert) a recurring schedule. 400 on a malformed entry."""
        try:
            return asdict(queue.register_schedule(entry))
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/schedules")
    def list_schedules(include_paused: bool = True) -> list[dict]:
        return [asdict(r) for r in queue.list_schedules(include_paused=include_paused)]

    @app.get("/schedules/{sid}")
    def get_schedule(sid: str) -> dict:
        rec = queue.get_schedule(sid)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such schedule")
        return asdict(rec)

    @app.delete("/schedules/{sid}")
    def remove_schedule(sid: str) -> dict:
        if not queue.remove_schedule(sid):
            raise HTTPException(status_code=404, detail="no such schedule")
        return {"removed": True, "id": sid}

    @app.post("/schedules/{sid}/pause")
    def pause_schedule(sid: str) -> dict:
        try:
            return asdict(queue.set_schedule_paused(sid, True))
        except TaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/schedules/{sid}/resume")
    def resume_schedule(sid: str) -> dict:
        try:
            return asdict(queue.set_schedule_paused(sid, False))
        except TaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -- supervisor registrations --------------------------------------------

    @app.post("/registrations")
    def register_registration(body: RegistrationBody) -> dict:
        """Register (or upsert) a supervision unit; return its handle. 400 on a
        malformed kind/spec."""
        try:
            return asdict(
                queue.register_registration(
                    body.kind,
                    body.spec,
                    reg_id=body.id,
                    machine=body.machine,
                    env=body.env,
                )
            )
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/registrations")
    def list_registrations(
        kind: str | None = None,
        machine: str | None = None,
        env: str | None = None,
        include_paused: bool = True,
    ) -> list[dict]:
        return [
            asdict(r)
            for r in queue.list_registrations(
                kind=kind, machine=machine, env=env, include_paused=include_paused
            )
        ]

    @app.get("/registrations/{rid}")
    def get_registration(rid: str) -> dict:
        rec = queue.get_registration(rid)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such registration")
        return asdict(rec)

    @app.delete("/registrations/{rid}")
    def remove_registration(rid: str) -> dict:
        if not queue.remove_registration(rid):
            raise HTTPException(status_code=404, detail="no such registration")
        return {"removed": True, "id": rid}

    @app.post("/registrations/{rid}/status")
    def set_registration_status(rid: str, body: RegistrationStatusBody) -> dict:
        try:
            return asdict(queue.set_registration_status(rid, body.status))
        except TaskError as exc:
            code = 404 if str(exc).startswith("no such registration") else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    # -- schedule job-leases -------------------------------------------------

    @app.post("/schedule-leases/{scope}/acquire")
    def acquire_lease(scope: str, body: ScheduleLeaseBody) -> dict:
        lease, granted = queue.acquire_schedule_lease(
            scope, body.holder, holder_session=body.holder_session, ttl=body.ttl
        )
        return {"granted": granted, "lease": asdict(lease)}

    @app.post("/schedule-leases/{scope}/release")
    def release_lease(scope: str, body: ReleaseLeaseBody) -> dict:
        try:
            released = queue.release_schedule_lease(scope, body.holder, force=body.force)
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"released": released, "scope": scope}

    @app.get("/schedule-leases")
    def list_leases() -> list[dict]:
        return [asdict(lease) for lease in queue.list_schedule_leases()]

    @app.get("/schedule-leases/{scope}")
    def get_lease(scope: str) -> dict | None:
        lease = queue.get_schedule_lease(scope)
        return asdict(lease) if lease else None

    # -- external producer resource reservations ----------------------------

    @app.post("/resource-reservations/acquire")
    def acquire_resource_reservation(
        body: AcquireResourceReservationBody,
    ) -> dict:
        try:
            reservation, granted = queue.acquire_resource_reservation(
                body.key, body.owner, ttl=body.ttl, token=body.token
            )
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = asdict(reservation)
        if not granted:
            payload.pop("token", None)
        return {"granted": granted, "reservation": payload}

    @app.post("/resource-reservations/bind")
    def bind_resource_reservation(body: BindResourceReservationBody) -> dict:
        try:
            return asdict(
                queue.bind_resource_reservation(
                    body.key, body.owner, body.token, body.task_id
                )
            )
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/resource-reservations/release")
    def release_resource_reservation(
        body: ReleaseResourceReservationBody,
    ) -> dict:
        try:
            released = queue.release_resource_reservation(
                body.key, body.owner, body.token
            )
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"released": released, "key": body.key}

    @app.get("/resource-reservations")
    def list_resource_reservations(
        owner_prefix: str | None = None,
        task_id: str | None = None,
    ) -> list[dict]:
        return [
            asdict(reservation)
            for reservation in queue.list_resource_reservations(
                owner_prefix=owner_prefix, task_id=task_id
            )
        ]
