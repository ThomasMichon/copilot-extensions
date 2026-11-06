"""Compatibility import for the canonical preference execution helper."""
import sys
from agent_remote_driver import preference_exec as _implementation

sys.modules[__name__] = _implementation
