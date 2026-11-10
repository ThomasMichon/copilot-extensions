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


def test_fresh_repository_projection_cli_uses_only_accepted_state_without_native_owner(tmp_path):
    state = _policy(tmp_path / "state", "example-receiver")
    (state / "accepted.json").write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 2, "parent": 1,
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-shared"},
            {"harness_repository_id": "repo-second", "knowledge_repository_id": "repo-shared"},
        ]}},
        "machines": {"example-worker-wsl": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-guest"},
        ]}}},
    }), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in state.iterdir()}
    for harness, machine, target in (
        ("repo-first", "example-worker", "repo-shared"),
        ("repo-second", "example-worker", "repo-shared"),
        ("repo-first", "example-worker-wsl", "repo-guest"),
    ):
        result = _cli(
            "repositories", "--state-dir", state, "--machine", machine, "--harness", harness,
        )
        assert result["knowledge_preference"]["knowledge_repository_id"] == target
        assert result["scope"]["identity_resolution"] == "not-performed"
        assert result["read_only"] and not result["configuration_applied"] and not result["binding_changed"]
    invalid = subprocess.run([
        sys.executable, "-I", "-m", "agent_machines", "preferences", "repositories",
        "--state-dir", str(state), "--machine", "example-worker", "--harness", "repo-first", "--apply",
    ], capture_output=True, text=True, timeout=20, **no_window_kwargs())
    assert invalid.returncode == 2
    assert "unrecognized arguments: --apply" in invalid.stderr
    assert {path.name: path.read_bytes() for path in state.iterdir()} == before


def _carrier_cli(*arguments, code=0):
    child = subprocess.run(
        [sys.executable, "-I", "-m", "agent_machines", "preferences", "carrier", *map(str, arguments)],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert child.returncode == code, (child.stdout, child.stderr)
    return json.loads(child.stderr if code == 2 else child.stdout)


def _carrier_state(path, participant, replica):
    _policy(path, participant)
    replica.mkdir()
    (path / "carrier.json").write_text(json.dumps({
        "schema": "agent-machines.preferences.carrier", "version": 1,
        "provider": "onedrive", "account": "example-account", "directory": str(replica),
        "participants": ["example-publisher", "example-receiver"],
    }), encoding="utf-8")
    return path


def _carrier_inputs(tmp_path):
    publisher_replica, receiver_replica = tmp_path / "publisher-replica", tmp_path / "receiver-replica"
    publisher = _carrier_state(tmp_path / "publisher", "example-publisher", publisher_replica)
    receiver = _carrier_state(tmp_path / "receiver", "example-receiver", receiver_replica)
    candidate, local = tmp_path / "candidate.json", tmp_path / "local.json"
    original = {
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None,
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-shared"},
            {"harness_repository_id": "repo-second", "knowledge_repository_id": "repo-shared"},
        ]}},
    }
    candidate.write_text(json.dumps(original), encoding="utf-8")
    local.write_text(json.dumps({
        "schema": "agent-machines.preferences.local", "version": 1, "machine": "example-worker",
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-local"},
        ]}},
    }), encoding="utf-8")
    publish = ["publish", "--input", candidate, "--state-dir", publisher]
    receive = [
        "receive", "--state-dir", receiver, "--machine", "example-worker",
        "--harness", "repo-first", "--local-overrides", local,
    ]
    return publisher, receiver, publisher_replica, receiver_replica, candidate, original, publish, receive


