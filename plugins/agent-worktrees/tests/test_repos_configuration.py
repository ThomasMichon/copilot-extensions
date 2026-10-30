"""Real native owner CLI, redirected roots and exact source-safety boundaries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from agent_procutil import no_window_kwargs
import pytest
import yaml

from agent_worktrees import config as cfg, repos
from agent_worktrees.repos_configuration import apply, preview
from agent_worktrees.repos_configuration_sources import ConfigurationError

SECRET = "github_pat_SENTINEL_DO_NOT_RENDER"


@pytest.mark.parametrize("outcome", ["absent", "disappeared", "empty"])
def test_mapping_acquisition_owner_distinguishes_empty_without_replay(tmp_path, monkeypatch, outcome):
    """Strict acquisition distinguishes empty values without repeating native reads."""
    from agent_worktrees import repos_configuration_sources as sources

    path = tmp_path / "strict.yaml"
    if outcome != "absent":
        path.write_text("", encoding="utf-8")
    original_read, original_parse, original_exists = Path.read_bytes, sources.parse_mapping, Path.exists
    reads, parses = [], []

    def read(candidate):
        if candidate == path:
            reads.append(candidate)
            if outcome == "disappeared":
                raise FileNotFoundError("synthetic disappearance after selection")
        return original_read(candidate)

    def parse(text):
        parses.append(text)
        return original_parse(text)

    def exists(candidate):
        if candidate == path:
            pytest.fail("strict owner must use its original read, not add a presence probe")
        return original_exists(candidate)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(sources, "parse_mapping", parse)
    assert sources.mapping(path) == {}
    assert reads == [path]
    assert parses == ([""] if outcome == "empty" else [])
    reads.clear()
    parses.clear()
    report = sources.mapping_report(path)
    assert report.value == {}
    assert reads == [path]
    assert parses == ([""] if outcome == "empty" else [])
    assert report.authority.value == ("complete" if outcome == "empty" else "absent")
    assert any(o.source == str(path) for o in report.observations)
    assert report.reusable is False
    assert "stable-owner-input-frontier" in report.missing_coverage


def test_mapping_acquisition_owner_preserves_invalid_composition(tmp_path, monkeypatch):
    """The strict report keeps the parser's fatal sanitized failure."""
    from agent_worktrees import config_acquisition, repos_configuration_sources as sources

    path = tmp_path / "strict.yaml"
    path.write_text("private_setting: [\n", encoding="utf-8")
    original = Path.read_bytes
    reads = []

    def read(candidate):
        if candidate == path:
            reads.append(candidate)
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", read)
    for load in (sources.mapping, lambda candidate: sources.mapping_report(candidate)):
        reads.clear()
        with pytest.raises(ConfigurationError) as caught:
            load(path)
        assert caught.value.code == "invalid-composition"
        assert "private_setting" not in str(caught.value)
        assert reads == [path]
        assert config_acquisition.current_observations() is None


def test_mapping_acquisition_owner_preserves_nonabsence_exception_identity(tmp_path, monkeypatch):
    """A raw non-absence failure must not become an optional empty mapping."""
    import errno
    from agent_worktrees import config_acquisition, repos_configuration_sources as sources

    path = tmp_path / "strict.yaml"
    failure = OSError(errno.ELOOP, "synthetic non-absence read failure")
    reads = []

    def read(candidate):
        assert candidate == path
        reads.append(candidate)
        raise failure

    monkeypatch.setattr(Path, "read_bytes", read)
    for load in (sources.mapping, lambda candidate: sources.mapping_report(candidate)):
        reads.clear()
        with pytest.raises(OSError) as caught:
            load(path)
        assert caught.value is failure
        assert reads == [path]
        assert config_acquisition.current_observations() is None


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
                    "remote": "https://github.com/example/sample.git",
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
        str(Path(".agent-worktrees") / "pr-rewrite-authority.lock"),
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


