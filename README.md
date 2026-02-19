# mpwrd-config

MPWRD configuration tooling.

## Install (pipx)

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install git+https://github.com/mPWRD-OS/mpwrd-config.git
```

## Usage

```bash
sudo mpwrd-config
sudo mpwrd-config-cli --help
```

## Development

```bash
python3 -m pip install --user poetry
poetry install
poetry build
```
