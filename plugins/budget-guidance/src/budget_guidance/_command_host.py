"""POSIX reader host: create an owned group without detaching the test session."""

from __future__ import annotations

import os
import sys


def main() -> int:
    """Replace this exact PID with the caller-authorized executable."""
    os.setpgid(0, 0)
    try:
        os.execv(sys.argv[1], sys.argv[1:])
    except OSError:
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
