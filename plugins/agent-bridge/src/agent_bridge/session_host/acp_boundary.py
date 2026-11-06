"""Compatibility import for the canonical driver ACP continuation boundary."""
import sys
from agent_remote_driver.session_host import acp_boundary as _implementation

sys.modules[__name__] = _implementation
