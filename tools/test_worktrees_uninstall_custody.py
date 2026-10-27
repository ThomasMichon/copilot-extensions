"""Worktrees publication and finite removal share their native lifecycle lock."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def test_removal_profile_covers_every_published_hook_and_setup_helper() -> None:
    scripts = REPO / "plugins/agent-worktrees/scripts"
    profile = json.loads((scripts / "uninstall-profile.json").read_text("utf-8"))
    publication = ast.parse((scripts / "runtime_install_publication.py").read_text("utf-8"))
    for symbol, field in (("HOOKS", "bin_entries"), ("SETUP", "script_entries")):
        declarations = [
            node.value for node in publication.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == symbol for target in node.targets)
        ]
        assert len(declarations) == 1, f"Expected one literal publication list for {symbol}"
        published = set(ast.literal_eval(declarations[0]))
        assert published <= set(profile[field]), f"Unowned published {symbol}: {published - set(profile[field])}"


WORKER = r"""
import importlib.util,sys
from pathlib import Path
repo,root,mode,ready=map(Path,sys.argv[1:5])
payload=repo/"plugins/agent-worktrees"
sys.path.insert(0,str(payload/"scripts"))
import runtime_install_governance as governance
path=repo/"libs/installer-engine/installer_engine_uninstall.py"
spec=importlib.util.spec_from_file_location("finite_custody",path)
finite=importlib.util.module_from_spec(spec);sys.modules[spec.name]=finite;spec.loader.exec_module(finite)
if str(mode)=="publisher":
    held=governance.Governance(root,payload).held(timeout=1)
else:
    profile=finite.Profile(finite.BASE,finite.RESOLVERS,finite.RUNTIME_MODULES,(),"agent_worktrees")
    runtime=finite._load(payload/"scripts/versioned_runtime.py")
    owner=finite._ownership(payload,"agent-worktrees",root)
    held=finite._custody(payload,"agent-worktrees",root,owner,
                        finite._targets(payload,"agent-worktrees",root,profile,runtime),runtime)
with held:
    if str(ready)!="-":
        ready.write_text("held",encoding="utf-8")
        sys.stdin.buffer.read(1)
    print("acquired",flush=True)
"""


@pytest.mark.parametrize("first", ("publisher", "remover"))
def test_real_publisher_and_remover_share_whole_install_custody(tmp_path: Path, first: str) -> None:
    home = tmp_path / "home"
    root = home / ".agent-worktrees"
    root.mkdir(parents=True)
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": "agent-worktrees",
        "source": {"plugin": "agent-worktrees", "kind": "local"},
    }), encoding="utf-8")
    runtime = root / "versions/1.0.0/runtime.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"selected runtime\n")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_"))
    }
    environment.update(HOME=str(home), USERPROFILE=str(home))
    ready = tmp_path / "holder-ready"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    common = [sys.executable, "-I", "-B", "-c", WORKER, str(REPO), str(root)]
    holder = subprocess.Popen(
        [*common, first, str(ready)], cwd=tmp_path, env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags,
    )
    second = "remover" if first == "publisher" else "publisher"
    try:
        deadline = time.monotonic() + 15
        while not ready.is_file() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not ready.is_file():
            if holder.poll() is not None:
                stdout, stderr = holder.communicate(timeout=2)
                pytest.fail(f"Holder did not acquire custody: {stdout!r} {stderr!r}")
            pytest.fail("Holder did not announce custody before its deadline")
        lock = home / ".agent-worktrees.command-binding-publication.lock"
        identity = (lock.stat().st_dev, lock.stat().st_ino)
        refused = subprocess.run(
            [*common, second, "-"], cwd=tmp_path, env=environment,
            capture_output=True, timeout=15, check=False, **flags,
        )
        assert refused.returncode != 0, refused.stdout + refused.stderr
        assert b"lock" in refused.stderr.lower(), refused.stderr
        assert holder.poll() is None
        assert runtime.read_bytes() == b"selected runtime\n"
        assert (lock.stat().st_dev, lock.stat().st_ino) == identity
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.stdin = None
        stdout, stderr = holder.communicate(timeout=15)
    assert holder.returncode == 0, stdout + stderr
    admitted = subprocess.run(
        [*common, second, "-"], cwd=tmp_path, env=environment,
        capture_output=True, timeout=15, check=False, **flags,
    )
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr
    assert b"acquired" in admitted.stdout
    assert runtime.read_bytes() == b"selected runtime\n"
