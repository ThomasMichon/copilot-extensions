"""Control-authorized housekeeping recovery routes."""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from .queue_records import TaskError


class CleanupRecoveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_generation: int
    current_host: str
    apply: bool = False
    expected_preview: str | None = None
    reason: str | None = None
    request_id: str | None = None


def register(app, queue, bus, control_auth):
    @app.get("/spawn-reservations/{key}/conclusion/recoveries", dependencies=[Depends(control_auth)])
    def history(key: str):
        try:
            return queue.cleanup_recovery_history(key)
        except TaskError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.post("/spawn-reservations/{key}/conclusion/recover", dependencies=[Depends(control_auth)])
    def recover(key: str, body: CleanupRecoveryBody):
        try:
            result = queue.recover_held_cleanup(key, **body.model_dump())
        except TaskError as exc:
            status = 404 if str(exc).startswith("no such reservation") else 409
            raise HTTPException(status, detail=str(exc)) from exc
        if body.apply:
            bus.publish({"type": "spawn.cleanup_recovery", "recovery": result})
        return result
