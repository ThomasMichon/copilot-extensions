"""Repository plugin preparation through the existing static SSH connection."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from plugin_resolve import execution
from plugin_resolve.preparation import _GIT_SELECTORS
from plugin_resolve.reflection import build_prepare_python, parse_prepared, parse_prepared_context

from .venue_plugins import venue_plugin_plan


def _python_command(shell: str | None) -> str:
    if (shell or "bash").lower() not in {"pwsh", "powershell", "cmd"}:
        return "python3 -I -S -X utf8 -u -"
    script = r"""
$ErrorActionPreference = 'Stop'
$python = $null
$arguments = '-I -S -X utf8 -u -'
foreach ($name in @('python3', 'python', 'py')) {
    $candidate = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1
    if ($candidate) {
        $python = $candidate.Source
        if ($name -eq 'py') { $arguments = '-3 -I -S -X utf8 -u -' }
        break
    }
}
if (-not $python) { throw 'SSH plugin preparation requires Python 3.10 or newer' }
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $python
$start.Arguments = $arguments
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$start.StandardOutputEncoding = $utf8
$start.StandardErrorEncoding = $utf8
$start.EnvironmentVariables['PYTHONUTF8'] = '1'
$process = [System.Diagnostics.Process]::Start($start)
try {
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.Write([Console]::In.ReadToEnd())
    $process.StandardInput.Close()
    $process.WaitForExit()
    [Console]::Out.Write($stdout.GetAwaiter().GetResult())
    [Console]::Error.Write($stderr.GetAwaiter().GetResult())
    exit $process.ExitCode
} finally {
    $process.Dispose()
}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    executable = "pwsh" if shell.lower() == "pwsh" else "powershell"
    return f"{executable} -NoProfile -WindowStyle Hidden -EncodedCommand {encoded}"


async def _run_python(manager, target, program: str, *, timeout: float = 540) -> str:
    from plugin_resolve import receiver as ssh_plugin_receiver

    supervisor = Path(ssh_plugin_receiver.__file__).read_text(encoding="utf-8")
    encoded = base64.b64encode(program.encode("utf-8")).decode("ascii")
    execution = asyncio.create_task(manager.exec_command(
        target.host, _python_command(target.ssh_shell), input_bytes=(
            "import sys\n"
            "if sys.version_info < (3,10):\n"
            "    raise RuntimeError('SSH plugin preparation requires Python 3.10 or newer')\n"
            "exec("
            + repr(supervisor)
            + ")\n"
            + f"supervise({encoded!r},{timeout!r})\n"
        ).encode("utf-8"),
        timeout=timeout + 60,
    ))
    try:
        result = await asyncio.shield(execution)
    except asyncio.CancelledError:
        # Closing an SSH client does not prove that sshd reaped its receiver.
        # Keep the bounded channel alive until preparation quiesces instead.
        while not execution.done():
            try:
                await asyncio.shield(execution)
            except asyncio.CancelledError:
                continue
        execution.result()
        raise
    if result.timed_out or result.exit_code:
        detail = "timed out" if result.timed_out else f"exit {result.exit_code}"
        raise RuntimeError(
            f"Required SSH plugin preparation failed ({detail}): {result.stderr.strip()}",
        )
    return result.stdout


def _environment_program(environment: dict[str, str]) -> str:
    encoded = base64.b64encode(json.dumps(environment).encode()).decode("ascii")
    return f"import os,json,base64\nos.environ.update(json.loads(base64.b64decode({encoded!r})))\n"


async def prepare_ssh_plugins(target, manager, *, environment: dict[str, str]) -> list[str] | None:
    """Prepare the allocated receiver checkout without changing the SSH owner."""
    if not isinstance(target.cwd, str) or not target.cwd.strip():
        raise ValueError("SSH plugin preparation requires the allocated execution checkout")
    target.prepared_plugins = None
    prefix = _environment_program(environment)
    checkout = base64.b64encode(target.cwd.encode()).decode("ascii")
    execution_source = Path(execution.__file__).read_text(encoding="utf-8")
    probe = await _run_python(
        manager, target,
        prefix
        + "import pathlib,subprocess\n"
        + f"requested=base64.b64decode({checkout!r}).decode('utf-8')\n"
        + "checkout=pathlib.Path(requested).resolve(strict=True)\n"
        + "if not checkout.is_dir():\n"
        + "    raise ValueError('SSH execution checkout is not an existing directory')\n"
        + "applicable=any((parent/'.git').exists() for parent in (checkout,*checkout.parents))\n"
        + "repository_root=None\n"
        + "if applicable:\n"
        + "    lookup={'__name__':'venue_execution'}\n"
        + f"    exec({execution_source!r},lookup)\n"
        + "    environment={key:value for key,value in os.environ.items()\n"
        + f"        if key not in {tuple(sorted(_GIT_SELECTORS))!r}}}\n"
        + "    environment=lookup['execution_environment'](environment,checkout)\n"
        + "    git=lookup['execution_executable']('git',environment,checkout)\n"
        + "    result=subprocess.run([git,'rev-parse','--show-toplevel'],\n"
        + "        capture_output=True,text=True,timeout=30,cwd=checkout,env=environment,\n"
        + "        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        + "    if result.returncode:\n"
        + "        raise RuntimeError('SSH repository lookup failed: '+result.stderr.strip())\n"
        + "    if not result.stdout.strip(): raise RuntimeError('SSH repository lookup returned no root')\n"
        + "    repository_root=str(pathlib.Path(result.stdout.strip()).resolve(strict=True))\n"
        + "print('__VENUE_CHECKOUT__'+json.dumps({\n"
        + "    'requested_cwd':requested,'cwd':str(checkout),'applicable':applicable,\n"
        + "    'repository_root':repository_root}))\n",
    )
    line = next(
        (line for line in reversed(probe.splitlines()) if line.startswith("__VENUE_CHECKOUT__")),
        None,
    )
    if line is None:
        raise RuntimeError("SSH plugin preparation returned no checkout proof")
    proof = json.loads(line.removeprefix("__VENUE_CHECKOUT__"))
    if (
        not isinstance(proof, dict) or proof.get("requested_cwd") != target.cwd
        or not isinstance(proof.get("cwd"), str) or not proof["cwd"].strip()
        or type(proof.get("applicable")) is not bool
    ):
        raise ValueError("SSH plugin preparation checkout proof does not match the allocated checkout")
    target.cwd = proof["cwd"]
    if not proof["applicable"]:
        target.prepared_plugins = {
            "applicable": False, "cwd": target.cwd, "plugins": [], "logical_payloads": {},
        }
        return None
    repository_root = proof.get("repository_root")
    if not isinstance(repository_root, str) or not repository_root.strip():
        raise ValueError("SSH plugin preparation returned no verified repository root")
    plan = await venue_plugin_plan({"repo": target.project}, caller=target)
    output = await _run_python(
        manager, target,
        prefix + build_prepare_python(plan, target.cwd, include_prepared_context=True),
    )
    context = parse_prepared_context(output, expected_repo=repository_root)
    directories = parse_prepared(output)
    target.prepared_plugins = {
        **context, "repository_root": repository_root, "cwd": target.cwd,
    }
    return [arg for directory in directories for arg in ("--plugin-dir", directory)]
