from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


_BOOTSTRAP = (
    Path(__file__).resolve().parents[1] / "scripts" / "bootstrap-check.sh"
)


def test_bootstrap_falls_through_init_shim_to_stamp_capable_installer(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX bootstrap execution is covered on POSIX")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")

    plugin = tmp_path / "plugin"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(_BOOTSTRAP, scripts / "bootstrap-check.sh")
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "bootstrap-probe"}),
        encoding="utf-8",
    )
    (scripts / "init.sh").write_text(
        "#!/usr/bin/env bash\nexec bash \"$(dirname \"$0\")/install.sh\" install\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        "#!/usr/bin/env bash\n"
        "case \"${1:-}\" in\n"
        "  stamp) touch \"$HOME/stamp-ran\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    Path(env["HOME"]).mkdir()

    subprocess.run(
        [bash, str(scripts / "bootstrap-check.sh")],
        env=env,
        check=True,
    )

    assert (Path(env["HOME"]) / "stamp-ran").is_file()
