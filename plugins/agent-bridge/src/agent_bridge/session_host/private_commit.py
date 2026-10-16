"""Native commit custody; a timeout never becomes an activation verdict."""

from __future__ import annotations

from dataclasses import asdict

from zdd.private_protocol import Deadline, PrivateIPCError, Refusal
from zdd.routing import Endpoint

from ..predecessor_runtime import RetainedChannel, _matches_publication
from .private_receipt import VerifiedHostReceipt, decode_host_receipt


def verify_host_commit(
    payload: object, channel: RetainedChannel, original: VerifiedHostReceipt,
    frontend: Endpoint, recovery_snapshot: str, recovery_generation: int,
    deadline: Deadline,
) -> VerifiedHostReceipt:
    """Verify a fresh INSPECT_COMMIT response against original native host custody.

    The caller must retain the RESERVE token before ACTIVATE and query through a
    newly registered native grant. The response is not permission to read/write
    the ACP stream or an assertion about SDK or database completion.
    """
    if (
        type(original) is not VerifiedHostReceipt or type(frontend) is not Endpoint
        or type(recovery_generation) is not int or type(recovery_snapshot) is not str
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    before = decode_host_receipt(original.private_payload(deadline))
    after = decode_host_receipt(payload)
    if (
        after.host != before.host or after.instance != before.instance
        or after.session_id != before.session_id
        or after.config_identity != before.config_identity
        or after.data_endpoint != before.data_endpoint
        or after.child_pid != before.child_pid
        or after.protocol_version != before.protocol_version
        or after.host_version != before.host_version
        or after.attachment_generation != before.attachment_generation + 1
        or after.attachment_generation != recovery_generation
        or after.snapshot != recovery_snapshot or after.snapshot == before.snapshot
        or not _matches_publication(after.frontend.to_dict(), frontend)
        or frontend.generation <= before.frontend.generation
        or frontend.pid == before.frontend.pid
        or after.acp_wire != before.acp_wire
        or after.ack_cursor != before.ack_cursor or after.max_seq != before.max_seq
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    verified = VerifiedHostReceipt(
        asdict(after), channel, before.host, frontend, before.session_id,
        before.config_identity, deadline,
    )
    try:
        original.private_payload(deadline)
        return verified
    except BaseException:
        verified.close()
        raise
