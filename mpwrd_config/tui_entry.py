from __future__ import annotations

import os
import sys


def _print_starting_notice() -> None:
    try:
        sys.stdout.write("Starting...\n")
        sys.stdout.flush()
    except Exception:
        pass


def main() -> int:
    if os.getenv("MPWRD_ALLOW_NON_ROOT") != "1" and os.geteuid() != 0:
        print("mpwrd-config must be run as root. Try: sudo mpwrd-config")
        return 1
    os.environ["MPWRD_TUI_STARTING_SHOWN"] = "1"
    _print_starting_notice()
    from mpwrd_config.tui_dialog import main as tui_main

    return tui_main()


if __name__ == "__main__":
    raise SystemExit(main())
