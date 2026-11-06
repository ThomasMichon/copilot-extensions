"""Opt-in normal CLI/file flow in contained, fresh local state; no cloud writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs


def test_fresh_module_cli_projects_saved_mapping_and_local_override_without_writes(tmp_path):
    candidate = tmp_path / "received.json"
    candidate.write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None,
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-harness", "knowledge_repository_id": "repo-default"},
        ]}},
    }), encoding="utf-8")
    local = tmp_path / "local.json"
    local.write_text(json.dumps({
        "schema": "agent-machines.preferences.local", "version": 1,
        "machine": "example-worker", "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-harness", "knowledge_repository_id": "repo-override"},
        ]}},
    }), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    child = subprocess.run([
        sys.executable, "-m", "agent_machines", "preferences", "preview",
        "--input", str(candidate), "--local-overrides", str(local),
        "--publisher", "example-publisher", "--machine", "example-worker",
        "--harness", "repo-harness",
    ], capture_output=True, text=True, timeout=20, **no_window_kwargs())
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result["knowledge_preference"] == {
        "status": "suggested", "knowledge_repository_id": "repo-override",
    }
    assert result["scope"]["identity_resolution"] == "not-performed"
    assert result["configuration_applied"] is False
    assert result["binding_changed"] is False
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def _cli(*arguments):
    child = subprocess.run(
        [sys.executable, "-m", "agent_machines", "preferences", *map(str, arguments)],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def _policy(path, participant):
    path.mkdir()
    (path / "policy.json").write_text(json.dumps({
        "schema": "agent-machines.preferences.policy", "version": 1,
        "publisher": "example-publisher", "participant": participant,
        "domains": ["repositories"],
    }), encoding="utf-8")
    return path


def test_fresh_cli_publisher_to_receiver_flow_persists_only_explicit_local_state(tmp_path):
    publisher = _policy(tmp_path / "publisher", "example-publisher")
    receiver = _policy(tmp_path / "receiver", "example-receiver")
    candidate, output = tmp_path / "candidate.json", tmp_path / "published.json"
    candidate.write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None,
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-shared"},
            {"harness_repository_id": "repo-second", "knowledge_repository_id": "repo-shared"},
        ]}},
    }), encoding="utf-8")
    publish = ["publish", "--input", candidate, "--output", output, "--state-dir", publisher]
    plan = _cli(*publish)
    assert not output.exists()
    receipt = _cli(*publish, "--apply", "--expect", plan["preview_token"])
    assert receipt["local_published"] and receipt["remote_receipt"] == "not-observed"
    accept = [
        "accept", "--input", output, "--state-dir", receiver,
        "--machine", "example-worker", "--harness", "repo-second",
    ]
    plan = _cli(*accept)
    assert not (receiver / "accepted.json").exists()
    receipt = _cli(*accept, "--apply", "--expect", plan["preview_token"])
    assert receipt["applied"] and not receipt["configuration_applied"] and not receipt["binding_changed"]
    assert json.loads((receiver / "accepted.json").read_text()) == json.loads(output.read_text())
    output.unlink()
    offline = subprocess.run([
        sys.executable, "-m", "agent_machines", "preferences", *map(str, accept),
    ], capture_output=True, text=True, timeout=20, **no_window_kwargs())
    assert offline.returncode == 1, offline.stderr
    result = json.loads(offline.stdout)
    assert result["status"] == "unavailable"
    assert result["projection"]["knowledge_preference"]["knowledge_repository_id"] == "repo-shared"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["candidate.json", "publisher", "receiver"]
    assert sorted(path.name for path in receiver.iterdir()) == ["accepted.json", "exchange.lock", "policy.json"]
    assert sorted(path.name for path in publisher.iterdir()) == ["exchange.lock", "policy.json", "publication.json"]


def test_real_cli_writers_serialize_and_refuse_the_stale_preview(tmp_path):
    receiver = _policy(tmp_path / "receiver", "example-receiver")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None, "domains": {},
    }), encoding="utf-8")
    args = [
        "accept", "--input", candidate, "--state-dir", receiver,
        "--machine", "example-worker", "--harness", "repo-first",
    ]
    token = _cli(*args)["preview_token"]
    command = [
        sys.executable, "-m", "agent_machines", "preferences",
        *map(str, args), "--apply", "--expect", token,
    ]
    children = []
    try:
        for _ in range(2):
            children.append(subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **no_window_kwargs(),
            ))
        results = [(child, *child.communicate(timeout=20)) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
    assert sorted(child.returncode for child in children) == [0, 2]
    failure = next(stderr for child, stdout, stderr in results if child.returncode == 2)
    assert json.loads(failure) == {"ok": False, "error": "preview-changed"}
    assert json.loads((receiver / "accepted.json").read_text())["revision"] == 1


def test_fresh_copilot_cli_refuses_absent_native_owner_without_bootstrap(tmp_path):
    state = _policy(tmp_path / "receiver", "example-receiver")
    (state / "policy.json").write_text(json.dumps({
        "schema": "agent-machines.preferences.policy", "version": 1,
        "publisher": "example-publisher", "participant": "example-receiver", "domains": ["copilot"],
    }), encoding="utf-8")
    (state / "accepted.json").write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None,
        "domains": {"copilot": {"model": "example-model"}},
    }), encoding="utf-8")
    environment = dict(os.environ)
    for key in ("AGENT_HOME", "HOME", "USERPROFILE", "AGENT_RT_ROOT"):
        environment[key] = str(tmp_path / "fresh-home")
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        environment.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-m", "agent_machines", "preferences", "copilot",
         "--state-dir", str(state)],
        capture_output=True, text=True, timeout=20, env=environment, **no_window_kwargs(),
    )
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stderr) == {"ok": False, "error": "native-context-unavailable"}
    assert not (tmp_path / "fresh-home").exists()
    assert sorted(path.name for path in state.iterdir()) == ["accepted.json", "policy.json"]


def test_native_settings_writers_serialize_shared_target_and_refuse_stale_live_state(tmp_path):
    child_script = """
