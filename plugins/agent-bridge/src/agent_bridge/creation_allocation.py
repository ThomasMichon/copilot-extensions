"""Explicit original local allocation, without native or provider enrollment."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .acquisition_contract import AcquisitionRefused
from .acquisition_executor import executor
from .acquisition_launch_owner import require_local
from .creation_custody import CreationJournal, CreationRequest
from .frontend_process import frontend_producer, require_frontend_process
from .runtime_custody import RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer

if TYPE_CHECKING:
    from .db import Database
    from .session_manager import SessionManager
    from .transport import SpawnTarget


@dataclass(frozen=True)
class CreationFrontend:
    manager: SessionManager
    db: Database
    installation: RuntimeInstallation
    admission: RuntimeAdmission
    producer: RuntimeProducer
    database_identity: tuple[int, int]

    @classmethod
    def capture(
        cls, manager: SessionManager, installation: RuntimeInstallation, admission: RuntimeAdmission,
    ) -> CreationFrontend:
        db = manager.db
        producer = frontend_producer(db, installation, admission)
        stat = Path(installation.database).stat()
        owner = cls(manager, db, installation, admission, producer, (stat.st_dev, stat.st_ino))
        owner.require_current()
        return owner

    def require_current(self, *, effect: bool = True) -> None:
        if self.manager.db is not self.db or effect and self.manager.is_draining:
            raise RuntimeCustodyError("Original creation frontend changed or is draining")
        require_frontend_process(
            self.db, self.installation, self.admission, self.producer, self.database_identity,
            effect=effect,
        )

    async def allocate(
        self, target: SpawnTarget, *, key: str, prompt: str | None = None, **options: Any,
    ) -> dict:
        """Allocate once under the original executor; return an observation, not a session."""
        from .hosting_policy import require_target_hosting
        from .workspace_creation import allocate_workspace

        self.require_current(effect=False)
        require_local(target)
        if not target.project or target.worktree_id or target.explicit_cwd:
            raise RuntimeCustodyError("Original allocation requires a new local project workspace")
        if not isinstance(key, str) or not key.strip() or len(key) > 1024:
            raise AcquisitionRefused("creation_key_invalid", "creation key must be non-empty and at most 1024 characters")
        original_target = replace(target)
        digest = hashlib.sha256(json.dumps([target.project, key]).encode()).hexdigest()
        request = CreationRequest.capture("workspace", digest, {
            "target": json.loads(target.to_json()), "options": options, "prompt": prompt,
        })
        retained = json.loads(request.facts_json)
        owner_ref = retained["options"].get("caller_owner_ref")
        if owner_ref is not None and (type(owner_ref) is not str or not owner_ref.strip()):
            raise RuntimeCustodyError("Original allocation requires a nonblank owner reference")
        journal = CreationJournal(self.db)
        with executor(self.db, f"creation:{digest}"):
            row = journal.claim(request, self.installation, self.admission)
            if row["state"] == "allocated" and row["worktree_json"] is not None:
                return row
            if row["state"] != "allocating":
                raise AcquisitionRefused(
                    "creation_allocation_pending",
                    "original allocation outcome is unknown; observe the same attempt, never retry",
                )
            self.require_current()
            await require_target_hosting(original_target, operation="original keyed allocation")

            def before_spawn() -> None:
                self.require_current()
                if json.loads(original_target.to_json()) != retained["target"]:
                    raise RuntimeCustodyError("Original allocation target changed before owner spawn")
                journal.begin_allocation(request, self.installation, self.admission)
                self.require_current()

            worktree = await allocate_workspace(
                original_target, key, owner_ref, before_spawn=before_spawn,
            )
            return journal.report_allocation(request, worktree)
