"""Chat policy tests use the real canonical publication engine, not a copy."""

from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agent_chat_service import setup
from agent_chat_service.client import ClientError


@pytest.fixture
def installation(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3] / "libs" / "versioned-runtime"
    spec = importlib.util.spec_from_file_location(
        "chat_setup_test_publication", source / "versioned_runtime_publication.py",
    )
    publication = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, publication)
    spec.loader.exec_module(publication)
    monkeypatch.setattr(setup, "_publication", lambda: publication)
    runtime = SimpleNamespace(
        is_complete=lambda root, version, **kwargs: (root / "versions" / version).is_dir(),
        slot_python=lambda root, version: root / "versions" / version / "python",
    )
    monkeypatch.setattr(setup, "_engine", lambda: SimpleNamespace(runtime=runtime))
    calls = []

    @contextmanager
    def candidate(root, version, **kwargs):
        calls.append(version)
        slot = root / "versions" / version
        slot.mkdir(parents=True, exist_ok=True)
        yield SimpleNamespace(
            slot=slot, payload_hash=hashlib.sha256(version.encode()).hexdigest(),
        )

    monkeypatch.setattr(setup, "client_candidate", candidate)
    config = tmp_path / "client.json"
    config.write_text(json.dumps({
        "schema": "agent-chat.client", "version": 1,
        "actor": {"codename": "example-agent", "machine": "example-machine"},
        "state_dir": str(tmp_path / "state"),
        "endpoint": "https://example.invalid",
        "token_command": [sys.executable, "-c", "raise AssertionError('must not acquire')"],
    }))
    root = tmp_path / "runtime"
    arguments = dict(config=config, version="1.0.0", project=tmp_path / "source",
                     uv=tmp_path / "uv", python=Path(sys.executable))
    return SimpleNamespace(root=root, arguments=arguments, publication=publication, calls=calls)


def tree(root):
    return {
        str(path.relative_to(root)): (
            path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns,
        )
        for path in root.rglob("*") if path.is_file()
    }


def test_fresh_repeat_update_and_preserving_uninstall(installation):
    case = installation
    root, arguments = case.root, case.arguments
    assert setup.status(root, "example")["state"] == "absent"
    assert not root.exists()
    assert setup.install(root, "example", **arguments)["state"] == "selected"
    selected = tree(root)
    assert setup.status(root, "example")["state"] == "selected"
    assert tree(root) == selected
    assert setup.install(root, "example", **arguments)["state"] == "unchanged"
    assert setup.install(root, "example", **{**arguments, "version": "2.0.0"})["state"] == (
        "selected"
    )
    assert (root / "current-version").read_text() == "2.0.0\n"
    assert (root / "last-known-good").read_text() == "2.0.0\n"
    before = arguments["config"].read_bytes()
    assert setup.uninstall(root, "example")["state"] == "unselected"
    assert all(not (root / name).exists() for name in setup._TARGETS)
    assert (root / "versions" / "1.0.0").is_dir()
    assert (root / "versions" / "2.0.0").is_dir()
    assert arguments["config"].read_bytes() == before
    assert setup.status(root, "example")["state"] == "unselected"
    assert setup.uninstall(root, "example")["state"] == "unselected"
    setup.install(root, "example", **arguments)
    assert setup.status(root, "example")["state"] == "selected"
    setup.uninstall(root, "example")


@pytest.mark.parametrize("target", setup._TARGETS)
def test_same_bytes_foreign_replacement_is_never_withdrawn(installation, target):
    case = installation
    setup.install(case.root, "example", **case.arguments)
    path = case.root / target
    foreign = case.root / "foreign"
    foreign.write_bytes(path.read_bytes())
    os.replace(foreign, path)
    before = tree(case.root)
    for operation in (
        lambda: setup.status(case.root, "example"),
        lambda: setup.uninstall(case.root, "example"),
        lambda: setup.install(case.root, "example", **case.arguments),
    ):
        with pytest.raises(ClientError, match="physically replaced"):
            operation()
        assert tree(case.root) == before


def test_existing_root_and_foreign_identity_are_not_adopted(installation):
    case = installation
    case.root.mkdir()
    with pytest.raises(ClientError, match="legacy roots"):
        setup.install(case.root, "example", **case.arguments)
    assert not list(case.root.iterdir())
    case.root.rmdir()
    setup.install(case.root, "example", **case.arguments)
    before = tree(case.root)
    with pytest.raises(ClientError, match="explicit installation"):
        setup.install(case.root, "other", **case.arguments)
    assert tree(case.root) == before


def test_configuration_drift_never_rewrites_or_reconstructs(installation):
    case = installation
    setup.install(case.root, "example", **case.arguments)
    path = case.arguments["config"]
    config = json.loads(path.read_text())
    config["actor"]["codename"] = "changed"
    path.write_text(json.dumps(config))
    calls, before = list(case.calls), tree(case.root)
    with pytest.raises(ClientError, match="configuration drifted"):
        setup.status(case.root, "example")
    with pytest.raises(ClientError, match="migrate or rewrite"):
        setup.install(case.root, "example", **case.arguments)
    assert case.calls == calls and tree(case.root) == before
    assert json.loads(path.read_text()) == config
    # Removal does not require credentials or accept new configuration.
    assert setup.uninstall(case.root, "example")["state"] == "unselected"