import json,sys,time
from pathlib import Path
from agent_machines.surfaces import settings
home, label = Path(sys.argv[1]), sys.argv[2]
values, expected = json.loads(sys.argv[3]), json.loads(sys.argv[4])
(home / (label + '-entered')).touch()
def precondition(live):
    (home / (label + '-precondition')).touch()
    if live != expected:
        raise ValueError('stale preview')
    if label == 'first':
        (home / 'first-ready').touch()
        deadline = time.monotonic() + 5
        while not (home / 'release-first').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('test barrier')
            time.sleep(0.01)
try:
    result = settings.apply([('ensure-present', values)], home=home, dry_run=False, precondition=precondition)
except ValueError:
    sys.exit(7)
print(json.dumps({'changed': result.changed}))
"""
    home = tmp_path / "settings"
    home.mkdir()
    target = home / "settings.json"
    target.write_text('{"unmanaged":true}', encoding="utf-8")
    children = []
    environment = {
        **os.environ, "HOME": str(tmp_path / "user"), "USERPROFILE": str(tmp_path / "user"),
    }

    def start(label, values, expected):
        child = subprocess.Popen(
            [sys.executable, "-c", child_script, str(home), label, json.dumps(values), json.dumps(expected)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment, **no_window_kwargs(),
        )
        children.append(child)
        return child

    def wait(path: Path):
        deadline = time.monotonic() + 5
        while not path.exists():
            assert time.monotonic() < deadline, f"missing barrier {path.name}"
            time.sleep(0.01)

    try:
        first = start("first", {"model": "first-model"}, {"unmanaged": True})
        wait(home / "first-ready")
        second = start("second", {"effortLevel": "high"}, {"unmanaged": True})
        wait(home / "second-entered")
        time.sleep(0.1)
        assert second.poll() is None and not (home / "second-precondition").exists()
        (home / "release-first").touch()
        output, error = first.communicate(timeout=10)
        assert first.returncode == 0, error
        assert json.loads(output)["changed"]
        second.communicate(timeout=10)
        assert second.returncode == 7
        assert json.loads(target.read_text()) == {"unmanaged": True, "model": "first-model"}
        fresh = start("fresh", {"effortLevel": "high"}, json.loads(target.read_text()))
        output, error = fresh.communicate(timeout=10)
        assert fresh.returncode == 0, error
        assert json.loads(target.read_text()) == {
            "unmanaged": True, "model": "first-model", "effortLevel": "high",
        }
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


def test_copilot_cli_confirmation_survives_a_different_process_hash_seed(tmp_path):
    root, home, state = (tmp_path / name for name in ("base", "home", "state"))
    for path in (root, home, state):
        path.mkdir()
    package = root / ".copilot-extensions" / "agent-machines" / "all" / "native.yaml"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({
        "schema_version": 4, "package": "native",
        "manage": {"copilot.settings": {
            "disposition": "ensure-present",
            "values": {"model": "native-model", "effortLevel": "medium"},
        }},
    }), encoding="utf-8")
    (state / "policy.json").write_text(json.dumps({
        "schema": "agent-machines.preferences.policy", "version": 1,
        "publisher": "example-publisher", "participant": "example-receiver", "domains": ["copilot"],
    }), encoding="utf-8")
    (state / "accepted.json").write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None,
        "domains": {"copilot": {"model": "portable-model", "effortLevel": "high"}},
    }), encoding="utf-8")
    child_script = """
import contextlib,io,json,sys
from pathlib import Path
from unittest.mock import patch
from agent_machines import __main__ as cli, configuration, discover
root, home, state = map(Path, sys.argv[1:4])
report = {'schema': 'agent-worktrees.configuration', 'version': 1, 'context': {
    'repository_id': 'repo-qualified', 'execution_machine': 'logical-worker',
    'installation_cell': 'cell-one', 'binding': 'unbound', 'base_root': str(root),
}}
with patch.object(discover, 'home', lambda: home), \
     patch.object(cli._layout, 'resolve_cwd_repo', lambda: ('base-native', root, root)), \
     patch.object(discover, 'project_scope_repos', lambda *a, **k: [
         discover.RepoCandidate(name='base-native', path=root)]), \
     patch.object(configuration, 'owner_report', lambda _: report):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli.main(['preferences', 'copilot', '--state-dir', str(state), *sys.argv[4:]])
    if code == 0:
        print(json.dumps({'report': json.loads(output.getvalue()), 'field_order': list(configuration.FIELDS)}))
    sys.exit(code)
"""

    def invoke(seed, *arguments):
        result = subprocess.run(
            [sys.executable, "-c", child_script, str(root), str(home), str(state), *arguments],
            env={**os.environ, "PYTHONHASHSEED": str(seed), "HOME": str(home), "USERPROFILE": str(home)},
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    first = invoke(1)
    second = invoke(9)
    assert first["field_order"] != second["field_order"]
    assert first["report"]["preview_token"] == second["report"]["preview_token"]
    assert not (home / ".copilot" / "settings.json").exists()
    assert invoke(9, "--apply", "--expect", first["report"]["preview_token"])["report"]["configuration_applied"]
    assert json.loads((home / ".copilot" / "settings.json").read_text()) == {
        "model": "native-model", "effortLevel": "medium",
    }