def test_secret_shaped_repository_name_remains_browsable_and_editable(native):
    """A registered repository literally named like a secret (e.g. ``sk-ui``)
    is a resource name, not a credential: the secret-token rejection applies
    only to login values, never to repository/project identifiers."""
    registry = repos._repos_yaml_path()
    raw = yaml.safe_load(registry.read_text(encoding="utf-8"))
    raw["repos"]["sk-ui"] = {
        "class": "worktree", repos._current_platform(): str(native / "repository"),
    }
    registry.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "sk-ui")
    assert row["registration"] == "present"
    proposed = request("preview", "--login", "chosen", name="sk-ui")
    assert proposed.returncode == 0, proposed.stderr
    token = json.loads(proposed.stdout)["token"]
    result = request("apply", "--login", "chosen", "--preview-token", token, name="sk-ui")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert repos.copilot_account_for("sk-ui") == "chosen"


def test_overlong_registered_name_is_an_unsupported_row_not_a_catalog_abort(native):
    """A registered name longer than the shared character-length cap (native
    `add_repo()` imposes none) must only mark that one row unsupported, never
    abort the whole catalog for the properly named selected project."""
    registry = repos._repos_yaml_path()
    raw = yaml.safe_load(registry.read_text(encoding="utf-8"))
    overlong = "a" * 65
    raw["repos"][overlong] = {
        "class": "worktree", repos._current_platform(): str(native / "repository"),
    }
    registry.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    rows = {row["id"]: row for row in json.loads(result.stdout)["entries"]}
    assert rows["sample"]["effective"] == "inherited" and rows["sample"]["editable"] == "true"
    assert rows[overlong]["effective"] == "unknown" and rows[overlong]["editable"] == "false"
    assert rows[overlong]["configuration"]["findings"]


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
    f"? [{SECRET}]\n: invalid-key\n",
    f"? {{{SECRET}: value}}\n: invalid-key\n",
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


def test_malformed_projects_registry_is_sanitized_native_inspection(native):
    from agent_worktrees import installer

    installer.projects_yaml_path().write_text(f"projects: [{SECRET}\n", encoding="utf-8")
    before = tree(native)
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "sample")
    assert row["configuration"]["binding"]["status"] == "unknown"
    assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
    assert SECRET not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert tree(native) == before


def test_disabled_managed_dropin_does_not_block_valid_preferences(native):
    source = "sample@example-marketplace"
    root = native / "plugin"
    target = root / "config.yaml"
    target.write_text("default_copilot_account: disabled-value\n", encoding="utf-8")
    settings = native / ".copilot" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"enabledPlugins": {source: False}}), encoding="utf-8")
    directory = cfg.project_dir("sample") / "config.d"
    directory.mkdir(parents=True)
    (directory / "managed.json").write_text(json.dumps({
        "schema_version": 1, "plugin": source, "plugin_root": str(root), "target": str(target),
    }), encoding="utf-8")
    before = tree(native)
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    row = next(row for row in json.loads(result.stdout)["entries"] if row["id"] == "sample")
    assert row["effective"] == "inherited" and row["editable"] == "true"
    proposed = request("preview", "--login", "chosen")
    assert proposed.returncode == 0, proposed.stderr
    assert tree(native) == before
    result = request("apply", "--login", "chosen", "--preview-token", json.loads(proposed.stdout)["token"])
    assert result.returncode == 0, result.stderr
    reset = request("preview", "--operation", "reset")
    assert reset.returncode == 0, reset.stderr
    result = request("apply", "--operation", "reset", "--preview-token", json.loads(reset.stdout)["token"])
    assert result.returncode == 0, result.stderr
    assert repos.copilot_account_for("sample", project="sample", strict=True) == "inherited"
    assert target.read_text(encoding="utf-8") == "default_copilot_account: disabled-value\n"


