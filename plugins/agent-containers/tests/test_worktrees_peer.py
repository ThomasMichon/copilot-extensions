"""Same-cell configuration-owner admission contracts (``state-root --configuration``).

Shared real-process coverage lives in dispatch."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_containers import config
from agent_containers._peer_launch import ContextRefused
from configuration_layers import ConfigurationError


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    cell = tmp_path / "marketplaces" / "test-cell"
    root = cell / "plugins" / "agent-containers"
    root.mkdir(parents=True)
    (cell / "plugins" / "agent-worktrees").mkdir()
    own = {"cellRoot": str(cell), "pluginRoot": str(root)}
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "RUNTIME_DIR", root)
    monkeypatch.setattr(config._peer_launch, "validate_owner", lambda *args: own)
    monkeypatch.setattr("shutil.which", lambda _: pytest.fail("ambient PATH selected"))
    return own


def _report(owner: dict[str, str], tmp_path: Path, **context: str) -> dict:
    return {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "repo-qualified", "execution_machine": "logical-worker",
            "installation_cell": owner["cellRoot"], "binding": "unbound",
            "base_root": str(tmp_path), **context,
        },
    }


def _answer(code: int, stdout: bytes, stderr: bytes = b"") -> object:
    return lambda argv, **kwargs: subprocess.CompletedProcess(argv, code, stdout, stderr)


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
    report = _report(owner, tmp_path, binding="bound", knowledge_root=str(knowledge))

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert argv == [*prefix, "state-root", "--configuration", "--json"]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["timeout"] == 20
        return subprocess.CompletedProcess(argv, 0, json.dumps(report).encode("utf-8"), b"")

    monkeypatch.setattr(subprocess, "run", run)
    assert config.load_config().exec_user == "cell-user"


@pytest.mark.parametrize("response", [
    (126, b"", b"peer governance refused"),
    (1, b"", b"lookup failed"),
    (0, b"", b""),
    (0, b"{", b""),
    (0, b"[]", b""),
    (0, b"{}", b""),
    (0, json.dumps({
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {"repository_id": "r", "execution_machine": "m",
                    "installation_cell": "c", "binding": "unresolved"},
    }).encode("utf-8"), b""),
])
def test_refused_or_invalid_probe_never_becomes_defaults(
    owner: dict[str, str], response: tuple[int, bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", _answer(*response))
    with pytest.raises(ConfigurationError):
        config.load_config()


@pytest.mark.parametrize("error", [OSError("cannot launch"), subprocess.TimeoutExpired("peer", 20)])
def test_probe_execution_failures_are_refusals(
    owner: dict[str, str], error: Exception, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ConfigurationError, match="read failed"):
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

    monkeypatch.setattr(subprocess, "run", _answer(126, b"", b"peer governance refused"))
    monkeypatch.setattr(lifecycle, "list_containers", lambda *args: pytest.fail("Docker touched"))
    assert cli.main(["fleet"]) == 1
    assert "configuration owner refused" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_bound_knowledge_root_must_be_a_directory(
    owner: dict[str, str], tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "knowledge"
    if kind == "file":
        root.touch()
    report = _report(owner, tmp_path, binding="bound", knowledge_root=str(root))
    monkeypatch.setattr(subprocess, "run", _answer(0, json.dumps(report).encode("utf-8")))
    with pytest.raises(ConfigurationError, match="not a directory"):
        config.load_config()


def test_relay_profile_cannot_replace_refusal_with_default_allowlist(
    owner: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_containers import __main__ as cli
    from agent_containers import relay_provider

    monkeypatch.setattr(subprocess, "run", _answer(126, b"", b"peer governance refused"))
    monkeypatch.setattr(
        relay_provider, "_prepare_token_file", lambda: pytest.fail("token state mutated"),
    )
    with pytest.raises(ConfigurationError, match="refused"):
        relay_provider.relay_profile()
    with pytest.raises(ConfigurationError, match="refused"):
        relay_provider.register_relay(None)
    assert cli.main(["relay-profile"]) == 1
    captured = capsys.readouterr()
    assert "configuration owner refused" in captured.err
    assert captured.out == ""
