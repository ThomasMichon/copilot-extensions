"""Strict selected contexts, revision custody and exact runtime selection."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import shutil
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent_worktrees import repository_continuity as continuity, repository_setup as setup
from test_repository_setup import URL, commit, git
from test_repository_setup import source as source


def context_source(path: Path, value: dict) -> setup.Setup:
    declaration = json.loads((path / setup.DECLARATION).read_text())
    declaration.update(version=2, contexts={"harness-a": value})
    (path / setup.DECLARATION).write_text(json.dumps(declaration))
    commit(path)
    return setup.inspect(path, URL)


def test_context_is_revision_bound_and_source_is_not_opened_on_inspection(source: Path) -> None:
    path = source.parent / "not-yet-provisioned.json"
    selected = context_source(source, {"provider": "local-projection-v1", "source": str(path)})
    assert continuity.selected(selected, "harness-a").source == str(path)
    assert not path.exists()
    selected.verify()
    with pytest.raises(setup.SetupError, match="not declared"):
        continuity.selected(selected, "absent")
    declaration = json.loads((source / setup.DECLARATION).read_text())
    declaration["contexts"]["harness-a"]["source"] = str(source.parent / "changed.json")
    (source / setup.DECLARATION).write_text(json.dumps(declaration))
    with pytest.raises(setup.SetupError):
        selected.verify()


@pytest.mark.parametrize("value", [
    {"provider": "unknown", "source": "/selected"},
    {"provider": "local-projection-v1", "source": "relative"},
    {"provider": "local-projection-v1", "source": 1},
    {"provider": "local-projection-v1", "source": "/selected", "extra": True},
])
def test_context_cannot_invent_provider_or_source_defaults(source: Path, value: dict) -> None:
    with pytest.raises(setup.SetupError):
        context_source(source, value)


def test_selected_ssh_setup_is_explicit_revision_bound_and_old_forms_stay_outbound(source, tmp_path):
    context = {"provider": "local-projection-v1", "source": str(tmp_path / "projection.json")}
    legacy = context_source(source, context)
    assert continuity.selected(legacy, "harness-a").ssh_setup is None
    path = source / setup.DECLARATION
    declaration = json.loads(path.read_text())
    declaration["version"] = 3
    declaration["contexts"]["harness-a"]["ssh_setup"] = str(tmp_path / "ssh-setup.json")
    path.write_text(json.dumps(declaration))
    commit(source)
    current = setup.inspect(source, URL)
    assert continuity.selected(current, "harness-a").ssh_setup == str(tmp_path / "ssh-setup.json")
    assert not (tmp_path / "ssh-setup.json").exists()
    declaration["contexts"]["harness-a"]["ssh_setup"] = "relative"
    path.write_text(json.dumps(declaration))
    commit(source)
    with pytest.raises(setup.SetupError, match="SSH setup"):
        setup.inspect(source, URL)


def test_old_fleet_refuses_selected_ssh_before_bootstrap_or_private_request(source, tmp_path, monkeypatch):
    context_source(source, {
        "provider": "local-projection-v1", "source": str(tmp_path / "projection.json"),
    })
    path = source / setup.DECLARATION
    declaration = json.loads(path.read_text())
    declaration["version"] = 3
    declaration["contexts"]["harness-a"]["ssh_setup"] = str(tmp_path / "absent.json")
    path.write_text(json.dumps(declaration))
    commit(source)
    calls = []

    def runtime(arguments):
        calls.append(arguments)
        assert arguments == ["capabilities"]
        return {"schema": "fleet-manager.capabilities", "version": 1,
                "capabilities": [continuity.CAPABILITY, "local-projection-v1"]}

    monkeypatch.setattr(continuity, "_invoke", runtime)
    with pytest.raises(setup.SetupError, match="lacks selected SSH"):
        continuity.run(setup.inspect(source, URL), "harness-a", apply=True)
    assert calls == [["capabilities"]]


@pytest.mark.parametrize("failure", [None, "profile", "changed-request"])
def test_normal_ssh_setup_retains_source_admission_and_original_bootstrap_order(
    source, tmp_path, monkeypatch, failure,
):
    from agent_worktrees import repository_ssh_setup as ssh_setup

    context_source(source, {
        "provider": "local-projection-v1", "source": str(tmp_path / "projection.json"),
    })
    path = source / setup.DECLARATION
    declaration = json.loads(path.read_text())
    declaration["version"] = 3
    declaration["contexts"]["harness-a"]["ssh_setup"] = str(tmp_path / "request.json")
    path.write_text(json.dumps(declaration))
    commit(source)
    selected_setup = setup.inspect(source, URL)
    events = []
    identity = SimpleNamespace(source_commit="a" * 40, source_tree="b" * 40)
    selection = {
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "runtime_version": "0.1.0.profile.abc", "payload_hash": "c" * 64,
    }
    description = {
        "schema": "fleet-manager.continuity-ssh-setup", "version": 1,
        "snapshot": str(tmp_path / "snapshot"), "root": str(tmp_path / "installation"),
        "selection": selection, "request_digest": "d" * 64, "host": {},
    }
    project = SimpleNamespace(identity=identity, project={"application": "application"}, payload=tmp_path)
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "tools"))
    import runtime_application_project as application
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    metadata = tomllib.loads((root / "fleet-manager" / "pyproject.toml").read_text())["project"]
    archive = tmp_path / "fleet_manager-0.1.0-py3-none-any.whl"
    archive.write_bytes(b"fixture wheel reference; never installed")
    emitted = tmp_path / "application"
    callbacks = application.install_callbacks("fleet-manager", extras=("gateway-host", "ssh-client"))
    application.emit(
        emitted, distribution="fleet-manager", version="0.1.0", requires_python=">=3.10",
        commands=metadata["scripts"], wheels={"fleet-manager": archive},
        construction={"prepared": True}, setup=callbacks[0], start=callbacks[1],
    )

    @contextmanager
    def inputs(*_args):
        events.append("source-admission")
        yield project, object()

    def install(*_args, **options):
        assert options["expected_version"] == selection["runtime_version"]
        assert options["expected_hash"] == selection["payload_hash"]
        events.append("install")
        return {"state": "installed"}

    owner = SimpleNamespace(
        project_inputs=inputs,
        native_selection=lambda *_args: {
            "runtime_version": selection["runtime_version"], "payload_hash": selection["payload_hash"],
            "extras": ["gateway-host"] if failure == "profile" else ["gateway-host", "ssh-client"],
        },
        application_inputs=lambda *_args: {"prepared": True},
        _member=lambda *_args: emitted,
        _read=lambda path: json.loads(path.read_text()),
        install_application=install,
    )
    monkeypatch.setattr(ssh_setup, "_installer", lambda: owner)
    monkeypatch.setattr(ssh_setup, "status", lambda *_args, **_kwargs: "install" in events)
    monkeypatch.setattr(ssh_setup.shutil, "which", lambda *_args: sys.executable)

    def runtime(arguments):
        if arguments == ["capabilities"]:
            return {"schema": "fleet-manager.capabilities", "version": 1, "capabilities": [
                continuity.CAPABILITY, "local-projection-v1", ssh_setup.CAPABILITY,
                "continuity-machine-reference-v1",
            ]}
        if arguments[0] == "ssh-setup":
            events.append("prepare" if "--apply" in arguments else "inspect")
            return description | ({"request_digest": "changed"} if (
                failure == "changed-request" and "--apply" in arguments
            ) else {})
        assert arguments[0] == "bootstrap"
        events.append("bootstrap")
        return {"status": "ready", "stage": "connected"}

    monkeypatch.setattr(continuity, "_invoke", runtime)
    if failure is not None:
        with pytest.raises(setup.SetupError):
            continuity.run(selected_setup, "harness-a", apply=True)
        assert "install" not in events
        if failure == "profile":
            assert "bootstrap" not in events
    else:
        result = continuity.run(selected_setup, "harness-a", apply=True)
        assert result.status == "ready" and "converged" in result.message
        assert events.index("source-admission") < events.index("bootstrap")
        assert events.index("bootstrap") < events.index("prepare") < events.index("install")
        assert events.count("install") == 1
        events.clear()
        monkeypatch.setattr(ssh_setup, "status", lambda *_args, **_kwargs: True)
        assert continuity.run(selected_setup, "harness-a", apply=False).status == "ready"
        assert "prepare" not in events and "install" not in events


def test_consumer_selects_its_engine_interpreter_with_bounded_native_custody(monkeypatch) -> None:
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"status":"ready","stage":"connected"}', "")
    monkeypatch.setattr(continuity, "run_caller_owned", run)
    assert continuity._invoke(["bootstrap"]) == {"status": "ready", "stage": "connected"}
    argv, options = calls[0]
    assert argv == [sys.executable, "-I", "-m", "fleet_manager", "bootstrap"]
    assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 16384
    assert options["timeout"] == 30


@pytest.mark.parametrize("ready,complete,stage", [
    (True, True, None), (False, True, "not-running"), (True, False, "incomplete-construction"),
])
def test_setup_status_requires_live_convergence_after_installed_receipt(
    tmp_path, monkeypatch, ready, complete, stage,
):
    from agent_worktrees import repository_ssh_setup as ssh_setup

    root = tmp_path / "installation"
    root.mkdir()
    (root / "current-version").write_text("0.1.0\n")
    description = {
        "root": str(root), "selection": {"runtime_version": "0.1.0", "payload_hash": "a" * 64},
    }
    owner = SimpleNamespace(engine=SimpleNamespace(runtime=SimpleNamespace(
        current_version=lambda *_args: "0.1.0",
        is_complete=lambda *_args, **_kwargs: complete,
    )))
    monkeypatch.setattr(continuity, "_invoke", lambda *_args, **_kwargs: (
        {"ready": True, "selected": True, "revision": 7} if ready else {"state": "not-running"}
    ))
    if stage is None:
        assert ssh_setup.status(description, owner, require_ready=True)
    else:
        assert not ssh_setup.status(description, owner)
        with pytest.raises(setup.SetupError, match=stage):
            ssh_setup.status(description, owner, require_ready=True)


def test_two_repositories_with_same_context_keep_separate_stable_machine_state(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    first = context_source(source, {
        "provider": "local-projection-v1", "source": str(tmp_path / "first-projection.json")})
    other = tmp_path / "second-harness"
    shutil.copytree(source, other)
    other_url = "https://example.invalid/team/other-harness.git"
    git(other, "remote", "set-url", "origin", other_url)
    declaration = json.loads((other / setup.DECLARATION).read_text())
    declaration["contexts"]["harness-a"]["source"] = str(tmp_path / "second-projection.json")
    (other / setup.DECLARATION).write_text(json.dumps(declaration))
    commit(other)
    second = setup.inspect(other, other_url)
    operations = []

    def runtime(arguments: list[str]) -> dict:
        if arguments == ["capabilities"]:
            return {"schema": "fleet-manager.capabilities", "version": 1,
                    "capabilities": [continuity.CAPABILITY, "local-projection-v1"]}
        root = Path(arguments[arguments.index("--state-root") + 1])
        state = root / "machine.json"
        if state.exists():
            return {"status": "ready", "stage": "connected"}
        if "--apply" not in arguments:
            return {"status": "needed", "stage": "enrollment"}
        root.mkdir()
        operations.append(arguments[arguments.index("--source") + 1])
        state.write_text(json.dumps({"original_operation": operations[-1]}))
        return {"status": "ready", "stage": "connected"}

    monkeypatch.setattr(continuity, "_invoke", runtime)
    continuity.run(first, "harness-a", apply=True)
    first_root = continuity._state_root(first, "harness-a")
    original = (first_root / "machine.json").read_bytes()
    continuity.run(second, "harness-a", apply=True)
    assert continuity._state_root(second, "harness-a") != first_root
    assert len(operations) == 2
    (source / "new-revision.txt").write_text("Same harness, later revision.")
    commit(source)
    later = setup.inspect(source, URL)
    assert later.source.commit != first.source.commit
    continuity.run(later, "harness-a", apply=True)
    assert continuity._state_root(later, "harness-a") == first_root
    assert (first_root / "machine.json").read_bytes() == original
    assert len(operations) == 2
