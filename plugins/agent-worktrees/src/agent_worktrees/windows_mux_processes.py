"""Compatibility import for Driver's native mux identity reader."""
import sys
from agent_remote_driver import windows_mux_processes as _implementation

sys.modules[__name__] = _implementation
