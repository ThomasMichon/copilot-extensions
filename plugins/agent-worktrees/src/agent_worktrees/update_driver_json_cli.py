"""Keep staged acquisition and hook apply inside the suite execution owner."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from . import config as cfg
from . import update_cli, update_driver_cli, update_stage

_PROTOCOL_LIMIT = 1024 * 1024


class _ProtocolBuffer(io.StringIO):
    def __init__(self):
        super().__init__()
        self.size = 0

    def write(self, value):
        self.size += len(value.encode("utf-8"))
        if self.size > _PROTOCOL_LIMIT:
            raise ValueError("update executor JSON exceeded its output limit")
        return super().write(value)


def _result_path(request) -> Path:
    return request.log_path.with_suffix(".result.json")


def _publish_result(request, text: str) -> None:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("update executor did not return one JSON object")
    path = _result_path(request)
    if path.exists() or path.is_symlink():
        raise ValueError("update executor result already exists")
    descriptor, name = tempfile.mkstemp(prefix=f".{request.id}-result-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_request(
    verb: str, flags: list[str], args: argparse.Namespace, execute, *, emit_json: bool = True,
) -> int:
    import update_driver as driver

    backend = "engine-" + verb
    try:
        custody = driver.borrowed_custody()
        if custody is not None:
            with custody:
                request = driver.status(custody.request_id, root=custody.root)
                if request.state == "running" and request.spec.backend in {"manager", "engine"}:
                    with update_driver_cli._execution_context(request.spec.context):
                        custody.assert_owned()
                        with custody.execution_scope():
                            return execute(args)
                if (
                    request.state != "running" or request.spec.backend != backend
                    or request.spec.argv != tuple(flags)
                ):
                    raise driver.StateError("JSON executor does not own the active update request")
                with update_driver_cli._execution_context(request.spec.context):
                    if request.spec.source != str(cfg.install_dir().resolve()):
                        raise driver.StateError("engine installation changed after submission")
                    custody.assert_owned()
                    with custody.execution_scope():
                        buffer = _ProtocolBuffer()
                        with redirect_stdout(buffer):
                            code = execute(args)
                        text = buffer.getvalue()
                        if not text and code != 0:
                            text = json.dumps({"ok": False, "reason": "canonical-executor-failed"})
                        _publish_result(request, text)
                        return code
        project, cwd = cfg.active_project(), update_driver_cli._invocation_cwd()
        spec = driver.RequestSpec(
            backend=backend, argv=tuple(flags), project=project or "", cwd=str(cwd),
            source=str(cfg.install_dir().resolve()), context=update_driver_cli._request_context(),
        )
        project_args = ["--project", project] if project else []
        command, environment = driver.runtime_command(
            "agent_worktrees", [*project_args, verb, *flags],
        )
        request = driver.enqueue(
            spec, runner=driver.TrustedRunner(
                command, str(cwd), env=tuple(environment.items()),
                context_keys=update_driver_cli._CONTEXT_KEYS,
            ),
        )
        print(f"Update request {request.id}; log: {request.log_path}", file=sys.stderr)
        worker, environment = driver.runtime_command("update_driver", ["worker"])
        worker_process = driver.ensure_worker(worker, env={
            **os.environ, **environment, "GIT_TERMINAL_PROMPT": "0",
            "GH_PROMPT_DISABLED": "1", "GCM_INTERACTIVE": "never",
        })
        if getattr(args, "detach", False):
            if emit_json:
                print(json.dumps({
                    "action": "accepted", "request_id": request.id,
                    "log": str(request.log_path),
                }))
            return 0
        result = driver.wait(request.id, output=sys.stderr, worker=worker_process)
        if result.state != "completed" or result.exit_code is None:
            raise driver.StateError(result.error or "update request did not complete")
        path = _result_path(result)
        if path.stat().st_size > _PROTOCOL_LIMIT or path.is_symlink():
            raise driver.StateError("update executor result is invalid")
        text = path.read_text(encoding="utf-8")
        if not isinstance(json.loads(text), dict):
            raise driver.StateError("update executor result is not an object")
        if emit_json:
            print(text, end="" if text.endswith("\n") else "\n")
        return result.exit_code
    except (driver.DriverError, driver.ParticipantError, OSError, ValueError) as error:
        print(f"Update driver: {error}", file=sys.stderr)
        failure = {"ok": False, "reason": "update-driver", "message": str(error)}
        # Stage's lease has unwound here; never publish an unleased stage record.
        try:
            if verb == "reconcile-plugins":
                repo = getattr(args, "repo", None) or update_cli.worktree_identity._find_repo_dir()
                update_cli.write_reconcile_status(
                    getattr(args, "status", None),
                    {**failure, "repo": str(Path(repo).resolve()) if repo else None, "failed": []},
                )
        except (OSError, ValueError) as status_error:
            print(f"Update failure status could not be written: {status_error}", file=sys.stderr)
            failure["status_error"] = str(status_error)
        if emit_json:
            print(json.dumps(failure))
        return 1


def cmd_reconcile_request(args: argparse.Namespace) -> int:
    if not getattr(args, "apply", False) or getattr(args, "check_update", False):
        return update_cli.cmd_reconcile_plugins(args)
    flags = ["--apply"]
    for name in ("repo", "status", "machine"):
        value = getattr(args, name, None)
        if value:
            if name in {"repo", "status"}:
                value = str(Path(value).expanduser().resolve())
            flags += ["--" + name, value]
            setattr(args, name, value)
    if getattr(args, "with_payload_refresh", False):
        flags.append("--with-payload-refresh")
    if getattr(args, "peek", False):
        flags.append("--peek")
    return _run_request("reconcile-plugins", flags, args, update_cli.cmd_reconcile_plugins)


def cmd_stage_request(args: argparse.Namespace) -> int:
    if getattr(args, "indicator_state", False):
        return update_stage.cmd_stage_update(args)
    flags = ["--json"]
    value = getattr(args, "status", None)
    if value:
        value = str(Path(value).expanduser().resolve())
        flags += ["--status", value]
        args.status = value
    return _run_request(
        "stage-update", flags, args, update_stage.cmd_stage_update,
        emit_json=getattr(args, "json", False),
    )