@pytest.mark.parametrize("failure", ["candidate", "publication"])
def test_failure_retains_old_selection_and_explicitly_settles_recovery(
    installation, monkeypatch, failure,
):
    case = installation
    setup.install(case.root, "example", **case.arguments)
    previous = {name: (case.root / name).read_bytes() for name in setup._TARGETS}
    if failure == "candidate":
        @contextmanager
        def refuse(*args, **kwargs):
            raise RuntimeError("candidate refused")
            yield

        monkeypatch.setattr(setup, "client_candidate", refuse)
    else:
        original = os.replace
        fired = False

        def fail(source, target):
            nonlocal fired
            original(source, target)
            if Path(target) == case.root / "current-version" and not fired:
                fired = True
                raise RuntimeError("publication refused")

        monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(RuntimeError, match="refused"):
        setup.install(case.root, "example", **{**case.arguments, "version": "2.0.0"})
    if failure == "publication":
        assert setup.status(case.root, "example")["state"] == "recovery-required"
        with pytest.raises(ClientError, match="unresolved"):
            setup.install(case.root, "example", **case.arguments)
        assert setup.recover(case.root, "example")["state"] == "recovered"
    assert {name: (case.root / name).read_bytes() for name in setup._TARGETS} == previous
    assert setup.status(case.root, "example")["state"] == "selected"
    setup.uninstall(case.root, "example")


def test_config_and_state_must_be_outside_runtime_root(installation):
    case = installation
    config = json.loads(case.arguments["config"].read_text())
    config["state_dir"] = str(case.root / "state")
    case.arguments["config"].write_text(json.dumps(config))
    with pytest.raises(ClientError, match="state must be outside"):
        setup.install(case.root, "example", **case.arguments)
    assert not case.root.exists()


@pytest.mark.parametrize("damage", ["missing-backup", "wrong-backup", "backup-state"])
def test_persisted_context_must_match_the_journaled_baseline(installation, damage):
    case = installation
    setup.install(case.root, "example", **case.arguments)
    setup.install(case.root, "example", **{**case.arguments, "version": "2.0.0"})
    data = json.loads((case.root / "installation.json").read_text())
    context = case.root / "operations" / data["history"][-1]["id"] / "copy-context.json"
    document = json.loads(context.read_text())
    entry = document["entries"][0]
    if damage == "missing-backup":
        entry.update(backup=None, state=None)
    elif damage == "wrong-backup":
        entry.update(
            backup=document["entries"][1]["backup"], state=document["entries"][1]["state"],
        )
    else:
        entry["state"]["digest"] = "f" * 64
    context.write_text(json.dumps(document) + "\n")
    before = tree(case.root)
    with pytest.raises(ClientError, match="captured physical binding"):
        setup.uninstall(case.root, "example")
    assert tree(case.root) == before


def test_unsafe_root_ancestor_is_refused_before_directory_creation(installation, monkeypatch):
    case = installation
    original = case.publication.state_path

    def refuse(path, **kwargs):
        if path == case.root:
            raise case.publication.PublicationProofError("unsafe parent")
        return original(path, **kwargs)

    monkeypatch.setattr(case.publication, "state_path", refuse)
    with pytest.raises(ValueError, match="unsafe parent"):
        setup.install(case.root, "example", **case.arguments)
    assert not case.root.exists() and not case.calls


@pytest.mark.parametrize("fresh", [False, True])
def test_foreign_replacement_between_validation_and_capture_is_preserved(
    installation, monkeypatch, fresh,
):
    case = installation
    if not fresh:
        setup.install(case.root, "example", **case.arguments)
    original = case.publication.CopyPublication.publish
    captured = {}

    def publish(transaction):
        validate = transaction.validate_inputs
        first = True

        def replace_after_validation():
            nonlocal first
            validate()
            if first:
                first = False
                target = case.root / "current-version"
                peer = case.root / "peer"
                peer.write_bytes(target.read_bytes() if target.exists() else b"foreign\n")
                os.replace(peer, target)
                captured["state"] = case.publication.state_path(target)

        transaction.validate_inputs = replace_after_validation
        return original(transaction)

    monkeypatch.setattr(case.publication.CopyPublication, "publish", publish)
    with pytest.raises(ClientError, match="physically replaced|Foreign selectors appeared"):
        setup.install(case.root, "example", **{**case.arguments, "version": "2.0.0"})
    assert case.publication.state_path(case.root / "current-version") == captured["state"]


@pytest.mark.parametrize("version", [
    "", "a" * 129, "dev", "../outside", "white space", "non-ascii-\N{SNOWMAN}", None,
])
def test_unmanageable_slot_names_refuse_before_installation(installation, version):
    case = installation
    with pytest.raises(ClientError, match="1-128 ordinary ASCII"):
        setup.install(case.root, "example", **{**case.arguments, "version": version})
    assert not case.root.exists() and not case.calls


def test_maximum_slot_name_remains_manageable(installation):
    case = installation
    version = "a" * 128
    result = setup.install(case.root, "example", **{**case.arguments, "version": version})
    assert result["selection"]["slot"] == version
    assert setup.status(case.root, "example")["selection"]["slot"] == version
    assert setup.uninstall(case.root, "example")["state"] == "unselected"
