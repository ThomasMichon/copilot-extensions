"""Admit native interpreter bytecode owners without relaxing metadata ownership."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil import python_cache as cache
from agent_procutil import python_cache_permissions as permissions
from agent_procutil import python_cache_store as store


@pytest.mark.skipif(os.name != "nt", reason="Windows native TokenOwner boundary")
def test_real_import_retains_bytecode_and_metadata_owner_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = store.native(tmp_path / "user-cache")
    monkeypatch.setattr(store, "user_cache", lambda: base)
    monkeypatch.setattr(cache, "_store", lambda: store)
    root = tmp_path / "installation"
    target = root / "versions" / "1.2.3"
    record = target / "Lib" / "site-packages" / "fixture.dist-info" / "RECORD"
    record.parent.mkdir(parents=True)
    record.write_text("fixture.py,sha256=" + "A" * 43 + ",12\n", encoding="utf-8")
    (target / ".install-complete.json").write_text(json.dumps({
        "version": target.name, "completed_at": "fixture", "pid": 1,
        "payload_hash": "a" * 64,
    }), encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (source / "witness.py").write_text("VALUE = 42\n", encoding="utf-8")
    selected = cache.policy_arguments(root, runtime=target)
    assert selected[0] == "-X"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and fixture-only import.
        [
            sys.executable, "-I", "-B", "-c",
            "import sys;sys.pycache_prefix=sys.argv[2];sys.dont_write_bytecode=False;"
            "sys.path.insert(0,sys.argv[1]);import witness;"
            "assert witness.VALUE==42;print(witness.__cached__)",
            str(source), selected[1].split("=", 1)[1],
        ],
        capture_output=True, text=True, timeout=20, check=True, **no_window_kwargs(),
    )
    compiled = Path(result.stdout.strip())
    assert compiled.is_file() and compiled.is_relative_to(base)
    permissions._windows_check(
        compiled, ancestor=False, private=True, bytecode_descendant=True,
    )
    owner = permissions._owner()
    current, token_owner = owner._current_sids(os.getpid())
    if token_owner != current:
        with pytest.raises(PermissionError, match="ownership is not private"):
            permissions.check_file(compiled)
        monkeypatch.setattr(owner, "_current_sids", lambda _pid: (current, current))
        with pytest.raises(PermissionError, match="ownership is not private"):
            permissions._windows_check(
                compiled, ancestor=False, private=True, bytecode_descendant=True,
            )
        assert compiled.is_file()
