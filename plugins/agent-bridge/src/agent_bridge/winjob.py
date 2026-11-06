"""Compatibility import for canonical driver child containment."""
import sys
from agent_remote_driver import winjob as _implementation

sys.modules[__name__] = _implementation
