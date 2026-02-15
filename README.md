# mpwrd-config

## Install (APT)

```sh
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://mpwrd-os.github.io/mpwrd-config/apt/mpwrd-config-archive-keyring.asc \
  | sudo tee /etc/apt/keyrings/mpwrd-config-archive-keyring.asc >/dev/null

echo "deb [signed-by=/etc/apt/keyrings/mpwrd-config-archive-keyring.asc] https://mpwrd-os.github.io/mpwrd-config/apt/ ./" \
  | sudo tee /etc/apt/sources.list.d/mpwrd-config.list >/dev/null

sudo apt update
sudo apt install mpwrd-config
```
