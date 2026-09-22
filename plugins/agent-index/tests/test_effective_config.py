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
    config.parent.mkdir(parents=True)
    config.write_text(
        f"requires_external_state_root: {str(requires_external).lower()}\n",
        encoding="utf-8",
    )
    return path


def _write_active(path: Path) -> Path:
    config = path / ".agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
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
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)


def test_absent_config_is_inactive(tmp_path: Path) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")

    result = module.resolve(repo)

    assert result["opted_in"] is False
    assert result["reason"] == "repository-config-absent"


def test_bare_repository_is_config_absent_not_unavailable(tmp_path: Path) -> None:
    # A bare anchor checkout (e.g. an agent-worktrees anchor, which
    # deliberately has no attached work tree) must resolve like any other
    # repository lacking a local config -- "repository-config-absent" -- not
    # "repository-unavailable", which the companion provider treats as fatal
    # across every activation scope it resolves (see companion-provider.py).
    module = _module()
    bare = tmp_path / "bare-repo"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

    result = module.resolve(bare)

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
    config.parent.mkdir(parents=True)
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
    config.parent.mkdir(parents=True)
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
    config = repo / ".copilot-extensions" / "agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
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
    assert Path(result["repo_root"]) == repo.resolve()


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


def test_external_state_root_falls_back_to_project_flag_for_bare_repo(
    tmp_path: Path, monkeypatch
) -> None:
    # A bare anchor checkout (agent-worktrees' own anchor, deliberately
    # work-tree-less) can't be discovered by cwd, but it IS a known,
    # registered project -- state-root must retry with an explicit
    # `--project` instead of giving up.
    module = _module()
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    bare = tmp_path / "bare-anchor"
    bare.mkdir()
    (home / ".agent-worktrees" / "repos.yaml").write_text(
        "repos:\n"
        f"  example-anchor-repo:\n"
        f"    windows: {json.dumps(str(bare))}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module.Path, "home", lambda: home)
    monkeypatch.setattr(module, "_platform_key", lambda: "windows")
    monkeypatch.setattr(module, "_worktrees_command", lambda: "agent-worktrees")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "--project" not in argv:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="Could not resolve a project"
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "state_root": str(tmp_path / "knowledge"),
                    "source": "knowledge_repo",
                    "repo": "dotfiles",
                    "requires_external": True,
                    "bound": True,
                    "error": None,
                }
            ),
            stderr="",
        )

    (tmp_path / "knowledge").mkdir()
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    state, path = module._external_state_root(bare)

    assert state == "ready"
    assert path == (tmp_path / "knowledge").resolve()
    assert len(calls) == 2
    assert "--project" in calls[1] and "example-anchor-repo" in calls[1]


def test_external_state_root_suppresses_console_for_resolved_command(
    tmp_path: Path, monkeypatch
) -> None:
    # `_worktrees_command` can resolve to the PATH `.cmd` binstub (shutil.which
    # prefers .cmd over .ps1 on Windows), which Windows must interpret through
    # a fresh cmd.exe -- CREATE_NO_WINDOW must be set so it never flashes on
    # every companion-provider probe.
    module = _module()
    monkeypatch.setattr(
        module, "_worktrees_command", lambda: r"C:\bin\agent-worktrees.CMD"
    )
    monkeypatch.setattr(module.os, "name", "nt")
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    state, path = module._external_state_root(tmp_path)

    assert state == "invalid"  # {} has neither stateless nor requires_external key
    assert path is None
    assert captured["kwargs"].get("creationflags") == 0x08000000


class _FakePeerLaunch:
    """Stand-in for the vendored same-cell boundary in explicit-context tests."""

    class ContextRefused(RuntimeError):
        pass

    @staticmethod
    def launch_prefix(owner, own_root, raw_context, peer):
        return ["fake-python", "-I", "-X", "utf8", "fake-peer-launch.py", owner, str(own_root), raw_context, peer]

    @staticmethod
    def no_window_kwargs():
        return {}


def _fake_own(tmp_path: Path) -> dict:
    cell = tmp_path / "cell"
    (cell / "plugins" / "agent-worktrees").mkdir(parents=True)
    plugin_root = cell / "plugins" / "agent-index"
    plugin_root.mkdir(parents=True)
    return {"cellRoot": str(cell), "pluginRoot": str(plugin_root)}


