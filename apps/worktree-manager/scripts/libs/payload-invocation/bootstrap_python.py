"""Publish declared bootstrap requests and optional canonical uv selectors."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any


def expected_files(
    manifest: Path, data: dict[str, Any], *, canonical_root: Path | None = None,
) -> dict[Path, str | bytes]:
    guidance = data.get("powershellGuidance", False)
    if not isinstance(guidance, bool):
        raise ValueError(f"{manifest}: powershellGuidance must be a boolean")
    if guidance and data.get("bootstrapLauncher") != "uv":
        raise ValueError(f"{manifest}: powershellGuidance requires bootstrapLauncher uv")
    if guidance and not (manifest.parent / "scripts/write_session_guidance.py").is_file():
        raise ValueError(f"{manifest}: powershellGuidance requires its session guidance writer")
    destination = manifest.parent / "scripts/bootstrap-python-version.txt"
    regular = destination.is_file()
    occupied = regular or destination.is_symlink()
    if occupied:
        declared_inputs = [data.get("catalogGate")]
        dispatchers = data.get("payloadDispatcher")
        if isinstance(dispatchers, dict):
            declared_inputs.extend(dispatchers.values())
        for value in declared_inputs:
            if not isinstance(value, str) or not value:
                continue
            source = manifest.parent / value
            if source == destination or (regular and source.is_file() and source.samefile(destination)):
                if "bootstrapPython" in data:
                    raise ValueError(f"{manifest}: bootstrapPython output conflicts with a declared input")
                return {}
    if "bootstrapPython" not in data:
        if occupied:
            raise ValueError(f"{manifest}: obsolete bootstrap Python request; remove {destination.name}")
        marker = destination.with_name("bootstrap-python-launcher.txt")
        if marker.exists() or marker.is_symlink():
            raise ValueError(f"{manifest}: obsolete bootstrap launcher; remove {marker.name}")
        return {}
    version = data["bootstrapPython"]
    if (
        not isinstance(version, str) or len(version) > 32
        or not re.fullmatch(r"3\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version)
    ):
        raise ValueError(f"{manifest}: bootstrapPython must be an exact stable CPython version")
    outputs: dict[Path, str | bytes] = {destination: f"cpython@{version}\n"}
    marker = destination.with_name("bootstrap-python-launcher.txt")
    if data.get("bootstrapLauncher") != "uv" and (marker.exists() or marker.is_symlink()):
        raise ValueError(f"{manifest}: obsolete bootstrap launcher; remove {marker.name}")
    if data.get("bootstrapLauncher") == "uv":
        root = canonical_root or Path(__file__).resolve().parents[2]
        templates = root / "libs/payload-invocation/templates"
        provider = root / "libs/installer-engine"
        output = manifest.parent / "scripts/_bootstrap_python"
        outputs[marker] = "uv\n"
        for name in ("bootstrap-uv.ps1", "bootstrap-uv.sh", "bootstrap-capture.ps1", "bootstrap-capture.exe"):
            outputs[output / name] = (provider / name).read_bytes()
        outputs[output / "versioned_runtime_native.ps1"] = (
            root / "libs/versioned-runtime/versioned_runtime_native.ps1"
        ).read_bytes()
        outputs[output / "select.ps1"] = (templates / "bootstrap-python-powershell.tmpl").read_text(encoding="utf-8")
        outputs[output / "select.sh"] = (templates / "bootstrap-python-posix.tmpl").read_text(encoding="utf-8")
        if guidance:
            outputs[manifest.parent / "scripts/write-session-guidance.ps1"] = (
                f"$guidancePlugin = '{data['plugin']}'\n"
                + (templates / "bootstrap-guidance-powershell.tmpl").read_text(encoding="utf-8")
            )
        inputs = [data.get("catalogGate"), *data.get("payloadDispatcher", {}).values()]
        if any(isinstance(value, str) and manifest.parent / value in outputs for value in inputs):
            raise ValueError(f"{manifest}: bootstrap launcher output conflicts with a declared input")
    return outputs
