"""Optional diagnostic entry and validated installed-runtime association."""

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from runtime_install_governance import Governance


def load_diagnostics() -> Any:
    try:
        import installer_diagnostics

        return installer_diagnostics
    except Exception:
        # Only the optional import is isolated; implementation failures still escape.
        os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"] = "1"
        sink = sys.stderr

        def notify() -> None:
            descriptor = None
            try:
                descriptor = os.dup(sink.fileno())
                os.write(descriptor, b"WARNING: installer diagnostics unavailable; "
                         b"continuing with missing diagnostics.\n")
            except (OSError, ValueError, AttributeError):
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)

        try:
            thread = threading.Thread(target=notify, name="installer-missing-diagnostics", daemon=True)
            thread.start()
            thread.join(.05)
        except RuntimeError:
            pass

        class MissingDiagnostics:
            @staticmethod
            def writer(**kwargs: Any) -> Any:
                return nullcontext()

            @staticmethod
            def capture(**kwargs: Any) -> Any:
                return nullcontext({})

            @staticmethod
            def phase(name: str) -> None:
                pass

            @staticmethod
            def associate_installed(installation: dict[str, Any]) -> None:
                pass

            @staticmethod
            def warn() -> None:
                pass

        return MissingDiagnostics()


def run_entry(path: Path) -> None:
    # Re-enter the trusted source in-process before implementation imports.
    path = path.resolve(strict=True)
    diagnostics = load_diagnostics()
    with diagnostics.writer(announce=True):
        import runpy

        if hasattr(diagnostics, "ordinary"):
            diagnostics.ordinary(path)
        implementation = runpy.run_path(
            str(path), run_name="__installer_source__",
            init_globals={"_entry_diagnostics": diagnostics},
        )
        try:
            raise SystemExit(implementation["main"]())
        finally:
            implementation["verification"].close_clients()


def settle_publication(
    argv: list[str], environment: dict[str, str], *, diagnostics: Any, verifier: Any,
) -> subprocess.CompletedProcess[str]:
    """Keep publication with its existing verifier and private capture boundary."""
    import runtime_input_verifier as verification

    with diagnostics.capture(presentation=True) as destinations:
        return verification.wait_for_settlement(
            argv, environment, verifier=verifier, **destinations,
        )


def associate(
    governance: Governance, slot: Path, identity: dict[str, Any], manifest: bytes,
    *, runtime: ModuleType, diagnostics: Any, refusal: type[Exception],
) -> None:
    """Called only inside existing publication and retained-slot custody."""
    try:
        from agent_worktrees import runtime_deployment as deployment
        from packaging.version import Version

        governance.recheck()
        if (
            slot.parent != governance.root / "versions"
            or runtime.current_version(governance.root) != slot.name
            or deployment.read_manifest(governance.root) != manifest
        ):
            raise refusal("Diagnostic association requires the validated publication")
        published = json.loads(manifest)
        if (
            published["service"] != "agent-worktrees"
            or published["runtime_slot"] != slot.name
            or Path(published["venv"]) != slot
            or Version(published["source"]["version"]) != Version(identity["package_version"])
            or published["runtime_generation"] != identity["build_info"].get("runtime_generation")
            or published["payload_hash"] != runtime.read_marker(
                governance.root, slot.name,
            )["payload_hash"]
        ):
            raise refusal("Diagnostic association differs from installed identity")
        diagnostics.associate_installed({
            "runtime_root": str(governance.root), "runtime_slot": slot.name,
            "package_version": identity["package_version"],
            "runtime_generation": identity["build_info"].get("runtime_generation"),
            "deployment_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        })
    except Exception:
        diagnostics.warn()
