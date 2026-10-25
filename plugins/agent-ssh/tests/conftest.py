import os
from pathlib import Path
import stat
import tempfile

import pytest


@pytest.fixture
def native_target_root(tmp_path):
    if os.name == "nt":
        yield tmp_path
        return
    if (
        os.environ.get("AGENT_SSH_NATIVE_DISPOSABLE") != "1"
        or os.environ.get("COPILOT_EXTENSIONS_ALLOW_HOST_STATE") != "1"
    ):
        pytest.skip("Select a disposable native account and explicit runner host-state opt-in")
    import pwd

    uid = os.geteuid()
    assert uid != 0, "Native target acceptance requires its actual non-root account"
    home = Path(pwd.getpwuid(uid).pw_dir)
    for path in (home, *home.parents):
        info = path.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid)
        assert info.st_mode & 0o022 == 0, "StrictModes requires safe authorization ancestry"
    with tempfile.TemporaryDirectory(prefix=".ce-ssh-target-", dir=home) as temporary:
        yield Path(temporary)
