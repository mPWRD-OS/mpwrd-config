# Feature Gap Inventory (Shell → Python Refactor)

This document tracks **missing features** from the legacy shell tooling that still need to be implemented in the Python refactor. It reflects the items explicitly called out by the user and should be updated as each feature is ported.

## Not in scope
- USB config tool (explicitly not needed).

## In progress

- **Meshtastic configuration & service management**
  - Python CLI commands implemented (service control, URL/keys, radio selection, I2C toggle, mesh test).
  - Advanced config dump + summary + LoRa settings wizard added (CLI + TUI).
  - Source: `usr/local/bin/femto-meshtasticd-config.sh`

- **Kernel module management**
  - Initial Python CLI commands implemented (list boot/active/blacklist, enable/disable, blacklist/unblacklist).
  - Source: `usr/local/bin/femto-kernel-modules.sh`

- **Dialog TUI menus**
  - Initial dialog-based TUI added (main menu + implemented feature hooks).
  - Source: `usr/local/bin/femto-config`

- **Time & timezone configuration**
  - Initial CLI commands implemented (timedatectl status/timezone/time set).
  - Source: `usr/local/bin/femto-set-time.sh`

- **Software manager**
  - Python CLI + dialog TUI added; runs package scripts under `usr/local/bin/packages/`.
  - Source: `usr/local/bin/femto-software.sh`

- **Watchclock service**
  - Python watchclock loop added (time-change monitor + meshtasticd restart).
  - Systemd service unit added.
  - Source: `usr/local/bin/femto-watchclock-dog.sh`

- **Install wizard**
  - Python dialog wizard added (time, hostname, Wi-Fi, Meshtastic basics).
  - Source: `usr/local/bin/femto-install-wizard.sh`

- **Wi‑Fi mesh sync service**
  - Python sync loop added (config.proto + wlan0 state).
  - Systemd service unit added.
  - Source: `usr/local/bin/femto-wifi-mesh-control.sh`

- **System utilities**
  - Python utilities added (ACT LED, logging, ttyd, SSH keys, system info).
  - Source: `usr/local/bin/femto-utils.sh`

## Missing features (to be ported)

1) **Dialog TUI feature parity**
   - Expand menus and flows as needed to match all legacy UX polish.
   - Source: `usr/local/bin/femto-config`