@pytest.mark.parametrize("location", ["worktree", "anchor", "outside", "foreign"])
def test_picker_bootstrap_workspace_is_a_tracked_selected_project_root(native, monkeypatch, capfd, location):
    from agent_worktrees import context_cli, tracking

    monkeypatch.setattr(context_cli._core(), "_INVOCATION_CWD", None)
    workspace = native / "sample-worktree"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    record = tracking.WorktreeRecord(
        worktree_id="wt-sample", branch="worktree/wt-sample", worktree_path=str(workspace),
        repo="foreign" if location == "foreign" else "sample",
        machine="fixture", platform=repos._current_platform(),
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None, sessions=[],
    )
    tracking.save_record(record, cfg.project_dir("sample") / "worktrees" / "wt-sample.yaml")
    monkeypatch.chdir(
        native / "repository" if location == "anchor" else native if location == "outside" else nested,
    )
    before = tree(native)
    assert context_cli.cmd_picker_bootstrap(SimpleNamespace(json=True)) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["project"] == "sample"
    assert payload["workspace"] == (str(workspace) if location == "worktree" else None)
    assert payload["cwd"] != str(workspace)
    assert tree(native) == before


def test_request_errors_never_echo_raw_values(native):
    result = cli("preview", "--context-project", "sample", "--operation", SECRET)
    assert result.returncode != 0 and SECRET not in result.stdout + result.stderr
    with pytest.raises(ConfigurationError):
        apply("sample", "sample", "set", SECRET, "")


@pytest.fixture(params=["base", "marketplace"])
def route_policy(native, monkeypatch, request):
    from agent_worktrees import project_state, registry_paths

    policy = native / "repository" / ".copilot-extensions" / "agent-worktrees"
    if request.param == "marketplace":
        payload = Path(__file__).resolve().parents[1]
        helper = registry_paths._helper()._load_context_helper(payload)
        cell = helper.stamp_context(
            payload_root=payload, plugin_id="agent-worktrees",
            payload_version=json.loads((payload / "plugin.json").read_text(encoding="utf-8"))["version"],
            payload_origin="explicit", expected_namespace_generation=0,
            expected_install_generation=0, durable_home=native / "durable",
            source_descriptor={"source": "github", "repo": "example/marketplace"},
            marketplace_key="fixture", environment={},
        )
        for name in ("repos.yaml", "config.yaml"):
            (Path(cell["pluginRoot"]) / name).write_bytes((native / ".agent-worktrees" / name).read_bytes())
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", cell["installReceipt"])
        monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(payload))
        monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
        project_state.ensure_project_state("sample")
        policy.mkdir(parents=True)
        (policy / "related.yaml").write_text("related:\n  external:\n", encoding="utf-8")
        policy = policy / "marketplaces" / cell["marketplaceId"]
    policy.mkdir(parents=True)
    return policy / "related.yaml"


@pytest.mark.parametrize("route", [
    "related: [broken", "related:\n  external: 42\n",
    "related:\n  external:\n  external:\n",
    "related:\n  external: {role: 42}\n",
    "related:\n  external: {delegate: {via: 42}}\n",
])
def test_invalid_native_route_inspection_stays_unknown_not_valid_empty(native, route_policy, route):
    from agent_worktrees.repos_configuration_catalog import catalog

    route_policy.write_text(route, encoding="utf-8")
    before = tree(native)
    state = next(row["configuration"] for row in catalog("sample")["entries"] if row["id"] == "sample")
    assert state["route"]["status"] == "unknown"
    assert state["findings"][0]["code"] == "native-inspection-unavailable"
    assert tree(native) == before


