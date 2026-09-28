"""Self-check for the collector doctor check: python tests/test_doctor_collector.py"""

import plistlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macmonica.doctor import _check_collector


def _write_plist(dirpath, interpreter):
    path = Path(dirpath) / "com.macmonica.collector.plist"
    path.write_bytes(plistlib.dumps({"Label": "com.macmonica.collector",
                                     "ProgramArguments": [interpreter, "-m", "macmonica", "collect"]}))
    return path


def main():
    with tempfile.TemporaryDirectory() as d:
        missing = _check_collector(Path(d) / "absent.plist")
        assert missing[1] is None and "Not installed" in missing[2], missing

        gone = _check_collector(_write_plist(d, "/opt/homebrew/Caskroom/miniforge/base/bin/python"))
        assert gone[1] is False and "Interpreter is gone" in gone[2], gone

        present = _check_collector(_write_plist(d, sys.executable))
        assert present[1] is not False, present

        empty = Path(d) / "empty.plist"
        empty.write_bytes(plistlib.dumps({"Label": "x"}))
        assert _check_collector(empty)[1] is False

    print("ok: all collector doctor checks pass")


if __name__ == "__main__":
    main()
