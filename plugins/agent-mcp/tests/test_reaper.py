"""Tests for TreeReaper (server.reap process-control) + config plumbing."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from agent_mcp._reaper import TreeReaper
from agent_mcp.config import REAP_MODES, parse_config, validate_config


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_spawn_kwargs_platform():
    kw = TreeReaper().spawn_kwargs()
    if os.name == "nt":
        assert kw == {}
    else:
        assert kw == {"start_new_session": True}


def test_close_untracked_is_noop():
    # Must never raise even if nothing was ever tracked.
    TreeReaper().close()
    TreeReaper().close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group teardown path")
def test_tree_reap_kills_grandchild_posix(tmp_path):
    """reap=tree must kill a launcher's GRANDCHILD, not just the direct child."""
    marker = tmp_path / "gpid"
    # Parent spawns a grandchild `sleep`, records its pid, then blocks.
    code = (
        "import subprocess,time;"
        "p=subprocess.Popen(['sleep','30']);"
        f"open({str(marker)!r},'w').write(str(p.pid));"
        "time.sleep(30)"
    )
    reaper = TreeReaper()
    proc = subprocess.Popen([sys.executable, "-c", code], **reaper.spawn_kwargs())
    reaper.track(proc.pid)

    gpid = None
    for _ in range(50):
        if marker.exists() and marker.read_text().strip():
            gpid = int(marker.read_text().strip())
            break
        time.sleep(0.1)
    assert gpid is not None, "grandchild never started"
    assert _alive(gpid)

    reaper.close()
    proc.wait(timeout=5)  # reap the direct child zombie

    # The grandchild (reparented to init once its parent dies) must also be gone.
    deadline = time.time() + 5
    while time.time() < deadline and _alive(gpid):
        time.sleep(0.1)
    assert not _alive(gpid), "grandchild leaked past reaper.close()"


def test_config_reap_default_is_child():
    cfg = parse_config({"server": {"type": "stdio", "command": ["npx", "x"]}})
    assert cfg.server.reap == "child"
    assert validate_config(cfg) == []


@pytest.mark.parametrize("mode", REAP_MODES)
def test_config_reap_valid_modes(mode):
    cfg = parse_config(
        {"server": {"type": "stdio", "command": ["npx", "x"], "reap": mode}}
    )
    assert cfg.server.reap == mode
    assert validate_config(cfg) == []


def test_config_reap_rejects_unknown():
    from agent_mcp.config import ConfigError

    with pytest.raises(ConfigError, match=r"server\.reap"):
        parse_config(
            {"server": {"type": "stdio", "command": ["npx", "x"], "reap": "bogus"}}
        )


def test_config_reap_non_child_requires_stdio():
    from agent_mcp.config import ConfigError

    with pytest.raises(ConfigError, match="only meaningful for transport 'stdio'"):
        parse_config({"server": {"type": "http", "url": "https://x", "reap": "tree"}})
