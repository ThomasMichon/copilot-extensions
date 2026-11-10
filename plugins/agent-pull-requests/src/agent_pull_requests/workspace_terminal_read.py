"""Optional read-only serving of explicitly adopted terminal workspace state."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workspace_operations import WorkspaceOperations

PROTOCOL = "agent-pull-requests.workspace/v2"
SELECTION = "agent-pull-requests.terminal-workspace-read/v1"
OPERATIONS = ("capabilities", "load", "sync", "lookup", "chunk", "events")


def conversation_contract(version: int) -> dict:
    return {
        "protocol": "agent-pull-requests.terminal-conversation/v1",
        "owner_store_versions": [version], "ingest": "trusted_owner_only",
        "bodies": False, "acknowledges": False,
    }


def contract() -> dict:
    return {
        "protocol": SELECTION, "effects": "none",
        "retirement": "existing_lifecycle_only",
        "terminal_retention_version": 1, "terminal_recovery_version": 1,
        "terminal_cache_retirement_version": 1,
    }


def target_capture_contract() -> dict:
    return {
        "protocol": "agent-pull-requests.terminal-target-capture/v1",
        "phase": "before_acquisition", "budget": "request_checkpoint_and_expiry",
    }


def supports(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    versions = value.get("owner_store_versions")
    selected = value.get("terminal_read_contract")
    return (
        value.get("protocol") == PROTOCOL
        and type(versions) is list and len(versions) == 1
        and type(versions[0]) is int and versions[0] in (3, 4, 5, 6)
        and value.get("operations") == list(OPERATIONS)
        and isinstance(selected, dict) and selected == contract()
        and all(type(selected[name]) is int for name in (
            "terminal_retention_version", "terminal_recovery_version", "terminal_cache_retirement_version",
        ))
        and value.get("conversation_contract") == conversation_contract(versions[0])
        and value["conversation_contract"]["bodies"] is False
        and value["conversation_contract"]["acknowledges"] is False
    )


def adopted_version(workspace: WorkspaceOperations, *, expires_at: int,
                    checkpoint: Callable[[], None]) -> int:
    from .watch_replay_store import ReplayStoreInvalid

    def selected(document: dict) -> int:
        if any(document.get(name + "_version") != 1 for name in (
            "terminal_retention", "terminal_recovery", "terminal_cache_retirement",
        )):
            raise ReplayStoreInvalid("terminal workspace read requires explicit storage adoption")
        return document["version"]

    return workspace.store._review_transaction(
        selected, expires_at=expires_at, checkpoint=checkpoint, persist=False,
    )


def capabilities(workspace: WorkspaceOperations, *, expires_at: int,
                 checkpoint: Callable[[], None]) -> dict:
    from .native_read import NativeReadHandler
    from .workspace_operations import CHUNK_BYTES, MAX_GENERATION_BYTES

    version = adopted_version(workspace, expires_at=expires_at, checkpoint=checkpoint)
    from .workspace_terminal_mapping import FIELD, available, contract as mapping_contract
    mapping = available(workspace, expires_at=expires_at, checkpoint=checkpoint)
    return {
        "protocol": PROTOCOL, "operations": list(OPERATIONS),
        "generation_versions": [1], "owner_store_versions": [version],
        "terminal_read_contract": contract(),
        **({FIELD: mapping_contract()} if mapping else {}),
        "conversation_contract": conversation_contract(version),
        "conversation_acquisition": "workspace-projection/v1",
        "max_generation_bytes": MAX_GENERATION_BYTES, "chunk_bytes": CHUNK_BYTES,
        "refresh": "full_sections", "source": "existing_client_local_owner",
        **({"read_acquisition": workspace.read_owner.profile}
           if isinstance(workspace.read_owner, NativeReadHandler) else {}),
        "provision_or_adopt": False,
    }


def serve(workspace: WorkspaceOperations, operation: str, payload: dict, *,
          expires_at: int, checkpoint: Callable[[], None], selection: object = None,
          **options: object) -> dict:
    from .workspace_operations import WorkspaceUnavailable

    if workspace.terminal_reads:
        from .workspace_terminal_mapping import SELECTION as MAPPING_SELECTION, available
        mapping = operation == "retained-comment" and selection == MAPPING_SELECTION and available(
            workspace, expires_at=expires_at, checkpoint=checkpoint,
        )
        if not mapping and (
            operation not in OPERATIONS or operation != "capabilities" and selection != SELECTION
        ):
            raise WorkspaceUnavailable("terminal workspace read capability required")
        adopted_version(workspace, expires_at=expires_at, checkpoint=checkpoint)
    elif selection is not None:
        raise WorkspaceUnavailable("terminal workspace read receiver unavailable")
    elif workspace.store.terminal_retention:
        from .watch_replay_store import OWNER_SCHEMA, ReplayStoreInvalid
        owner = workspace.store
        owner.check_current(expires_at, checkpoint)
        # Atomic complete-envelope read only; do not introduce a contended
        # admission lock before concurrent baseline acquisition. Adoption still
        # requires the existing exclusive reader lifecycle.
        document = owner._read_document()
        if document.get("schema") != OWNER_SCHEMA:
            raise ReplayStoreInvalid("review operations require explicit owner adoption")
        owner._validate_owner(document)
        owner.check_current(expires_at, checkpoint)
        if "terminal_retention" in document:
            raise WorkspaceUnavailable("terminal workspace read receiver not selected")
    return workspace.execute(
        operation, payload, expires_at=expires_at, checkpoint=checkpoint, **options,
    )
