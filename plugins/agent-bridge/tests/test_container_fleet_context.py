import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge import __main__ as cli
from agent_bridge import session_lifecycle_cli
from agent_bridge.agent_registry_namespace import CliNamespaceResolver
from agent_bridge.container_fleet_context import container_fleet_context
from agent_bridge.session_start_models import StartSessionRequest
from agent_bridge.session_start_cli import _prepare_sender_preference


@pytest.mark.parametrize("scope,expected", [("anchor-controller", "controller"), ("", None), ("managed-1234", None)])
def test_container_anchor_preference(
    monkeypatch: pytest.MonkeyPatch, scope: str, expected: str | None,
) -> None:
    monkeypatch.setattr(cli, "_worktrees_get", {"session-scope-id": scope, "project": "controller"}.get)
    assert _prepare_sender_preference(SimpleNamespace(), "container:one").project == expected


@pytest.mark.asyncio
async def test_container_provider_gets_adopted_checkout_and_refuses_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    owner = tmp_path / "owner.py"
    owner.write_text(
        "import sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:] == ['--project', 'controller', 'get', 'repo-dir']\n"
        "print(Path(__file__).parent)\n",
    )
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda: [sys.executable, str(owner)])
    executable = tmp_path / "provider.py"
    executable.write_text(
        "import json, sys\n"
        "print('  --fleet-repository PATH' if sys.argv[1:] == ['--help'] else json.dumps(sys.argv[1:]))\n",
    )
    provider = CliNamespaceResolver("container", "example", command=[sys.executable, str(executable)])
    request = StartSessionRequest(agent="container:one", sender_repo="controller", caller_id="affinity")
    command = ["namespace-resolve", "one"]
    async with container_fleet_context("container:one", request):
        result = await provider._run(command)
        assert result and json.loads(result[1]) == ["--fleet-repository", str(tmp_path), *command]
        executable.write_text("import sys\nassert sys.argv[1:] == ['--help']\nprint('legacy options')\n")
        with pytest.raises(RuntimeError, match="Update the selected Containers runtime"):
            await provider._run(command)
    executable.write_text("import json,sys; print(json.dumps(sys.argv[1:]))")
    result = await provider._run(command)
    assert result and json.loads(result[1]) == command
