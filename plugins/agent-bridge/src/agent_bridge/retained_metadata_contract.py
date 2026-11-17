"""Versioned input for retained native metadata preparation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .acquisition_contract import WorktreeWriterRequirement
from .models import RetainedRecoveryRequest


class PrepareRetainedMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovery: RetainedRecoveryRequest
    worktree_writer: WorktreeWriterRequirement
