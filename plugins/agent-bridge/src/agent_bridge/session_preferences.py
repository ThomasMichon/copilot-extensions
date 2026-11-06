"""Compatibility import for canonical execution-side preferences."""
import sys
from agent_remote_driver import session_preferences as _implementation

sys.modules[__name__] = _implementation
