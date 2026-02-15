from __future__ import annotations

import os

from mpwrd_config.tui_dialog import main as tui_main


def main() -> int:
    if os.getenv("MPWRD_ALLOW_NON_ROOT") != "1" and os.geteuid() != 0:
        print("mpwrd-config must be run as root. Try: sudo mpwrd-config")
        return 1
    return tui_main()


if __name__ == "__main__":
    raise SystemExit(main())
