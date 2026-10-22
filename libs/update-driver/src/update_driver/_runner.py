"""Private caller-owned shim; not a public arbitrary-command entry point."""

import json
import importlib.util
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_update_driver_participant", Path(__file__).with_name("participant.py"),
)
_participant = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_participant)
participant = _participant.participant


def main() -> int:
    root, identifier, witness, procutil_path = sys.argv[1:]
    with participant(root=Path(root), request_id=identifier, witness=witness):
        return execute(root, identifier, witness, procutil_path)


def execute(root: str, identifier: str, witness: str, procutil_path: str) -> int:
    database = Path(root) / "queue.sqlite3"
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        row = db.execute(
            "SELECT runner,state,spec FROM requests WHERE id=?", (identifier,),
        ).fetchone()
    if row is None or row[1] != "running":
        raise RuntimeError("runner has no active request")
    runner = json.loads(row[0])
    context = dict(json.loads(row[2])["context"])
    for key in runner.get("context_keys", ()):
        os.environ.pop(key, None)
        if key in context:
            os.environ[key] = context[key]
    os.chdir(runner["cwd"])
    os.environ.update(dict(runner.get("env", ())))
    os.environ["UPDATE_DRIVER_WITNESS"] = witness
    os.environ["PYTHONUNBUFFERED"] = "1"
    with (Path(root) / f"{identifier}.log").open("ab", buffering=0) as logfile:
        os.dup2(logfile.fileno(), 1)
        os.dup2(logfile.fileno(), 2)
        if os.name == "nt":
            # CRT exec on Windows retires the shim before its replacement
            # settles. Retain the caller-owned Job root until the child exits.
            proc_spec = importlib.util.spec_from_file_location("agent_procutil", procutil_path)
            procutil = importlib.util.module_from_spec(proc_spec)
            sys.modules["agent_procutil"] = procutil
            proc_spec.loader.exec_module(procutil)
            return subprocess.call(
                runner["argv"], stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
                **procutil.no_window_kwargs(),
            )
        os.execv(runner["argv"][0], runner["argv"])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
