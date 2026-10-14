"""Real native owner CLI, redirected roots and exact source-safety boundaries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

from agent_procutil import no_window_kwargs
import pytest
import yaml

from agent_worktrees import config as cfg, repos
from agent_worktrees.repos_configuration import apply, preview
from agent_worktrees.repos_configuration_sources import ConfigurationError

SECRET = "github_pat_SENTINEL_DO_NOT_RENDER"


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    previous = cfg.active_project()
    cfg.set_active_project("sample")
    try:
        for name in ("repository", "knowledge", "plugin", "preset"):
            root = tmp_path / name
            root.mkdir()
            (root / "sentinel.yaml").write_text(
                yaml.safe_dump({"session_env": {"PRIVATE_SENTINEL": SECRET}}), encoding="utf-8",
            )
        registry = repos._repos_yaml_path()
        registry.parent.mkdir()
        registry.write_text(yaml.safe_dump({
            "unrecognized_owner_metadata": {"sentinel": SECRET},
            "repos": {
                "sample": {
                    "class": "worktree", repos._current_platform(): str(tmp_path / "repository"),
                    "agent": False, "custom_owner_metadata": {"secret": SECRET},
                },
                "stale": {"class": "reference", repos._current_platform(): str(tmp_path / "gone")},
                "foreign": {"class": "reference"},
            },
        }), encoding="utf-8")
        cfg.global_config_path().write_text(
            yaml.safe_dump({"default_copilot_account": "inherited", "session_env": {"SECRET": SECRET}}),
            encoding="utf-8",
        )
        yield tmp_path
    finally:
        cfg.set_active_project(previous)


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "repos", "configuration", *args],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )


def request(command, *args, name="sample"):
    return cli(
        command, "--context-project", "sample", "--repository", name, *args,
    )


def tree(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def test_real_cli_set_reset_inheritance_and_allowlisted_local_writes(native):
    before = tree(native)
    catalog = request("catalog")
    assert catalog.returncode == 0, catalog.stderr
    rows = json.loads(catalog.stdout)["entries"]
    sample = next(row for row in rows if row["id"] == "sample")
    assert sample["effective"] == "inherited"
    assert sample["registration"] == "present"
    assert sample["configuration"]["same_machine_agent"] == "disabled"
    assert sample["configuration"]["pair"]["status"] == "unsupported"
    assert next(row for row in rows if row["id"] == "stale")["registration"] == "stale"
    assert next(row for row in rows if row["id"] == "foreign")["registration"] == "unsupported"
    proposed = request("preview", "--login", "chosen")
    assert proposed.returncode == 0, proposed.stderr
    token = json.loads(proposed.stdout)["token"]
    assert tree(native) == before
    result = request("apply", "--login", "chosen", "--preview-token", token)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["status"] == "persisted"
    assert repos.copilot_account_for("sample") == "chosen"
    assert repos.copilot_account_for("sample", project="sample", strict=True) == "chosen"
    after = tree(native)
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    assert changed == {
        str(Path(".agent-worktrees") / "repos.yaml"),
        str(Path(".agent-worktrees") / "repos.lock"),
    }
    raw = yaml.safe_load(repos._repos_yaml_path().read_text(encoding="utf-8"))
    assert raw["unrecognized_owner_metadata"]["sentinel"] == SECRET
    assert raw["repos"]["sample"]["custom_owner_metadata"]["secret"] == SECRET
    source = cfg.global_config_path()
    source.write_text("default_copilot_account: changed-inheritance\n", encoding="utf-8")
    before_reset = tree(native)
    reset = request("preview", "--operation", "reset")
    assert reset.returncode == 0
    assert "changed-inheritance" in reset.stdout
    token = json.loads(reset.stdout)["token"]
    assert tree(native) == before_reset
    result = request("apply", "--operation", "reset", "--preview-token", token)
    assert result.returncode == 0
    assert repos.copilot_account_for("sample") == "changed-inheritance"
    raw = yaml.safe_load(repos._repos_yaml_path().read_text(encoding="utf-8"))
    assert "copilot_account" not in raw["repos"]["sample"]
    after_reset = tree(native)
    assert {
        key for key in before_reset if before_reset[key] != after_reset[key]
    } == {str(Path(".agent-worktrees") / "repos.yaml")}
    assert SECRET not in "".join([
        catalog.stdout, catalog.stderr, proposed.stdout, proposed.stderr, result.stdout, result.stderr,
    ])


@pytest.mark.parametrize("change", ["registry", "global", "machine", "drop-in", "target", "login"])
def test_stale_previews_and_old_host_refuse_writes(native, change):
    proposed = preview("sample", "sample", "set", "chosen")
    name, login = "sample", "chosen"
    if change == "registry":
        # An ordinary real CLI mutation shares the very same native registry fence.
        result = subprocess.run(
            [sys.executable, "-m", "agent_worktrees", "repos", "account", "set", "example", "other"],
            capture_output=True, text=True, timeout=15, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
    elif change == "global":
        cfg.global_config_path().write_text("default_copilot_account: other\n", encoding="utf-8")
    elif change == "machine":
        cfg.project_dir("sample").mkdir()
        (cfg.project_dir("sample") / "config.yaml").write_text(
            "default_copilot_account: other\n", encoding="utf-8",
        )
    elif change == "drop-in":
        directory = cfg.project_dir("sample") / "config.d"
        directory.mkdir(parents=True)
        (directory / "local.yaml").write_text("default_copilot_account: other\n", encoding="utf-8")
    elif change == "target":
        name = "stale"
    else:
        login = "different"
    before = tree(native)
    for token in ("", "forged", proposed["token"]):
        result = request("apply", "--login", login, "--preview-token", token, name=name)
        assert result.returncode != 0, result.stdout
        assert tree(native) == before


def test_competing_real_cli_applies_have_one_winner(native):
    token = preview("sample", "sample", "set", "chosen")["token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: request("apply", "--login", "chosen", "--preview-token", token), range(2),
        ))
    assert sorted(result.returncode for result in results) == [0, 1]
    assert "preview-conflict" in next(result.stdout for result in results if result.returncode)


@pytest.mark.parametrize("content", [
    "[broken", "default_copilot_account: [not-a-login]\n",
    f"default_copilot_account: {SECRET}\n",
    'default_copilot_account: first\ndefault_copilot_account: ""\n',
    "copilot_identity_switch_enabled: not-a-boolean\n",
])
@pytest.mark.parametrize("source", ["global", "drop-in"])
def test_invalid_composition_is_unknown_not_empty_identity_and_sanitized(native, content, source):
    path = cfg.global_config_path()
    if source == "drop-in":
        directory = cfg.project_dir("sample") / "config.d"
        directory.mkdir(parents=True)
        path = directory / "local.yaml"
    path.write_text(content, encoding="utf-8")
    before = tree(native)
    catalog = request("catalog")
    assert catalog.returncode == 0, catalog.stderr
    row = next(row for row in json.loads(catalog.stdout)["entries"] if row["id"] == "sample")
    assert row["effective"] == "unknown" and row["editable"] == "false"
    assert row["configuration"]["findings"]
    proposed = request("preview", "--login", "chosen")
    assert proposed.returncode != 0
    assert SECRET not in catalog.stdout + catalog.stderr + proposed.stdout + proposed.stderr
    assert tree(native) == before
    with pytest.raises(ConfigurationError):
        repos.copilot_account_for("sample", project="sample", strict=True)


def test_absent_registration_and_fresh_root_are_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "first-touch"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)["entries"][0]
    assert row["registration"] == "absent" and row["editable"] == "false"
    assert not (tmp_path / "first-touch").exists()


def test_request_errors_never_echo_raw_values(native):
    result = cli("preview", "--context-project", "sample", "--operation", SECRET)
    assert result.returncode != 0 and SECRET not in result.stdout + result.stderr
    with pytest.raises(ConfigurationError):
        apply("sample", "sample", "set", SECRET, "")


@pytest.mark.parametrize("route", ["related: [broken", "related:\n  external: 42\n"])
def test_invalid_native_route_inspection_stays_unknown_not_valid_empty(native, route):
    policy = native / "repository" / ".copilot-extensions" / "agent-worktrees"
    policy.mkdir(parents=True)
    (policy / "related.yaml").write_text(route, encoding="utf-8")
    before = tree(native)
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    state = next(row["configuration"] for row in json.loads(result.stdout)["entries"] if row["id"] == "sample")
    assert state["route"]["status"] == "unknown"
    assert state["findings"][0]["code"] == "native-inspection-unavailable"
    assert tree(native) == before


def test_default_dropin_and_machine_precedence(native):
    directory = cfg.project_dir("sample") / "config.d"
    directory.mkdir(parents=True)
    (directory / "local.yaml").write_text("default_copilot_account: dropin\n", encoding="utf-8")
    source = directory.parent / "config.yaml"
    source.write_text("default_copilot_account: machine\ncopilot_identity_switch_enabled: true\n", encoding="utf-8")
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "sample")
    state = row["configuration"]
    assert state["effective"] == "machine" and state["switch_enabled"] is True
    assert [source["value"] for source in state["sources"]] == ["inherited", "dropin", "machine"]
    assert [source["wins"] for source in state["sources"]] == [False, False, True]
    assert repos.copilot_account_for("sample", project="sample", strict=True) == "machine"


def test_native_routes_binding_and_real_tracked_pair_present_stale_absent(native):
    from agent_worktrees import tracking

    registry_path = repos._repos_yaml_path()
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    raw["repos"]["knowledge"] = {
        "class": "knowledge", repos._current_platform(): str(native / "knowledge"),
    }
    registry_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    policy = native / "repository" / ".copilot-extensions" / "agent-worktrees"
    policy.mkdir(parents=True)
    (policy / "config.yaml").write_text("stateless: true\n", encoding="utf-8")
    (policy / "related.yaml").write_text(yaml.safe_dump({
        "related": {
            "external": {
                "role": "product", "locus": {"preferred": "codespace"},
                "delegate": {"via": "agent-codespaces"}, "secret": SECRET,
            },
        },
    }), encoding="utf-8")
    machine = cfg.project_dir("sample")
    machine.mkdir()
    (machine / "config.yaml").write_text("knowledge_repo: knowledge\n", encoding="utf-8")
    paths = {name: native / f"{name}-worktree" for name in ("sample", "knowledge")}
    records = {}
    for name, role, sibling in (("sample", "harness", "knowledge"), ("knowledge", "knowledge", "sample")):
        paths[name].mkdir()
        record = tracking.WorktreeRecord(
            worktree_id=f"wt-{name}", branch=f"worktree/wt-{name}", worktree_path=str(paths[name]),
            repo=name, machine="fixture", platform=repos._current_platform(),
            started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
            resume_count=0, title=None, status="active", completed_at=None, sessions=[],
            pair_id="fixture-pair", pair_role=role, pair_ref=f"fixture/{sibling}/wt-{sibling}",
            pair_kind="worktree",
        )
        path = cfg.project_dir(name) / "worktrees" / f"wt-{name}.yaml"
        tracking.save_record(record, path)
        records[name] = path

    for expected in ("present", "stale", "absent"):
        before = tree(native)
        result = request("catalog", "--workspace", str(paths["sample"]))
        assert result.returncode == 0, result.stderr
        rows = json.loads(result.stdout)["entries"]
        row = next(row for row in rows if row["id"] == "sample")
        state = row["configuration"]
        assert not state["findings"], state["findings"]
        assert state["binding"]["identity"] == "knowledge"
        assert state["binding"]["anchor"] == str(native / "knowledge")
        assert state["binding"]["writable_workspace"] is False
        assert state["pair"]["status"] == expected
        if expected == "present":
            assert state["pair"]["workspace"] == str(paths["knowledge"])
            assert state["pair"]["writable_workspace"] is True
        else:
            assert state["pair"]["writable_workspace"] is False
        route = next(row for row in rows if row["id"] == "external")
        assert route["registration"] == "absent" and route["participation"] == "related"
        assert route["configuration"]["route"]["preferred"] == "codespace"
        assert route["configuration"]["route"]["editable"] is False
        assert SECRET not in result.stdout + result.stderr
        assert tree(native) == before
        if expected == "present":
            retired = tracking.load_record(records["knowledge"])
            retired.status = "finalized"
            tracking.save_record(retired, records["knowledge"])
            result = request("catalog", "--workspace", str(paths["sample"]))
            assert result.returncode == 0, result.stderr
            state = next(
                row["configuration"] for row in json.loads(result.stdout)["entries"]
                if row["id"] == "sample"
            )
            assert state["pair"]["workspace_status"] == "finalized"
            assert state["pair"]["writable_workspace"] is False
            records["knowledge"].unlink()
        elif expected == "stale":
            records["sample"].unlink()
