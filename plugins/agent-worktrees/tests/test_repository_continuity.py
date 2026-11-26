"""Strict selected contexts, revision custody and exact runtime selection."""

from __future__ import annotations

import json
import base64
import hashlib
from pathlib import Path
import subprocess
import sys
import shutil
from contextlib import contextmanager
from types import SimpleNamespace
from collections.abc import Iterator
from typing import Any, cast

import pytest

from agent_worktrees import repository_continuity as continuity, repository_setup as setup
from test_repository_setup import URL, commit, git
from test_repository_setup import source as source


def context_source(path: Path, value: dict[str, Any]) -> setup.Setup:
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
def test_context_cannot_invent_provider_or_source_defaults(source: Path, value: dict[str, Any]) -> None:
    with pytest.raises(setup.SetupError):
        context_source(source, value)


@pytest.mark.parametrize("version", [3, 4, 5])
def test_selected_ssh_setup_is_explicit_revision_bound_and_old_forms_stay_outbound(
    source: Path, tmp_path: Path, version: int,
) -> None:
    context = {"provider": "local-projection-v1", "source": str(tmp_path / "projection.json")}
    legacy = context_source(source, context)
    assert continuity.selected(legacy, "harness-a").ssh_setup is None
    path = source / setup.DECLARATION
    declaration = json.loads(path.read_text())
    declaration["version"] = version
    declaration["contexts"]["harness-a"]["ssh_setup"] = str(tmp_path / "ssh-setup.json")
    if version >= 4:
        declaration["contexts"]["harness-a"]["ssh_key"] = None
    path.write_text(json.dumps(declaration))
    commit(source)
    current = setup.inspect(source, URL)
    assert continuity.selected(current, "harness-a").ssh_setup == str(tmp_path / "ssh-setup.json")
    assert continuity.selected(current, "harness-a").ssh_key is None
    assert not (tmp_path / "ssh-setup.json").exists()
    declaration["contexts"]["harness-a"]["ssh_setup"] = "relative"
    path.write_text(json.dumps(declaration))
    commit(source)
    with pytest.raises(setup.SetupError, match="SSH setup"):
        setup.inspect(source, URL)


