"""Fixed native-exec continuation, imported only from the frozen source closure."""

from pathlib import Path
import sys

SOURCE = Path(__file__).absolute().parent.parent
sys.path[:0] = [
    str(SOURCE / "src"), str(SOURCE.parent / "agent-procutil" / "src"),
    str(SOURCE.parent / "single-instance-lease" / "src"),
]

from update_driver.foundation_linux import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
