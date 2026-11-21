"""Manager release admission and source binding, without Git or a native lock."""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import application_footprint as footprint
import artifact_bundle_validation as validation
from artifact_source_build import PromotionArtifactError
import build_toolchain_lock as toolchain
import local_install_source as source
import installer_wheel_snapshot as snapshot


@pytest.mark.parametrize("case", ["manifest", "digest", "pristine"])
def test_manager_release_refuses_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    filename = "manager-1-py3-none-any.whl"
    digest = "a" * 64
    artifact: dict[str, Any] = {
        "component": "worktree-manager", "wheels": [{"filename": filename, "sha256": digest}],
    }
    if case != "manifest":
        monkeypatch.setattr(validation, "validate_manifest", lambda _: None)
    uv = tmp_path / "uv"
    uv.touch()
    monkeypatch.setattr(toolchain, "_query_marker_environment", lambda *a, **kw: {})
    retain = Mock(return_value=nullcontext(None))
    monkeypatch.setattr(snapshot, "retain_wheel_inputs", retain)
    compose = Mock(return_value={})
    monkeypatch.setattr(source, "compose", compose)
    output = tmp_path / "prepared"
    binding = Mock(side_effect=ValueError("pristine payload mismatch"))
    monkeypatch.setattr(validation, "validate_source_binding", binding)
    projects = Mock(side_effect=AssertionError("deployment metadata written before validation"))
    monkeypatch.setattr(source, "_source_projects", projects)
    expected = {"manifest": "invalid manifest payload hash", "digest": "admitted wheel inputs",
                "pristine": "pristine payload mismatch"}
    with pytest.raises(PromotionArtifactError if case == "manifest" else ValueError,
                       match=expected[case]):
        footprint.prepare_manager_release(
            tmp_path, "b" * 40, output, artifact=artifact,
            archives={filename: tmp_path / filename},
            digests={filename: "c" * 64 if case == "digest" else digest},
            target={"environment": {}}, policy_id="approval", acquisition_id="acquisition",
            uv=uv, deadline=float("inf"),
        )
    if case == "pristine":
        compose.assert_called_once()
        binding.assert_called_once_with(artifact, Path(source.native_retained_path(output)) / "source")
    else:
        retain.assert_not_called()
        compose.assert_not_called()
    projects.assert_not_called()
    assert not output.exists()
