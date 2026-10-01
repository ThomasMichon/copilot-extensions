from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB / "src"))
# plugin_activation imports dropin_registry and plugin_resolve at runtime
# (real dependencies, declared in libs/plugin-activation/pyproject.toml's own
# [tool.uv.sources]) -- when this suite runs standalone (not combined with
# those sibling canonical libs' own tests in the same pytest invocation, as
# CI's canonical-lib test step does), both sibling canonical libs' src/ must
# be on sys.path too, or the import fails outright.
sys.path.insert(0, str(LIB.parent / "dropin-registry" / "src"))
sys.path.insert(0, str(LIB.parent / "plugin-resolve" / "src"))
