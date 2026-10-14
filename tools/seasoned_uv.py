"""Repository tooling adapter for the canonical, shipped UV seasoning policy."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from installer_dependency_policy import DependencyPolicyError, main, prepare_uv_install  # noqa: E402,F401

if __name__ == "__main__":
    raise SystemExit(main())
