# AGENTS.md

Local guidance for Codex/assistants working in this repo.

## Current session instructions
- Work in repository: `https://github.com/Ruledo/mpwrd-config`
- Do not commit or push without explicit user permission.
- Use branch: `rework`

## Workflow rules (non‑negotiable)
- **Never commit, push, or open a PR unless the user explicitly asks.**
- **Always work from the `developer` branch unless the user explicitly asks otherwise.**
- When a PR is requested, open a **new** PR each time.
- **Bump the Debian package version whenever building new .deb packages.**
- **Do as much testing as possible yourself** (you have sudo). The user should not have to test for you.

## Project overview
- `mpwrd_config/` is the main Python package.
- TUI lives in `mpwrd_config/tui_dialog.py` (InquirerPy + prompt_toolkit).
- Systemd units are in `systemd/` and are installed onto the system.
## Legacy note
- Legacy `usr/` scripts have been fully ported; the `usr/` folder is removed from the repo.
- User note: Ari is one of the main authors of Foxbuntu and mpwrd-config (Ruledo on the femtofox GitHub).

## Runtime notes
- Most functionality requires root. Use `sudo` when executing CLI/TUI or system changes.

## Device access
- Pemtofox Pro (Armbian) IP: `10.0.10.168`
- SSH login: user `osc`, password `femto`
- Serial access: `/dev/ttyUSB0`
- Use `120s` timeout for SSH/SCP operations to this device; the Femtofox can respond slowly at times.

## Common commands
- TUI: `sudo ./mpwrd-config`

## Quick checks
- `python -m py_compile mpwrd_config/tui_dialog.py`

## CI packaging
- CI test debs build on push to `developer` and on PRs targeting `main`.
  - Find them in GitHub Actions → run → Artifacts (`mpwrd-config-deb`).
- CI builds auto-stamp a version suffix (e.g., `+gitYYYYMMDD.<sha>`) without committing changes.
- Release debs build on tag pushes like `v0.1.2-1` (tags should be on `main`).
  - Tag flow:
    - `git checkout main`
    - `git pull`
    - `git tag v0.1.2-1`
    - `git push origin v0.1.2-1`
  - The release workflow attaches `.deb` files to the GitHub Release.

## Deb versioning (manual for releases)
- When preparing a **release** build, bump versions in BOTH places:
  - `setup.cfg` → `version = X.Y.Z`
  - `debian/changelog` → `mpwrd-config (X.Y.Z-1) ...`
- CI builds **do not** commit any version changes; they add a temporary `+gitYYYYMMDD.<sha>` suffix in the runner.

## CI artifacts vs releases
- **Artifacts** (CI): transient test builds; download from Actions → run → Artifacts.
- **Releases**: created only by tagging `main` (`vX.Y.Z-N`); assets attach to the GitHub Release.
