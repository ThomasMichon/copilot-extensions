"""Explicit, physically attributed client commands; no PATH or hosting changes."""

from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import time
import uuid

from . import artifact_staging, client_activation as activation, client_admission
from . import client_construction as construction
from .planning import PlanError, decode, digest, record

REGISTERED = "client-frontdoor.json"
PENDING = "client-frontdoor-pending.json"
HISTORY = "client-frontdoors"
REMOVING = "client-frontdoor-removing.json"
FIELDS = {"active", "directory", "publication", "target_digest"}


def _command_paths(directory):
    names = ("fleet-manager.ps1", "fleet-manager.cmd") if os.name == "nt" else ("fleet-manager",)
    return [directory / name for name in names]


def _commands(directory, slot):
    python = str(construction._python(slot))
    bootstrap = str(slot / construction.BOOTSTRAP / "runtime_launcher.py")
    if any(ord(character) < 32 for value in (python, bootstrap, str(directory)) for character in value):
        raise PlanError("client command paths must not contain control characters")
    if os.name != "nt":
        command = " ".join(shlex.quote(value) for value in (
            python, "-I", "-S", "-B", bootstrap, "-m", "fleet_manager",
        ))
        return dict(zip(_command_paths(directory), [f'#!/bin/sh\nexec {command} "$@"\n'.encode()], strict=True))
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    ps1 = (
        "$ErrorActionPreference = 'Stop'\n"
        f"& {quote(python)} -I -S -B {quote(bootstrap)} -m fleet_manager @args\n"
        "exit $LASTEXITCODE\n"
    )
    cmd = (
        "@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
        '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0fleet-manager.ps1" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )
    return dict(zip(_command_paths(directory), [ps1.encode("utf-8-sig"), cmd.encode("ascii")], strict=True))


def _expected(commands):
    return [[str(path), hashlib.sha256(data).hexdigest()] for path, data in commands.items()]


def _binding(namespace, identity, directory, value):
    record(value, "fleet-manager.client-frontdoor", FIELDS)
    if set(value) != {"schema", "version", *FIELDS}:
        raise PlanError("client frontdoor receipt has unsupported fields")
    activation._state(value["active"], identity)
    if (
        value["directory"] != str(directory)
        or not isinstance(value["publication"], str)
        or len(value["publication"]) != 32
        or any(character not in "0123456789abcdef" for character in value["publication"])
        or not isinstance(value["target_digest"], str)
        or len(value["target_digest"]) != 64
        or any(character not in "0123456789abcdef" for character in value["target_digest"])
    ):
        raise PlanError("client frontdoor receipt has a different directory or invalid binding")
    workspace = namespace / HISTORY / value["publication"]
    artifact_staging._directory(workspace)
    if construction._read(workspace / "intent.json") != value:
        raise PlanError("client frontdoor receipt differs from its registration intent")
    commands = _commands(directory, namespace / "versions" / value["active"]["candidate"])
    return workspace, commands


def _receipt(namespace, identity, directory, publication):
    if os.path.lexists(namespace / REMOVING):
        raise PlanError("client frontdoor removal is unsettled; owned unregister retry is required")
    if os.path.lexists(namespace / PENDING):
        raise PlanError("client frontdoor outcome is unknown; owned registration recovery is required")
    receipt = namespace / REGISTERED
    if not os.path.lexists(receipt):
        return None
    value = construction._read(receipt)
    workspace, commands = _binding(namespace, identity, directory, value)
    root, entries = publication.read_context(workspace / "copy-context.json")
    if root != namespace or set(entries) != set(commands):
        raise PlanError("client frontdoor proof has a different root or command family")
    proofs = publication._proofs(
        [workspace / "copy-forward.jsonl"], root, entries, _expected(commands),
    )
    for path, expected in _expected(commands):
        target = Path(path)
        current = publication.state_path(target, private=True)
        if current not in proofs.get(target, ()) or current.digest != expected:
            raise PlanError("client frontdoor is not its producer-created command object")
    return value


def _health(directory, slot, checkpoint, descriptor, evidence, expected):
    checkpoint()
    if os.name == "nt":
        powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        commands = [
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", directory / "fleet-manager.ps1"],
            [os.environ["COMSPEC"], "/d", "/c", directory / "fleet-manager.cmd"],
        ]
    else:
        commands = [[directory / "fleet-manager"]]
    for argv in commands:
        result = decode(construction._run(
            [*argv, "doctor", "--role", "client"], slot, "registered command diagnostics",
            descriptor=descriptor, resource_evidence=evidence,
        ))
        if result != expected:
            raise PlanError("registered client command returned different local health evidence")
        checkpoint()


def register_client_frontdoor(root, identity, selection, staged, *, command_directory,
                             current_policy, current_target, clock=lambda: int(time.time())):
    """Register only the exact active client into an explicit existing private directory."""
    identity, selection, binding = construction._request(
        identity, selection, staged, current_policy, current_target,
    )
    evidence = artifact_staging.diagnose_client()
    publishing = False
    try:
        with (
            client_admission._owned(root) as (path, database_root, unchanged),
            client_admission._root(command_directory) as (directory, _anchor, commands_unchanged),
            client_admission._connection(path, database_root=database_root, readonly=False) as connection,
            artifact_staging._primitive() as primitive,
            ExitStack() as retained,
        ):
            if any(directory.is_relative_to(other) or other.is_relative_to(directory)
                   for other in (path, staged._root)):
                raise PlanError("client commands require a separate explicit private directory")
            connection.execute("BEGIN IMMEDIATE")
            namespace = path / construction.NAMESPACE
            artifact_staging._directory(namespace)
            construction._namespace(path, identity)
            version = f"client-{selection['sequence']}-{selection['release_digest']}"
            publication = primitive._runtime_component("versioned_runtime_publication")
            with primitive.construction_lease(namespace, version) as descriptor:
                completion = construction._completion(primitive, namespace, version, binding)
                retained.enter_context(primitive.retention_lease(
                    namespace, version, payload_hash=digest(completion),
                ))
                slot = primitive.version_dir(namespace, version)
                artifact_staging._directory(slot / construction.BOOTSTRAP)
                client_admission._ordinary_file(slot / construction.BOOTSTRAP / "runtime_launcher.py")
                commands = _commands(directory, slot)
                with primitive._publication_lease(namespace):
                    active = activation._active(namespace, identity, publication)
                    if active is None or (active["candidate"], active["runtime_sha256"]) != (
                        version, completion["runtime_sha256"],
                    ):
                        raise PlanError("client frontdoor requires the exact active admitted candidate")

                    target_digest = None

                    def checkpoint():
                        commands_unchanged()
                        _, target, admitted = construction._checkpoint(
                            connection, identity, selection, binding, staged,
                            current_policy, current_target, clock, unchanged,
                        )
                        if activation._active(namespace, identity, publication) != active:
                            raise PlanError("client active selection changed before frontdoor settlement")
                        if target_digest is not None and digest(target) != target_digest:
                            raise PlanError("client target changed during command registration")
                        construction._completion(primitive, namespace, version, binding)
                        return target, admitted

                    target, admitted = checkpoint()
                    target_digest = digest(target)
                    previous = _receipt(namespace, identity, directory, publication)
                    if previous:
                        retained.enter_context(primitive.retention_lease(
                            namespace, previous["active"]["candidate"],
                        ))
                    elif any(os.path.lexists(command) for command in commands):
                        raise PlanError("client command directory contains unattributed commands")
                    health = construction._health(slot, checkpoint, descriptor, evidence)
                    checkpoint()
                    reused = previous is not None and (
                        previous["active"] == active and previous["target_digest"] == digest(target)
                    )
                    if reused:
                        with publication.target_leases(namespace, list(commands)):
                            if _receipt(namespace, identity, directory, publication) != previous:
                                raise PlanError("client command receipt changed before reuse")
                            _health(directory, slot, checkpoint, descriptor, evidence, health)
                            _receipt(namespace, identity, directory, publication)
                    else:
                        history = namespace / HISTORY
                        if not os.path.lexists(history):
                            history.mkdir(mode=0o700)
                            client_admission._sync_directory(namespace)
                        artifact_staging._directory(history)
                        value = {
                            "schema": "fleet-manager.client-frontdoor", "version": 1,
                            "active": active, "directory": str(directory),
                            "publication": uuid.uuid4().hex, "target_digest": digest(target),
                        }
                        workspace = history / value["publication"]
                        workspace.mkdir(mode=0o700)
                        construction._write(workspace / "intent.json", value)
                        copies = []
                        for index, (command, content) in enumerate(commands.items()):
                            source = workspace / f"command.{index}"
                            with source.open("xb") as stream:
                                os.chmod(source, 0o600)
                                stream.write(content)
                                stream.flush()
                                os.fsync(stream.fileno())
                            copies.append(publication.CopyBinding(
                                source, command, hashlib.sha256(content).hexdigest(),
                                executable=os.name != "nt",
                            ))
                        client_admission._sync_directory(workspace)
                        client_admission._sync_directory(history)
                        first = True

                        def validate():
                            current, _admitted = checkpoint()
                            if digest(current) != value["target_digest"]:
                                raise PlanError("client target changed during command registration")
                            if first:
                                if _receipt(namespace, identity, directory, publication) != previous:
                                    raise PlanError("client command owner changed before registration")
                                if previous is None and any(os.path.lexists(command) for command in commands):
                                    raise PlanError("client command directory gained foreign commands")

                        def before_effects():
                            nonlocal first, publishing
                            validate()
                            client_admission._sync_directory(workspace)
                            publishing = True
                            construction._write(namespace / PENDING, value)
                            first = False

                        copy = publication.CopyPublication(
                            namespace, workspace, copies, validate, before_effects=before_effects,
                        )
                        acknowledging = False
                        try:
                            copy.publish()
                            client_admission._sync_directory(workspace)
                            for parent in {command.parent for command in commands}:
                                client_admission._sync_directory(parent)
                            _health(directory, slot, checkpoint, descriptor, evidence, health)
                            copy.verify()
                            checkpoint()
                            acknowledging = True
                            acknowledgement = workspace / "acknowledgement.json"
                            construction._write(acknowledgement, value)
                            os.replace(acknowledgement, namespace / REGISTERED)
                            client_admission._sync_directory(namespace)
                            (namespace / PENDING).unlink()
                            client_admission._sync_directory(namespace)
                            publishing = False
                        except BaseException:
                            if copy.published and not acknowledging:
                                copy.rollback()
                            raise
                        finally:
                            # Registration keeps native producer proofs for later ownership checks.
                            # Do not call keep(), whose cleanup deliberately erases that evidence.
                            copy._release()
                    return {
                        "schema": "fleet-manager.client-frontdoor-registration", "version": 1,
                        "identity": identity, "candidate": version, "admitted": admitted,
                        "command_directory": str(directory), "commands": [str(command) for command in commands],
                        "active": True, "frontdoor_registered": True, "health": health,
                        "reused": reused, "mutations_performed": not reused,
                        "path_modified": False, "live_readiness_verified": False,
                    }
    except PlanError:
        if publishing:
            raise PlanError("client frontdoor outcome is unknown; retained evidence requires owned recovery") from None
        raise
    except (OSError, sqlite3.Error, subprocess.SubprocessError, RuntimeError, ValueError):
        raise PlanError(
            "client frontdoor outcome is unknown; retained evidence requires owned recovery"
            if publishing else "client frontdoor could not establish owned command registration"
        ) from None
