"""Cold capability checks must use the incoming executor, not the bootstrap kit."""
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

import onboard


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
