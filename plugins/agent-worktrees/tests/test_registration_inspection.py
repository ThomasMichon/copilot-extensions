"""Read-only registration selection through the actual native CLI parser."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_worktrees import installer, repos, repos_cli


@pytest.fixture
def registered(monkeypatch, tmp_path):
    root = tmp_path / "registry"
    root.mkdir()
    anchor = tmp_path / "selected checkout"
    anchor.mkdir()
    other = tmp_path / "other checkout"
    other.mkdir()
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: root / "repos.yaml")
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: root / "projects.yaml")
    return root, anchor, other


def publish(root, repository_rows=None, project_rows=None, *, project_version=2):
    if repository_rows is not None:
        (root / "repos.yaml").write_text(
            yaml.safe_dump({"repos": repository_rows}), encoding="utf-8",
        )
    if project_rows is not None:
        (root / "projects.yaml").write_text(yaml.safe_dump({
            "schema_version": project_version, "projects": project_rows,
        }), encoding="utf-8")


def repository(path, *, agent=True):
    return {"class": "worktree", repos._current_platform(): str(path), "agent": agent}


def request(anchor, capsys, *, default="control", expected=0, extra=()):
    result = repos_cli.cmd_repos_dispatch([
        "registration", "inspect", "--anchor", str(anchor),
        "--default-name", default, "--contract-version", "1", *extra,
    ])
    output = capsys.readouterr()
    assert result == expected, output.out + output.err
    data = json.loads(output.out)
    assert data["contract"] == "copilot-extensions.project-registration-inspection"
    assert data["contract_version"] == 1
    return data


@pytest.mark.parametrize("agent", [True, False])
def test_native_parser_selects_unique_alias_and_preserves_canonical_policy(registered, capsys, agent):
    root, anchor, other = registered
    publish(root, {
        "custom-control": repository(anchor, agent=agent),
        "control": repository(other),
    }, {
        "custom-control": {"expose_agent": not agent, "unrelated": "retained"},
        "control": {"expose_agent": True},
    })
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    data = request(anchor, capsys)
    assert data["status"] == "ready"
    assert data["project"] == "custom-control"
    assert data["anchor"] == str(anchor.resolve())
    assert data["registered"] is True
    assert data["agent"] is agent
    assert data["exposure_source"] == "repository"
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_absent_registries_offer_unclaimed_default_without_creating_state(registered, capsys):
    root, anchor, _other = registered
    data = request(anchor, capsys)
    assert data["project"] == "control"
    assert data["registered"] is False and data["agent"] is True
    assert data["exposure_source"] == "fresh-default"
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("content", [b"", b"projects: null\n"])
def test_native_empty_legacy_project_defaults_remain_usable(registered, capsys, content):
    root, anchor, _other = registered
    (root / "projects.yaml").write_bytes(content)
    data = request(anchor, capsys)
    assert data["registered"] is False
    assert (root / "projects.yaml").read_bytes() == content


@pytest.mark.parametrize("agent", [True, False])
def test_legacy_only_alias_retains_its_root_and_exposure(registered, capsys, agent):
    root, anchor, _other = registered
    publish(root, project_rows={
        "legacy-control": {"anchor": str(anchor), "expose_agent": agent},
    }, project_version=1)
    before = (root / "projects.yaml").read_bytes()
    data = request(anchor, capsys)
    assert data["project"] == "legacy-control" and data["registered"] is True
    assert data["agent"] is agent and data["exposure_source"] == "legacy-project"
    assert (root / "projects.yaml").read_bytes() == before
    assert not (root / "repos.yaml").exists()


def test_native_home_expansion_and_equivalent_legacy_root(registered, capsys, monkeypatch):
    root, anchor, _other = registered
    monkeypatch.setenv("HOME", str(anchor.parent))
    monkeypatch.setenv("USERPROFILE", str(anchor.parent))
    paths = [os.path.join("~", anchor.name)]
    if os.name == "nt":
        paths.append("\\\\?\\" + str(anchor))
    for path in paths:
        publish(root, {"custom-control": repository(path, agent=False)}, {
            "custom-control": {"anchor": str(anchor) + os.sep, "expose_agent": False},
        }, project_version=1)
        before = {item.name: item.read_bytes() for item in root.iterdir()}
        data = request(anchor, capsys)
        assert data["project"] == "custom-control" and data["agent"] is False
        assert {item.name: item.read_bytes() for item in root.iterdir()} == before


@pytest.mark.parametrize("scenario,code", [
    ("duplicate-alias", "ambiguous-anchor"),
    ("inconsistent-root", "inconsistent-root"),
    ("claimed-default", "name-collision"),
    ("unresolved-adoption", "unresolved-project-root"),
])
def test_ambiguous_or_conflicting_registration_is_never_fresh(registered, capsys, scenario, code):
    root, anchor, other = registered
    rows = {"custom-control": repository(anchor)}
    projects = {}
    version = 2
    if scenario == "duplicate-alias":
        alias = "\\\\?\\" + str(anchor) if os.name == "nt" else str(anchor)
        rows["second-control"] = repository(alias)
    elif scenario == "inconsistent-root":
        projects = {"custom-control": {"anchor": str(other), "expose_agent": True}}
        version = 1
    elif scenario == "claimed-default":
        rows = {"control": repository(other)}
    else:
        rows = {}
        projects = {"orphan-control": {"expose_agent": False}}
    publish(root, rows, projects, project_version=version)
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    data = request(anchor, capsys, expected=2)
    assert data["status"] == "failed" and data["code"] == code
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


@pytest.mark.parametrize("filename,content", [
    ("repos.yaml", b"repos: [invalid]\n"),
    ("repos.yaml", b"repos:\n  duplicate: {}\n  duplicate: {}\n"),
    ("repos.yaml", b"\xff"),
    ("repos.yaml", b"schema_version: 99\nrepos: {}\n"),
    ("projects.yaml", b"projects: [invalid]\n"),
    ("projects.yaml", b"projects:\n  duplicate: {}\n  duplicate: {}\n"),
    ("projects.yaml", b"\xff"),
    ("projects.yaml", b"schema_version: 99\nprojects: {}\n"),
])
def test_malformed_registry_cannot_supply_absence(registered, capsys, filename, content):
    root, anchor, _other = registered
    (root / filename).write_bytes(content)
    data = request(anchor, capsys, expected=2)
    assert data["status"] == "failed" and data["code"] == "registration-unavailable"
    assert (root / filename).read_bytes() == content
    assert len(list(root.iterdir())) == 1


@pytest.mark.parametrize("filename", ["repos.yaml", "projects.yaml"])
def test_unreadable_registry_is_not_an_empty_inventory(registered, capsys, filename):
    root, anchor, _other = registered
    (root / filename).mkdir()
    data = request(anchor, capsys, expected=2)
    assert data["status"] == "failed" and data["code"] == "registration-unavailable"
    assert (root / filename).is_dir()


@pytest.mark.parametrize("record", [
    {"anchor": "relative-checkout"},
    {"anchor": True},
    {"anchor": None},
    {"anchor": "/placeholder", "expose_agent": "false"},
    {"anchor": "/placeholder", "base_repo": "true"},
])
def test_invalid_legacy_identity_or_policy_never_guesses(registered, capsys, record):
    root, anchor, _other = registered
    if record.get("anchor") == "/placeholder":
        record = {**record, "anchor": str(anchor)}
    publish(root, project_rows={"custom-control": record}, project_version=1)
    data = request(anchor, capsys, expected=2)
    assert data["status"] == "failed"


def test_unknown_request_is_rejected_before_registry_acquisition(registered, capsys, monkeypatch):
    _root, anchor, _other = registered
    monkeypatch.setattr(repos, "read_registry", lambda **_kwargs: pytest.fail("read registry"))
    data = request(anchor, capsys, extra=("--misspelled",), expected=2)
    assert data["code"] == "invalid-request"


def test_unsupported_contract_is_pre_read_and_truthful(registered, capsys, monkeypatch):
    _root, anchor, _other = registered
    monkeypatch.setattr(repos, "read_registry", lambda **_kwargs: pytest.fail("read registry"))
    data = request(anchor, capsys, extra=("--contract-version", "2"), expected=2)
    assert data["code"] == "unsupported-contract"
    assert data["supported_contract_versions"] == [1]


def test_case_distinct_project_commands_respect_the_native_platform(registered, capsys):
    root, anchor, _other = registered
    publish(root, {
        "Custom-Control": repository(anchor),
        "custom-control": repository(anchor),
    })
    data = request(anchor, capsys, expected=2)
    assert data["code"] == ("name-collision" if os.name == "nt" else "ambiguous-anchor")


def test_registered_reference_class_is_not_promoted_by_inspection(registered, capsys):
    root, anchor, _other = registered
    row = {**repository(anchor, agent=False), "class": "reference"}
    publish(root, {"read-only-control": row})
    before = (root / "repos.yaml").read_bytes()
    data = request(anchor, capsys)
    assert data["registered"] is True and data["repository_class"] == "reference"
    assert data["agent"] is False
    assert (root / "repos.yaml").read_bytes() == before


def test_legacy_base_repository_is_not_promoted_to_worktrees(registered, capsys):
    root, anchor, _other = registered
    publish(root, project_rows={
        "base-control": {"anchor": str(anchor), "base_repo": True},
    }, project_version=1)
    data = request(anchor, capsys)
    assert data["registered"] is True and data["repository_class"] == "singleton"


def test_native_namespace_selection_and_invalid_context_do_not_use_legacy_state(
    tmp_path, monkeypatch, capsys,
):
    from test_registry_paths import _payload

    home = tmp_path / "home"
    legacy = home / ".agent-worktrees"
    legacy.mkdir(parents=True)
    legacy_source = legacy / "repos.yaml"
    legacy_source.write_bytes(b"invalid: [\n")
    monkeypatch.setenv("AGENT_HOME", str(home))
    for key in ("COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_WORKTREES_PAYLOAD_ROOT"):
        monkeypatch.delenv(key, raising=False)
    payload = _payload(tmp_path, "inspection-payload")
    stamped = subprocess.run([
        sys.executable, str(payload / "scripts" / "installation-context" / "installation_context.py"),
        "stamp", "--source-json", json.dumps({"source": "github", "repo": "example/registration"}),
        "--marketplace-key", "inspection-fixture", "--plugin-id", "agent-worktrees",
        "--payload-root", str(payload), "--payload-version", "0.0.1-dev1",
        "--payload-origin", "explicit", "--expected-namespace-generation", "0",
        "--expected-install-generation", "0", "--durable-home", str(tmp_path / "managed"),
    ], capture_output=True, text=True, timeout=15, **no_window_kwargs())
    assert stamped.returncode == 0, stamped.stderr + stamped.stdout
    selected = json.loads(stamped.stdout)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", selected["installReceipt"])
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(payload))
    root = repos._repos_yaml_path().parent
    root.mkdir(parents=True, exist_ok=True)
    assert root != legacy
    anchor = tmp_path / "selected checkout"
    anchor.mkdir()
    publish(root, {"namespaced-control": repository(anchor, agent=False)})
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    data = request(anchor, capsys)
    assert data["project"] == "namespaced-control" and data["agent"] is False
    assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == before
    assert legacy_source.read_bytes() == b"invalid: [\n"
    invalid_context = tmp_path / "invalid-context.json"
    invalid_context.write_text("invalid", encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(invalid_context))
    data = request(anchor, capsys, expected=2)
    assert data["code"] == "registration-unavailable"
    assert legacy_source.read_bytes() == b"invalid: [\n"


def test_actual_neutral_cwd_cli_and_existing_inventory_remain_read_only(tmp_path):
    home = tmp_path / "home"
    root = home / ".agent-worktrees"
    root.mkdir(parents=True)
    anchor = tmp_path / "selected checkout"
    anchor.mkdir()
    other = tmp_path / "other checkout"
    other.mkdir()
    neutral = tmp_path / "neutral"
    neutral.mkdir()
    publish(root, {
        "custom-control": repository(anchor, agent=False),
        "control": repository(other),
    }, {"custom-control": {"expose_agent": True}})
    sentinel = home / "preserved-knowledge.yaml"
    sentinel.write_bytes(b"knowledge_repo: retained\n")
    before = {str(path.relative_to(home)): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_WORKTREES_", "WORKTREE_MANAGER_"))
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        APPDATA=str(home / "roaming"), LOCALAPPDATA=str(home / "local"),
        XDG_CONFIG_HOME=str(home / ".config"), PYTHONDONTWRITEBYTECODE="1",
        AGENT_WORKTREES_NO_SELFPROVISION="1",
    )
    arguments = [
        "registration", "inspect", "--anchor", str(anchor),
        "--default-name", "control", "--contract-version", "1",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", *arguments],
        cwd=neutral, env=environment, capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    data = json.loads(result.stdout)
    assert data["project"] == "custom-control" and data["agent"] is False
    inventory = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", "list", "--strict", "--json"],
        cwd=neutral, env=environment, capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert inventory.returncode == 0, inventory.stderr + inventory.stdout
    assert {row["name"] for row in json.loads(inventory.stdout)["repos"]} == {"custom-control", "control"}
    assert {str(path.relative_to(home)): path.read_bytes() for path in home.rglob("*") if path.is_file()} == before