def test_old_fleet_refuses_selected_ssh_before_bootstrap_or_private_request(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def runtime(arguments: list[str]) -> dict[str, Any]:
        calls.append(arguments)
        assert arguments == ["capabilities"]
        return {"schema": "fleet-manager.capabilities", "version": 1,
                "capabilities": [continuity.CAPABILITY, "local-projection-v1"]}

    monkeypatch.setattr(continuity, "_invoke", runtime)
    with pytest.raises(setup.SetupError, match="lacks selected SSH"):
        continuity.run(setup.inspect(source, URL), "harness-a", apply=True)
    assert calls == [["capabilities"]]


@pytest.mark.parametrize("failure", [
    None, "profile", "changed-request", "installer", "pin-capability", "already-ready",
    "vault-order", "vault-locked", "vault-sync", "vault-pin", "vault-healthy",
    "vault-capability", "vault-install-unknown", "vault-location",
    "vault-healthy-request", "vault-healthy-pin",
    "vault-late-ready", "vault-late-request", "vault-late-pin",
    "vault-v5-order", "vault-v5-locked", "vault-v5-healthy",
    "vault-v5-owner-before-enroll", "vault-v5-owner-before-prepare",
    "vault-v5-owner-before-config", "vault-v5-owner-before-install",
])
def test_normal_ssh_setup_retains_source_admission_and_original_bootstrap_order(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Any,
) -> None:
    from agent_worktrees import repository_ssh_setup as ssh_setup
    owner_changed = False
    data_only = isinstance(failure, str) and failure.startswith("vault-v5-")
    if data_only:
        failure = failure.replace("vault-v5-", "vault-")
        from agent_worktrees import repository_vault

        def verify_owner() -> None:
            if owner_changed:
                raise setup.SetupError("Installed Vault selection changed")

        monkeypatch.setattr(repository_vault, "selected", lambda: SimpleNamespace(verify=verify_owner))

    path = source / setup.DECLARATION
    declaration = json.loads(path.read_text())
    declaration["version"] = 3
    declaration["contexts"] = {"harness-a": {
        "provider": "local-projection-v1", "source": str(tmp_path / "projection.json"),
        "ssh_setup": str(tmp_path / "request.json"),
    }}
    vault = isinstance(failure, str) and failure.startswith("vault-")
    wire = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
    public = "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(wire).digest()).decode("ascii").rstrip("=")
    if vault:
        declaration["version"] = 5 if data_only else 4
        key_selector: dict[str, Any] = (
            {"owner": "agent-vault"} if data_only else {"argv": [sys.executable]}
        )
        declaration["contexts"]["harness-a"]["ssh_key"] = {
            **key_selector,
            "configuration_root": str(tmp_path),
            "entry": "SSH/node-a", "directory": str(tmp_path / "key"),
            "fingerprint": fingerprint,
        }
    path.write_text(json.dumps(declaration))
    commit(source)
    selected_setup = setup.inspect(source, URL)
    events = []
    identity = SimpleNamespace(source_commit="a" * 40, source_tree="b" * 40)
    selection = {
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "runtime_version": "0.1.0.profile.abc", "payload_hash": "c" * 64,
    }
    description: dict[str, Any] = {
        "schema": "fleet-manager.continuity-ssh-setup", "version": 1,
        "snapshot": str(tmp_path / "snapshot"), "root": str(tmp_path / "installation"),
        "selection": selection, "request_digest": "d" * 64, "host": {},
    }
    if vault:
        description.update(version=2, key_files={
            "identity_file": str(tmp_path / "key" / "id_ed25519"),
            "public_key_file": str(tmp_path / "key" / "id_ed25519.pub"),
        })
    project = SimpleNamespace(identity=identity, project={"application": "application"}, payload=tmp_path)
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "tools"))
    import runtime_application_project as application
    if sys.version_info >= (3, 11):
        import tomllib
    else:
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
    def inputs(*_args: Any) -> Iterator[tuple[SimpleNamespace, object]]:
        events.append("source-admission")
        yield project, object()

    def install(*_args: Any, **options: Any) -> dict[str, Any]:
        assert options["expected_version"] == selection["runtime_version"]
        assert options["expected_hash"] == selection["payload_hash"]
        events.append("install")
        if failure == "installer":
            raise subprocess.CalledProcessError(1, ["private-source-path"], stderr="private diagnostic")
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
    def status(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal owner_changed
        if failure == "vault-owner-before-config" and "enroll" in events:
            owner_changed = True
        return failure == "already-ready" or (
            isinstance(failure, str) and failure.startswith("vault-healthy")
        ) or (
            isinstance(failure, str) and failure.startswith("vault-late") and "enroll" in events
        ) or "install" in events

    monkeypatch.setattr(ssh_setup, "status", status)
    monkeypatch.setattr(shutil, "which", lambda *_args: sys.executable)

    def vault_runtime(
        selection: Any, arguments: list[str], *, timeout: int, invocation: Any = None,
        source: Any = None, verify_source_after: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        if source is not None:
            source.verify()
        result = vault_outcome(selection, arguments, timeout=timeout, invocation=invocation)
        if source is not None and verify_source_after:
            source.verify()
        return result

    def vault_outcome(
        selection: Any, arguments: list[str], *, timeout: int, invocation: Any = None,
    ) -> tuple[int, dict[str, Any]]:
        assert timeout <= 120
        assert (invocation is not None) == data_only
        assert selection.entry == "SSH/node-a" and selection.configuration_root == str(tmp_path)
        assert "initialize" not in arguments and "--prompt" not in arguments
        if arguments == ["invocation-contract", "--json"]:
            events.append("vault-contract")
            return 0, {"schema": "agent-vault.invocation", "version": 1, "capabilities": (
                ["configuration-root-v1"] if failure == "vault-capability"
                else ["configuration-root-v1", "ssh-key-install-pin-v1"]
            )}
        operation = arguments[1]
        events.append("vault-" + operation)
        assert "--require-running" in arguments
        if operation == "prepare" and failure in ("vault-locked", "vault-sync"):
            return 1, {"version": 1, "error": "needs_unlock" if failure == "vault-locked" else "entry_missing",
                       "message": "private provider diagnostic"}
        if operation == "install":
            assert arguments[arguments.index("--expected-fingerprint") + 1] == fingerprint
            assert arguments[arguments.index("--directory") + 1] == str(tmp_path / "key")
            if failure == "vault-install-unknown":
                return 1, {"version": 1, "error": "local_install_failed", "message": "private key path"}
        result: dict[str, Any] = {"version": 1, "status": "reused", "public_key": public, "fingerprint": fingerprint}
        if failure == "vault-pin":
            result["fingerprint"] = "SHA256:" + "A" * 43
        if operation == "install":
            result.update(private_key_path=description["key_files"]["identity_file"],
                          public_key_path=description["key_files"]["public_key_file"])
            if failure == "vault-location":
                result["private_key_path"] = str(tmp_path / "another-key")
        return 0, result

    monkeypatch.setattr(ssh_setup, "_vault_call", vault_runtime)

    def runtime(arguments: list[str]) -> dict[str, Any]:
        nonlocal owner_changed
        if arguments == ["capabilities"]:
            return {"schema": "fleet-manager.capabilities", "version": 1, "capabilities": [
                continuity.CAPABILITY, "local-projection-v1", ssh_setup.CAPABILITY,
                "continuity-machine-reference-v1",
                *([] if failure == "pin-capability" else [ssh_setup.PIN_CAPABILITY]),
                *([ssh_setup.KEY_REFERENCE_CAPABILITY, ssh_setup.KEY_PIN_CAPABILITY] if vault else []),
            ]}
        if arguments[0] == "ssh-setup":
            if vault:
                healthy = failure.startswith("vault-healthy") and "inspect" in events
                late = failure.startswith("vault-late") and "enroll" in events
                if not late:
                    assert ("--key-references-only" in arguments) == ("vault-install" not in events and not healthy)
                if "--key-references-only" not in arguments:
                    assert arguments[arguments.index("--expected-key-fingerprint") + 1] == fingerprint
                    assert arguments[arguments.index("--expected-request-digest") + 1] == description["request_digest"]
                    if late:
                        events.append("final-inspection")
                    if failure == "vault-healthy-pin" or (late and failure == "vault-late-pin"):
                        raise setup.SetupError("selected public fingerprint mismatch")
            if "--apply" in arguments:
                assert arguments[arguments.index("--expected-request-digest") + 1] == description["request_digest"]
            events.append("prepare" if "--apply" in arguments else "inspect")
            if failure == "vault-owner-before-install" and "--apply" in arguments:
                owner_changed = True
            if failure == "vault-owner-before-enroll" and "vault-install" in events:
                owner_changed = True
            response = (description if not vault or "--key-references-only" in arguments else {
                name: value for name, value in description.items() if name != "key_files"
            } | {"version": 1})
            return response | ({"request_digest": "changed"} if (
                (failure == "changed-request" and "--apply" in arguments)
                or (failure == "vault-healthy-request" and "--key-references-only" not in arguments)
                or (failure == "vault-late-request" and "enroll" in events and "--key-references-only" not in arguments)
            ) else {})
        assert arguments[0] == "bootstrap"
        events.append("bootstrap")
        if vault and not failure.startswith("vault-healthy") and "--apply" not in arguments:
            return {"status": "needed", "stage": "enrollment"}
        if vault and "--apply" in arguments:
            assert events.index("vault-prepare") < events.index("vault-install")
            assert "prepare" not in events
            events.append("enroll")
            if failure == "vault-owner-before-prepare":
                owner_changed = True
        return {"status": "ready", "stage": "connected"}

    monkeypatch.setattr(continuity, "_invoke", runtime)
    if failure in ("already-ready", "vault-healthy"):
        assert continuity.run(selected_setup, "harness-a", apply=False).status == "ready"
        assert events.index("source-admission") < events.index("bootstrap")
        assert "prepare" not in events and "install" not in events
        assert "vault-contract" not in events
        if vault:
            assert events.count("inspect") == 2
    elif failure in ("vault-locked", "vault-sync", "vault-capability", "vault-install-unknown"):
        result = continuity.run(selected_setup, "harness-a", apply=True)
        assert result.status == ("refused" if failure == "vault-capability" else "waiting")
        assert "private" not in result.message
        assert "enroll" not in events and "prepare" not in events and "install" not in events
        if failure in ("vault-locked", "vault-sync", "vault-capability"):
            assert "vault-install" not in events
    elif failure == "vault-order":
        result = continuity.run(selected_setup, "harness-a", apply=True)
        assert result.status == "ready"
        assert events.index("vault-prepare") < events.index("vault-install") < events.index("enroll")
        assert events.index("enroll") < events.index("prepare") < events.index("install")
        assert events.count("vault-install") == events.count("enroll") == events.count("install") == 1
    elif failure == "vault-late-ready":
        assert continuity.run(selected_setup, "harness-a", apply=True).status == "ready"
        assert events.index("vault-install") < events.index("enroll") < events.index("final-inspection")
        assert "prepare" not in events and "install" not in events
    elif data_only and failure.startswith("vault-owner-"):
        with pytest.raises(setup.SetupError, match="Vault selection changed"):
            continuity.run(selected_setup, "harness-a", apply=True)
        assert ("enroll" in events) == (failure != "vault-owner-before-enroll")
        assert ("prepare" in events) == (failure == "vault-owner-before-install")
        assert "install" not in events
    elif failure is not None:
        with pytest.raises(setup.SetupError) as refused:
            continuity.run(selected_setup, "harness-a", apply=True)
        assert ("install" in events) == (failure == "installer")
        assert "private" not in str(refused.value)
        if failure in ("profile", "pin-capability"):
            assert "bootstrap" not in events
        if vault:
            assert ("enroll" in events) == failure.startswith("vault-late")
            assert "prepare" not in events
    else:
        result = continuity.run(selected_setup, "harness-a", apply=True)
        assert result.status == "ready" and "converged" in result.message
        assert events.index("source-admission") < events.index("bootstrap")
        assert events.index("bootstrap") < events.index("prepare") < events.index("install")
        assert events.count("install") == 1


def test_consumer_selects_its_engine_interpreter_with_bounded_native_custody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"status":"ready","stage":"connected"}', "")
    monkeypatch.setattr(continuity, "run_caller_owned", run)
    assert continuity._invoke(["bootstrap"]) == {"status": "ready", "stage": "connected"}
    argv, options = calls[0]
    assert argv == [sys.executable, "-I", "-m", "fleet_manager", "bootstrap"]
    assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 16384
    assert options["timeout"] == 30


def test_vault_selection_refuses_implicit_launchers_and_ambiguous_key_identity(tmp_path: Path) -> None:
    valid: dict[str, Any] = {
        "argv": [sys.executable], "configuration_root": str(tmp_path),
        "entry": "SSH/node-a", "directory": str(tmp_path / "key"),
        "fingerprint": "SHA256:" + "A" * 43,
    }
    assert cast(setup.SSHKeySelection, setup._ssh_key(valid)).argv == (sys.executable,)
    for replacement in (
        {"argv": ["agent-vault"]}, {"argv": [str(tmp_path / "agent-vault.ps1")]},
        {"argv": [sys.executable, "line\nbreak"]}, {"configuration_root": "relative"},
        {"directory": str(tmp_path / ".." / "key")}, {"entry": "\n"},
        {"fingerprint": "SHA256:" + "B" * 43}, {"extra": True},
    ):
        with pytest.raises(setup.SetupError):
            setup._ssh_key(valid | replacement)


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("failure", [None, "needs_unlock", "local_install_failed", "invalid-json", "unsupported"])
def test_scoped_vault_adapter_uses_real_bounded_process_and_no_provisioning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None, installed: bool,
) -> None:
    from agent_worktrees import repository_ssh_setup as ssh_setup

    script = tmp_path / "selected-vault.py"
    log = tmp_path / "calls.jsonl"
    wire = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
    public = "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(wire).digest()).decode("ascii").rstrip("=")
    directory = tmp_path / "key"
    entry = "-SSH/node-a" if failure is None else "SSH/node-a"
    references = {"identity_file": str(directory / "id_ed25519"),
                  "public_key_file": str(directory / "id_ed25519.pub")}
    script.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "arguments=sys.argv[2:]\n"
        "assert os.environ['AGENT_VAULT_NO_SELFPROVISION']=='1'\n"
        "assert '_AGENT_WORKTREES_VAULT_RUNTIME_GUARD' not in os.environ\n"
        "assert 'AGENT_VAULT_PINNED_CONTEXT' not in os.environ\n"
        f"assert arguments[:2]==['--configuration-root',{str(tmp_path)!r}]\n"
        "arguments=arguments[2:]\n"
        "with Path(sys.argv[1]).open('a') as stream: stream.write(json.dumps(arguments)+'\\n')\n"
        f"failure={failure!r}\n"
        "if arguments==['invocation-contract','--json']:\n"
        " print(json.dumps({'schema':'agent-vault.invocation','version':1,"
        "'capabilities':[] if failure=='unsupported' else "
        "['configuration-root-v1','ssh-key-install-pin-v1']}));sys.exit(0)\n"
        "import argparse\n"
        "parser=argparse.ArgumentParser()\n"
        "parser.add_argument('--entry',required=True)\n"
        "selected,_remaining=parser.parse_known_args(arguments[2:])\n"
        f"assert selected.entry=={entry!r}\n"
        "assert '--require-running' in arguments and '--prompt' not in arguments\n"
        "assert '--initialize' not in arguments and '--confirm-initialization' not in arguments\n"
        "if failure=='invalid-json': print('private unstructured output');sys.exit(1)\n"
        "if failure=='needs_unlock' or (failure=='local_install_failed' and arguments[1]=='install'):\n"
        " print(json.dumps({'version':1,'error':failure,'message':'private provider diagnostic'}));sys.exit(1)\n"
        f"result={{'version':1,'status':'reused','public_key':{public!r},'fingerprint':{fingerprint!r}}}\n"
        "if arguments[1]=='install':\n"
        f" assert arguments[arguments.index('--expected-fingerprint')+1]=={fingerprint!r}\n"
        f" assert arguments[arguments.index('--directory')+1]=={str(directory)!r}\n"
        f" result.update(private_key_path={references['identity_file']!r},public_key_path={references['public_key_file']!r})\n"
        "print(json.dumps(result))\n",
        encoding="utf-8",
    )
    selection = setup.SSHKeySelection(
        () if installed else (sys.executable, str(script), str(log)),
        str(tmp_path), entry, str(directory), fingerprint,
        owner="agent-vault" if installed else None,
    )
    if installed:
        from agent_worktrees import repository_vault

        binding = repository_vault.VaultInvocation(
            (sys.executable, str(script), str(log)), "selected-installation", dict(__import__("os").environ),
        )
        monkeypatch.setattr(repository_vault, "selected", lambda: binding)
    context = SimpleNamespace(ssh_key=selection)
    original = []
    selected = SimpleNamespace(verify=lambda: original.append("same-source"))
    monkeypatch.setenv("AGENT_VAULT_NO_SELFPROVISION", "1")
    monkeypatch.setenv("_AGENT_WORKTREES_VAULT_RUNTIME_GUARD", "1")
    foreign_pin = json.dumps({
        "version": 1, "kpdb": str(tmp_path / "other-database.kdbx"),
        "group": "Other", "vault_name": "other-vault", "port": 12456,
    })
    monkeypatch.setenv("AGENT_VAULT_PINNED_CONTEXT", foreign_pin)
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path / "other-identity-cache"))
    if failure == "invalid-json":
        with pytest.raises(setup.SetupError, match="unconfirmed") as refused:
            ssh_setup.prepare_key(selected, context, {"key_files": references})
        assert "private" not in str(refused.value)
    else:
        result = ssh_setup.prepare_key(selected, context, {"key_files": references})
        if failure is None:
            assert result is None
        else:
            assert result is not None
            assert result.status == ("refused" if failure == "unsupported" else "waiting")
        if result is not None:
            assert "private" not in result.message
            assert ("Unlock" in result.message) == (failure == "needs_unlock")
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [item[:2] for item in calls] == (
        [["invocation-contract", "--json"]]
        + ([] if failure == "unsupported" else [["ssh-key", "prepare"]])
        + ([["ssh-key", "install"]] if failure in (None, "local_install_failed") else [])
    )
    assert len(original) >= (1 if failure == "unsupported" else 2)
    assert not directory.exists()
    assert __import__("os").environ["_AGENT_WORKTREES_VAULT_RUNTIME_GUARD"] == "1"
    assert __import__("os").environ["AGENT_VAULT_PINNED_CONTEXT"] == foreign_pin


