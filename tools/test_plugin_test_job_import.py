"""The explicit process-only bundle imports without a checkout or site packages."""

from pathlib import Path
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import native_process_sources as sources
    import plugin_test_containment as containment
else:
    from tools import native_process_sources as sources, plugin_test_containment as containment


def test_process_bundle_imports_its_actual_containment_dependencies(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    tools = tmp_path / "tools"
    package = tmp_path / "libs" / "agent-procutil" / "src" / "agent_procutil"
    tools.mkdir()
    package.mkdir(parents=True)
    for name in sources.TOOLS:
        shutil.copyfile(root / "tools" / name, tools / name)
    for path in (root / "libs" / "agent-procutil" / "src" / "agent_procutil").glob("*.py"):
        shutil.copyfile(path, package / path.name)
    result = subprocess.run(
        [
            sys.executable, "-I", "-S", "-B", "-c",
            "import sys\n"
            "sys.path[:0] = sys.argv[1:]\n"
            "selected = [False]\n"
            "def audit(event, arguments):\n"
            "    if event == 'ctypes.dlopen' and not selected[0]:\n"
            "        raise AssertionError('unselected native import loaded a DLL')\n"
            "sys.addaudithook(audit)\n"
            "import plugin_test_job_native, plugin_test_job_waits\n"
            "selected[0] = True\n"
            "import ctypes\n"
            "ctypes.WinDLL = lambda *a, **k: (_ for _ in ()).throw(AssertionError('DLL loaded'))\n"
            "import plugin_test_containment, plugin_test_lifecycle, plugin_test_job_observation\n"
            "print('declared containment imports complete')",
            str(tools), str(package.parent),
        ],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
        **containment.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "declared containment imports complete"