def test_external_state_root_same_cell_never_touches_ambient_path(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "explicit")
    monkeypatch.delenv("AGENT_WORKTREES_COMMAND", raising=False)
    own = _fake_own(tmp_path)
    monkeypatch.setattr(module, "_load_peer_launch", lambda: _FakePeerLaunch)
    monkeypatch.setattr(module, "_validate_index_owner_or_refuse", lambda _ctx: own)
    monkeypatch.setattr(
        "shutil.which", lambda _: pytest.fail("ambient PATH selected under explicit context")
    )
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        assert "agent-worktrees" in argv
        assert "state-root" in argv and "--json" in argv
        return subprocess.CompletedProcess(
            argv, 0,
            stdout=json.dumps({
                "requires_external": True, "bound": True, "source": "knowledge_repo",
                "repo": "dotfiles", "state_root": str(tmp_path / "knowledge"), "error": None,
            }),
            stderr="",
        )

    (tmp_path / "knowledge").mkdir()
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    state, path = module._external_state_root(tmp_path)

    assert state == "ready"
    assert path == (tmp_path / "knowledge").resolve()
    assert seen["argv"][0] == "fake-python"


def test_external_state_root_same_cell_refusal_is_not_swallowed_to_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    # An explicit context that fails to validate at the peer boundary (exit
    # 126) must propagate as a refusal, never silently degrade to
    # "unavailable" -- that would be indistinguishable from a genuinely
    # absent peer and could mask an isolation violation.
    module = _module()
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "explicit")
    monkeypatch.delenv("AGENT_WORKTREES_COMMAND", raising=False)
    own = _fake_own(tmp_path)
    monkeypatch.setattr(module, "_load_peer_launch", lambda: _FakePeerLaunch)
    monkeypatch.setattr(module, "_validate_index_owner_or_refuse", lambda _ctx: own)
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 126, stdout="", stderr="peer launch refused",
        ),
    )

    with pytest.raises(_FakePeerLaunch.ContextRefused, match="peer launch refused"):
        module._external_state_root(tmp_path)


def test_worktrees_command_override_still_wins_over_explicit_context(
    tmp_path: Path, monkeypatch
) -> None:
    # A test-only AGENT_WORKTREES_COMMAND override must still take priority,
    # matching _worktrees_command's own explicit-wins precedence.
    module = _module()
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "explicit")
    monkeypatch.setenv("AGENT_WORKTREES_COMMAND", "agent-worktrees")
    monkeypatch.setattr(
        module, "_validate_index_owner_or_refuse",
        lambda _ctx: pytest.fail("same-cell path selected despite command override"),
    )

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0,
            stdout=json.dumps({
                "requires_external": True, "bound": True, "source": "knowledge_repo",
                "repo": "dotfiles", "state_root": str(tmp_path / "knowledge"), "error": None,
            }),
            stderr="",
        )

    (tmp_path / "knowledge").mkdir()
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    state, path = module._external_state_root(tmp_path)
    assert state == "ready"


def test_packaged_peer_launch_bytes_are_canonical() -> None:
    root = Path(__file__).resolve().parents[3]
    packaged = PLUGIN / "src" / "agent_index" / "_peer_launch.py"
    assert (root / "libs" / "peer-launch" / "peer_launch.py").read_bytes() == packaged.read_bytes()
    context_dir = root / "libs" / "installation-context"
    assert (
        (context_dir / "installation_context.py").read_bytes()
        == (PLUGIN / "src" / "agent_index" / "_installation_context.py").read_bytes()
    )
    for fragment in sorted(context_dir.glob("_installation_context_*.py")):
        assert fragment.read_bytes() == (PLUGIN / "src" / "agent_index" / fragment.name).read_bytes()


