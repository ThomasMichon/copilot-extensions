"""Compatibility import for the canonical driver wire protocol."""
import sys
from agent_remote_driver.session_host import protocol as _implementation

sys.modules[__name__] = _implementation
