# mpwrd-config

## Build/Test (poetry)

```bash
pipx install poetry
poetry install
poetry build
poetry run mpwrd-config-cli --help
```

## Run With sudo After pipx Install

```bash
sudo "$(command -v mpwrd-config)"
sudo "$(command -v mpwrd-config-cli)" --help
```
