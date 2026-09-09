#!/usr/bin/env python3
"""Portable, stdlib-only probe for the `supervise serve` daemon's own live
version-staleness check (#2259).

Drives a REAL, installed ``agent-dispatch`` daemon process, in an isolated HOME,
against a genuinely SEPARATE, real "newer" ``versions/<v>`` slot -- exercising
the actual process-level handoff (real spawn, real single-instance lease
release/reacquire, and a genuine live successor that STAYS put because it
converges on its own stamped version), not just the in-process unit tests in
``plugins/agent-dispatch/tests/test_supervisor_daemon.py``.

The "newer" slot is built the SAME way the real installer builds a slot: a
signed base Python + ``venv --copies`` on Windows (falling back to unsigned
``uv venv`` only with a loud warning -- see ``docs/install-contract.md``
§SAC-safe launchers), then ``uv pip install <plugin-source>`` against the SAME
already-installed plugin payload (cache-hits the already-resolved dependency
graph, so this normally completes in single-digit seconds). Its
``agent_dispatch/_build_info.py`` is then stamped with a distinct fake version
string, mirroring what the real installer's ``scripts/stamp_build_info.py``
does per slot (see ``durable-vs-versioned-runtime``). This is what makes the
successor genuinely converge instead of endlessly re-triggering itself: from
the successor's own perspective its running version now equals the marker, so
it is no longer stale.

**Lock tracking is state-based, not pid-based.** Windows enforces a mandatory
byte-range lock over the single-instance lock file (``msvcrt.locking``), so an
external ``open(...).read()`` of it throws ``PermissionError`` continuously
(not intermittently) while ANY process holds it -- there is no reliable way to
read "whose pid" from outside. Every check here instead uses
``single_instance.is_locked()`` and the fact that the OS releases a process's
hold the instant that process exits: "still held right after the confirmed
exit of the process we were watching" is already proof a NEW process acquired
it, with no pid-parsing needed. Cleanup follows the same logic -- each check
gives its daemon a unique ``--machine`` tag and kills by command-line match at
teardown, rather than tracking a pid.

**Why this exists as a clean-room check and not only a unit test:** the check
is DEFAULT-ON / opt-out (there is no launch-path protocol in this harness to
flip an opt-in env var before a daemon's first boot, so an opt-in gate would
never actually activate for a real operator). Default-on behavior warrants
end-to-end validation before it ships to everyone -- a real process must really
hand off, and a healthy/non-stale daemon must NOT spuriously self-terminate.

It is the reusable core of the clean-room `agent-dispatch-supervisor-self-update`
scenario: the thin scenario.sh installs + provisions the plugin on a fresh box
and runs this probe, so the process-level orchestration is verifiable
independently of Docker (it runs on any OS with `uv` + the agent-dispatch
venv, including this dev box).

Checks (each prints `PROBE: <name> PASS|FAIL <detail>`):
  default-on-handoff   WITH NO ENV VAR SET, a stale current-version marker
                       makes the daemon hand off for real: it exits with the
                       distinct self-update code, the singleton lease is held
                       again by a genuinely new successor, and that successor
                       CONVERGES (stays held -- no respawn loop).
  opt-out-disables     the same stale marker, but with the opt-out env var
                       set, leaves the ORIGINAL daemon alive with no handoff.
  no-marker-fail-safe  with no current-version marker at all, the (default-on)
                       daemon also stays alive -- the fail-safe default when
                       there is nothing to be stale against.

Usage:
    python supervisor_self_update_probe.py --python <agent_dispatch-venv-python> \
        --source <plugin-source-dir-with-pyproject.toml> [--checks a,b]

Exit 0 iff every selected check PASSes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

ALL_CHECKS = ["default-on-handoff", "opt-out-disables", "no-marker-fail-safe"]

# Building the second real venv (uv venv + uv pip install, cache-hit deps).
_SLOT_BUILD_TIMEOUT_S = 180.0
# How long a stale-marker daemon gets to complete a real handoff (coordinator
# autostart + a 1s self-update poll interval + process spawn overhead).
_HANDOFF_TIMEOUT_S = 45.0
# How long the successor must be observed staying alive AFTER the handoff to
# call it "converged" (did not immediately consider itself stale again).
_CONVERGENCE_WINDOW_S = 5.0
# How long a NON-handoff daemon must be observed staying alive past its own
# poll window before we call it "did not spuriously self-terminate".
_STAY_ALIVE_WINDOW_S = 6.0


def _kill_matching_processes(needle: str) -> None:
    """Best-effort: kill every process whose command line contains
    ``needle``.

    Used to clean up a successor daemon this probe spawned (a real,
    breakaway-detached process this script never tracks by pid -- see
    ``Ctx.is_locked``). Never raises; a failed cleanup only leaves a harmless
    (isolated-HOME, distinctly-tagged) stray process behind, not a check
    failure.
    """
    try:
        if sys.platform == "win32":
            ps = shutil.which("powershell") or shutil.which("pwsh")
            if not ps:
                return
            cmd = (
                "Get-CimInstance Win32_Process | "
                f"Where-Object {{ $_.CommandLine -like '*{needle}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                "-ErrorAction SilentlyContinue }"
            )
            subprocess.run(
                [ps, "-NoProfile", "-NonInteractive", "-Command", cmd],
                capture_output=True, timeout=20,
            )
        else:
            subprocess.run(["pkill", "-f", needle], capture_output=True, timeout=20)
    except Exception:
        pass


def _kill_pid(pid: int | None) -> None:
    """Best-effort: kill a single process by pid (tree-kill on Windows).

    Used for the autostarted coordinator (see ``Ctx.coordinator_pid``), which
    carries no command-line tag to match on. Never raises.
    """
    if not pid:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True, timeout=20,
            )
        else:
            import signal

            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _uv() -> str:
    return shutil.which("uv") or "uv"


def _signed_base_python() -> str | None:
    """A PSF-signed base interpreter on Windows, or ``None`` if none is found.

    Mirrors the SAC-safe-launchers rule in ``docs/install-contract.md``: a
    default ``uv``-managed venv python is unsigned and Smart App Control can
    hard-block it, so the real installer resolves a signed base interpreter
    (``py -3.x`` whose ``Get-AuthenticodeSignature`` reports ``Valid``) and
    builds with ``--copies``. This probe's synthetic "newer slot" follows the
    same rule for fidelity, not just because it's more portable -- a signed
    ``--copies`` venv is also a standalone real interpreter, with no separate
    launcher/trampoline process indirection to reason about.
    """
    if sys.platform != "win32":
        return None
    launcher = shutil.which("py")
    if not launcher:
        return None
    r = subprocess.run(
        [launcher, "-0p"], capture_output=True, text=True, timeout=15,
    )
    candidates = [
        line.strip().split()[-1]
        for line in (r.stdout or "").splitlines()
        if line.strip() and os.path.isfile(line.strip().split()[-1])
    ]
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return None
    for candidate in candidates:
        check = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-AuthenticodeSignature '{candidate}').Status"],
            capture_output=True, text=True, timeout=15,
        )
        if (check.stdout or "").strip() == "Valid":
            return candidate
    return None


def _build_slot_venv(vdir: str, env: dict) -> tuple[bool, str]:
    """Build the fake slot venv the SAME way the real installer would.

    On Windows: a signed base Python + ``venv --copies`` (a real standalone
    signed interpreter, no trampoline) -- falls back to ``uv venv`` only when
    no signed Python is found, exactly as ``docs/install-contract.md`` §SAC
    rule 1 specifies (with a loud warning, surfaced via the returned detail
    string). On POSIX there is no signing concept; ``uv venv`` is fine there.
    Returns ``(used_signed_copies, detail)``.
    """
    signed = _signed_base_python()
    if signed:
        r = subprocess.run(
            [signed, "-m", "venv", "--copies", vdir], env=env,
            capture_output=True, text=True, timeout=_SLOT_BUILD_TIMEOUT_S,
        )
        if r.returncode != 0:
            raise RuntimeError(f"venv --copies failed for the fake newer slot: {r.stderr}")
        return True, f"built with a signed base Python + --copies ({signed})"
    r = subprocess.run(
        [_uv(), "venv", vdir], env=env,
        capture_output=True, text=True, timeout=_SLOT_BUILD_TIMEOUT_S,
    )
    if r.returncode != 0:
        raise RuntimeError(f"uv venv failed for the fake newer slot: {r.stderr}")
    warn = "" if sys.platform != "win32" else (
        " -- WARNING: no signed base Python found; falling back to an "
        "unsigned uv venv (SAC-blocked on a real Windows host, see "
        "docs/install-contract.md)"
    )
    return False, f"built with uv venv{warn}"


def _site_packages_dir(venv_dir: str) -> str:
    if sys.platform == "win32":
        return os.path.join(venv_dir, "Lib", "site-packages")
    libdir = os.path.join(venv_dir, "lib")
    pyver_dir = next(
        (d for d in os.listdir(libdir) if d.startswith("python")), None
    ) if os.path.isdir(libdir) else None
    if pyver_dir:
        return os.path.join(libdir, pyver_dir, "site-packages")
    return os.path.join(venv_dir, "lib", "site-packages")


class Ctx:
    """An isolated HOME + the machinery to spawn/query a supervisor daemon."""

    def __init__(self, python: str, home: str, source_dir: str):
        self.python = python
        self.home = home
        self.source_dir = source_dir
        # A unique --machine tag per Ctx: gives each test run an isolated
        # lease scope (no cross-test collisions) and a precise command-line
        # needle for best-effort teardown of any breakaway-detached successor
        # this check spawned (we deliberately do NOT track successor pids --
        # see Ctx.is_locked).
        self.machine_tag = f"cr-probe-{os.getpid()}-{id(self):x}"
        self.env = dict(os.environ)
        self.env.update(
            USERPROFILE=home, HOME=home,
            AGENT_DISPATCH_HOST="127.0.0.1",
            PYTHONUTF8="1",
        )
        for k in ("AGENT_DISPATCH_PORT", "AGENT_DISPATCH_URL", "AGENT_DISPATCH_ENDPOINT"):
            self.env.pop(k, None)
        self.root = os.path.join(home, ".agent-dispatch")
        self.last_slot_build_detail = ""

    def _query(self, code: str, timeout: float = 20.0) -> str:
        r = subprocess.run(
            [self.python, "-c", code], env=self.env,
            capture_output=True, text=True, timeout=timeout,
        )
        return (r.stdout or "").strip()

    def running_version(self) -> str:
        return self._query(
            "import agent_dispatch; print(agent_dispatch.__version__)"
        )

    def self_update_exit_code(self) -> int:
        return int(self._query(
            "from agent_dispatch.supervisor_daemon import SELF_UPDATE_EXIT_CODE;"
            "print(SELF_UPDATE_EXIT_CODE)"
        ))

    def lock_path(self, machine: str, env_name: str) -> str:
        code = (
            "from agent_dispatch.supervisor_daemon import supervisor_lease_scope;"
            "from agent_dispatch.single_instance import lock_path_for;"
            "from agent_dispatch.config import run_dir;"
            f"print(lock_path_for(run_dir(), supervisor_lease_scope({machine!r}, {env_name!r})))"
        )
        return self._query(code)

    def make_stale_newer_slot(self) -> str:
        """Build a REAL, physically independent "newer" version slot and name
        it in ``current-version`` -- not a copied interpreter binary.

        Built the SAME way the real installer builds a slot (signed base
        Python + ``venv --copies`` on Windows, falling back to unsigned ``uv
        venv`` only with a loud warning -- see ``docs/install-contract.md``
        §SAC-safe launchers), so this is a real, standalone interpreter with
        no separate launcher/trampoline indirection. ``uv pip install
        <source>`` then cache-hits the already-resolved dependency graph
        (normally single-digit seconds). Its own physical
        ``agent_dispatch/_build_info.py`` is then stamped with a distinct fake
        version -- mirroring the real installer's per-slot stamping, and
        critically making the SUCCESSOR converge (its own running version then
        equals the marker) instead of endlessly re-triggering itself.
        """
        running = self.running_version()
        fake = f"{running}-cr-fake-newer"
        vdir = os.path.join(self.root, "versions", fake)
        _used_signed, build_detail = _build_slot_venv(vdir, self.env)
        self.last_slot_build_detail = build_detail
        slot_py = os.path.join(
            vdir, "Scripts", "python.exe"
        ) if sys.platform == "win32" else os.path.join(vdir, "bin", "python")
        r = subprocess.run(
            [_uv(), "pip", "install", self.source_dir, "--python", slot_py],
            env=self.env, capture_output=True, text=True, timeout=_SLOT_BUILD_TIMEOUT_S,
        )
        if r.returncode != 0:
            raise RuntimeError(f"uv pip install failed for the fake newer slot: {r.stderr}")
        bi = os.path.join(_site_packages_dir(vdir), "agent_dispatch", "_build_info.py")
        content = open(bi, encoding="utf-8").read()
        stamped = content.replace('"version": "",', f'"version": {fake!r},')
        if stamped == content:
            raise RuntimeError(f"could not stamp a fake version into {bi}")
        with open(bi, "w", encoding="utf-8") as f:
            f.write(stamped)
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, "current-version"), "w", encoding="utf-8") as f:
            f.write(fake)
        return fake

    def is_locked(self, lockp: str) -> bool:
        """Whether the singleton lease at ``lockp`` is CURRENTLY held by a
        live process.

        Deliberately does NOT parse the lock file's raw content: Windows
        enforces a mandatory byte-range lock over it (``msvcrt.locking``), so
        an external ``open(...).read()`` throws ``PermissionError``
        continuously (not intermittently) while ANY process holds it -- there
        is no reliable way to read "whose pid" from outside. Lock *state* is
        enough: the OS releases a process's hold the instant that process
        exits, so "still held right after the original's confirmed exit" is
        already proof of a NEW holder, with no pid-parsing needed.
        """
        code = (
            "from agent_dispatch.single_instance import is_locked;"
            f"print(is_locked({lockp!r}))"
        )
        return self._query(code) == "True"

    def coordinator_pid(self) -> int | None:
        """The pid of the coordinator autostarted for this isolated HOME, or
        ``None``.

        ``supervise serve`` autostarts its own local coordinator
        (``_ensure_local_coordinator``) the first time it needs one, and that
        coordinator is a **breakaway-detached** process this probe never
        otherwise tracks. Its cmdline carries no ``--machine`` tag (only
        ``supervise serve`` does), so ``_kill_matching_processes`` alone
        cannot reach it -- read its pid from the zdd routing table (the same
        source ``has_live_local_coordinator`` trusts) instead, so teardown
        cleans it up too instead of leaking it onto the host for every check
        this probe runs.
        """
        code = (
            "from zdd.routing import read_active_endpoint;"
            "from agent_dispatch.config import routing_dir;"
            "ep = read_active_endpoint(routing_dir());"
            "print(ep.pid if ep is not None else '')"
        )
        out = self._query(code)
        return int(out) if out.strip().isdigit() else None

    def spawn_supervise(self, *, extra_env=None):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        argv = [
            self.python, "-m", "agent_dispatch", "supervise", "serve",
            "--machine", self.machine_tag, "--env", "default",
            "--no-declared", "--interval", "1",
        ]
        kw: dict = {}
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(
            argv, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **kw,
        )


class Result:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.detail: list[str] = []

    def check(self, cond: bool, msg: str) -> bool:
        if not cond:
            self.ok = False
            self.detail.append("FAILED: " + msg)
        else:
            self.detail.append("ok: " + msg)
        return cond

    def emit(self) -> bool:
        status = "PASS" if self.ok else "FAIL"
        summary = "; ".join(d for d in self.detail if d.startswith("FAILED")) or "; ".join(self.detail[-3:])
        print(f"PROBE: {self.name} {status} {summary}")
        return self.ok


# --------------------------------------------------------------------------


def check_default_on_handoff(python: str, source_dir: str) -> Result:
    """WITH NO ENV VAR SET, a stale marker makes the daemon really hand off,
    and the successor converges (stays alive, does not respawn again).

    NOTE on lock tracking: this deliberately checks lock STATE
    (``single_instance.is_locked``), not pid identity. Windows enforces a
    mandatory byte-range lock over the lock file (``msvcrt.locking``), so an
    external ``open(...).read()`` throws ``PermissionError`` continuously (not
    intermittently) while ANY process holds it -- there is no reliable way to
    read "whose pid" from outside. State is sufficient: the OS releases a
    process's hold the instant that process exits, so "the lease is held
    again right after the ORIGINAL's own confirmed exit" is already proof a
    NEW (successor) process acquired it -- no pid needed.
    """
    r = Result("default-on-handoff")
    home = tempfile.mkdtemp(prefix="cr-su-don-")
    c = Ctx(python, home, source_dir)
    proc = None
    try:
        fake_ver = c.make_stale_newer_slot()
        r.check(
            bool(fake_ver),
            f"real, independent newer slot + current-version marker created "
            f"({c.last_slot_build_detail})",
        )
        expected_exit = c.self_update_exit_code()
        lockp = c.lock_path(c.machine_tag, "default")
        # Deliberately no AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE in extra_env --
        # this is the point: the check must be armed by DEFAULT.
        extra_env = {
            "AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S": "1",
            "AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_COOLDOWN_S": "1",
        }
        proc = c.spawn_supervise(extra_env=extra_env)
        acquired = False
        for _ in range(40):
            if c.is_locked(lockp):
                acquired = True
                break
            time.sleep(0.25)
        r.check(acquired, "original daemon acquired its singleton lease before handing off")
        try:
            rc = proc.wait(timeout=_HANDOFF_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            rc = None
        r.check(
            rc == expected_exit,
            f"original daemon exited with the self-update code "
            f"(rc={rc}, expected={expected_exit}) -- no opt-in env var was set",
        )
        # The original's OS process has now fully exited (proc.wait returned),
        # which necessarily released ITS hold on the lock (kernel-enforced,
        # regardless of our own code's explicit release() call). Any
        # CONTINUED lock-holding from here on can only belong to a NEW
        # (successor) process.
        held_again = False
        for _ in range(80):
            if c.is_locked(lockp):
                held_again = True
                break
            time.sleep(0.25)
        r.check(
            held_again,
            "the singleton lease is held AGAIN after the original's own "
            "process fully exited -- proof a NEW (successor) process acquired it",
        )
        time.sleep(_CONVERGENCE_WINDOW_S)
        r.check(
            c.is_locked(lockp),
            f"the successor CONVERGED -- the lease is still held "
            f"{_CONVERGENCE_WINDOW_S}s later (no respawn gap)",
        )
        return r
    finally:
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
        _kill_matching_processes(c.machine_tag)
        _kill_pid(c.coordinator_pid())
        shutil.rmtree(home, ignore_errors=True)


def check_opt_out_disables(python: str, source_dir: str) -> Result:
    """The escape hatch: the same stale marker, opted OUT, must not hand off."""
    r = Result("opt-out-disables")
    home = tempfile.mkdtemp(prefix="cr-su-oo-")
    c = Ctx(python, home, source_dir)
    proc = None
    try:
        fake_ver = c.make_stale_newer_slot()
        r.check(bool(fake_ver), "real, independent newer slot + current-version marker created")
        extra_env = {
            "AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE": "0",
            "AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S": "1",
        }
        proc = c.spawn_supervise(extra_env=extra_env)
        time.sleep(_STAY_ALIVE_WINDOW_S)
        r.check(
            proc.poll() is None,
            f"original daemon is STILL ALIVE past the poll window with the "
            f"opt-out set, despite the stale marker (poll={proc.poll()})",
        )
        return r
    finally:
        try:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            pass
        time.sleep(0.5)
        _kill_matching_processes(c.machine_tag)
        _kill_pid(c.coordinator_pid())
        shutil.rmtree(home, ignore_errors=True)


def check_no_marker_fail_safe(python: str, source_dir: str) -> Result:
    """No current-version marker at all -- the default-on daemon stays put."""
    r = Result("no-marker-fail-safe")
    home = tempfile.mkdtemp(prefix="cr-su-nm-")
    c = Ctx(python, home, source_dir)
    proc = None
    try:
        os.makedirs(c.root, exist_ok=True)  # root exists; NO current-version file
        marker = os.path.join(c.root, "current-version")
        r.check(not os.path.exists(marker), "no current-version marker present")
        extra_env = {"AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S": "1"}
        proc = c.spawn_supervise(extra_env=extra_env)
        time.sleep(_STAY_ALIVE_WINDOW_S)
        r.check(
            proc.poll() is None,
            f"daemon is STILL ALIVE past the poll window with nothing to be "
            f"stale against (poll={proc.poll()})",
        )
        return r
    finally:
        try:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            pass
        time.sleep(0.5)
        _kill_matching_processes(c.machine_tag)
        _kill_pid(c.coordinator_pid())
        shutil.rmtree(home, ignore_errors=True)


CHECKS = {
    "default-on-handoff": check_default_on_handoff,
    "opt-out-disables": check_opt_out_disables,
    "no-marker-fail-safe": check_no_marker_fail_safe,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True, help="the installed agent-dispatch venv python")
    ap.add_argument(
        "--source", required=True,
        help="the plugin source dir (containing pyproject.toml) used to build a "
             "real, independent 'newer' slot venv",
    )
    ap.add_argument("--checks", default=",".join(ALL_CHECKS),
                    help="comma-separated subset of: " + ",".join(ALL_CHECKS))
    args = ap.parse_args()
    selected = [x.strip() for x in args.checks.split(",") if x.strip()]
    failed = 0
    for name in selected:
        fn = CHECKS.get(name)
        if not fn:
            print(f"PROBE: {name} FAIL unknown check")
            failed += 1
            continue
        try:
            res = fn(args.python, args.source)
            if not res.emit():
                failed += 1
        except Exception as e:  # a probe crash is a FAIL, not a wedge
            print(f"PROBE: {name} FAIL probe-exception {type(e).__name__}: {e}")
            failed += 1
    print(f"PROBE-SUMMARY: {len(selected) - failed}/{len(selected)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
