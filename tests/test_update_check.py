"""Self-check for version comparison: python tests/test_update_check.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macmonica.__main__ import _is_newer


def main():
    assert _is_newer("1.2.1", "1.2.0")
    assert _is_newer("1.10.0", "1.9.9")
    assert _is_newer("2.0", "1.9.9")
    assert not _is_newer("1.1.5", "1.2.0"), "a stale cache must not advertise a downgrade"
    assert not _is_newer("1.2.0", "1.2.0")
    assert not _is_newer("1.2", "1.2.0")
    assert not _is_newer("1.2.0", "1.2"), "padding must treat 1.2 and 1.2.0 as equal"
    print("ok: version comparison behaves")


if __name__ == "__main__":
    main()
