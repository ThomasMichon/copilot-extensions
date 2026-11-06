"""Compatibility import for canonical execution-side preference attestation."""
import sys
from agent_remote_driver.session_host import preference_spawn as _implementation

sys.modules[__name__] = _implementation
