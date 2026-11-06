"""Compatibility import for the canonical driver process utilities."""
import sys
from agent_remote_driver.session_host import osutil as _implementation

sys.modules[__name__] = _implementation
