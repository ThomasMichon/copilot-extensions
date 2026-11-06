"""Compatibility import for the canonical driver Session Host."""
import sys
from agent_remote_driver.session_host import host as _implementation

sys.modules[__name__] = _implementation
