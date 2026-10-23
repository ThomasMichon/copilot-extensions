"""Keep mutable fixtures out of source retained by concurrent installer checks."""

import json

import pytest

from update_driver.foundation_inputs import PACKAGES, SUPPORT


@pytest.fixture
def area(tmp_path_factory):
    return tmp_path_factory.mktemp("ud")


@pytest.fixture
def native_source(area):
    common = area / "source"
    for directory, (name, dependencies) in PACKAGES.items():
        package = common / directory
        package.mkdir(parents=True)
        lines = [
            "[project]", f"name = {json.dumps(name)}", 'version = "1.0.0"',
            "dependencies = " + json.dumps(sorted(dependencies)), "[tool.uv.sources]",
        ]
        for dependency in sorted(dependencies):
            target = next(key for key, value in PACKAGES.items() if value[0] == dependency)
            lines.append(f'{dependency} = {{ path = "../{target}", editable = true }}')
        (package / "pyproject.toml").write_text("\n".join(lines), encoding="utf-8")
        (package / "source.py").write_text("pass\n", encoding="utf-8")
    plugin = common / "update-driver"
    (plugin / "scripts").mkdir()
    for name in SUPPORT:
        (plugin / "scripts" / name).write_text("# fixture\n", encoding="utf-8")
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")
    (plugin / "Z.py").write_text("Z\n", encoding="utf-8")
    (plugin / "a.py").write_text("a\n", encoding="utf-8")
    (plugin / "\ue000.py").write_text("bmp\n", encoding="utf-8")
    (plugin / "\U00010000.py").write_text("astral\n", encoding="utf-8")
    return plugin
