"""Compatibility import for the portable driver policy receiver."""
import sys
from agent_remote_driver.session_host import receiver_policy as _implementation

sys.modules[__name__] = _implementation
