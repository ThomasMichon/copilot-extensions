"""Configured Fleet lifetime for existing SSH notifications and local writers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
import logging
import os
import signal
import stat
import sys

from .planning import PlanError
from .private_state import (
    create_private_file, private_directory, validate_private_directory, windows_private,
)
from .ssh_machine_config import SSHMachineConfig, absolute

LEASE = "fleet-ssh-client.lock"
_LOG = logging.getLogger(__name__)


def _consumer(config):
    from agent_ssh.gateway_directory import (
        apply_update, validate_consumer_state, validate_writer_destinations,
    )

    value = config.to_dict()
    validate_writer_destinations(
        config_d=absolute(value["config_d"]), ssh_config=absolute(value["ssh_config"]),
        authorization_dir=absolute(value["authorization_dir"]) if value["authorization_dir"] else None,
        authorization_target={"machine": value["machine"], "environment": value["environment"]},
        authorization_controller=value["controller"],
        gateway=value["gateway"], identity_file=absolute(value["identity_file"]),
    )
    for path in (absolute(value["state_dir"]), *config.input_paths()):
        validate_consumer_state(
            path, config_d=absolute(value["config_d"]), ssh_config=absolute(value["ssh_config"]),
            authorization_dir=absolute(value["authorization_dir"]) if value["authorization_dir"] else None,
        )
    return partial(apply_update, **config.local_scope())


def _validate_lease(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PlanError("SSH client lease is not an ordinary owned file")
    if os.name == "nt":
        windows_private(path)
    elif info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PlanError("SSH client lease is not private to its owner")


def _validate_state(root):
    validate_private_directory(root)
    if {path.name for path in root.iterdir()} - {LEASE}:
        raise PlanError("SSH client state directory contains unowned files")
    path = absolute(str(root / LEASE))
    if os.path.lexists(path):
        _validate_lease(path)


def _lease(root):
    from single_instance_lease import SingleInstance

    private_directory(root)
    _validate_state(root)
    path = absolute(str(root / LEASE))
    try:
        descriptor = create_private_file(path)
    except FileExistsError:
        _validate_lease(path)
    else:
        os.close(descriptor)
    return SingleInstance(str(root), service="fleet-ssh-client")


async def _settle(task):
    joined = asyncio.gather(task, return_exceptions=True)
    interrupted = False
    while True:
        try:
            return (await asyncio.shield(joined)), interrupted
        except asyncio.CancelledError:
            interrupted = True


def _continuity_reference(config: SSHMachineConfig):
    try:
        from .continuity_bootstrap import MACHINE_REFERENCE_CAPABILITY, machine_caller_reference
    except ImportError:
        raise PlanError("Selected continuity provider lacks the machine-reference-v1 capability") from None
    if MACHINE_REFERENCE_CAPABILITY != "continuity-machine-reference-v1":
        raise PlanError("Selected continuity provider has an unsupported machine reference")
    value = config.to_dict()
    credential, selected = value["credential_source"], value["authentication"]
    return machine_caller_reference(
        absolute(credential["source"]), harness=credential["harness"],
        state_root=absolute(credential["state_root"]), machine=value["machine"],
        gateway_origin=value["gateway_origin"], gateway_ca=value["gateway_ca"],
        principal=selected["principal"], audience=selected["audience"], resource=selected["resource"],
        timeout=35,
    )


def prepare(config: SSHMachineConfig):
    """Validate the selected local writer and credentials without network or writes."""
    config = SSHMachineConfig.from_dict(config.to_dict())
    accept = _consumer(config)
    from .credential_references import (
        load_service_credentials, read_access_token_reference, read_credential_reference,
    )
    from .gateway_contract import GatewayError
    from .gateway_service_caller import ServiceCaller
    from .gateway_ssh_provider import public_key
    from .ssh_directory import SSHDirectoryClient

    value = config.to_dict()
    state = absolute(value["state_dir"])
    if os.path.lexists(state):
        _validate_state(state)
    elif not state.parent.is_dir():
        raise PlanError("SSH client state parent must be an existing ordinary directory")
    with absolute(value["public_key_file"]).open("rb") as stream:
        public = stream.read(257)
    if len(public) > 256:
        raise PlanError("Gateway local public-key file exceeds its bound")
    public_key(public.decode("ascii").strip())
    if value["version"] == 3:
        caller = _continuity_reference(config).caller
    else:
        if value["version"] == 1:
            credentials = load_service_credentials(
                proof_key_file=absolute(value["proof_key_file"]),
                access_token_file=absolute(value["access_token_file"]),
            )
            read_access_token_reference(credentials.access_token_file)
            authentication = {
                "access_token_callback": credentials.access_token, "proof_key": credentials.proof_key,
            }
        else:
            from joserfc.jwk import ECKey
            from joserfc.errors import JoseError
            from cryptography.hazmat.primitives.asymmetric.ec import (
                EllipticCurvePrivateKey, SECP256R1,
            )

            try:
                raw = read_credential_reference(absolute(value["proof_key_file"]))
                if not raw.lstrip().startswith((
                    b"-----BEGIN PRIVATE KEY-----", b"-----BEGIN EC PRIVATE KEY-----",
                )):
                    raise ValueError
                proof_key = ECKey.import_key(raw)
                signing_key = proof_key.get_op_key("sign")
                if not isinstance(signing_key, EllipticCurvePrivateKey) or not isinstance(
                    signing_key.curve, SECP256R1,
                ):
                    raise ValueError
            except (ValueError, TypeError, JoseError):
                raise GatewayError("credential-key-unavailable") from None
            selected = value["authentication"]
            authentication = {
                "proof_key": proof_key, "machine_principal": selected["principal"],
                "machine_audience": selected["audience"], "machine_resource": selected["resource"],
            }
        caller = ServiceCaller(
            gateway_origin=value["gateway_origin"], gateway_ca=value["gateway_ca"],
            timeout=35, **authentication,
        )
    client = SSHDirectoryClient(caller, namespace=value["namespace"])
    return accept, client


async def run(
    config: SSHMachineConfig, *, stop: asyncio.Event | None = None,
    install_signals: bool = False, on_applied: Callable[[int], None] | None = None,
) -> None:
    """No enrollment or service action; the embedding owns this foreground task."""
    stop = stop if stop is not None else asyncio.Event()
    if stop.is_set():
        return
    accept, client = prepare(config)
    value = config.to_dict()
    lease = _lease(absolute(value["state_dir"]))
    lease.acquire()
    previous, task = {}, None
    loop = asyncio.get_running_loop()

    def withdraw(_signal, _frame):
        loop.call_soon_threadsafe(stop.set)

    def apply(update):
        # The existing writers are synchronous: cancellation cannot orphan a write.
        result = accept(update)
        _LOG.info("ssh-client-directory-applied revision=%s", result["directory_revision"])
        if on_applied is not None:
            on_applied(result["directory_revision"])

    try:
        if install_signals:
            for selected in (signal.SIGINT, signal.SIGTERM):
                previous[selected] = signal.signal(selected, withdraw)
        task = asyncio.create_task(client.watch(stop, apply))
        await asyncio.shield(task)
        if not stop.is_set():
            raise PlanError("SSH client lifetime ended without a stop request")
    finally:
        stop.set()
        results, interrupted = [], False
        try:
            if task is not None:
                task.cancel()
                results, interrupted = await _settle(task)
        finally:
            for selected, handler in previous.items():
                signal.signal(selected, handler)
            lease.release()
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise result
        if interrupted:
            raise asyncio.CancelledError


def serve(path) -> int:
    try:
        from .gateway_contract import GatewayError
        from .gateway_service_channel import TransportError

        asyncio.run(run(SSHMachineConfig.load(path), install_signals=True))
    except ImportError:
        print("fleet-manager: SSH hosting requires the ssh-client extra", file=sys.stderr)
        return 1
    except (GatewayError, TransportError, OSError, ValueError, RuntimeError) as exc:
        print(f"fleet-manager: {exc}", file=sys.stderr)
        return 1
    return 0
