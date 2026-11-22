"""Cold capability checks must use the incoming executor, not the bootstrap kit."""
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

import onboard


def test_select_each_harness_independently(tmp_path: Path) -> None:
    anchors = {name: tmp_path / name for name in ("default", "other", "bound")}
    choices = [("explicit", "example/one"), ("portable", "example/two"), ("local", "retained")]
    with patch("worktree_manager.bootstrap_harness._git", return_value=""), patch(
        "worktree_manager.bootstrap_pairing.select", side_effect=choices,
    ) as select:
        result = onboard.selected_companions(tmp_path, "default", anchors, "example/one", tmp_path)
    assert result == {"default": "example/one", "other": "example/two"}
    assert [(call.args[0].parent.name, call.args[2]) for call in select.call_args_list] == [
        ("default", "example/one"), ("other", None), ("bound", None),
    ]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_probe_uses_incoming_environment(tmp_path: Path, exit_code: int) -> None:
    python = tmp_path / "executor" / "python"
    env = {"UV_OFFLINE": "1"}
    with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], exit_code)) as run:
        if exit_code:
            with pytest.raises(RuntimeError, match="no app slots staged"):
                onboard.require_onboarding(python, env)
        else:
            onboard.require_onboarding(python, env)
        assert run.call_args.args[0][:3] == [str(python), "-I", "-B"]
        assert run.call_args.kwargs["env"] == env
        assert not list(tmp_path.iterdir())
