"""Publication adapter exporting source-verified native daemon wheel assets."""
from pathlib import Path
import zipfile

from build_python_artifacts import read_wheel_generator
from native_daemon_artifacts import _fail
from promotion_python_artifacts import validate_wheel


def wheel_assets(wheel: Path, *, source_dir: Path) -> dict[Path, bytes]:
    with wheel.open("rb") as stream:
        validate_wheel(
            stream, {"filename": wheel.name, "generator": read_wheel_generator(wheel)},
            source_dir=source_dir,
        )
    with zipfile.ZipFile(wheel) as bundle:
        members = [
            name for name in bundle.namelist()
            if name.startswith("agent_procutil/native/") and name.endswith("/daemon-instance")
        ]
        if len(members) != 1:
            _fail("native payload assembly requires one supported built executable")
        return {
            Path(members[0].removeprefix("agent_procutil/")): bundle.read(members[0]),
        }
