from __future__ import annotations

import os
import sys
import threading
from typing import Callable, TypeVar

SPINNER_FRAMES = ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷")
SPINNER_INTERVAL = 0.08

T = TypeVar("T")


def _run_with_spinner(label: str, action: Callable[[], T]) -> T:
    stop = threading.Event()

    def _spin() -> None:
        index = 0
        while not stop.is_set():
            frame = SPINNER_FRAMES[index % len(SPINNER_FRAMES)]
            try:
                sys.stdout.write(f"\r{frame} {label}")
                sys.stdout.flush()
            except Exception:
                return
            index += 1
            stop.wait(SPINNER_INTERVAL)
        frame = SPINNER_FRAMES[index % len(SPINNER_FRAMES)]
        try:
            sys.stdout.write(f"\r{frame} {label}\n")
            sys.stdout.flush()
        except Exception:
            return

    spinner = threading.Thread(target=_spin, name="mpwrd-start-spinner", daemon=True)
    spinner.start()
    try:
        return action()
    finally:
        stop.set()
        spinner.join(timeout=0.5)


def main() -> int:
    if os.getenv("MPWRD_ALLOW_NON_ROOT") != "1" and os.geteuid() != 0:
        print("mpwrd-config must be run as root. Try: sudo mpwrd-config")
        return 1
    os.environ["MPWRD_TUI_STARTING_SHOWN"] = "1"
    tui_main = _run_with_spinner(
        "Starting...",
        lambda: __import__("mpwrd_config.tui_dialog", fromlist=["main"]).main,
    )

    return tui_main()


if __name__ == "__main__":
    raise SystemExit(main())
