"""Compatibility import for canonical preference attestation."""
import sys
from agent_remote_driver import preference_attestation as _implementation

sys.modules[__name__] = _implementation
