"""Test-only PEP 517 backend that builds actual source with complete RECORDs."""
import base64
import csv
import hashlib
import io
import zipfile
from pathlib import Path

import tomllib


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    name, version = project["name"].replace("-", "_"), project["version"]
    filename = f"{name}-{version}-py3-none-any.whl"
    prefix = f"{name}-{version}.dist-info/"
    metadata = f'Metadata-Version: 2.4\nName: {project["name"]}\nVersion: {version}\n'
    if project.get("requires-python"):
        metadata += f'Requires-Python: {project["requires-python"]}\n'
    for requirement in project.get("dependencies", []):
        metadata += f"Requires-Dist: {requirement}\n"
    for extra, requirements in project.get("optional-dependencies", {}).items():
        metadata += f"Provides-Extra: {extra}\n"
        for requirement in requirements:
            metadata += f"Requires-Dist: {requirement}; extra == '{extra}'\n"
    entries = {
        prefix + "METADATA": (metadata + "\n").encode(),
        prefix + "WHEEL": b"Wheel-Version: 1.0\nGenerator: source-runtime-fixture\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n\n",
    }
    for source in Path("src").rglob("*"):
        if source.is_file() and "__pycache__" not in source.parts:
            entries[source.relative_to("src").as_posix()] = source.read_bytes()
    rows = [
        [path, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode(),
         str(len(content))]
        for path, content in entries.items()
    ]
    rows.append([prefix + "RECORD", "", ""])
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    entries[prefix + "RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries.items():
            info = zipfile.ZipInfo(path, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return filename
