"""The suite's source checkout opts out through the generic repository policy."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from repository_policy import AgentHostingDenied, require_agent_hosting


def test_source_repository_marker_uses_its_existing_base_configuration() -> None:
    root = Path(__file__).resolve().parents[3]
    policy = yaml.safe_load((root / ".agent-worktrees/config.yaml").read_text(encoding="utf-8"))
    assert policy["expose_agent"] is False
    assert policy["default_branch"] == "dev"
    assert policy["pr"]["required"] is True


@pytest.mark.parametrize("operation", ["launch", "adopt", "resume", "review"])
def test_source_repository_has_no_managed_authoring_or_review_label_exemption(
    operation: str,
) -> None:
    root = Path(__file__).resolve().parents[3]
    with pytest.raises(AgentHostingDenied, match="expose_agent: false"):
        require_agent_hosting(root, operation=operation)
