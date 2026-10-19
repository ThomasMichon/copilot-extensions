"""Publishing refuses unreadable Terminal documents without pruning outputs."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from worktree_manager import local_profiles as lp, terminal_fragment as tf
from worktree_manager.terminal_deploy_inputs import TerminalInputs, read_optional_json


@pytest.fixture
def publication(monkeypatch, tmp_path):
    home, fragments, state = tmp_path / "home", tmp_path / "fragments", tmp_path / "state"
    (home / ".demo").mkdir(parents=True)
    (home / ".demo" / "config.yaml").write_text("machine: host\n", encoding="utf-8")
    (fragments / "AgentWorktrees").mkdir(parents=True)
    state.mkdir()
    fragment = tf.build_fragment([tf.ProjectInput("demo", "Demo")], "host").fragment()
    guids = [profile["guid"] for profile in fragment["profiles"]]
    paths = {
        "fragment": fragments / "AgentWorktrees" / "agent-worktrees.json",
        "state": state / "state.json",
        "settings": state / "settings.json",
        "foreign": fragments / "foreign.json",
    }
    documents = {
        "fragment": fragment, "state": {"generatedProfiles": guids},
        "settings": {"profiles": {"list": [
            {"guid": guid, "source": "AgentWorktrees"} for guid in guids
        ]}},
        "foreign": {"profiles": []},
    }
    for kind, path in paths.items():
        path.write_text(json.dumps(documents[kind]), encoding="utf-8")
    monkeypatch.setattr(lp.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lp.harness_state, "home", lambda: home)
    monkeypatch.setattr(tf, "_wt_fragments_dir", lambda: fragments)
    monkeypatch.setattr(tf, "_wt_local_state_dir", lambda: state)
    monkeypatch.setattr(lp, "refresh_shortcuts", lambda projects: 0)
    return paths


@pytest.mark.parametrize("kind", ["settings", "state", "fragment", "foreign"])
@pytest.mark.parametrize("damage", ["json", "denied", "shape", "null", "duplicate"])
def test_invalid_terminal_input_preserves_every_output(publication, monkeypatch, capsys, kind, damage):
    target = publication[kind]
    if damage != "denied":
        bad_shape = {
            "settings": {"profiles": {"list": ["invalid"]}},
            "state": {"generatedProfiles": "invalid"},
            "fragment": {"profiles": {}}, "foreign": {"profiles": {}},
        }
        text = {
            "json": "{", "shape": json.dumps(bad_shape[kind]),
            "null": "null", "duplicate": '{"profiles": [], "profiles": []}',
        }[damage]
        target.write_text(text, encoding="utf-8")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()}
    original = Path.read_bytes
    if damage == "denied":
        def denied(path):
            if path == target:
                raise PermissionError("fixture Terminal input denied")
            return original(path)
        monkeypatch.setattr(Path, "read_bytes", denied)
    assert lp.main(["demo"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert {path: (original(path), path.stat().st_mtime_ns) for path in publication.values()} == before
    assert read_optional_json(target, kind="fragment" if kind == "foreign" else kind) is None


@pytest.mark.parametrize("kind", ["settings", "state"])
@pytest.mark.parametrize("contents", ['{"profiles": {"list": []}}', "{"])
def test_input_drift_after_planning_does_not_publish(publication, monkeypatch, capsys, kind, contents):
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()}
    target = publication[kind]
    apply = tf._apply_deploy_plan

    def interleaved(plan, state, inputs=None):
        target.write_text(contents, encoding="utf-8")
        return apply(plan, state, inputs)

    monkeypatch.setattr(tf, "_apply_deploy_plan", interleaved)
    assert lp.main(["demo"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert target.read_text(encoding="utf-8") == contents
    for path, original in before.items():
        if path != target:
            assert (path.read_bytes(), path.stat().st_mtime_ns) == original


def test_fragment_inventory_drift_refuses_publication(publication, monkeypatch, capsys):
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()}
    apply = tf._apply_deploy_plan

    def interleaved(plan, state, inputs=None):
        publication["foreign"].with_name("new.json").write_text('{"profiles": []}', encoding="utf-8")
        return apply(plan, state, inputs)

    monkeypatch.setattr(tf, "_apply_deploy_plan", interleaved)
    assert lp.main(["demo"]) == 1
    assert "inventory changed" in json.loads(capsys.readouterr().out)["error"]
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()} == before


@pytest.mark.parametrize("during_verify", [False, True])
def test_denied_fragment_directory_never_authorizes_orphan_pruning(
    publication, monkeypatch, capsys, during_verify,
):
    directory = publication["foreign"].parent / "private"
    directory.mkdir()
    guid = "{00000001-0000-1000-8000-000000000001}"
    (directory / "foreign.json").write_text(json.dumps({"profiles": [{"guid": guid}]}), encoding="utf-8")
    state = publication["state"]
    data = json.loads(state.read_text(encoding="utf-8"))
    data["generatedProfiles"].append(guid)
    state.write_text(json.dumps(data), encoding="utf-8")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()}
    scandir, apply = os.scandir, tf._apply_deploy_plan

    def denied(path):
        if Path(path) == directory:
            raise PermissionError("fixture fragment directory denied")
        return scandir(path)

    if during_verify:
        def interleaved(plan, local_state, inputs=None):
            monkeypatch.setattr(os, "scandir", denied)
            return apply(plan, local_state, inputs)
        monkeypatch.setattr(tf, "_apply_deploy_plan", interleaved)
    else:
        monkeypatch.setattr(os, "scandir", denied)
    assert lp.main(["demo"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()} == before


@pytest.mark.parametrize(("target", "text"), [
    ("projects", "projects: {demo: {}, peer: {}}\nprojects: {}\n"),
    ("projects", "projects:\n  demo: {display_name: Demo}\n  demo: {}\n"),
    ("projects", "projects: !!map []\n"),
    ("repos", "repos: {demo: {}}\nrepos: {}\n"),
    ("config", "terminal_profiles: []\nterminal_profiles: []\n"),
    ("roster", "machines: {host: {}}\nmachines: {}\n"),
    ("roster", "machines:\n  host:\n    ssh: {ready: true}\n    ssh: {}\n"),
])
def test_ambiguous_yaml_inputs_do_not_publish(publication, tmp_path, capsys, target, text):
    home = lp.harness_state.home()
    registry = home / ".agent-worktrees"
    registry.mkdir()
    if target == "config":
        path = home / ".demo" / "config.yaml"
    elif target == "roster":
        repo = tmp_path / "repo"
        path = repo / ".agent-worktrees" / "machines.yaml"
        path.parent.mkdir(parents=True)
        (registry / "projects.yaml").write_text(
            json.dumps({"projects": {"demo": {"anchor": str(repo)}}}), encoding="utf-8",
        )
    else:
        path = registry / f"{target}.yaml"
    path.write_text(text, encoding="utf-8")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()}
    assert lp.main(["demo", "--machine", "host"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()} == before


def test_fragment_inventory_includes_uppercase_extension(tmp_path):
    path = tmp_path / "foreign.JSON"
    path.write_text('{"profiles": []}', encoding="utf-8")
    assert TerminalInputs().fragments(tmp_path) == {path}


@pytest.mark.parametrize("kind", ["registry", "project-config", "canonical-roster", "roster-root"])
def test_dangling_directory_inputs_never_authorize_profile_removal(
    publication, tmp_path, capsys, kind,
):
    home = lp.harness_state.home()
    if kind == "registry":
        target = home / ".agent-worktrees"
    elif kind == "project-config":
        target = home / ".demo"
        (target / "config.yaml").unlink()
        target.rmdir()
    else:
        repo = tmp_path / "repo"
        registry = home / ".agent-worktrees" / "projects.yaml"
        registry.parent.mkdir()
        registry.write_text(json.dumps({"projects": {"demo": {"anchor": str(repo)}}}), encoding="utf-8")
        if kind == "canonical-roster":
            repo.mkdir()
            (repo / "machines.yaml").write_text("machines:\n  host: {}\n", encoding="utf-8")
            target = repo / ".agent-worktrees"
        else:
            target = repo
    try:
        target.symlink_to(tmp_path / "missing-directory", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Directory symlink creation unavailable: {error}")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()}
    assert lp.main(["demo", "--machine", "host"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in publication.values()} == before


@pytest.mark.parametrize("cleanup", [False, True])
def test_valid_jsonc_settings_preserve_original_bytes(publication, capsys, cleanup):
    path = publication["settings"]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["help"] = 'https://example.invalid/"quoted"/*literal*/'
    if cleanup:
        data["profiles"]["list"].append({"guid": "{stale}", "source": "AgentWorktrees"})
    text = "// Terminal settings\n" + json.dumps(data)[:-1] + ", /* trailing */ }\n"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert lp.main(["demo"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified_current"
    if cleanup:
        assert json.loads(path.read_text(encoding="utf-8"))["help"] == data["help"]
        backups = list(path.parent.glob("settings.json.wt-backup-*"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == before[0]
    else:
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("kind", ["state", "settings"])
def test_absent_optional_terminal_input_is_supported(tmp_path, kind):
    path = tmp_path / f"{kind}.json"
    assert TerminalInputs().read(path, kind=kind) is None
    try:
        path.symlink_to(tmp_path / "absent.json")
    except OSError as error:
        pytest.skip(f"Symlink creation unavailable: {error}")
    with pytest.raises(ValueError, match="Cannot read valid Terminal input"):
        TerminalInputs().read(path, kind=kind)
