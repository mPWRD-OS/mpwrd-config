import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mpwrd_config.core import Config, NetworkingConfig, WifiNetwork, load_config, save_config


class TestCoreConfig(unittest.TestCase):
    def test_networking_to_dict_omits_optional_fields(self) -> None:
        cfg = NetworkingConfig()
        data = cfg.to_dict()

        self.assertEqual(data["hostname"], "mpwrd")
        self.assertEqual(data["country_code"], "US")
        self.assertFalse(data["wifi_enabled"])
        self.assertNotIn("wifi", data)
        self.assertNotIn("wifi_interface", data)
        self.assertNotIn("ethernet_interface", data)

    def test_networking_from_dict_parses_wifi(self) -> None:
        payload = {
            "hostname": "node-1",
            "wifi_enabled": True,
            "country_code": "CA",
            "wifi": [{"ssid": "mesh", "psk": "secret"}],
            "wifi_interface": "wlan0",
            "ethernet_interface": "eth0",
        }

        cfg = NetworkingConfig.from_dict(payload)

        self.assertEqual(cfg.hostname, "node-1")
        self.assertTrue(cfg.wifi_enabled)
        self.assertEqual(cfg.country_code, "CA")
        self.assertEqual(cfg.wifi_interface, "wlan0")
        self.assertEqual(cfg.ethernet_interface, "eth0")
        self.assertEqual(len(cfg.wifi), 1)
        self.assertIsInstance(cfg.wifi[0], WifiNetwork)
        self.assertEqual(cfg.wifi[0].ssid, "mesh")
        self.assertEqual(cfg.wifi[0].psk, "secret")

    def test_save_config_is_idempotent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            cfg = Config(networking=NetworkingConfig(hostname="alpha"))

            self.assertTrue(save_config(cfg, path))
            self.assertFalse(save_config(cfg, path))

            loaded = load_config(path)
            self.assertEqual(loaded.networking.hostname, "alpha")


if __name__ == "__main__":
    unittest.main()
