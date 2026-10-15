"""Minimal owned process fixture: deliberately no pytest or test-module imports."""

import subprocess
import sys
import time
from pathlib import Path

from agent_procutil import no_window_kwargs

from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import child_fts_owner


def main():
    role, path, *args = sys.argv[1:]
    directory = Path(path)
    if role == "parent":
        store = MultiModelStore(directory)
        with (store._source_domain.write(store.content_store.db),
              store._source_domain.fts_handoff() as nonce):
            child = subprocess.Popen(  # noqa: S603 -- fixed fixture and owned paths
                [sys.executable, "-I", __file__, "builder", path, nonce],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **no_window_kwargs(),
            )
            (directory / "child-pid").write_text(str(child.pid), encoding="utf-8")
            if child.wait(timeout=45) != 0:
                raise RuntimeError("fixture builder failed")
        return
    with child_fts_owner(path, args[0]):
        (directory / "builder-ready").write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 35
        while not (directory / "release-builder").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("fixture builder not released")
            time.sleep(0.05)
        import lancedb

        table = lancedb.connect(path).open_table("chunks")
        table.optimize()
        table.create_fts_index("content", replace=True)
    (directory / "builder-finished").write_text("done", encoding="utf-8")
    while not (directory / "exit-builder").exists() and time.monotonic() < deadline:
        time.sleep(0.05)


if __name__ == "__main__":
    main()
