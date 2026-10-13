"""One-shot, unpaid Linux-container OS proof. No Copilot, auth or eval launch."""

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid
try:
    import pwd
except ModuleNotFoundError:
    pwd = None

import controller
import validation

WORKSPACE = Path("/home/operator/native-cli-repo")
DEFAULT_SESSION = "native-os-default-probe"
SOURCE = Path(__file__).resolve()
VALIDATOR = Path(validation.__file__).resolve()


def command(argv, *, env=None, check=True):
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=15)
    if check and result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {shlex.join(argv)}\n{result.stderr}")
    return result


def worker(argv, *, check=True):
    account = pwd.getpwnam("operator")
    env = {"HOME": account.pw_dir, "USER": "operator", "LOGNAME": "operator",
           "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
           "LANG": "C.UTF-8", "TERM": "xterm-256color", "PYTHONDONTWRITEBYTECODE": "1"}
    return command(["setpriv", "--reuid", str(account.pw_uid), "--regid", str(account.pw_gid),
                    "--init-groups", *argv], env=env, check=check)


def nnp(pid):
    values = (Path("/proc") / str(pid) / "status").read_text().splitlines()
    return next((int(line.split(":", 1)[1].strip()) for line in values if line.startswith("NoNewPrivs:")), None)


def file_hashes(directory):
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def denied(action):
    try:
        action()
    except OSError as error:
        return {"denied": error.errno in (errno.EACCES, errno.EPERM, errno.EROFS),
                "errno": error.errno, "error": type(error).__name__}
    return {"denied": False}


def append(path, text):
    with path.open("a") as stream:
        stream.write(text)


def attempt_probe(destination, lane):
    capsule = validation.BASELINE_DIR
    task = capsule / "task.json"
    seed, errors = validation.seed_baseline()
    if errors:
        raise RuntimeError("; ".join(errors))
    checks = {
        "direct_write": denied(lambda: append(task, "permission-probe")),
        "direct_chmod": denied(lambda: task.chmod(0o600)),
        "direct_rename": denied(lambda: capsule.rename(capsule.with_name("native-cli-baseline-moved"))),
        "trusted_validator_write": denied(lambda: append(VALIDATOR, "permission-probe")),
    }
    root_actions = {
        "write": f"from pathlib import Path; Path({str(task)!r}).open('a').write('permission-probe')",
        "chmod": f"from pathlib import Path; Path({str(task)!r}).chmod(0o600)",
        "rename": f"from pathlib import Path; Path({str(capsule)!r}).rename({str(capsule.with_name('native-cli-baseline-moved'))!r})",
        "validator_write": f"from pathlib import Path; Path({str(VALIDATOR)!r}).open('a').write('permission-probe')",
    }
    for name, code in root_actions.items():
        result = command(["sudo", "-n", "python3", "-c", code], check=False)
        checks[f"sudo_{name}"] = {"denied": result.returncode != 0, "exit_code": result.returncode,
                                  "stderr": result.stderr[:1000]}
    task_copy = WORKSPACE / "task.json"
    original = json.loads(task_copy.read_text())
    original["original_objective"] = "Allowed ordinary workspace mutation during unpaid OS probe."
    task_copy.write_text(json.dumps(original))
    changes = validation.workspace_seed_errors(WORKSPACE, seed)
    report = {"lane": lane, "uid": os.geteuid(), "no_new_privs": nnp(os.getpid()),
              "checks": checks, "workspace_changed": "driven seed input changed: task.json" in changes}
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(destination)
    return report


def probe_errors(report, expected_nnp, expected_uid):
    errors = []
    if report.get("uid") != expected_uid or expected_uid == 0:
        errors.append("probe did not run as the ordinary operator")
    if report.get("no_new_privs") != expected_nnp:
        errors.append("probe has wrong server/process NNP inheritance")
    required = {"direct_write", "direct_chmod", "direct_rename", "trusted_validator_write",
                "sudo_write", "sudo_chmod", "sudo_rename", "sudo_validator_write"}
    checks = report.get("checks", {})
    if set(checks) != required or any(checks[name].get("denied") is not True for name in required):
        errors.append("a protected mutation was allowed or missing")
    if report.get("workspace_changed") is not True:
        errors.append("ordinary workspace mutation was not detected")
    return errors


def arrange_workspace():
    account = pwd.getpwnam("operator")
    WORKSPACE.mkdir()
    task = {key: key for key in ("original_objective", "parent_gate", "current_slice",
                                 "unresolved_user_requests", "decisions", "live_references")}
    files = {"AGENTS.md": "Unpaid OS permission fixture; no agent is launched.\n",
             "task.json": json.dumps(task), "audit-contract.md": "OS boundary probe, not native acceptance.\n",
             "ledger.json": '{"canary":"os-fixture"}\n', ".context-handoff/config.yaml": "mode: auto\n",
             "corpus/shard-000.txt": "record=00000 severity=0\nrecord=00001 severity=1\n"}
    for name, content in files.items():
        path = WORKSPACE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for path in [WORKSPACE, *WORKSPACE.rglob("*")]:
        os.chown(path, account.pw_uid, account.pw_gid)
    return account


def wait_report(path):
    deadline = time.monotonic() + 30
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"bounded pane probe timed out: {path.name}")
        time.sleep(.2)
    return json.loads(path.read_text())


