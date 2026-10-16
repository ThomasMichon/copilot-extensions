"""SDK diagnostics are scrubbed only inside the remote connection context."""

import logging
from pathlib import Path

import acp

from agent_bridge.remote_acp_logging import remote_sdk_log_scope


def test_remote_sdk_record_redaction_preserves_local_and_non_sdk_logging():
    sdk_path = str(Path(acp.__file__).parent / "connection.py")
    marker = "SYNTHETIC-SENSITIVE-MARKER"

    def make(path):
        return logging.getLogRecordFactory()(
            "root", logging.ERROR, path, 1, "Invalid SDK input: %s", (marker,),
            (ValueError, ValueError(marker), None),
        )

    with remote_sdk_log_scope():
        remote = make(sdk_path)
        application = make(__file__)
    local = make(sdk_path)
    assert marker not in remote.getMessage()
    assert remote.exc_info is None and remote.exc_text is None and remote.stack_info is None
    assert marker in application.getMessage() and application.exc_info is not None
    assert marker in local.getMessage() and local.exc_info is not None
