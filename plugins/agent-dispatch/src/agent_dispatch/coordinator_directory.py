"""Coordinator fleet-awareness and satellite-directory routes."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .satellites import ROLE_SATELLITE, FleetDirectory, UnknownInstance


class SatelliteRegisterBody(BaseModel):
    machine: str
    worktrees: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    gate_state: str = "open"
    agent_versions: dict[str, str] = Field(default_factory=dict)
    status: dict = Field(default_factory=dict)


class SatelliteHeartbeatBody(BaseModel):
    status: dict | None = None
    worktrees: list[str] | None = None
    gate_state: str | None = None


class DirectoryRegisterBody(BaseModel):
    instance: str
    role: str = "peer"
    epoch: int = 0
    machine: str | None = None
    worktrees: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    gate_state: str = "open"
    agent_versions: dict[str, str] = Field(default_factory=dict)
    status: dict = Field(default_factory=dict)


class DirectoryHeartbeatBody(BaseModel):
    status: dict | None = None
    worktrees: list[str] | None = None
    gate_state: str | None = None
    role: str | None = None
    epoch: int | None = None


def register_directory_routes(app: FastAPI, directory: FleetDirectory) -> None:
    @app.post("/directory/register")
    def directory_register(body: DirectoryRegisterBody) -> dict:
        return directory.register(
            body.instance,
            role=body.role,
            epoch=body.epoch,
            machine=body.machine,
            worktrees=body.worktrees,
            capabilities=body.capabilities,
            gate_state=body.gate_state,
            agent_versions=body.agent_versions,
            status=body.status,
        )

    @app.post("/directory/{instance}/heartbeat")
    def directory_heartbeat(instance: str, body: DirectoryHeartbeatBody) -> dict:
        try:
            return directory.heartbeat(
                instance,
                status=body.status,
                worktrees=body.worktrees,
                gate_state=body.gate_state,
                role=body.role,
                epoch=body.epoch,
            )
        except UnknownInstance as exc:
            raise HTTPException(
                status_code=404, detail="unknown instance"
            ) from exc

    @app.delete("/directory/{instance}")
    def directory_deregister(instance: str) -> dict:
        return {"deregistered": directory.deregister(instance)}

    @app.get("/directory")
    def directory_list(role: str | None = None) -> list[dict]:
        return directory.discover_peers(role=role)

    @app.get("/directory/coordinator")
    def directory_coordinator() -> dict | None:
        return directory.discover_coordinator()

    @app.post("/satellites/register")
    def satellite_register(body: SatelliteRegisterBody) -> dict:
        return directory.register(
            body.machine,
            role=ROLE_SATELLITE,
            worktrees=body.worktrees,
            capabilities=body.capabilities,
            gate_state=body.gate_state,
            agent_versions=body.agent_versions,
            status=body.status,
        )

    @app.post("/satellites/{machine}/heartbeat")
    def satellite_heartbeat(machine: str, body: SatelliteHeartbeatBody) -> dict:
        try:
            return directory.heartbeat(
                machine,
                status=body.status,
                worktrees=body.worktrees,
                gate_state=body.gate_state,
            )
        except UnknownInstance as exc:
            raise HTTPException(status_code=404, detail="unknown satellite") from exc

    @app.delete("/satellites/{machine}")
    def satellite_deregister(machine: str) -> dict:
        return {"deregistered": directory.deregister(machine)}

    @app.get("/satellites")
    def satellite_list() -> list[dict]:
        return directory.discover_peers(role=ROLE_SATELLITE)
