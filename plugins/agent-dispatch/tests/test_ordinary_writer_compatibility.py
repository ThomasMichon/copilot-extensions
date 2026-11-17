"""Compatible executors preserve standing admission's original native owner."""

import json
import subprocess
from collections.abc import Callable
from typing import Any, cast

import pytest

from agent_dispatch.ordinary_writer_admission import OrdinaryWriterAdmission
from agent_dispatch.queue_records import TaskError
from tests.test_local_standing_admission import (
    admission as admission,
    local as local,
    materialization as materialization,
)


@pytest.mark.parametrize("defect", [
    None, "foreign-executor", "foreign-birth", "nonce", "malformed-executor",
    "contract", "instance", "legacy", "null-executor",
])
@pytest.mark.parametrize("expected", ["birth", "current"])
def test_standing_admission_separates_birth_from_executor(
    local: Any, defect: str | None, expected: str,
) -> None:
    # Exercise the supported observer using the real standing fixture's transport.
    from agent_dispatch import assignment_relationship_owner as owner

    original_capture = cast(
        Callable[..., subprocess.CompletedProcess[str]], getattr(owner, "run_background_capture"),
    )

    def observe(arguments: list[str], *, read_only: bool) -> subprocess.CompletedProcess[str]:
        assert read_only
        response = original_capture(["selected-bridge", *arguments])
        value = json.loads(response.stdout)
        observation = value.get("worktree_writer_observation")
        if observation is not None:
            observation["executing_writer_identity"] = {
                **observation["worktree_writer"]["identity"], "runtime_generation": "b" * 32,
            }
            if defect == "foreign-executor":
                observation["executing_writer_identity"]["installation_root"] += "-foreign"
            elif defect == "foreign-birth":
                observation["worktree_writer"]["identity"]["installation_root"] += "-foreign"
            elif defect == "nonce":
                observation["worktree_writer"]["creation_nonce"] += "-changed"
            elif defect == "malformed-executor":
                observation["executing_writer_identity"]["runtime_generation"] = True
            elif defect == "contract":
                observation["writer_contract"]["retained_relationships"] = 0
            elif defect == "instance":
                observation["bridge_instance"]["generation"] += 1
            elif defect == "legacy":
                del observation["executing_writer_identity"]
            elif defect == "null-executor":
                observation["executing_writer_identity"] = None
        return subprocess.CompletedProcess(response.args, response.returncode, json.dumps(value), response.stderr)

    selected = dict(local.support["writer_identity"])
    if expected == "current":
        selected["runtime_generation"] = "b" * 32
    admission = OrdinaryWriterAdmission({
        "version": 1, "identity": selected, "creation_nonce": local.workspace.creation_nonce,
    }, {
        "worktree": local.workspace.worktree, "allocation_project": local.workspace.project,
        "worktree_path": local.workspace.path,
    })
    if defect is None or defect == "legacy" and expected == "birth":
        result = admission.inspect_workspace(observe, native_session_id=local.head["session_id"])
        if defect is None:
            assert result["worktree_writer_observation"]["executing_writer_identity"] == {
                **selected, "runtime_generation": "b" * 32,
            }
    else:
        with pytest.raises(TaskError):
            admission.inspect_workspace(observe, native_session_id=local.head["session_id"])
