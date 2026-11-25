"""Pause the actual receiver only at its post-commit/pre-execution seam."""

from pathlib import Path
import sys
import time
from typing import Any

from update_driver import core, first_use


if __name__ == "__main__":
    marker = Path(sys.argv.pop(1))
    def pause(**kwargs: Any) -> int:
        marker.write_text("admitted", encoding="utf-8")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            time.sleep(.02)
        raise AssertionError("fixture caller was not interrupted")

    core.drain = pause
    raise SystemExit(first_use.main())
