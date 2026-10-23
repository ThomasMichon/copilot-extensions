"""Exec successor: consume actual inherited custody before writing readiness."""

import os
from pathlib import Path
import sys

from single_instance_lease.operation import OperationLock

path = Path(sys.argv[1])
owned = OperationLock.from_inherited(path, 9)
try:
    try:
        os.fstat(9)
    except OSError:
        pass
    else:
        raise AssertionError("original native descriptor was not consumed")
    owned.assert_owned()
    (path.parent / "python-ready").write_text("same kernel custody")
    sys.stdin.readline()
    owned.assert_owned()
finally:
    owned.release()
