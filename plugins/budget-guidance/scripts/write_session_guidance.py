#!/usr/bin/env python3
"""Use this owning payload's pure posture source without provisioning a runtime."""

import os
import signal
import sys

if __name__ == "__main__":
    if os.name != "nt":
        if not all(hasattr(signal, name) for name in ("SIGALRM", "ITIMER_REAL", "setitimer")):
            sys.stderr.write("budget-guidance: session cue deadline is unsupported\n")
            sys.stdout.write("{}")
            raise SystemExit(1)
        # This isolated one-shot process has no children or foreign signal policy.
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.setitimer(signal.ITIMER_REAL, 5)
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from budget_guidance.session_guidance import main

    raise SystemExit(main())
