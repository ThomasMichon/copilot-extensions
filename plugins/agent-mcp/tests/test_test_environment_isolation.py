"""Run the canonical test-environment owner's focused admission controls."""

import sys
from pathlib import Path

_repository = Path(__file__).parents[3]
sys.path.insert(0, str(_repository))
try:
    from tools.test_plugin_test_containment import (
        test_poisoned_parent_state_is_detached_before_preparation as test_private_preparation,
        test_poisoned_roots_reach_only_explicit_private_native_resolvers as test_private_native_roots,
    )
finally:
    sys.path.remove(str(_repository))

__all__ = ["test_private_preparation", "test_private_native_roots"]
