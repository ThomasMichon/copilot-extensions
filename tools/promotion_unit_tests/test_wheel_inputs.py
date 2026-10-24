"""Synthetic byte-only controls; native custody/bootstrap cases stay in tools."""
import io
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libs" / "installer-engine"))
import installer_wheel_inputs as inspection
from wheel_input_fixture import wheel_bytes


@pytest.mark.parametrize("kind", ["valid", "record", "metadata", "path", "limit"])
def test_wheel_bytes_are_inspected_without_running_package_code(kind):
    limits = inspection.WheelLimits(100_000, 100_000, 20_000, 10_000, 30, 100, 10_000)
    kwargs = {}
    if kind == "record":
        kwargs["record_mutator"] = lambda rows: rows.pop(0)
    elif kind == "metadata":
        kwargs["metadata"] = b"Name: duplicate\nName: duplicate\nVersion: 1\n"
    elif kind == "path":
        kwargs["extra"] = {"../escape": b"never extracted"}
    elif kind == "limit":
        kwargs["metadata"] = b"Description: " + b"x" * 15_000
    data = wheel_bytes(**kwargs)
    if kind != "valid":
        with pytest.raises(inspection.WheelError):
            inspection.inspect_archive(io.BytesIO(data), limits=limits, deadline=time.monotonic() + 5)
    else:
        facts = inspection.inspect_archive(io.BytesIO(data), limits=limits, deadline=time.monotonic() + 5)
        assert facts.name == "demo-pkg"
        assert facts.requires_dist == ("helper>=2; extra == 'fast'",)
