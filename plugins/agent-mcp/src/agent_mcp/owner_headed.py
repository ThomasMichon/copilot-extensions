"""Explicit MCP-owned retained receiver for the Windows headed creation owner."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from functools import partial
import io
import json
import os
from pathlib import Path
import sys
import time


def _report(message: str) -> None:
    try:
        print("agent-mcp: " + message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


def _retry_release(resources, deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("MCP handoff cleanup budget expired")
    _release(resources, None)


def _release(resources, active: BaseException | None) -> None:
    from agent_procutil.owned_process import OwnedCleanupError

    failed, errors = [], []
    for name, resource in resources:
        try:
            resource.close()
        except BaseException as error:
            failed.append((name, resource))
            errors.append(error)
    if not failed:
        return
    retained = tuple(failed)
    cleanup = OwnedCleanupError(
        None, cleanup_resources=retained,
        cleanup_retry=lambda deadline: _retry_release(retained, deadline),
    )
    cleanup.mcp_cleanup_errors = tuple(errors)
    _report("native MCP handoff resource cleanup is unconfirmed")
    if active is not None:
        active.mcp_cleanup = cleanup
        return
    raise cleanup from errors[0]


async def run_headed(child_argv: list[str], *, configuration: dict, names: list[str], **kwargs) -> int:
    from agent_remote_driver.headed.launcher import run_headed as run_native
    from agent_remote_driver.headed.native_handoff import WindowsNativeHandoff

    from .owner_exec import windows_overlay
    from .owner_overlay_pipe import OverlayPipe
    from .owner_section import OwnerSection

    if os.name != "nt" or kwargs.get("auth_wait") is not None or kwargs.get("native_handoff") is not None:
        raise ValueError("MCP native ownership requires the Windows non-auth headed receiver")
    if any(
        arg == "--" or arg.partition("=")[0] == "--additional-mcp-config"
        for arg in child_argv[1:]
    ):
        raise ValueError("MCP headed ownership does not support delimiters or layered overlays")
    worker, transfer, failure = None, None, None
    finishing = False
    section = OwnerSection()
    resources = [("owner-section", section)]
    try:
        replacement = windows_overlay(configuration, names, binding=section.binding)
        if set(replacement["mcpServers"]) != set(names):
            raise ValueError("Every selected native MCP entry must support the literal launch form")
        transfer = OverlayPipe(replacement)
        resources.insert(0, ("configuration-transfer", transfer))

        def completed(task):
            if task.cancelled():
                return
            try:
                task.result()
            except BaseException:
                if not finishing:
                    _report("native MCP configuration transfer failed; original client wait retained")

        def publish(process):
            nonlocal worker
            section.publish(int(process._handle), process.pid)
            transfer.bind(int(process._handle), process.pid)
            sending = asyncio.to_thread(transfer.send)
            try:
                worker = asyncio.create_task(sending)
                worker.add_done_callback(completed)
            except BaseException:
                sending.close()
                raise

        result = await run_native(
            [*child_argv, "--additional-mcp-config", "@" + transfer.path],
            native_handoff=WindowsNativeHandoff(
                publish, (section.inherited_handle, transfer.inherited_handle),
            ),
            **kwargs,
        )
        if worker is not None:
            await asyncio.shield(worker)
        return result
    except BaseException as error:
        failure = error
        raise
    finally:
        finishing = True
        try:
            if transfer is not None:
                transfer.cancel()
            if worker is not None:
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        if failure is None:
                            raise
                    except BaseException:
                        if failure is None:
                            raise
                if failure is None:
                    worker.result()
                else:
                    # Preserve native custody/cancellation while observing the transfer outcome.
                    try:
                        worker.result()
                    except BaseException:
                        pass
        finally:
            _release(resources, failure if failure is not None else sys.exc_info()[1])


def main(argv: list[str] | None = None) -> int:
    from agent_remote_driver.session_host.launcher import main as host_main

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--support-json"]:
        output = io.StringIO()
        with redirect_stdout(output):
            result = host_main(argv)
        support = json.loads(output.getvalue())
        support["mcp_origin"] = ["windows-headed-native-v1"]
        print(json.dumps(support))
        return result
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-config", required=True, help="existing explicit MCP configuration file")
    parser.add_argument("--server", action="append", required=True, help="selected local server")
    parser.add_argument("receiver", nargs=argparse.REMAINDER, help="headed receiver arguments after --")
    args = parser.parse_args(argv)
    receiver = args.receiver[1:] if args.receiver[:1] == ["--"] else args.receiver
    try:
        if os.name != "nt":
            raise ValueError("MCP native headed ownership requires Windows")
        configuration = json.loads(Path(args.mcp_config).read_text(encoding="utf-8"))
        if not isinstance(configuration, dict):
            raise ValueError("MCP configuration must be an object")
        return host_main(
            receiver, headed_run=partial(run_headed, configuration=configuration, names=args.server),
        )
    except (OSError, ValueError):
        print("agent-mcp: native headed ownership refused", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
