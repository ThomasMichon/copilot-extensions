"""Config admission contracts; shared real-process coverage lives in dispatch."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_containers import config
from agent_containers._peer_launch import ContextRefused


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    cell = tmp_path / "marketplaces" / "test-cell"
    root = cell / "plugins" / "agent-containers"
    root.mkdir(parents=True)
    (cell / "plugins" / "agent-worktrees").mkdir()
    own = {"cellRoot": str(cell), "pluginRoot": str(root)}
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "RUNTIME_DIR", root)
    monkeypatch.setattr(config._peer_launch, "validate_owner", lambda *args: own)
    monkeypatch.setattr("shutil.which", lambda _: pytest.fail("ambient PATH selected"))
    return own


def test_lookup_uses_native_same_cell_prefix(
    owner: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "containers.yaml").write_text("exec_user: cell-user\n", encoding="utf-8")
    prefix = config._peer_launch.launch_prefix(
        "agent-containers", Path(owner["pluginRoot"]),
        str(Path(owner["pluginRoot"]) / "install.json"), "agent-worktrees",
    )

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv == [*prefix, "state-root", "--json"]
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["timeout"] == 20
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "requires_external": True, "bound": True, "state_root": str(knowledge),
        }), "")

    monkeypatch.setattr(subprocess, "run", run)
    assert config.load_config().exec_user == "cell-user"


@pytest.mark.parametrize("response", [
    subprocess.CompletedProcess([], 126, "", "peer governance refused"),
    subprocess.CompletedProcess([], 1, "", "lookup failed"),
    subprocess.CompletedProcess([], 0, "", ""),
    subprocess.CompletedProcess([], 0, "{", ""),
    subprocess.CompletedProcess([], 0, "[]", ""),
    subprocess.CompletedProcess([], 0, "{}", ""),
    subprocess.CompletedProcess([], 0, json.dumps({
        "requires_external": True, "bound": False, "state_root": "/knowledge",
    }), ""),
])
def test_refused_or_invalid_probe_never_becomes_defaults(
    owner: dict[str, str], response: subprocess.CompletedProcess[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: response)
    with pytest.raises(ContextRefused):
        config.load_config()


@pytest.mark.parametrize("error", [OSError("cannot launch"), subprocess.TimeoutExpired("peer", 20)])
def test_probe_execution_failures_are_refusals(
    owner: dict[str, str], error: Exception, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ContextRefused, match="lookup failed"):
        config.load_config()


@pytest.mark.parametrize("raw", ["{", " ", "missing-receipt"])
def test_explicit_config_cannot_bypass_owner_admission(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "containers.yaml"
    path.write_text("exec_user: forbidden\n", encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", raw)
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path)
    with pytest.raises(ContextRefused):
        config.load_config()


def test_cli_stops_before_provider_on_refusal(
    owner: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_containers import __main__ as cli
    from agent_containers import lifecycle

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        [], 126, "", "peer governance refused",
    ))
    monkeypatch.setattr(lifecycle, "list_containers", lambda *args: pytest.fail("Docker touched"))
    assert cli.main(["fleet"]) == 1
    assert "peer governance refused" in capsys.readouterr().err
