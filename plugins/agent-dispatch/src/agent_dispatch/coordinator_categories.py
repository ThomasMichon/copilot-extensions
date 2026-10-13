"""Control-authorized, preview-first historical category classification."""

from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .coordinator_auth import _make_control_auth
from .queue_records import TaskError

CategorySelection = Literal["work", "maintenance", "all"]
TaskCategory = Literal["work", "maintenance"]


class ClassifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str
    source: str = Field(min_length=1)
    origin_ref: str = Field(min_length=1)
    labels: list[str] = Field(min_length=1)
    apply: bool = False
    preview_token: str | None = None
    actor: str = Field(default="operator", min_length=1)
    limit: int = Field(default=500, ge=1)


def register_category_routes(app, queue, *, control_token, emit) -> None:
    @app.post(
        "/task-categories/classify",
        dependencies=[
            Depends(
                _make_control_auth(
                    control_token,
                    emit,
                    operation="category_classify",
                    rejection_event="task.categories_classify_rejected",
                    code_prefix="category_control",
                    unavailable_message="category classification requires a configured control token",
                    forbidden_message="invalid or missing category control bearer",
                )
            ),
        ],
    )
    def classify(body: ClassifyBody) -> dict:
        try:
            result = queue.classify_maintenance(**body.model_dump())
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result["changed"]:
            emit("task.categories_classified", {"count": result["changed"]})
        return result