@pytest.mark.parametrize("entry", [
    {"locus": {"preferred": {"secret": SECRET}}},
    {"locus": {"preferred": [SECRET]}},
    {"locus": {"preferred": None}},
    {"locus": {"preferred": SECRET}},
    {"locus": {"preferred": f"machine:{SECRET}"}},
    {"locus": [SECRET]},
    {"delegate": {"via": {"secret": SECRET}}},
    {"delegate": {"via": SECRET}},
])
def test_route_projection_never_stringifies_or_exposes_invalid_source_values(native, route_policy, entry):
    from agent_worktrees.repos_configuration_catalog import catalog

    route_policy.write_text(yaml.safe_dump({"related": {"external": entry}}), encoding="utf-8")
    before = tree(native)
    result = catalog("sample")
    state = next(row["configuration"] for row in result["entries"] if row["id"] == "sample")
    assert state["route"]["status"] == "unknown"
    assert state["findings"][0]["code"] == "native-inspection-unavailable"
    assert SECRET not in json.dumps(result)
    assert tree(native) == before


@pytest.mark.parametrize("entry", [
    None, 42, SECRET, {"base_repo": "false"}, {"base_repo": {"secret": SECRET}},
])
def test_invalid_project_entry_with_matching_route_is_a_sanitized_finding(native, route_policy, entry):
    from agent_worktrees import installer
    from agent_worktrees.repos_configuration_catalog import catalog

    route_policy.write_text("related:\n  external:\n", encoding="utf-8")
    installer.projects_yaml_path().write_text(
        yaml.safe_dump({"projects": {"external": entry}}), encoding="utf-8",
    )
    before = tree(native)
    result = catalog("sample")
    assert result["entries"]
    for row in result["entries"]:
        assert row["configuration"]["route"]["status"] == "unknown"
        assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
    assert SECRET not in json.dumps(result)
    assert tree(native) == before


def test_native_minimal_related_link_is_valid(native, route_policy):
    from agent_worktrees.repos_configuration_catalog import catalog
    from agent_worktrees.repos_configuration_sources import defaults

    route_policy.write_text("related:\n  external:\n", encoding="utf-8")
    assert defaults("sample").login == "inherited"
    before = tree(native)
    rows = {row["id"]: row for row in catalog("sample")["entries"]}
    assert not rows["sample"]["configuration"]["findings"]
    assert rows["external"]["participation"] == "related"
    assert rows["external"]["configuration"]["route"]["status"] == "present"
    assert tree(native) == before


@pytest.mark.parametrize("content", ["[broken", "stateless: true\nstateless: false\n", "42\n"])
def test_invalid_selected_repository_configuration_is_unknown(native, route_policy, content):
    from agent_worktrees.repos_configuration_catalog import catalog

    route_policy.with_name("config.yaml").write_text(content, encoding="utf-8")
    before = tree(native)
    row = next(row for row in catalog("sample")["entries"] if row["id"] == "sample")
    assert row["configuration"]["binding"]["status"] == "unknown"
    assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
    assert tree(native) == before


def test_machine_route_keeps_the_resolved_peer(native):
    from agent_worktrees.repos_configuration_catalog import catalog

    policy = native / "repository" / ".copilot-extensions" / "agent-worktrees"
    policy.mkdir(parents=True)
    (policy / "related.yaml").write_text(
        "related:\n  external: {locus: {preferred: 'machine:peer-one'}}\n", encoding="utf-8",
    )
    row = next(row for row in catalog("sample")["entries"] if row["id"] == "external")
    assert row["configuration"]["route"]["preferred"] == "machine"
    assert row["configuration"]["route"]["target_machine"] == "peer-one"


def test_invalid_committed_configuration_cannot_hide_behind_valid_disk_copy(native):
    from agent_worktrees import git_ops
    from agent_worktrees.repos_configuration_catalog import catalog

    root = native / "repository"

    def git(*args):
        return git_ops.git(*args, cwd=root, capture=True, timeout=15)

    git("init", "-q", "-b", "main")
    git("config", "core.hooksPath", str(root / "no-hooks"))
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    policy = root / ".copilot-extensions" / "agent-worktrees"
    policy.mkdir(parents=True)
    source = policy / "config.yaml"
    source.write_text("[broken", encoding="utf-8")
    git("add", ".")
    git("-c", "commit.gpgSign=false", "commit", "-qm", "fixture")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    source.write_text("stateless: true\n", encoding="utf-8")
    before = tree(native)
    row = next(row for row in catalog("sample")["entries"] if row["id"] == "sample")
    assert row["configuration"]["binding"]["status"] == "unknown"
    assert row["configuration"]["findings"][0]["code"] == "native-inspection-unavailable"
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


