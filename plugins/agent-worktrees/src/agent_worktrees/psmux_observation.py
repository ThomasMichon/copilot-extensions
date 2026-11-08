"""Compatibility import for Driver's complete action-time mux inventory."""
import sys
from agent_remote_driver import mux_inventory as _implementation

sys.modules[__name__] = _implementation
