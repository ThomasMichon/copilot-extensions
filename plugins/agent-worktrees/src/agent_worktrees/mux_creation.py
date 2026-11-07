"""Compatibility import for the canonical Driver mux creation owner."""
import sys
from agent_remote_driver import mux_creation as _implementation

sys.modules[__name__] = _implementation
