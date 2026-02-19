# femto-config Refactor Plan (Python-First)

This document outlines a proposed refactor and restructure plan for the Femto configuration tool. The goal is to create a **maintainable, idempotent, and KISS-first** Python-based system that supports:

- **CLI** (explicit subcommands)
- **Dialog-based TUI** (optional wrapper over the CLI)

Hardware support should be **agnostic by design**, while keeping **Luckfox Pico** as the primary supported target in the first implementation.

---

## Goals & Non-Goals

### Goals
- Keep the design **simple and tightly organized** (KISS).
- Provide a **single core configuration engine** with idempotent operations.
- Keep configuration **“in house”** (minimize reliance on third-party tooling).
- Create a **canonical configuration model** and derive system files from it.
- Support **explicit CLI commands** and dialog TUI using the same backend.
- Package as a Debian `.deb` with necessary systemd units.
- Leverage **pipx** for isolated tool installs where appropriate (available on Armbian).
- Enable automated tests (unit + integration as practical).

### Non-Goals (initial phase)
- Cross-platform support beyond Debian/Armbian targets.
- Migration from legacy configs (fresh install on Debian 13).
- First-boot wizard (explicitly not required).

---

## Proposed Repository Layout (Simple)

```
femto-config/
├─ pyproject.toml
├─ femto_config/
│  ├─ cli.py                 # CLI entrypoint + subcommands
│  ├─ core.py                # Canonical model + idempotency helpers
│  ├─ system.py              # System file adapters
│  ├─ platform_luckfox_pico_mini.py # Luckfox Pico Mini specifics (only when needed)
│  ├─ tui_dialog.py          # Dialog wrapper calling CLI
├─ legacy/                   # Old scripts kept temporarily (read-only)
├─ systemd/                  # Optional service units for helpers
├─ packaging/
│  └─ deb/
├─ tests/
│  ├─ unit/
│  └─ integration/
└─ docs/
   ├─ refactor-plan.md
   └─ architecture.md
```

---

## Canonical Config Model

### Format Recommendation
Use **TOML** for readability and structured data. (If YAML preferred later, the store can be swapped.)

### Model Example (TOML)
```toml
[networking]
hostname = "femto"
wifi_enabled = true
country_code = "US"

[[networking.wifi]]
ssid = "my-ssid"
psk = "secret"

[services]
avahi_daemon = { enabled = true, running = true }
meshtasticd = { enabled = true, running = true }

[hardware]
act_led = "enable"
spi0 = { enabled = true, speed = 1000000 }
i2c3 = { enabled = false }
uart3 = { enabled = true }
```

### Idempotency
All writes to system files should:
- Read existing state.
- Compare to desired state.
- Only write if changes are needed.
- Preserve formatting/metadata when possible.

---

## CLI Design (Explicit Commands)

### Examples
```
femto-config status
femto-config networking show
femto-config networking wifi set --ssid <ssid> --psk <psk> --country <cc>
femto-config networking wifi enable|disable
femto-config services list
femto-config services set meshtasticd --enable --start
femto-config hardware led set enable
```

### Output
Human-readable by default. JSON optional only if needed later.

---

## Dialog TUI
- Keep **dialog** as a wrapper over the CLI.
- The TUI should never mutate state directly; it should call CLI commands.
- This preserves idempotency and keeps a single source of truth.

---

## Hardware Abstraction

### Strategy
- Keep core logic **hardware-agnostic**.
- Put hardware-specific behaviors in `platforms/`.
- Luckfox Pico implementation will include:
  - LED control paths
  - SPI/I2C/UART config files
  - Device tree-specific info

---

## Systemd Integration

### Example Units
- Device helper services only (no web UI socket/service).

---

## Testing Strategy

### Unit Tests
- Pure Python tests for config parsing, idempotency checks.

### Integration Tests
- Run on CI with mocked file systems.
- Optional hardware-in-the-loop tests later.

---

## Next Steps (Keep It Simple)

1. Implement canonical model + idempotent writes in `core.py`.
2. Implement CLI subcommands in `cli.py`.
3. Add system adapters in `system.py` (networking, services, hardware).
4. Add dialog wrapper in `tui_dialog.py`.
5. Create packaging + systemd units.
6. Add automated tests.
