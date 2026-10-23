"""The original native parent deliberately execs this isolated successor."""

import json
import os
from pathlib import Path
import signal
import stat
import sys

sys.path.insert(0, sys.argv[1])
from agent_procutil.native_child import NativeChildCustody  # noqa: E402
from agent_procutil._caller_owned import _pidfd_exited  # noqa: E402
from agent_procutil._linux_pidfd import open_pidfd  # noqa: E402

child = int(sys.argv[2])
area = Path(sys.argv[3])
assert stat.S_ISFIFO(os.fstat(8).st_mode), "native lifetime writer did not survive exec"
custody = NativeChildCustody(child)
try:
    custody.assert_owned()
    descendant = int((area / "descendant").read_text())
    assert os.getpgid(descendant) == child
    descriptor = open_pidfd(descendant)
    try:
        assert custody.retire() == -signal.SIGKILL
        assert _pidfd_exited(descriptor), "adopted group's descendant survived settlement"
    finally:
        os.close(descriptor)
    assert custody.retire() == -signal.SIGKILL
    try:
        os.waitid(os.P_PID, child, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        pass
    else:
        raise AssertionError("settled native root was not reaped")
    print(json.dumps({"child": child, "descendant": descendant, "settled": True}))
finally:
    os.close(8)
    custody.retire()
