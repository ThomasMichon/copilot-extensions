"""Passive exact-checkout discovery, not ambient project or provider selection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError

from agent_logger import configuration_layers
from agent_logger.aggregate import Admission
from agent_logger.sync import worktree_state_providers as providers
from agent_logger.sync.worktree_state import WorktreeStateError

from .conftest import init_git_repo

SOURCE = Path(__file__).resolve().parents[3]
CAPABILITY = "worktree-data-project-inventory-v1"
REPOSITORY = "example.test/owner/source"


@pytest.mark.no_autotrust
def test_readonly_logger_resolver_refuses_before_import(tmp_path, monkeypatch):
    from configuration_context.readiness import PROBE_CONTRACT, PROBE_ENV, ProbeContractError

    payload = tmp_path / "payload"
    resolver = payload / "scripts" / "versioned_runtime.py"
    resolver.parent.mkdir(parents=True)
    resolver.write_text("raise AssertionError('unqualified resolver imported')\n", encoding="utf-8")
    (payload / "plugin.json").write_text('{"name":"agent-logger"}', encoding="utf-8")
    monkeypatch.setattr(configuration_layers, "__file__", str(payload / "src" / "agent_logger" / "configuration_layers.py"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "non-retained"))
    monkeypatch.setenv(PROBE_ENV, PROBE_CONTRACT)
    with pytest.raises(ProbeContractError, match="unqualified"):
        configuration_layers._legacy_owner_prefix()


def admission(tmp_path: Path) -> Admission:
    return Admission(REPOSITORY, tmp_path / "checkout")


def inventory(tmp_path: Path) -> dict[str, Any]:
    scope = {
        "project": "registered-alias",
        "repository": "registered-alias",
        "repository_id": "opaque-owner-identity",
        "marketplace_id": None,
        "project_root_device": 1,
        "project_root_inode": 2,
    }
    placement = {
        "scope": scope,
        "worktree_id": "retired-full-id",
        "creation_nonce": "incarnation",
        "domain": "agendas",
        "lane": "main",
        "store_id": "a" * 32,
        "placement_generation": 1,
        "state_home": str(tmp_path / "retained" / "main"),
        "state_home_allocated": True,
        "store_content_validated": False,
        "live_authority": False,
    }
    return {
        "schema": "agent-worktrees.worktree-data",
        "version": 1,
        "ok": True,
        "project": "registered-alias",
        "capability": CAPABILITY,
        "data": {
            "project": "registered-alias",
            "repository_identity": "network:" + REPOSITORY,
            "scope": scope,
            "worktrees": [
                {
                    "schema": "copilot-extensions.worktree-data",
                    "version": 1,
                    "scope": scope,
                    "worktree_id": "retired-full-id",
                    "creation_nonce": "incarnation",
                    "placements": [placement],
                    "canonical_record": "missing",
                    "live_authority": False,
                }
            ],
            "live_authority": False,
        },
    }


def transport(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]) -> list[list[str]]:
    calls = []
    native_run = providers.run_caller_owned
    prefix, environment = [sys.executable, "-m", "agent_worktrees"], {"selected": "owner"}
    monkeypatch.setattr(
        configuration_layers,
        "worktrees_owner_command",
        lambda home: (prefix, environment, None),
    )
    monkeypatch.setattr(
        providers,
        "_checkout_repository_identity",
        lambda selected, **kwargs: "network:" + REPOSITORY,
    )

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if argv[0] == "git":
            return native_run(argv, **kwargs)
        calls.append(argv)
        assert kwargs["env"] == environment
        assert kwargs["cwd"].name == "checkout"
        assert "--project" not in argv
        value = (
            {
                "schema": "agent-worktrees.worktree-data",
                "version": 1,
                "ok": True,
                "capabilities": ["worktree-data-v1"],
                "optional_capabilities": [CAPABILITY],
            }
            if "--capabilities" in argv
            else reply
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode(), b"")

    monkeypatch.setattr(providers, "run_caller_owned", run)
    return calls


@pytest.mark.parametrize("cleanup", [False, True])
def test_origin_probe_uses_the_same_execution_budget_and_preserves_git_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup: bool,
) -> None:
    original = providers._checkout_repository_identity
    transport(monkeypatch, inventory(tmp_path))
    owner_run = providers.run_caller_owned
    monkeypatch.setattr(providers, "_checkout_repository_identity", original)
    budget = OwnedExecutionBudget.from_timeout(30, cleanup_reserve=5)
    uncertain = OwnedCleanupError(None)
    probes = []
    for key in providers.repo_trust._AMBIENT_GIT_ENV_VARS:
        monkeypatch.setenv(key, "foreign-git-routing")

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        if argv[0] != "git":
            assert options["budget"] is budget
            return owner_run(argv, **options)
        probes.append(argv)
        assert options["budget"] is budget
        assert options["max_stdout_bytes"] == 65536 and options["max_stderr_bytes"] == 4096
        assert all(key not in options["env"] for key in providers.repo_trust._AMBIENT_GIT_ENV_VARS)
        if cleanup:
            raise uncertain
        return subprocess.CompletedProcess(
            argv, 0, ("https://" + REPOSITORY + ".git\n").encode(), b""
        )

    monkeypatch.setattr(providers, "run_caller_owned", run)
    if cleanup:
        with pytest.raises(OwnedCleanupError) as caught:
            providers.read_project_inventory(
                admission(tmp_path), tmp_path / "logger", budget=budget
            )
        assert caught.value is uncertain
    else:
        result = providers.read_project_inventory(
            admission(tmp_path),
            tmp_path / "logger",
            budget=budget,
        )
        assert result.owner_repository_identity == "network:" + REPOSITORY
    assert probes == [["git", "-C", str(tmp_path / "checkout"), "remote", "get-url", "origin"]]


def test_complete_inventory_cannot_succeed_after_its_execution_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport(monkeypatch, inventory(tmp_path))
    budget = OwnedExecutionBudget.from_timeout(30, cleanup_reserve=5)
    monkeypatch.setattr(providers.time, "monotonic", lambda: budget.execution_deadline + 1)
    with pytest.raises(WorktreeStateError, match="shared time budget"):
        providers.read_project_inventory(admission(tmp_path), tmp_path / "logger", budget=budget)


@pytest.mark.parametrize(
    ("remote", "owner_identity", "accepted"),
    [
        ("https://git.example/Team/Repo.git", "network:git.example/Team/Repo", True),
        ("https://git.example/Team/Repo.git", "network:git.example/team/repo", False),
        ("ssh://git@git.example:2222/owner/repo.git", "network:git.example:2222/owner/repo", True),
        (
            "ssh://git@git.example:2222/owner/repo.git",
            "network:git.example:2223/owner/repo",
            False,
        ),
        ("https://git.example:443/Team/Repo.git", "network:git.example/Team/Repo", True),
        ("ssh://git@git.example:22/Team/Repo.git", "network:git.example/Team/Repo", True),
        ("https://github.com/Team/Repo.git", "network:github.com/team/repo", True),
    ],
)
def test_actual_checkout_remote_preserves_owner_identity_distinctions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote: str,
    owner_identity: str,
    accepted: bool,
) -> None:
    selected = Admission(remote, tmp_path / "checkout")
    init_git_repo(selected.authoritative_checkout, remote=remote)
    original = providers._checkout_repository_identity
    reply = inventory(tmp_path)
    reply["data"]["repository_identity"] = owner_identity
    transport(monkeypatch, reply)
    monkeypatch.setattr(providers, "_checkout_repository_identity", original)
    if not accepted:
        with pytest.raises(WorktreeStateError):
            providers.read_project_inventory(selected, tmp_path / "logger")
    else:
        result = providers.read_project_inventory(selected, tmp_path / "logger")
        assert result.owner_repository_identity == owner_identity


def test_inventory_uses_actual_checkout_and_retains_retired_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = transport(monkeypatch, inventory(tmp_path))
    result = providers.read_project_inventory(admission(tmp_path), tmp_path / "logger")
    assert result.project == "registered-alias"
    assert result.repository == REPOSITORY
    assert len(result.placements) == 1
    placement = result.placements[0]
    assert placement.worktree == "retired-full-id" and placement.canonical_record == "missing"
    assert placement.state_home == tmp_path / "retained/main"
    assert placement.store_id == "a" * 32 and placement.placement_generation == 1
    assert [call[-2] for call in calls] == ["--capabilities", "--inventory"]
    assert not (tmp_path / "retained").exists()


@pytest.mark.parametrize(
    "kind",
    [
        "repository",
        "cell",
        "scope",
        "project",
        "duplicate",
        "count",
        "generation",
        "authority",
        "relative-home",
        "partial",
        "worktree-version",
        "scope-absent",
    ],
)
def test_foreign_partial_or_ambiguous_inventory_is_not_an_empty_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    reply = inventory(tmp_path)
    data = reply["data"]
    worktree = data["worktrees"][0]
    placement = worktree["placements"][0]
    if kind == "repository":
        data["repository_identity"] = "network:example.test/other/source"
    elif kind == "cell":
        data["scope"]["marketplace_id"] = "foreign-cell"
    elif kind == "scope":
        placement["scope"] = {**placement["scope"], "repository_id": "another-owner"}
    elif kind == "project":
        reply["project"] = "another-alias"
    elif kind == "duplicate":
        worktree["placements"].append(deepcopy(placement))
    elif kind == "count":
        data["worktrees"] *= 129
    elif kind == "generation":
        placement["placement_generation"] = True
    elif kind == "authority":
        placement["live_authority"] = True
    elif kind == "relative-home":
        placement["state_home"] = "../foreign"
    elif kind == "partial":
        del placement["state_home"]
    elif kind == "worktree-version":
        worktree["version"] = True
    else:
        data["scope"] = None
    transport(monkeypatch, reply)
    with pytest.raises(WorktreeStateError):
        providers.read_project_inventory(admission(tmp_path), tmp_path / "logger")


@pytest.mark.parametrize("mode", ["legacy", "unknown-parser", "refused", "malformed"])
def test_inventory_capability_is_negotiated_before_new_syntax(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    calls = []
    monkeypatch.setattr(
        configuration_layers,
        "worktrees_owner_command",
        lambda home: ([sys.executable, "-m", "agent_worktrees"], {}, None),
    )

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        assert "--inventory" not in argv
        body = {
            "schema": "agent-worktrees.worktree-data",
            "version": 1,
            "ok": True,
            "capabilities": ["worktree-data-v1"],
        }
        return subprocess.CompletedProcess(
            argv,
            2 if mode == "unknown-parser" else 126 if mode == "refused" else 0,
            b"{" if mode == "malformed" else json.dumps(body).encode(),
            b"unsupported",
        )

    monkeypatch.setattr(providers, "run_caller_owned", run)
    with pytest.raises(WorktreeStateError):
        providers.read_project_inventory(admission(tmp_path), tmp_path / "logger")
    assert len(calls) == 1


def test_empty_inventory_with_no_state_root_is_truthful_and_passive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = inventory(tmp_path)
    reply["data"].update(scope=None, worktrees=[])
    transport(monkeypatch, reply)
    assert (
        providers.read_project_inventory(
            admission(tmp_path),
            tmp_path / "logger",
        ).placements
        == ()
    )


def test_explicit_context_refusal_never_falls_back_to_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
    monkeypatch.setattr(
        configuration_layers._peer_launch,
        "validate_owner",
        lambda *args: (_ for _ in ()).throw(ValueError("refused owner")),
    )
    monkeypatch.setattr(
        configuration_layers,
        "_legacy_owner_prefix",
        lambda: pytest.fail("explicit context fell back"),
    )
    with pytest.raises(WorktreeStateError, match="refused owner"):
        providers.read_project_inventory(admission(tmp_path), tmp_path / "logger")


def test_same_cell_inventory_reuses_existing_native_peer_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cell = tmp_path / "marketplaces/owned-cell"
    home = cell / "plugins/agent-logger"
    receipt = cell / "plugins/agent-worktrees/install.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")
    raw = str(home / "install.json")
    own = {"cellRoot": str(cell), "pluginRoot": str(home), "marketplaceId": "owned-cell"}
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", raw)
    monkeypatch.setattr(configuration_layers._peer_launch, "validate_owner", lambda *args: own)
    monkeypatch.setattr(
        providers,
        "_checkout_repository_identity",
        lambda selected, **kwargs: "network:" + REPOSITORY,
    )
    monkeypatch.setattr(
        configuration_layers,
        "_legacy_owner_prefix",
        lambda: pytest.fail("legacy fallback"),
    )
    expected = configuration_layers._peer_launch.launch_prefix(
        "agent-logger",
        home,
        raw,
        "agent-worktrees",
    )
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        assert argv[: len(expected)] == expected and kwargs["env"] is None
        reply = inventory(tmp_path)
        reply["data"]["scope"]["marketplace_id"] = "owned-cell"
        value = (
            {
                "schema": "agent-worktrees.worktree-data",
                "version": 1,
                "ok": True,
                "capabilities": ["worktree-data-v1"],
                "optional_capabilities": [CAPABILITY],
            }
            if "--capabilities" in argv
            else reply
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode(), b"")

    monkeypatch.setattr(providers, "run_caller_owned", run)
    assert len(providers.read_project_inventory(admission(tmp_path), home).placements) == 1
    assert len(calls) == 2


@pytest.mark.parametrize("baseline", [False, True])
def test_actual_prior_worktrees_parser_never_receives_unnegotiated_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    baseline: bool,
) -> None:
    selected = admission(tmp_path)
    selected.authoritative_checkout.mkdir()
    script = tmp_path / "prior-owner.py"
    script.write_text(
        "import argparse,json\nparser=argparse.ArgumentParser(allow_abbrev=False)\n"
        + (
            "parser.add_argument('command',choices=['worktree-data-read'])\n"
            "parser.add_argument('--capabilities',required=True,action='store_true')\n"
            "parser.add_argument('--json',action='store_true')\n"
            "parser.parse_args()\nprint(json.dumps({'schema':'agent-worktrees.worktree-data',"
            "'version':1,'ok':True,'capabilities':['worktree-data-v1']}))\n"
            if baseline
            else "parser.add_argument('command',choices=['list'])\nparser.parse_args()\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        configuration_layers,
        "worktrees_owner_command",
        lambda home: ([sys.executable, "-I", "-B", str(script)], {}, None),
    )
    with pytest.raises(
        WorktreeStateError,
        match="lacks worktree-data" if baseline else "exit 2",
    ):
        providers.read_project_inventory(selected, tmp_path / "logger")


@pytest.fixture
def native_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated native -m provider; no live runtime, registry or host start."""
    runtime = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(runtime)
    packages = next(runtime.rglob("site-packages"))
    paths = [
        SOURCE / "plugins/agent-worktrees/src",
        *(root / "src" for root in (SOURCE / "libs").iterdir() if (root / "src").is_dir()),
        *(Path(path) for path in sys.path if path and Path(path).is_absolute()),
    ]
    (packages / "fixture.pth").write_text(
        "\n".join(str(path) for path in paths) + "\n",
        encoding="utf-8",
    )
    executable = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    registry = tmp_path / "worktrees-owner/.agent-worktrees"
    registry.mkdir(parents=True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("WORKTREE_PROJECT", "foreign-alias")
    monkeypatch.setattr(
        configuration_layers,
        "_legacy_owner_prefix",
        lambda: [str(executable), "-B", "-m", "agent_worktrees"],
    )
    monkeypatch.setattr("agent_logger.repo_trust._default_repos_yaml_root", lambda: registry)
    return registry


@pytest.mark.parametrize("retained", [False, True])
def test_actual_native_parser_selects_admitted_checkout_not_ambient_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_owner: Path,
    retained: bool,
) -> None:
    selected = admission(tmp_path)
    init_git_repo(selected.authoritative_checkout, remote="https://" + REPOSITORY + ".git")
    foreign = tmp_path / "foreign"
    init_git_repo(foreign, remote="https://example.test/other/source.git")
    import yaml

    (native_owner / "projects.yaml").write_text(
        yaml.safe_dump(
            {
                "projects": {
                    "registered-alias": {},
                    "foreign-alias": {},
                }
            }
        ),
        encoding="utf-8",
    )
    (native_owner / "repos.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "registered-alias": {
                        "remote": "https://" + REPOSITORY + ".git",
                        **{
                            platform: str(selected.authoritative_checkout)
                            for platform in ("linux", "wsl", "windows")
                        },
                    },
                    "foreign-alias": {
                        "remote": "https://example.test/other/source.git",
                        **{platform: str(foreign) for platform in ("linux", "wsl", "windows")},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    if retained:
        prefix, environment, _ = configuration_layers.worktrees_owner_command(tmp_path / "logger")
        produced = subprocess.run(
            [prefix[0], "-B", "-c", _RETAINED],
            cwd=selected.authoritative_checkout,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert produced.returncode == 0, produced.stderr
    before = {path: path.read_bytes() for path in native_owner.parent.rglob("*") if path.is_file()}
    result = providers.read_project_inventory(selected, tmp_path / "logger")
    assert result.project == "registered-alias"
    assert [placement.store_id for placement in result.placements] == (
        ["a" * 32, "b" * 32] if retained else []
    )
    assert all(placement.canonical_record == "missing" for placement in result.placements)
    assert {
        path: path.read_bytes() for path in native_owner.parent.rglob("*") if path.is_file()
    } == before


_RETAINED = """
import json
from agent_worktrees import config as cfg
from agent_worktrees.worktree_data_store import allocate_worktree_data
project,worktree="registered-alias","retired-full-id"
root=cfg.project_dir(project)
directory=root/"worktrees"
directory.mkdir(parents=True)
record=directory/(worktree+".yaml")
record.write_text(json.dumps({
    "worktree_id":worktree,"creation_nonce":"incarnation","repo":project,
    "branch":"worktree/retired","worktree_path":str(root/"absent-checkout"),
    "machine":"fixture","platform":"linux",
}))
for lane,store in (("main","a"*32),("secondary","b"*32)):
    allocate_worktree_data(project,worktree,creation_nonce="incarnation",
        domain="agendas",lane=lane,store_id=store)
record.rename(root/"retired-record.retained")
"""


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def efforts_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    home, root = tmp_path / "user", tmp_path / "checkout"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    plugin = root / ".ai/efforts"
    _json(
        root / ".ai/.claude-plugin/marketplace.json",
        {
            "name": "fixture",
            "plugins": [{"name": "efforts", "source": "./efforts"}],
        },
    )
    _json(plugin / ".claude-plugin/plugin.json", {"name": "efforts"})
    _json(
        root / ".github/copilot/settings.json",
        {
            "extraKnownMarketplaces": {
                "fixture": {"source": {"source": "directory", "path": "./.ai"}},
            },
            "enabledPlugins": {"efforts@fixture": True},
        },
    )
    relative = Path("skills/driving-effort-agendas/scripts")
    shutil.copytree(SOURCE / "plugins/efforts" / relative, plugin / relative)
    return home, root, plugin / relative / "agenda_export_cli.py"


def test_actual_efforts_activation_and_info_select_exact_project_provider(
    efforts_payload: tuple[Path, Path, Path],
) -> None:
    _, root, script = efforts_payload
    before = set(root.rglob("*"))
    selected = providers.select_efforts_exporter(root)
    assert selected.source == "efforts@fixture" and selected.script == script
    assert set(root.rglob("*")) == before


@pytest.mark.parametrize("kind", ["disabled", "malformed-settings", "ambiguous", "indirect"])
def test_efforts_selection_never_uses_another_scope_or_first_provider(
    efforts_payload: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    home, root, script = efforts_payload
    settings = root / ".github/copilot/settings.json"
    value = json.loads(settings.read_text(encoding="utf-8"))
    if kind == "disabled":
        _json(home / ".copilot/settings.json", value)
        value["enabledPlugins"]["efforts@fixture"] = False
    elif kind == "malformed-settings":
        _json(home / ".copilot/settings.json", value)
        settings.write_text("{", encoding="utf-8")
    elif kind == "ambiguous":
        value["enabledPlugins"]["efforts@other"] = True
        value["extraKnownMarketplaces"]["other"] = {
            "source": {"source": "directory", "path": "./.ai", "manifestName": "fixture"},
        }
    else:
        retained = script.with_suffix(".retained")
        script.rename(retained)
        try:
            script.symlink_to(retained)
        except OSError:
            pytest.skip("native symlink creation unavailable")
    if kind != "malformed-settings":
        _json(settings, value)
    monkeypatch.setattr(
        providers,
        "run_caller_owned",
        lambda *args, **kwargs: pytest.fail("unselected provider executed"),
    )
    with pytest.raises(WorktreeStateError):
        providers.select_efforts_exporter(root)


@pytest.mark.parametrize("kind", ["legacy", "bad-version", "failed", "malformed", "overflow"])
def test_efforts_support_comes_from_actual_selected_script_info(
    efforts_payload: tuple[Path, Path, Path],
    kind: str,
) -> None:
    _, root, script = efforts_payload
    value = {
        "schema": "efforts.agenda-export-cli",
        "version": 1,
        "ok": True,
        "capabilities": ["agenda-complete-export-v1"],
        "optional_capabilities": ["agenda-export-contributors-v1"],
    }
    if kind == "legacy":
        del value["optional_capabilities"]
    elif kind == "bad-version":
        value["version"] = True
    elif kind == "failed":
        value["ok"] = False
    text = "{" if kind == "malformed" else " " * 65537 if kind == "overflow" else json.dumps(value)
    script.write_text(
        "import argparse\nparser=argparse.ArgumentParser(allow_abbrev=False)\n"
        "parser.add_subparsers(dest='command',required=True).add_parser('info')\n"
        f"parser.parse_args()\nprint({text!r})\n",
        encoding="utf-8",
    )
    with pytest.raises(WorktreeStateError):
        providers.select_efforts_exporter(root)
