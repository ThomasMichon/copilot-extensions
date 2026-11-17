"""Actual source finalization retains the verified user deliverable."""

from pathlib import Path
import secrets

import pytest

from agent_worktrees import artifact_output, config as cfg, finalize, tracking
from agent_worktrees.artifact_output_policy import resolve_user_output
from agent_worktrees.artifact_root import admit_root
from agent_worktrees.tracking_claims import format_claim_ref
from test_resource_producer_finalize import _final_owner_seed as _final_owner_seed
from test_resource_producer_finalize import final_owner as final_owner


def test_verified_user_output_allows_actual_finalize_with_deliverable_retained(
    final_owner: tuple[Path, Path, cfg.Config], monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    _, path, config = final_owner
    initial = tracking.load_record(path)
    initial.creation_nonce = secrets.token_hex(16)
    tracking.save_record(initial, path)
    root = tmp_path_factory.mktemp("delivered-user-output") / "selected"
    with admit_root(root, forbidden=(), create=True):
        pass
    selection = resolve_user_output({}, topic="Report", owner_roots={"downloads": str(root)})
    owner_ref = format_claim_ref(config.machine, config.repo_name, "owner")
    delivered = artifact_output.produce_files(
        path, owner_ref, selection=selection, files={"report.txt": b"retained deliverable"},
        operation_id=secrets.token_urlsafe(24),
    )
    assert delivered["phase"] == "delivered"
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    assert finalize.validate_and_finalize("owner", config) is True
    current = tracking.load_record(path)
    claim = current.resources[0]
    assert isinstance(claim.producer, dict)
    assert current.status == "finalized" and claim.producer["phase"] == "delivered"
    assert (Path(selection["destination"]) / "report.txt").read_bytes() == b"retained deliverable"
