"""Byte-only seed controls; native compiler/constructor proof stays in tools."""
import io
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "versioned-runtime"))
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))
import installer_packaging_seed as seed
from installer_wheel_inputs import WheelLimits
from wheel_input_fixture import wheel_bytes

SELECTED = {
    "name": "packaging", "version": "25.0", "filename": "packaging-25.0-py3-none-any.whl",
    "sha256": "sha256:" + "a" * 64,
}


def native_lock():
    return (
        'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.11"\n'
        '[[packages]]\nname = "packaging"\nversion = "25.0"\n'
        '[[packages.wheels]]\nurl = "https://feed.example/files/' + SELECTED["filename"]
        + '"\nhashes = {sha256 = "' + "a" * 64 + '"}\n'
    ).encode()


@pytest.mark.parametrize("kind", [
    "valid", "extra-package", "dependency", "multiple-wheels", "source", "hash",
    "public-url", "filename", "version", "unknown",
])
def test_seed_reads_only_one_exact_native_witness(kind):
    content = native_lock()
    if kind == "extra-package":
        content += b'\n[[packages]]\nname="helper"\nversion="1"\n'
    elif kind == "dependency":
        content = content.replace(b'name = "packaging"', b'dependencies = []\nname = "packaging"')
    elif kind == "multiple-wheels":
        content += b'\n[[packages.wheels]]\nurl="https://feed.example/other.whl"\n'
    elif kind == "source":
        content = content.replace(b"https://feed.example", b"file:///outside")
    elif kind == "hash":
        content = content.replace(b"a" * 64, b"b" * 63)
    elif kind == "public-url":
        content = content.replace(b"https://feed.example", b"https://user:secret@feed.example")
    elif kind == "filename":
        content = content.replace(b"py3-none-any.whl", b"py3-none-win_amd64.whl")
    elif kind == "version":
        content = content.replace(b"25.0", b"25.0rc1")
    elif kind == "unknown":
        content = b'unknown = "x"\n' + content
    if kind == "valid":
        assert seed._selected_wheel(content, deadline=time.monotonic() + 2) == SELECTED
    else:
        with pytest.raises(ValueError, match="exact native"):
            seed._selected_wheel(content, deadline=time.monotonic() + 2)


@pytest.mark.parametrize("kind", [
    "valid", "dependency", "extra", "pth", "data", "entry-point", "entry-point-case", "binary",
    "identity", "version", "purelib", "tag", "record", "budget",
])
def test_actual_seed_bytes_reject_installation_escape_before_execution(kind):
    metadata = "Metadata-Version: 2.4\nName: packaging\nVersion: 25.0\nRequires-Python: >=3.9\n"
    extra = {}
    wheel = "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
    if kind == "dependency":
        metadata += "Requires-Dist: helper>=1\n"
    elif kind == "extra":
        metadata += "Provides-Extra: hidden\n"
    elif kind == "pth":
        extra["escape.pth"] = b"import never_executed\n"
    elif kind == "data":
        extra["packaging-25.0.data/scripts/escape"] = b"never executed\n"
    elif kind == "entry-point":
        extra["packaging-25.0.dist-info/entry_points.txt"] = b"[console_scripts]\nx=y:z\n"
    elif kind == "entry-point-case":
        extra["packaging-25.0.dist-info/ENTRY_POINTS.TXT"] = b"[console_scripts]\nx=y:z\n"
    elif kind == "binary":
        extra["packaging/native.pyd"] = b"never executed"
    elif kind == "identity":
        metadata = metadata.replace("Name: packaging", "Name: other")
    elif kind == "version":
        metadata = metadata.replace("Version: 25.0", "Version: 24.0")
    elif kind == "purelib":
        wheel = wheel.replace("true", "false")
    elif kind == "tag":
        wheel = wheel.replace("py3-none-any", "cp311-cp311-win_amd64")
    options = {"record_mutator": lambda rows: rows.pop(0)} if kind == "record" else {}
    data = wheel_bytes(
        name="packaging", prefix="packaging-25.0.dist-info/",
        metadata=(metadata + "\n").encode(), wheel=wheel.encode(), extra=extra, **options,
    )
    limits = WheelLimits(10 if kind == "budget" else 100_000, 100_000, 20_000, 10_000, 30, 100, 10_000)
    if kind == "valid":
        assert seed.inspect_seed(
            io.BytesIO(data), SELECTED, limits=limits, deadline=time.monotonic() + 2,
        ).name == "packaging"
    else:
        with pytest.raises(ValueError):
            seed.inspect_seed(io.BytesIO(data), SELECTED, limits=limits, deadline=time.monotonic() + 2)
