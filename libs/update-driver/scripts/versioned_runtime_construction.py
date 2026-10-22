"""Finite attached construction protocol and native shell adapters."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager


@dataclass(frozen=True)
class RuntimeOperations:
    version_dir: Callable[[Path, str], Path]
    _load_unique_json: Callable[[Path], dict]
    current_version: Callable[..., str | None]
    _is_link: Callable[[Path], bool]
    LAST_KNOWN_GOOD_FILE: str
    retention_lease: Callable[..., ContextManager[Path]]
    construction_lease: Callable[..., ContextManager[None]]
    check_admission: Callable[..., str]
    ADMIT_CONSTRUCT: str
    ADMIT_REUSE: str
    is_dev_generation: Callable[[str], bool]
    _require_dev_owner: Callable[[Path, str | None], dict]
    mark_complete: Callable[..., Path]
    slot: Callable[..., Path]
    discard_failed_candidate: Callable[..., bool]


@dataclass(frozen=True)
class CleanupOperations:
    version_dir: Callable[[Path, str], Path]
    marker_path: Callable[[Path, str], Path]
    _is_link: Callable[[Path], bool]
    _cleanup_lease: Callable[..., ContextManager[bool]]
    _gc_protected_versions: Callable[..., set[str]]
    _ownership_key: Callable[[Path, str], object]
    _construction_owners: object
    _remove_slot: Callable[..., bool]


def discard_failed_candidate(runtime: CleanupOperations, root: Path, version: str, *,
                             link_name: str = "current", validate=None) -> bool:
    """Delete only an unfinished candidate held by this construction owner.

    ``validate`` can enforce an installation-cell ownership receipt while all
    cleanup guards are held. It must raise on changed or unowned evidence.
    This operation never removes completion evidence or recreates an empty slot.
    """
    if runtime._ownership_key(root, version) not in getattr(runtime._construction_owners, "keys", ()):
        raise RuntimeError("failed candidate cleanup requires held construction ownership")
    with runtime._cleanup_lease(root, version, allow_construction_owner=True) as admitted:
        if not admitted:
            raise RuntimeError(f"failed candidate cleanup is busy: {version}")
        vdir = runtime.version_dir(root, version)
        if not os.path.lexists(vdir):
            return False
        if not vdir.is_dir() or runtime._is_link(vdir):
            raise RuntimeError(f"failed candidate is not an ordinary directory: {vdir}")
        # Malformed/linked completion evidence is also immutable evidence, not
        # permission to reclaim a candidate under a failed-build label.
        if os.path.lexists(runtime.marker_path(root, version)):
            raise RuntimeError(f"failed candidate has completion evidence: {version}")
        if version.lower() in runtime._gc_protected_versions(root, None, True, link_name):
            raise RuntimeError(f"failed candidate is selected or in use: {version}")
        if validate is not None:
            validate(vdir)
        if not runtime._remove_slot(vdir, label="failed construction"):
            raise RuntimeError(f"could not remove failed candidate: {vdir}")
        return True


def remove_owned_candidate(runtime: CleanupOperations, root: Path, version: str, *, remove,
                           link_name: str = "current") -> None:
    """Run one installation owner's exact-candidate removal under cleanup custody.

    The mandatory callback validates completion and installation authority before
    first deletion, and retains physical ownership outside the candidate for
    partial-delete retries. It must refuse changed/new objects, remove only this
    candidate and raise on incomplete deletion. No raw recursive-delete fallback
    or completion-marker downgrade is provided here. Absence still reaches the
    callback so only its durable receipt can authorize a completed retry.
    """
    if not callable(remove):
        raise ValueError("owned candidate removal requires its installation owner")
    with runtime._cleanup_lease(root, version) as admitted:
        if not admitted:
            raise RuntimeError(f"owned candidate removal is busy: {version}")
        if version.lower() in runtime._gc_protected_versions(root, None, True, link_name):
            raise RuntimeError(f"owned candidate is selected or in use: {version}")
        candidate = runtime.version_dir(root, version)
        if os.path.lexists(candidate) and (runtime._is_link(candidate) or not candidate.is_dir()):
            raise RuntimeError("owned candidate removal requires an ordinary directory")
        remove(candidate)
        if os.path.lexists(candidate):
            raise RuntimeError("owned candidate removal did not settle")


def construction_session(runtime: RuntimeOperations, root: Path, version: str, *, payload_hash: str,
                         link_name: str = "current", dev_owner: str | None = None) -> int:
    """Own one shell constructor through a private, caller-held stdin pipe.

    The attached helper is finite: EOF aborts construction or ends retention.
    Completion transfers to retention before construction is released. There
    is no background service, PID/timestamp ownership, or directory promotion.
    """
    from contextlib import ExitStack
    import hashlib

    if not re.fullmatch(r"[0-9a-f]{64}", payload_hash):
        raise ValueError("construction requires an attributable SHA-256 payload fingerprint")
    executable = Path(sys.executable).resolve()
    if executable.is_relative_to(runtime.version_dir(root, version).resolve()):
        raise RuntimeError("construction supervisor must use a base interpreter outside the candidate")

    def reply(**record):
        print(json.dumps(record), flush=True)

    cell_arguments = None
    cell_module = None
    ownership_digest = None
    ownership_file = runtime.version_dir(root, version) / ".runtime-slot-ownership.json"
    if os.path.lexists(ownership_file):
        import importlib.util
        from types import SimpleNamespace

        record = runtime._load_unique_json(ownership_file)
        context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT") or str(root / "install.json")
        source = Path(__file__).resolve().parent / "installation-context" / "installation_context.py"
        if not source.is_file():
            source = Path(__file__).resolve().parent.parent / "installation-context" / "installation_context.py"
        spec = importlib.util.spec_from_file_location("construction_cell_context", source)
        if spec is None or spec.loader is None:
            raise RuntimeError("cell construction requires the canonical receipt validator")
        cell_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cell_module
        spec.loader.exec_module(cell_module)
        durable = Path(record["namespaceReceipt"]["path"]).parents[2]
        cell_arguments = dict(
            context=context, expected_marketplace_id=record["marketplaceId"],
            expected_plugin_id=record["pluginId"], snapshot_id=record["snapshot"]["id"],
            runtime_version=version, durable_home=durable,
        )
        owned = cell_module.validate_runtime_slot_ownership(**cell_arguments, environment={})
        if Path(owned["slotRoot"]) != runtime.version_dir(root, version):
            raise RuntimeError("cell ownership does not match the construction candidate")
        validated = cell_module.validate_context_receipt(
            context, durable, expected_marketplace_id=record["marketplaceId"],
            expected_plugin_id=record["pluginId"], environment={},
        )
        # Failed-candidate reclamation has stricter generation binding than
        # read-only reuse of historical snapshot receipts.
        receipt_generations = dict(
            expected_namespace_generation=validated["namespaceGeneration"],
            expected_install_generation=validated["generation"],
        )
        install_receipt = cell_module.read_json(validated["installReceipt"])
        origin = install_receipt["payload"]
        cleanup_arguments = dict(
            **cell_arguments, **receipt_generations,
            expected_payload_root=origin["root"],
            expected_payload_version=origin["version"],
        )
        cleanup_runtime = SimpleNamespace(discard_failed_candidate=runtime.discard_failed_candidate)
        ownership_digest = hashlib.sha256(ownership_file.read_bytes()).hexdigest()
    elif os.environ.get("COPILOT_EXTENSIONS_CONTEXT") and (root / "install.json").is_file():
        raise RuntimeError("cell construction requires a reserved ownership receipt before writes")

    def discard():
        if cell_module is None:
            return runtime.discard_failed_candidate(root, version, link_name=link_name)
        return cell_module.discard_failed_runtime_slot(
            runtime=cleanup_runtime, expected_ownership_sha256=ownership_digest,
            **cleanup_arguments,
        )

    with ExitStack() as retained, ExitStack() as building:
        references = {runtime.current_version(root, link_name)}
        last_good = root / runtime.LAST_KNOWN_GOOD_FILE
        if os.path.lexists(last_good):
            if runtime._is_link(last_good) or not last_good.is_file():
                raise RuntimeError("last-known-good selector must be an ordinary file")
            references.add(last_good.read_text(encoding="utf-8").strip())
        for previous in references:
            if previous:
                retained.enter_context(runtime.retention_lease(root, previous))
        building.enter_context(runtime.construction_lease(root, version))
        if runtime.is_dev_generation(version):
            runtime._require_dev_owner(root, dev_owner)
        admission = runtime.check_admission(root, version, payload_hash=payload_hash)
        if admission not in (runtime.ADMIT_CONSTRUCT, runtime.ADMIT_REUSE):
            raise RuntimeError(f"construction admission refused: {version}: {admission}")
        complete = admission == runtime.ADMIT_REUSE
        if complete:
            retained.enter_context(runtime.retention_lease(root, version, payload_hash=payload_hash))
            building.close()
        else:
            if cell_module is None:
                if os.path.lexists(runtime.version_dir(root, version) / ".runtime-slot-completion.json"):
                    raise RuntimeError("cell completion evidence requires owned immutable admission")
                runtime.slot(root, version, clean_incomplete=True, link_name=link_name)
            else:
                discard()
                cell_module.provision_runtime_slot(**cleanup_arguments, environment={})
                ownership_digest = hashlib.sha256(ownership_file.read_bytes()).hexdigest()
        try:
            reply(admission=admission)
            for line in sys.stdin:
                if len(line) > 4096:
                    raise ValueError("construction command exceeds its bounded protocol")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("construction command must be a JSON object")
                command = request.get("command")
                if command == "complete":
                    if request.get("payload_hash") != payload_hash:
                        raise RuntimeError("payload changed during construction; completion refused")
                    if not complete:
                        runtime.mark_complete(root, version, payload_hash=payload_hash, dev_owner=dev_owner)
                        retained.enter_context(runtime.retention_lease(
                            root, version, payload_hash=payload_hash,
                        ))
                        building.close()
                        complete = True
                    reply(complete=True)
                elif command == "settled":
                    if not complete:
                        raise RuntimeError("construction ended before isolated completion")
                    reply(settled=True)
                    return 0
                elif command == "abort":
                    break
                else:
                    raise ValueError("unsupported construction command")
        finally:
            if not complete:
                discard()
        if complete:
            return 0
        reply(aborted=True)
        return 1


def construction_shell_helpers(shell: str) -> str:
    """Render the canonical attached-pipe adapter, not a separate installer."""
    if shell == "sh":
        return r'''
_runtime_construction_channels_close() {
    if [[ -n "${_RUNTIME_CONSTRUCTION_BOOTSTRAP:-}" ]]; then
        eval "exec $_RUNTIME_CONSTRUCTION_BOOTSTRAP>&-"
        _RUNTIME_CONSTRUCTION_BOOTSTRAP=""
    fi
    if [[ -n "${_RUNTIME_CONSTRUCTION_WRITE:-}" ]]; then
        eval "exec $_RUNTIME_CONSTRUCTION_WRITE>&-"
        _RUNTIME_CONSTRUCTION_WRITE=""
    fi
    if [[ -n "${_RUNTIME_CONSTRUCTION_READ:-}" ]]; then
        eval "exec $_RUNTIME_CONSTRUCTION_READ<&-"
        _RUNTIME_CONSTRUCTION_READ=""
    fi
    if [[ -n "${_RUNTIME_CONSTRUCTION_CHANNEL:-}" ]]; then
        rm -f -- "$_RUNTIME_CONSTRUCTION_CHANNEL/commands" "$_RUNTIME_CONSTRUCTION_CHANNEL/replies"
        rmdir -- "$_RUNTIME_CONSTRUCTION_CHANNEL" 2>/dev/null ||
            [[ ! -e "$_RUNTIME_CONSTRUCTION_CHANNEL" ]]
        _RUNTIME_CONSTRUCTION_CHANNEL=""
    fi
    return 0
}
_runtime_construction_begin() {
    local python="$1" helper="$2" root="$3" version="$4" hash="$5" link="$6"
    local owner="${7:-}"
    if [[ -n "${_RUNTIME_CONSTRUCTION_PID:-}" ]]; then
        [[ "$_RUNTIME_CONSTRUCTION_KEY" == "$root/$version" ]] || {
            printf '%s\n' 'A different runtime construction is still unsettled' >&2; return 1;
        }
        kill -0 "$_RUNTIME_CONSTRUCTION_PID" 2>/dev/null || return 1
        return 0
    fi
    local base
    base="$("$python" -I -c 'import sys; print(sys._base_executable)')" || return 1
    # Bash 3.2 cannot reliably wait on a process-substitution PID.
    # Probe before reserving numeric descriptors; caller-owned descriptors survive.
    local fd read_fd="" write_fd="" bootstrap_fd=""
    for ((fd=10; fd<64; fd++)); do
        if ! (eval ": >&$fd") 2>/dev/null && ! (eval ": <&$fd") 2>/dev/null; then
            if [[ -z "$write_fd" ]]; then write_fd="$fd"
            elif [[ -z "$read_fd" ]]; then read_fd="$fd"
            else bootstrap_fd="$fd"; break; fi
        fi
    done
    [[ -n "$bootstrap_fd" ]] || {
        printf '%s\n' 'No private construction descriptors available' >&2; return 1;
    }
    if [[ "${_RUNTIME_CONSTRUCTION_TRAPS_INSTALLED:-0}" != 1 ]]; then
        local previous
        previous="$(trap -p EXIT)"
        _RUNTIME_CONSTRUCTION_PREVIOUS_EXIT=""
        if [[ -n "$previous" ]]; then
            previous="${previous#trap -- }"; previous="${previous% EXIT}"
            eval "_RUNTIME_CONSTRUCTION_PREVIOUS_EXIT=$previous"
        fi
        _RUNTIME_CONSTRUCTION_TRAPS_INSTALLED=1
    fi
    trap '_runtime_construction_exit "$?"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    _RUNTIME_CONSTRUCTION_COMPLETE=0
    _RUNTIME_CONSTRUCTION_CHANNEL=""
    mkdir -p -- "$root" || return 1
    _RUNTIME_CONSTRUCTION_CHANNEL="$(mktemp -d "$root/.construction-channel.XXXXXXXX")" || return 1
    chmod 700 "$_RUNTIME_CONSTRUCTION_CHANNEL" &&
        mkfifo -m 600 "$_RUNTIME_CONSTRUCTION_CHANNEL/commands" "$_RUNTIME_CONSTRUCTION_CHANNEL/replies" || {
        _runtime_construction_channels_close; return 1;
    }
    # Bootstrap read/write endpoints make every FIFO open nonblocking, even if
    # the caller dies before the child starts. The child closes all inherited
    # endpoints after binding stdio; only the caller then keeps stdin alive.
    eval "exec $write_fd<>\"\$_RUNTIME_CONSTRUCTION_CHANNEL/commands\"" || {
        _runtime_construction_channels_close; return 1;
    }
    _RUNTIME_CONSTRUCTION_WRITE="$write_fd"
    eval "exec $bootstrap_fd<>\"\$_RUNTIME_CONSTRUCTION_CHANNEL/replies\"" || {
        _runtime_construction_channels_close; return 1;
    }
    _RUNTIME_CONSTRUCTION_BOOTSTRAP="$bootstrap_fd"
    eval "exec $read_fd<\"\$_RUNTIME_CONSTRUCTION_CHANNEL/replies\"" || {
        _runtime_construction_channels_close; return 1;
    }
    _RUNTIME_CONSTRUCTION_READ="$read_fd"
    {
        trap - EXIT INT TERM
        exec 0<"$_RUNTIME_CONSTRUCTION_CHANNEL/commands" 1>"$_RUNTIME_CONSTRUCTION_CHANNEL/replies"
        eval "exec $write_fd>&- $read_fd<&- $bootstrap_fd>&-"
        rm -f -- "$_RUNTIME_CONSTRUCTION_CHANNEL/commands" "$_RUNTIME_CONSTRUCTION_CHANNEL/replies" &&
            rmdir -- "$_RUNTIME_CONSTRUCTION_CHANNEL" || exit 1
        set -- "$base" -I "$helper" --root "$root" --link-name "$link" construction-session "$version" --payload-hash "$hash"
        [[ -z "$owner" ]] || set -- "$@" --dev-owner "$owner"
        exec "$@"
    } &
    _RUNTIME_CONSTRUCTION_PID="$!"
    _RUNTIME_CONSTRUCTION_KEY="$root/$version"
    eval "exec $bootstrap_fd>&-"
    _RUNTIME_CONSTRUCTION_BOOTSTRAP=""
    local reply
    if ! IFS= read -r reply <&"$_RUNTIME_CONSTRUCTION_READ"; then
        _runtime_construction_channels_close
        if ! wait "$_RUNTIME_CONSTRUCTION_PID"; then
            _RUNTIME_CONSTRUCTION_PID=""
            return 1
        fi
        _RUNTIME_CONSTRUCTION_PID=""
        return 1
    fi
    # The child removed its private rendezvous before emitting admission.
    _RUNTIME_CONSTRUCTION_CHANNEL=""
    case "$reply" in
        '{"admission": "reuse"}') _RUNTIME_CONSTRUCTION_REUSE=1; _RUNTIME_CONSTRUCTION_COMPLETE=1;;
        '{"admission": "construct"}') _RUNTIME_CONSTRUCTION_REUSE=0; _RUNTIME_CONSTRUCTION_COMPLETE=0;;
        *) printf '%s\n' "Invalid construction admission: $reply" >&2
           _runtime_construction_channels_close
           if ! wait "$_RUNTIME_CONSTRUCTION_PID"; then
               _RUNTIME_CONSTRUCTION_PID=""
               return 1
           fi
           _RUNTIME_CONSTRUCTION_PID=""
           return 1;;
    esac
}
_runtime_construction_complete() {
    local hash="$1" reply
    [[ -n "${_RUNTIME_CONSTRUCTION_PID:-}" ]] || {
        printf '%s\n' 'Completion requires attached construction ownership' >&2; return 1;
    }
    [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || return 1
    printf '{"command":"complete","payload_hash":"%s"}\n' "$hash" >&"$_RUNTIME_CONSTRUCTION_WRITE" || return 1
    IFS= read -r reply <&"$_RUNTIME_CONSTRUCTION_READ" || return 1
    [[ "$reply" == '{"complete": true}' ]] || return 1
    _RUNTIME_CONSTRUCTION_COMPLETE=1
}
_runtime_construction_finish() {
    local command=abort reply="" rc=0
    [[ -n "${_RUNTIME_CONSTRUCTION_PID:-}" ]] || {
        _runtime_construction_channels_close; return 0;
    }
    [[ "${_RUNTIME_CONSTRUCTION_COMPLETE:-0}" == 1 ]] && command=settled
    printf '{"command":"%s"}\n' "$command" >&"$_RUNTIME_CONSTRUCTION_WRITE" || rc=1
    IFS= read -r reply <&"$_RUNTIME_CONSTRUCTION_READ" || rc=1
    if [[ "$command" == settled ]]; then
        [[ "$reply" == '{"settled": true}' ]] || rc=1
    else
        [[ "$reply" == '{"aborted": true}' ]] || rc=1
    fi
    _runtime_construction_channels_close
    wait "$_RUNTIME_CONSTRUCTION_PID" || rc=1
    _RUNTIME_CONSTRUCTION_PID=""
    return "$rc"
}
_runtime_construction_exit() {
    local rc="$1"
    _runtime_construction_finish || { [[ "$rc" != 0 ]] || rc=1; }
    if [[ -n "${_RUNTIME_CONSTRUCTION_PREVIOUS_EXIT:-}" ]]; then
        if (exit "$rc"); then
            eval "$_RUNTIME_CONSTRUCTION_PREVIOUS_EXIT"
        else
            eval "$_RUNTIME_CONSTRUCTION_PREVIOUS_EXIT"
        fi
    fi
    exit "$rc"
}
'''
    if shell != "ps1":
        raise ValueError("construction shell must be sh or ps1")
    return r'''
$script:RuntimeConstructionSession = $null
function script:ConvertTo-RuntimeConstructionArgument([string]$Value) {
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
function script:Start-RuntimeConstruction {
    param([string]$Python, [string]$Helper, [string]$Root, [string]$Version,
          [string]$PayloadHash, [string]$LinkName, [string]$DevOwner = '')
    if ($script:RuntimeConstructionSession) {
        if ($script:RuntimeConstructionSession.Key -ne "$Root/$Version" -or
            $script:RuntimeConstructionSession.Process.HasExited) {
            throw 'A different runtime construction is still unsettled or its owner exited'
        }
        return $script:RuntimeConstructionSession.Reuse
    }
    $query = New-Object System.Diagnostics.ProcessStartInfo
    $query.FileName = $Python
    $query.Arguments = '-I -c "import sys; print(sys._base_executable)"'
    $query.UseShellExecute = $false
    $query.CreateNoWindow = $true
    $query.RedirectStandardOutput = $true
    $query.RedirectStandardError = $true
    $probe = [Diagnostics.Process]::Start($query)
    $probeOut = $probe.StandardOutput.ReadToEndAsync()
    $probeErr = $probe.StandardError.ReadToEndAsync()
    $probe.WaitForExit()
    [Threading.Tasks.Task]::WaitAll($probeOut, $probeErr)
    $base = $probeOut.Result.Trim()
    $probeCode = $probe.ExitCode
    $probe.Dispose()
    if ($probeCode -ne 0 -or -not (Test-Path -LiteralPath $base -PathType Leaf)) {
        throw 'A base interpreter outside the candidate is required for construction'
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $base
    $argv = @('-I', $Helper, '--root', $Root, '--link-name', $LinkName,
              'construction-session', $Version, '--payload-hash', $PayloadHash)
    if ($DevOwner) { $argv += @('--dev-owner', $DevOwner) }
    $psi.Arguments = ($argv | ForEach-Object { ConvertTo-RuntimeConstructionArgument $_ }) -join ' '
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($psi)
    $errors = $process.StandardError.ReadToEndAsync()
    $line = $process.StandardOutput.ReadLine()
    if (-not $line) {
        $process.WaitForExit()
        throw ("Construction admission failed: " + $errors.Result)
    }
    $admission = $line | ConvertFrom-Json
    if ($admission.admission -notin @('reuse', 'construct')) {
        $process.StandardInput.Close()
        $process.WaitForExit()
        throw 'Invalid construction admission'
    }
    $reuse = $admission.admission -eq 'reuse'
    $script:RuntimeConstructionSession = [pscustomobject]@{
        Key = "$Root/$Version"; Process = $process; Errors = $errors;
        Reuse = $reuse; Complete = $reuse
    }
    return $reuse
}
function script:Complete-RuntimeConstruction([string]$PayloadHash) {
    $session = $script:RuntimeConstructionSession
    if (-not $session) { throw 'Completion requires attached construction ownership' }
    $session.Process.StandardInput.WriteLine(
        (@{command='complete'; payload_hash=$PayloadHash} | ConvertTo-Json -Compress)
    )
    $session.Process.StandardInput.Flush()
    $line = $session.Process.StandardOutput.ReadLine()
    if (-not $line -or -not ($line | ConvertFrom-Json).complete) {
        throw 'Construction completion refused; candidate remains non-selectable'
    }
    $session.Complete = $true
}
function script:Stop-RuntimeConstruction {
    if (-not $script:RuntimeConstructionSession) { return }
    $session = $script:RuntimeConstructionSession
    $script:RuntimeConstructionSession = $null
    try {
        $command = if ($session.Complete) { 'settled' } else { 'abort' }
        $session.Process.StandardInput.WriteLine(
            (@{command=$command} | ConvertTo-Json -Compress)
        )
        $session.Process.StandardInput.Flush()
        $line = $session.Process.StandardOutput.ReadLine()
        $session.Process.StandardInput.Close()
        $session.Process.WaitForExit()
        $record = if ($line) { $line | ConvertFrom-Json } else { $null }
        $aborted = $command -eq 'abort' -and $record -and $record.aborted -and
            $session.Process.ExitCode -eq 1
        if ($aborted) {
            throw 'Runtime construction aborted; the owned incomplete candidate was reclaimed'
        }
        if (-not $line -or $session.Process.ExitCode -ne 0) {
            throw ("Construction did not complete: " + $session.Errors.Result)
        }
    } finally {
        $session.Process.Dispose()
    }
}
'''
