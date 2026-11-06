"""Compatibility import for the canonical driver broker client."""
import sys
from agent_remote_driver.session_host import client as _implementation

sys.modules[__name__] = _implementation