@pytest.mark.parametrize("version", [4, 5])
def test_versioned_vault_selector_is_strict_and_inspection_is_data_only(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int,
) -> None:
    from agent_worktrees import repository_vault

    monkeypatch.setattr(repository_vault, "selected", lambda: pytest.fail("inspection resolved Vault"))
    key: dict[str, Any] = {
        "configuration_root": str(tmp_path / "absent-config"), "entry": "-SSH/node-a",
        "directory": str(tmp_path / "absent-keys"), "fingerprint": "SHA256:" + "A" * 43,
    }
    selector: dict[str, Any] = (
        {"argv": [sys.executable]} if version == 4 else {"owner": "agent-vault"}
    )
    key.update(selector)
    declaration = json.loads((source / setup.DECLARATION).read_text())
    declaration.update(version=version, contexts={"harness-a": {
        "provider": "local-projection-v1", "source": str(tmp_path / "projection.json"),
        "ssh_setup": str(tmp_path / "request.json"), "ssh_key": key,
    }})
    (source / setup.DECLARATION).write_text(json.dumps(declaration))
    commit(source)
    selected = setup.inspect(source, URL)
    selected.verify()
    bound = cast(setup.SSHKeySelection, continuity.selected(selected, "harness-a").ssh_key)
    assert bound.owner == ("agent-vault" if version == 5 else None)
    assert bound.argv == (() if version == 5 else (sys.executable,))
    declaration["contexts"]["harness-a"]["ssh_key"]["entry"] = "SSH/changed"
    (source / setup.DECLARATION).write_text(json.dumps(declaration))
    with pytest.raises(setup.SetupError):
        selected.verify()


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("invalid", ["other-selector", "unknown-owner", "mixed", "missing"])
def test_vault_key_versions_do_not_accept_each_others_selector(
    tmp_path: Path, installed: bool, invalid: str,
) -> None:
    key: dict[str, Any] = {
        "configuration_root": str(tmp_path), "entry": "SSH/node-a",
        "directory": str(tmp_path / "key"), "fingerprint": "SHA256:" + "A" * 43,
        **({"owner": "agent-vault"} if installed else {"argv": [sys.executable]}),
    }
    if invalid == "other-selector":
        key.pop("owner" if installed else "argv")
        selector: dict[str, Any] = (
            {"argv": [sys.executable]} if installed else {"owner": "agent-vault"}
        )
        key.update(selector)
    elif invalid == "unknown-owner":
        key["owner"] = "other-vault"
    elif invalid == "mixed":
        key.update(argv=[sys.executable], owner="agent-vault")
    else:
        key.pop("owner" if installed else "argv")
    with pytest.raises(setup.SetupError):
        setup._ssh_key(key, installed=installed)


