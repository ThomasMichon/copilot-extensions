"""Capture-only child bootstrap; Python must not swallow a failed admission."""

from __future__ import annotations

import os
import sys

if "BRIDGE_HTTP47_CAPTURE_REQUEST" in os.environ:
    try:
        from bridge_http47_runtime import install
        install()
    except Exception as exc:
        print(f"HTTP-47 capture interpreter admission failed: {exc}", file=sys.stderr, flush=True)
        os._exit(86)