def test_catalog_source_winners_are_independent_per_repository(native):
    path = repos._repos_yaml_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["repos"]["sample"]["copilot_account"] = "chosen"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = request("catalog")
    assert result.returncode == 0, result.stderr
    rows = {row["id"]: row["configuration"] for row in json.loads(result.stdout)["entries"]}
    overridden = rows["sample"]["sources"]
    inherited = rows["stale"]["sources"]
    assert [source["value"] for source in overridden] == ["inherited", "chosen"]
    assert [source["wins"] for source in overridden] == [False, True]
    assert [source["masked"] for source in overridden] == [True, False]
    assert [source["wins"] for source in inherited] == [True]
    assert [source["masked"] for source in inherited] == [False]


@pytest.mark.parametrize("operation", ["set", "reset"])
def test_preference_edit_does_not_modify_aliased_unselected_data(native, operation):
    path = repos._repos_yaml_path()
    path.write_text(
        "metadata: &shared\n  copilot_account: existing\n  custom: unchanged\n"
        "repos: &registrations\n  sample: *shared\n  other: *shared\n"
        "registry_metadata: *registrations\n",
        encoding="utf-8",
    )
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    login = "chosen" if operation == "set" else ""
    token = preview("sample", "sample", operation, login)["token"]
    apply("sample", "sample", operation, login, token)
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["metadata"] == before["metadata"]
    assert after["registry_metadata"] == before["registry_metadata"]
    assert after["repos"]["other"] == before["repos"]["other"]
    assert after["repos"]["sample"]["custom"] == "unchanged"
    assert after["repos"]["sample"].get("copilot_account", "") == login


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
            for missing in paths.values():
                missing.rmdir()
                before_missing = tree(native)
                result = request("catalog", "--workspace", str(paths["sample"]))
                assert result.returncode == 0, result.stderr
                state = next(
                    row["configuration"] for row in json.loads(result.stdout)["entries"]
                    if row["id"] == "sample"
                )
                assert state["pair"]["status"] == "stale"
                assert state["pair"]["writable_workspace"] is False
                assert state["pair"]["workspace"] != str(native / "knowledge")
                assert tree(native) == before_missing
                missing.mkdir()
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


@pytest.mark.parametrize("mode", ["empty", "populated", "bytes-only"])
def test_mapping_report_replacement_is_opaque_without_reacquisition(tmp_path, monkeypatch, mode):
    from agent_worktrees import repos_configuration_sources as sources

    path = tmp_path / "strict.yaml"
    path.write_text("marker: disk\n", encoding="utf-8")
    value = {} if mode == "empty" else {"marker": "available"}
    calls = []
    original_read, original_exists = Path.read_bytes, Path.exists

    def read(selected):
        if selected == path:
            calls.append("native-read")
        return original_read(selected)

    def exists(selected):
        if selected == path:
            pytest.fail("report added a presence probe")
        return original_exists(selected)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "exists", exists)

    def replacement(selected):
        assert selected == path
        calls.append("mapping")
        if mode == "bytes-only":
            sources.read_bytes(selected)
            calls.append("original-bytes")
        return value

    monkeypatch.setattr(sources, "mapping", replacement)
    monkeypatch.setattr(sources, "parse_mapping", lambda *a: pytest.fail("report replayed the parser"))
    report = sources.mapping_report(path)
    assert report.value is value
    assert calls == (["mapping", "native-read", "original-bytes"] if mode == "bytes-only" else ["mapping"])
    assert report.authority.value == "indeterminate"
    assert any(f.reason == "owner-coverage-unavailable" for f in report.findings)
