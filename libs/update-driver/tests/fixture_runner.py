"""Deterministic trusted product stand-in; never invokes an installer or network."""

import os
import sys
import time
from pathlib import Path

from update_driver import borrowed_custody

root, marker, delay, code = sys.argv[1:]
with borrowed_custody(root=Path(root)) as custody:
    custody.assert_owned()
    with Path(marker).open("a", encoding="utf-8") as stream:
        stream.write(f"start {os.getpid()}\n")
        stream.flush()
        print("fixture-start", flush=True)
        time.sleep(float(delay))
        custody.assert_owned()
        stream.write(f"end {os.getpid()}\n")
    print("fixture-end", flush=True)
raise SystemExit(int(code))