def test_invalid_local_config_never_falls_through(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo", requires_external=True)
    local = repo / ".agent-index" / "config.yaml"
    local.parent.mkdir(parents=True)
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


def test_legacy_repository_config_falls_back_when_canonical_absent(
    tmp_path: Path,
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = repo / ".copilot-extensions" / "agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("indexer:\n  machine: legacy\n", encoding="utf-8")

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert Path(result["config"]) == config.resolve()
    assert result["indexers"] == [{"machine": "legacy"}]


def test_shareable_base_merges_with_repo_local_overlay(
    tmp_path: Path,
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    legacy = repo / ".copilot-extensions" / "agent-index" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("indexer:\n  machine: legacy\n", encoding="utf-8")
    config = _write_active(repo)

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert Path(result["config"]) == config.resolve()
    assert result["indexers"] == [{"machine": "legacy"}]
    assert result["sources"] == [{"name": "git:example", "repo": "example"}]


def test_repo_local_machine_overlay_wins_over_in_repo_for_duplicate_names(
    tmp_path: Path,
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "corpus:\n"
        "  sources:\n"
        "    - name: github:gim-home/odsp-web-harness\n"
        "      trust_domain: harness\n",
        encoding="utf-8",
    )
    overlay = repo / ".copilot-extensions" / "agent-index" / "config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        "indexer:\n"
        "  machine: overlay-box\n"
        "  ssh: overlay-box\n"
        "corpus:\n"
        "  sources:\n"
        "    - name: github:gim-home/odsp-web-harness\n"
        "      trust_domain: machine-local\n"
        "    - name: github:ThomasMichon/copilot-extensions\n"
        "      trust_domain: machine-local\n",
        encoding="utf-8",
    )

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert Path(result["config"]) == config.resolve()
    assert result["indexers"] == [{"machine": "overlay-box", "ssh": "overlay-box"}]
    assert result["sources"] == [
        {
            "name": "github:gim-home/odsp-web-harness",
            "trust_domain": "machine-local",
        },
        {
            "name": "github:ThomasMichon/copilot-extensions",
            "trust_domain": "machine-local",
        },
    ]


def test_duplicate_source_name_uses_higher_precedence_machine_local_layer(
    tmp_path: Path,
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "corpus:\n"
        "  sources:\n"
        "    - name: git:dotfiles\n"
        "      repo: dotfiles\n"
        "      trust_domain: shareable\n",
        encoding="utf-8",
    )
    overlay = repo / ".copilot-extensions" / "agent-index" / "config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        "corpus:\n"
        "  sources:\n"
        "    - name: git:dotfiles\n"
        "      repo: override\n"
        "      trust_domain: overlay\n",
        encoding="utf-8",
    )

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert result["sources"] == [
        {"name": "git:dotfiles", "repo": "override", "trust_domain": "overlay"}
    ]


def test_knowledge_overlay_beats_in_repo_base_for_duplicate_names(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo", requires_external=True)
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "corpus:\n"
        "  sources:\n"
        "    - name: github:gim-home/odsp-web-harness\n",
        encoding="utf-8",
    )
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    knowledge_config = knowledge / ".agent-index" / "config.yaml"
    knowledge_config.parent.mkdir(parents=True)
    knowledge_config.write_text(
        "corpus:\n"
        "  sources:\n"
        "    - name: github:gim-home/odsp-web-harness\n"
        "      repo: knowledge-copy\n"
        "    - name: git:dotfiles\n"
        "      repo: dotfiles\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module, "_external_state_root", lambda _root: ("ready", knowledge)
    )

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert Path(result["config"]) == config.resolve()
    assert result["sources"] == [
        {"name": "github:gim-home/odsp-web-harness", "repo": "knowledge-copy"},
        {"name": "git:dotfiles", "repo": "dotfiles"},
    ]


def test_machine_local_overlay_beats_knowledge_overlay_for_duplicate_names(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo", requires_external=True)
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "corpus:\n"
        "  sources:\n"
        "    - name: git:dotfiles\n"
        "      repo: repo-base\n",
        encoding="utf-8",
    )
    overlay = repo / ".copilot-extensions" / "agent-index" / "config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        "corpus:\n"
        "  sources:\n"
        "    - name: git:dotfiles\n"
        "      repo: machine-local\n",
        encoding="utf-8",
    )
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    knowledge_config = knowledge / ".agent-index" / "config.yaml"
    knowledge_config.parent.mkdir(parents=True)
    knowledge_config.write_text(
        "corpus:\n"
        "  sources:\n"
        "    - name: git:dotfiles\n"
        "      repo: knowledge\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module, "_external_state_root", lambda _root: ("ready", knowledge)
    )

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert result["sources"] == [{"name": "git:dotfiles", "repo": "machine-local"}]


def test_marketplace_overlay_merges_on_top_of_base(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    repo = _repo(tmp_path / "repo")
    config = _write_active(repo)
    overlay = (
        repo
        / ".copilot-extensions"
        / "agent-index"
        / "marketplaces"
        / "example-marketplace"
        / "config.yaml"
    )
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        "indexers:\n"
        "  - machine: overlay\n"
        "    ssh: overlay\n"
        "indexer:\n"
        "  machine: overlay\n"
        "  ssh: overlay\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        json.dumps({"marketplaceId": "example-marketplace"}),
    )

    result = module.resolve(repo)

    assert result["opted_in"] is True
    assert Path(result["config"]) == config.resolve()
    assert result["indexers"] == [{"machine": "overlay", "ssh": "overlay"}]
    assert result["sources"] == [{"name": "git:example", "repo": "example"}]


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
