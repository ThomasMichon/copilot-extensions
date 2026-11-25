"""Support declared by the selected ordinary or compact Host executable."""

from __future__ import annotations

from importlib.util import find_spec
import sys
from typing import Any

from . import protocol as proto
from .inspection import CONTRACT, MAX_BYTES


def support_info(
    *, legacy: bool, headed_available: bool, lifetime_policies: tuple[str, ...],
) -> dict[str, Any]:
    """Describe the selected export without inferring support from a package version."""
    recovery_intents = []
    if headed_available:
        from ..headed.recovery import PERMIT_INTENT_CONTRACT, PERMIT_INTENT_VERSION

        recovery_intents = [{
            "contract": PERMIT_INTENT_CONTRACT, "versions": [PERMIT_INTENT_VERSION],
            "scope": "data-only", "owner_store": "unqualified", "effects_wired": False,
        }]
    sdk_available = False
    if not legacy and find_spec("agent_remote_driver.session_host.sdk_backend") is not None:
        from .sdk_backend import available

        sdk_available = available()
    websocket_available = (
        not legacy
        and find_spec("agent_remote_driver.session_host.ws_transport") is not None
        and find_spec("websockets") is not None
    )
    return {
        "contract": "agent-remote-driver.session-host",
        "version": 1,
        "modes": ["headless"] + (["headed"] if headed_available else []),
        "headless_backends": ["native-acp"] + (["sdk"] if sdk_available else []),
        "transports": ["tcp-frame"] + (["acp.v1"] if websocket_available else []),
        "protocol_version": proto.PROTOCOL_VERSION,
        "host_inspection": {
            "contract": CONTRACT, "versions": [1], "max_bytes": MAX_BYTES,
            "channel": "separate-read-only-connection",
            "private_attachment": "unsupported",
        },
        "lifetime_policies": list(lifetime_policies),
        "startup_admission": ["live-owner-v1"],
        "headed_process_receipts": ["native-child-v1"] if headed_available else [],
        "headed_receivers": ["explicit-inprocess-v1"] if headed_available else [],
        "headed_connectors": ["driver-native-v1"] if headed_available else [],
        "headed_client": ["native-send-v1"] if headed_available else [],
        "headed_recovery_intents": recovery_intents,
        "mux_creation": ["posix-private-create-v1"] if not legacy and sys.platform != "win32" else [],
        "automatic_idle_retirement": {
            "availability": "unavailable",
            "reason": "structured-runtime-work-inventory-unqualified",
        },
    }
