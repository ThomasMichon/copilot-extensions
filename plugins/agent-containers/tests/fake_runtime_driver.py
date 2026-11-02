"""A fake external runtime driver implementing the agent-containers protocol.

Test-only. Members are directories under ``$FAKE_DRIVER_ROOT``; their state
lives in ``$FAKE_DRIVER_ROOT/state.json`` and every invocation is appended to
``calls``. ``exec``/``exec-stream`` run the requested argv on the host inside
the member directory, which is enough to exercise env/workdir/stdin/stdout
plumbing without a real container runtime.

Knobs (environment):
  FAKE_DRIVER_CAPS      JSON object returned as ``capabilities``.
  FAKE_DRIVER_FAIL      ``<verb>:<code>`` -- fail that verb with that code.
  FAKE_DRIVER_PROTOCOL  protocolVersion to answer with (default 1).

Leading options (set in a manifest command):
  --root <dir>          use ``<dir>`` instead of ``$FAKE_DRIVER_ROOT``.
  --hang <verb>         that verb never answers; it first starts a descendant
                        that holds stdout/stderr and records its pid in
                        ``<root>/hung-descendant.pid``.
  --delay <verb> <sec>  answer that verb only after ``sec`` seconds.
  --bad-bytes <verb> <stream>  answer that verb with invalid UTF-8 on
                        ``stdout`` or ``stderr``.
  --invalid-tail        ``list`` appends one malformed member after the valid ones.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ["FAKE_DRIVER_ROOT"])
STATE = ROOT / "state.json"


def _load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"members": {}, "calls": [], "next_id": 1}


def _save(state: dict) -> None:
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2))
    os.replace(temporary, STATE)


def _respond(payload: dict, code: int = 0) -> int:
    version = int(os.environ.get("FAKE_DRIVER_PROTOCOL", "1"))
    print(json.dumps({"protocolVersion": version, **payload}))
    return code


def _error(code: str, message: str) -> int:
    return _respond({"ok": False, "error": {"code": code, "message": message}}, 1)


def _member_view(name: str, member: dict) -> dict:
    return {
        "name": name, "id": member["id"], "fleet": member["fleet"],
        "instance": member["instance"], "state": member["state"],
        "status": member["state"].capitalize(), "image": "example/image:1",
        "repo": "example-repo",
    }


def _member_dir(name: str) -> Path:
    path = ROOT / "members" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _host_cwd(name: str, workdir: str | None) -> Path:
    base = _member_dir(name)
    if not workdir:
        return base
    path = base / workdir.lstrip("/")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lock_state():
    """Serialize concurrent invocations (parallel fleet listings) portably.

    The descriptor is not inherited, so ``exec-stream`` releases it on exec.
    """
    handle = open(ROOT / "state.lock", "a+")
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return handle
            except OSError:
                time.sleep(0.05)
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def main(argv: list[str]) -> int:
    global ROOT, STATE
    hang = delay = bad = None
    invalid_tail = False
    while argv[:1] in (["--root"], ["--hang"], ["--delay"], ["--bad-bytes"], ["--invalid-tail"]):
        if argv[0] == "--invalid-tail":
            invalid_tail, argv = True, argv[1:]
        elif argv[0] == "--bad-bytes":
            bad, argv = (argv[1], argv[2]), argv[3:]
        elif argv[0] == "--root":
            ROOT, argv = Path(argv[1]), argv[2:]
            STATE = ROOT / "state.json"
        elif argv[0] == "--hang":
            hang, argv = argv[1], argv[2:]
        else:
            delay, argv = (argv[1], float(argv[2])), argv[3:]
    ROOT.mkdir(parents=True, exist_ok=True)
    if argv[:1] == [hang]:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        (ROOT / "hung-descendant.pid").write_text(str(child.pid))
        time.sleep(300)
        return 1
    if delay is not None and argv[:1] == [delay[0]]:
        time.sleep(delay[1])
    if bad is not None and argv[:1] == [bad[0]]:
        stream = sys.stdout if bad[1] == "stdout" else sys.stderr
        stream.buffer.write(b"\xff\xfe not utf-8\n")
        stream.flush()
        return 0 if bad[1] == "stderr" else 1
    lock = _lock_state()  # noqa: F841 -- held for the life of this invocation
    if len(argv) < 2 or "--json" not in argv[1:]:
        print("usage: fake_runtime_driver.py <verb> --json", file=sys.stderr)
        return 2
    verb = argv[0]
    if verb == "exec-stream":
        request = json.loads(argv[argv.index("--request") + 1])
    else:
        request = json.loads(sys.stdin.read())
    state = _load()
    state["calls"].append({
        "verb": verb, "request": request,
    })
    _save(state)
    if request.get("protocolVersion") != 1:
        return _error("unsupported_protocol", "expected protocolVersion 1")
    fail = os.environ.get("FAKE_DRIVER_FAIL", "")
    if fail.split(":", 1)[0] == verb:
        return _error(fail.split(":", 1)[1], f"injected {verb} failure")
    members = state["members"]
    fleet = request["fleet"]

    if verb == "capabilities":
        caps = json.loads(os.environ.get("FAKE_DRIVER_CAPS", "{}"))
        return _respond({"ok": True, "driver": {"name": "fake", "version": "1"},
                         "capabilities": caps})
    if verb == "provision":
        member = request["member"]
        name = member["name"]
        if name in members:
            return _error("conflict", f"{name} exists")
        members[name] = {
            "id": f"{state['next_id']:012x}", "fleet": member["fleet"],
            "instance": member["instance"], "state": "running",
            "environment": request.get("environment", {}),
        }
        state["next_id"] += 1
        _member_dir(name)
        _save(state)
        return _respond({"ok": True, "member": _member_view(name, members[name])})
    if verb == "list":
        listed = [
            _member_view(name, member)
            for name, member in sorted(members.items())
            if member["fleet"] == fleet
        ]
        if invalid_tail:
            listed.append({"name": "broken", "fleet": fleet, "state": "running"})
        return _respond({"ok": True, "members": listed})

    if verb == "exec-stream":
        name = request["name"]
        if name not in members or request.get("id") not in (None, members[name]["id"]):
            print(f"no member {name} at the requested incarnation", file=sys.stderr)
            return 125
        env = os.environ.copy()
        env.update(request.get("env") or {})
        env["FAKE_MEMBER_USER"] = request.get("user") or ""
        os.chdir(_host_cwd(name, request.get("workdir")))
        os.execvpe(request["argv"][0], request["argv"], env)

    name = request.get("name")
    if name not in members:
        return _error("not_found", f"no member {name}")
    member = members[name]
    if request.get("id") and request["id"] != member["id"]:
        return _error("conflict", "identity changed")
    if verb == "inspect":
        return _respond({"ok": True, "member": _member_view(name, member)})
    if verb in {"start", "stop"}:
        member["state"] = "running" if verb == "start" else "exited"
        _save(state)
        return _respond({"ok": True})
    if verb == "remove":
        if member["state"] == "running" and not request.get("force"):
            return _error("conflict", "member is running")
        del members[name]
        shutil.rmtree(ROOT / "members" / name, ignore_errors=True)
        _save(state)
        return _respond({"ok": True})
    if verb == "exec":
        env = os.environ.copy()
        env.update(request.get("env") or {})
        env["FAKE_MEMBER_USER"] = request.get("user") or ""
        done = subprocess.run(
            request["argv"], input=request.get("stdin"), capture_output=True,
            text=True, env=env, cwd=_host_cwd(name, request.get("workdir")),
            timeout=request.get("timeoutSeconds") or 30,
        )
        return _respond({"ok": True, "exitCode": done.returncode,
                         "stdout": done.stdout, "stderr": done.stderr})
    if verb == "copy-in":
        source = Path(request["source"])
        target = _member_dir(name) / request["target"].lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
        return _respond({"ok": True})
    return _error("unsupported", f"unknown verb {verb}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