def test_fresh_carrier_cli_two_local_replicas_preserve_mapping_override(tmp_path):
    publisher, receiver, publisher_replica, receiver_replica, _, _, publish, receive = _carrier_inputs(tmp_path)
    first = _carrier_cli(*publish)
    assert not list(publisher_replica.iterdir()) and not list(receiver_replica.iterdir())
    sent = _carrier_cli(*publish, "--apply", "--expect", first["preview_token"])
    assert sent["local_published"] and sent["carrier"]["peer_receipt"] == "not-observed"
    output = publisher_replica / "preferences.json"
    received = receiver_replica / "preferences.json"
    # A fixture copies replicas; this does not exercise a synchronization provider.
    received.write_bytes(output.read_bytes())
    preview = _carrier_cli(*receive)
    assert not (receiver / "accepted.json").exists()
    accepted = _carrier_cli(*receive, "--apply", "--expect", preview["preview_token"])
    assert accepted["projection"]["knowledge_preference"]["knowledge_repository_id"] == "repo-local"
    assert accepted["projection"]["provenance"]["repositories.knowledge:repo-first"] == "local-machine"
    assert accepted["projection"]["values"]["repositories"]["knowledge"] == [
        {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-local"},
        {"harness_repository_id": "repo-second", "knowledge_repository_id": "repo-shared"},
    ]
    assert not any(result["configuration_applied"] or result["binding_changed"] for result in (sent, accepted))
    assert sorted(path.name for path in publisher.iterdir()) == [
        "carrier.json", "exchange.lock", "policy.json", "publication.json",
    ]
    assert sorted(path.name for path in receiver.iterdir()) == [
        "accepted.json", "carrier.json", "exchange.lock", "policy.json",
    ]


def test_native_carrier_withdrawal_preserves_local_override(tmp_path):
    publisher, receiver, publisher_replica, receiver_replica, candidate, original, publish, receive = (
        _carrier_inputs(tmp_path)
    )
    (publisher / "publication.json").write_text(json.dumps(original), encoding="utf-8")
    (receiver / "accepted.json").write_text(json.dumps(original), encoding="utf-8")
    output = publisher_replica / "preferences.json"
    received = receiver_replica / "preferences.json"
    output.write_text(json.dumps(original), encoding="utf-8")
    candidate.write_text(json.dumps({**original, "revision": 2, "parent": 1, "domains": {}}), encoding="utf-8")
    plan = _carrier_cli(*publish)
    _carrier_cli(*publish, "--apply", "--expect", plan["preview_token"])
    received.write_bytes(output.read_bytes())
    plan = _carrier_cli(*receive)
    withdrawn = _carrier_cli(*receive, "--apply", "--expect", plan["preview_token"])
    assert withdrawn["projection"]["values"]["repositories"]["knowledge"] == [
        {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-local"},
    ]
    assert not withdrawn["configuration_applied"] and not withdrawn["binding_changed"]
    assert json.loads((receiver / "accepted.json").read_bytes())["revision"] == 2
    assert json.loads((publisher / "publication.json").read_bytes())["revision"] == 2


def test_native_carrier_offline_and_old_replica_do_not_undo_accepted_withdrawal(tmp_path):
    _, receiver, _, receiver_replica, _, original, _, receive = _carrier_inputs(tmp_path)
    withdrawn = {**original, "revision": 2, "parent": 1, "domains": {}}
    (receiver / "accepted.json").write_text(json.dumps(withdrawn), encoding="utf-8")
    received = receiver_replica / "preferences.json"
    receiver_replica.rmdir()
    offline = _carrier_cli(*receive, code=1)
    assert offline["status"] == "carrier-unavailable"
    assert offline["projection"]["projected_revision"] == 2
    assert offline["projection"]["knowledge_preference"]["knowledge_repository_id"] == "repo-local"
    receiver_replica.mkdir()
    received.write_text(json.dumps(original), encoding="utf-8")
    stale = _carrier_cli(*receive, code=1)
    assert stale["status"] == "older-revision" and not stale["eligible"]
    assert json.loads((receiver / "accepted.json").read_bytes())["revision"] == 2
    assert not any(result["configuration_applied"] or result["binding_changed"] for result in (
        offline, stale,
    ))
    assert sorted(path.name for path in receiver.iterdir()) == [
        "accepted.json", "carrier.json", "policy.json",
    ]


def test_native_carrier_closed_stdout_keeps_unknown_acknowledgement_and_exit_two(tmp_path):
    replica = tmp_path / "replica"
    state = _carrier_state(tmp_path / "publisher", "example-publisher", replica)
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None, "domains": {},
    }), encoding="utf-8")
    arguments = ["publish", "--input", candidate, "--state-dir", state]
    plan = _carrier_cli(*arguments)
    reader, writer = os.pipe()
    os.close(reader)
    try:
        child = subprocess.run([
            sys.executable, "-I", "-m", "agent_machines", "preferences", "carrier",
            *map(str, arguments), "--apply", "--expect", plan["preview_token"],
        ], stdout=writer, stderr=subprocess.PIPE, text=True, timeout=20, **no_window_kwargs())
    finally:
        os.close(writer)
    assert child.returncode == 2, child.stderr
    failure = json.loads(child.stderr)
    assert failure["error"] == "response-output-unavailable" and failure["applied"] is None
    assert json.loads((replica / "preferences.json").read_bytes())["revision"] == 1
    assert json.loads((state / "publication.json").read_bytes())["revision"] == 1


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


def test_separate_cli_processes_adopt_through_real_dispatch_with_fixture_resolved_context(tmp_path):
    state = _policy(tmp_path / "receiver", "example-receiver")
    (state / "policy.json").write_text(json.dumps({
        "schema": "agent-machines.preferences.policy", "version": 1,
        "publisher": "example-publisher", "participant": "example-receiver", "domains": ["telemetry"],
    }), encoding="utf-8")
    (state / "accepted.json").write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None,
        "domains": {"telemetry": {"capture": "retained-local"}},
    }), encoding="utf-8")
    native, directory = tmp_path / "native", tmp_path / "spool"
    native.mkdir()
    (directory / "retained").mkdir(parents=True)
    source = Path(__file__).resolve().parents[3] / "agent-dispatch" / "src"
    script = """
import sys
from agent_machines import __main__ as cli, configuration, preference_telemetry
source, root = sys.argv[1:3]
configuration.owner_report = lambda path: {
    'schema': 'agent-worktrees.configuration', 'version': 1,
    'context': {'repository_id': 'repo-example', 'execution_machine': 'example-worker',
                'installation_cell': 'example-cell', 'binding': 'unbound', 'base_root': root},
    'repository_machine_root': root,
}
preference_telemetry._prefix = lambda producer: [
    sys.executable, '-c',
    "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('agent_dispatch',run_name='__main__')",
    source,
]
sys.exit(cli.main(sys.argv[3:]))
"""
    environment = dict(os.environ, AGENT_DISPATCH_INSTALL_DIR=str(native))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT", "AGENT_DISPATCH_TELEMETRY_SINK"):
        environment.pop(key, None)
    arguments = [
        "preferences", "telemetry", "--state-dir", str(state), "--producer", "agent-dispatch",
        "--directory", str(directory),
    ]

    def call(extra):
        return subprocess.run(
            [sys.executable, "-c", script, str(source), str(tmp_path), *arguments, *extra],
            env=environment, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )

    target = native / "telemetry.json"
    preview = call([])
    assert preview.returncode == 0, preview.stderr
    assert not target.exists()
    receipt = call(["--apply", "--expect", json.loads(preview.stdout)["preview_token"]])
    assert receipt.returncode == 0, receipt.stderr
    assert json.loads(receipt.stdout)["configuration_applied"]
    assert json.loads(target.read_bytes()) == {"retained_spool": {"directory": str(directory)}}
    assert list((directory / "retained").iterdir()) == []


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