def test_installed_vault_change_refuses_before_next_credential_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import repository_ssh_setup as ssh_setup, repository_vault

    original = repository_vault.VaultInvocation((sys.executable,), "original", {})
    changed = repository_vault.VaultInvocation((sys.executable,), "changed", {})
    monkeypatch.setattr(repository_vault, "selected", lambda: changed)
    monkeypatch.setattr(ssh_setup, "run_caller_owned", lambda *_a, **_kw: pytest.fail("credential child ran"))
    selection = setup.SSHKeySelection(
        (), str(tmp_path), "SSH/node-a", str(tmp_path / "key"), "SHA256:" + "A" * 43,
        owner="agent-vault",
    )
    with pytest.raises(setup.SetupError, match="selection changed"):
        ssh_setup._vault_call(selection, ["ssh-key", "prepare"], timeout=30, invocation=original)

@pytest.mark.parametrize("missing", ["continuity-ssh-key-reference-v1", "continuity-ssh-key-pin-v1"])
def test_selected_vault_refuses_old_fleet_before_request_or_credential_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    from agent_worktrees import repository_ssh_setup as ssh_setup

    key = setup.SSHKeySelection((sys.executable,), str(tmp_path), "SSH/node-a", str(tmp_path / "key"),
                                "SHA256:" + "A" * 43)
    context = setup.ContinuityContext("harness-a", "local-projection-v1", str(tmp_path / "source"),
                                      str(tmp_path / "absent-request"), key)
    selected: Any = SimpleNamespace(contexts=(context,), verify=lambda: None)
    calls = []

    def runtime(arguments: list[str]) -> dict[str, Any]:
        calls.append(arguments)
        return {"schema": "fleet-manager.capabilities", "version": 1, "capabilities": [
            item for item in (
                continuity.CAPABILITY, context.provider, ssh_setup.CAPABILITY, ssh_setup.PIN_CAPABILITY,
                "continuity-machine-reference-v1", ssh_setup.KEY_REFERENCE_CAPABILITY, ssh_setup.KEY_PIN_CAPABILITY,
            ) if item != missing
        ]}

    monkeypatch.setattr(continuity, "_invoke", runtime)
    with pytest.raises(setup.SetupError, match="lacks selected SSH"):
        continuity.run(selected, "harness-a", apply=True)
    assert calls == [["capabilities"]]


