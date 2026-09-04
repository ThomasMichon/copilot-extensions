from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "resolve_effective_config.py"


def _module():
    spec = importlib.util.spec_from_file_location("effective_config", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(path: Path, *, requires_external: bool = False) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    config = path / ".agent-worktrees" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        f"requires_external_state_root: {str(requires_external).lower()}\n",
        encoding="utf-8",
    )
    return path


def _write_active(path: Path) -> Path:
    config = path / ".agent-index" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "indexers:\n"
        "  - machine: primary\n"
        "    ssh: primary\n"
        "  - machine: secondary\n"
        "    ssh: secondary\n"
        "indexer:\n"
        "  machine: primary\n"
        "  ssh: primary\n"
        "corpus:\n"
        "  sources:\n"
        "    - name: git:example\n"
        "      repo: example\n",
        encoding="utf-8",
    )
    return config


@pytest.fixture(autouse=True)
def _clean_activation_env(monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_CONFIG_DATA_B64", raising=False)
    monkeypatch.delenv("AGENT_INDEX_REPO", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_COMMAND", raising=False)


def test_absent_config_is_inactive(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "repository-config-absent"


def test_valid_repository_config_is_effective(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = _write_active(repo)

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert result["source"] == "repository"
    assert Path(result["config"]) == config.resolve()
    assert [item["machine"] for item in result["indexers"]] == [
        "primary",
        "secondary",
    ]
    assert result["sources"] == [{"name": "git:example", "repo": "example"}]


def test_dependency_light_parser_accepts_supported_config_shape() -> None:
    module = _module()
    parsed = module._parse_simple_yaml(
        "indexers:\n"
        "  - machine: primary\n"
        "    ssh: primary\n"
        "corpus:\n"
        "  sources:\n"
        "    - name: github:example/repo\n"
        "      auth: { account: example }\n"
        "      include: '**/*.py'\n"
    )

    assert parsed["indexers"][0]["machine"] == "primary"
    assert parsed["corpus"]["sources"][0]["auth"] == {"account": "example"}
    assert parsed["corpus"]["sources"][0]["include"] == "**/*.py"


def test_corpus_only_config_is_an_explicit_opt_in(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "corpus:\n  sources:\n    - name: git:example\n",
        encoding="utf-8",
    )

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert result["indexers"] == []


def test_valid_local_config_wins_before_external_state_policy(
    tmp_path: Path,
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    _write_active(repo)
    (repo / ".agent-worktrees" / "config.yaml").write_text(
        "requires_external_state_root: [\n",
        encoding="utf-8",
    )

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert result["source"] == "repository"


@pytest.mark.parametrize(
    "content",
    [
        "{}\n",
        "indexers: []\n",
        "indexers: [\n  machine: host\n",
        "indexers:\n  - machine: first\n  - machine: FIRST\n",
        "indexers:\n  - machine: first\nindexer:\n  machine: other\n",
        "indexers:\n  - machine: first\nindexers:\n  - machine: second\n",
    ],
)
def test_invalid_repository_config_is_inactive(
    tmp_path: Path, content: str
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir()
    config.write_text(content, encoding="utf-8")

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "repository-config-invalid"


@pytest.mark.parametrize(
    "ssh",
    [
        "-oProxyCommand=echo-injected",
        "host with spaces",
        "host\talias",
        "host;command",
    ],
)
def test_unsafe_ssh_alias_is_inactive(tmp_path: Path, ssh: str) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        f"indexer:\n  machine: primary\n  ssh: {ssh!r}\n",
        encoding="utf-8",
    )

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "repository-config-invalid"


def test_present_unsafe_local_config_blocks_external_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo", requires_external=True)
    local = repo / ".agent-index" / "config.yaml"
    local.mkdir(parents=True)
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_active(knowledge)
    monkeypatch.setattr(
        module, "_external_state_root", lambda _root: ("ready", knowledge)
    )

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "repository-config-invalid"


def test_bound_external_state_config_is_effective(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo", requires_external=True)
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    config = _write_active(knowledge)
    monkeypatch.setattr(
        module, "_external_state_root", lambda _root: ("ready", knowledge)
    )

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert result["source"] == "external-state-root"
    assert Path(result["config"]) == config.resolve()


@pytest.mark.parametrize("state", ["unavailable", "invalid"])
def test_required_external_state_resolution_fails_closed(
    tmp_path: Path, monkeypatch, state: str
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo", requires_external=True)
    monkeypatch.setattr(
        module, "_external_state_root", lambda _root: (state, None)
    )

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == f"external-state-root-{state}"


def test_required_external_state_without_config_is_inactive(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo", requires_external=True)
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(
        module, "_external_state_root", lambda _root: ("ready", knowledge)
    )

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "external-state-root-config-absent"


def test_invalid_local_config_never_falls_through(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo", requires_external=True)
    local = repo / ".agent-index" / "config.yaml"
    local.parent.mkdir()
    local.write_text("indexers: [\n", encoding="utf-8")
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _write_active(knowledge)
    called = False

    def external(_root):
        nonlocal called
        called = True
        return "ready", knowledge

    monkeypatch.setattr(module, "_external_state_root", external)

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "repository-config-invalid"
    assert called is False


def test_agent_index_repo_override_selects_repository(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = _write_active(repo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("AGENT_INDEX_REPO", str(repo))

    result = module.resolve(elsewhere)

    assert result["opted_in"] is True
    assert Path(result["config"]) == config.resolve()


def test_invalid_agent_index_repo_override_never_uses_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    _write_active(repo)
    monkeypatch.setenv("AGENT_INDEX_REPO", str(tmp_path / "missing"))

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "repository-override-unavailable"


def test_valid_forwarded_config_preserves_remote_activation(monkeypatch) -> None:
    module = _module()
    raw = json.dumps({"indexers": [{"machine": "primary"}]}).encode("utf-8")
    monkeypatch.setenv(
        "AGENT_INDEX_CONFIG_DATA_B64",
        base64.urlsafe_b64encode(raw).decode("ascii"),
    )

    result = module.resolve()

    assert result["opted_in"] is True
    assert result["source"] == "forwarded"
    assert result["indexers"] == [{"machine": "primary"}]


def test_invalid_forwarded_config_never_falls_back(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    _write_active(repo)
    monkeypatch.setenv("AGENT_INDEX_CONFIG_DATA_B64", "not-base64")

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "forwarded-config-invalid"