def run_smoke(results):
    if sys.platform != "linux" or os.geteuid() != 0 or not Path("/.dockerenv").is_file():
        raise RuntimeError("root execution in an exclusively owned disposable Linux container is required")
    for tool in ("tmux", "setpriv", "sudo", "visudo", "python3"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"missing prerequisite: {tool}; install only in the ephemeral venue before this script")
    if not os.statvfs(SOURCE.parent).f_flag & os.ST_RDONLY:
        raise RuntimeError("scenario source must be mounted read-only")
    if WORKSPACE.exists() or validation.BASELINE_DIR.exists() or controller.TMUX_SOCKET.exists() or results.exists():
        raise RuntimeError("fresh one-shot venue/results required; refusing reuse or repair")
    if worker(["tmux", "list-sessions"], check=False).returncode == 0:
        raise RuntimeError("pre-existing default server is not owned by this probe")
    account = arrange_workspace()
    results.mkdir(parents=True)
    os.chown(results, account.pw_uid, account.pw_gid)
    servers = []
    report = {"kind": "unpaid-os-permission-smoke", "paid_cli_or_eval": False, "lanes": {}, "errors": []}
    try:
        # Real default daemon exists before the privilege seal, without NNP.
        worker(["tmux", "new-session", "-d", "-s", DEFAULT_SESSION, "sleep 300"])
        default_pid = worker(["tmux", "display-message", "-p", "-t", DEFAULT_SESSION, "#{pid}"]).stdout.strip()
        default_socket = worker(["tmux", "display-message", "-p", "-t", DEFAULT_SESSION, "#{socket_path}"]).stdout.strip()
        if not default_pid.isdecimal() or not default_socket or nnp(int(default_pid)) != 0:
            raise RuntimeError("could not establish the genuine unrestricted default server lane")
        servers.append((default_socket, int(default_pid), DEFAULT_SESSION))
        validation.capture_baseline(WORKSPACE, validation.BASELINE_DIR, str(uuid.uuid4()))
        validation.seal_disposable_venue(WORKSPACE, validation.BASELINE_DIR)
        expected_hashes = file_hashes(validation.BASELINE_DIR)
        validator_hash = hashlib.sha256(VALIDATOR.read_bytes()).hexdigest()
        report["baseline_before"] = expected_hashes

        direct = results / "direct-nnp.json"
        worker(["setpriv", "--no-new-privs", "python3", str(SOURCE), "--probe", str(direct), "--lane", "direct-nnp"],
               check=False)
        report["lanes"]["direct-nnp"] = wait_report(direct)

        WORKSPACE.joinpath("task.json").write_bytes(validation.BASELINE_DIR.joinpath("task.json").read_bytes())
        probe = shlex.join(["python3", str(SOURCE), "--probe", str(results / "default-server.json"),
                           "--lane", "default-server"])
        # NNP on this client deliberately does not protect the old server.
        worker(["setpriv", "--no-new-privs", "tmux", "-S", default_socket, "new-window", "-t", DEFAULT_SESSION, probe])
        report["lanes"]["default-server"] = wait_report(results / "default-server.json")

        WORKSPACE.joinpath("task.json").write_bytes(validation.BASELINE_DIR.joinpath("task.json").read_bytes())
        code = ("import sys; sys.path.insert(0, '/home/operator/scenario'); import controller; "
                "controller.tmux('new-session','-d','-s',controller.TMUX,'sleep 300'); "
                "print(controller.verify_tmux_server())")
        worker(["python3", "-c", code])
        dedicated_pid = worker(["tmux", "-S", str(controller.TMUX_SOCKET), "display-message",
                                "-p", "-t", controller.TMUX, "#{pid}"]).stdout.strip()
        if not dedicated_pid.isdecimal() or nnp(int(dedicated_pid)) != 1:
            raise RuntimeError("new dedicated server does not actually have NNP")
        servers.append((str(controller.TMUX_SOCKET), int(dedicated_pid), controller.TMUX))
        probe = shlex.join(["python3", str(SOURCE), "--probe", str(results / "dedicated-server.json"),
                           "--lane", "dedicated-server"])
        # Unrestricted client; actual pane inherits the dedicated server's NNP.
        worker(["tmux", "-S", str(controller.TMUX_SOCKET), "new-window", "-t", controller.TMUX, probe])
        report["lanes"]["dedicated-server"] = wait_report(results / "dedicated-server.json")
        for lane, expected in (("direct-nnp", 1), ("default-server", 0), ("dedicated-server", 1)):
            report["errors"].extend(f"{lane}: {error}" for error in probe_errors(report["lanes"][lane], expected, account.pw_uid))
        report["baseline_after"] = file_hashes(validation.BASELINE_DIR)
        if report["baseline_after"] != expected_hashes or hashlib.sha256(VALIDATOR.read_bytes()).hexdigest() != validator_hash:
            report["errors"].append("protected baseline or validator bytes changed")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        report["errors"].append(str(error))
    finally:
        # Only identity-matching servers created by this probe are retired.
        for socket, pid, session in reversed(servers):
            try:
                current = worker(["tmux", "-S", socket, "display-message", "-p", "-t", session, "#{pid}"], check=False)
                if current.returncode == 0 and current.stdout.strip() == str(pid):
                    cleanup = worker(["tmux", "-S", socket, "kill-server"], check=False)
                    if cleanup.returncode:
                        report["errors"].append(f"owned server cleanup failed: {session}")
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                report["errors"].append(f"owned server cleanup unavailable: {session}")
        report["passed"] = not report["errors"]
        (results / "os-permission-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("/home/operator/out/os-permission-smoke"))
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--lane")
    args = parser.parse_args()
    if args.probe:
        attempt_probe(args.probe, args.lane)
    else:
        try:
            result = run_smoke(args.results)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            print(json.dumps({"passed": False, "stopped_before_proof": True, "error": str(error)}))
            sys.exit(1)
        print(json.dumps({"passed": result["passed"], "errors": result["errors"],
                          "report": str(args.results / "os-permission-report.json")}))
        sys.exit(not result["passed"])