def test_vault_gate_requalifies_source_before_credential_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import repository_ssh_setup as ssh_setup

    selection = setup.SSHKeySelection((sys.executable,), str(tmp_path), "SSH/node-a", str(tmp_path / "key"),
                                      "SHA256:" + "A" * 43)
    context = SimpleNamespace(ssh_key=selection)
    checks, calls = [], []

    def verify() -> None:
        checks.append("check")
        if len(checks) == 2:
            raise setup.SetupError("source changed")

    def runtime(
        _selection: Any, arguments: list[str], **kwargs: Any,
    ) -> tuple[int, dict[str, Any]]:
        calls.append(arguments)
        return 0, {"schema": "agent-vault.invocation", "version": 1,
                   "capabilities": ["configuration-root-v1", "ssh-key-install-pin-v1"]}

    monkeypatch.setattr(ssh_setup, "_vault_call", runtime)
    with pytest.raises(setup.SetupError, match="source changed"):
        ssh_setup.prepare_key(SimpleNamespace(verify=verify), context, {"key_files": {
            "identity_file": str(tmp_path / "key" / "id_ed25519"),
            "public_key_file": str(tmp_path / "key" / "id_ed25519.pub"),
        }})
    assert calls == [["invocation-contract", "--json"]]


@pytest.mark.parametrize("ready,complete,stage", [
    (True, True, None), (False, True, "not-running"), (True, False, "incomplete-construction"),
])
def test_setup_status_requires_live_convergence_after_installed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ready: bool, complete: bool, stage: str | None,
) -> None:
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
    def runtime_status(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ready": True, "selected": True, "revision": 7} if ready else {"state": "not-running"}

    monkeypatch.setattr(continuity, "_invoke", runtime_status)
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

    def runtime(arguments: list[str]) -> dict[str, Any]:
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
