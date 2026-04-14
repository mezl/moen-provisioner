"""
Unit tests for setup_moen.py.

setup_moen.py is a flat script (no importable functions), so tests run it via
runpy.run_path() with all interactive and network calls mocked out.
"""

import json, os, runpy, sys, unittest
from unittest.mock import MagicMock, patch, mock_open

SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "setup_moen.py",
)


def _run_script(existing_config=None, inputs=(), passwords=(),
                token_response=b'{"token":"TESTTOKEN123"}', token_error=None):
    """
    Execute setup_moen.py with all I/O mocked.

    Returns the dict that was passed to json.dump (i.e. what would be written
    to moen_config.json), or None if json.dump was never called.
    """
    saved = {}

    def fake_dump(obj, f, **kwargs):
        saved.update(obj)

    def fake_urlopen(req, timeout=None):
        if token_error:
            raise token_error
        mock_r = MagicMock()
        mock_r.read.return_value = token_response
        return mock_r

    config_exists = existing_config is not None
    read_data = json.dumps(existing_config) if config_exists else "{}"

    with patch("os.path.exists", return_value=config_exists), \
         patch("builtins.open", mock_open(read_data=read_data)), \
         patch("json.load", return_value=dict(existing_config or {})), \
         patch("json.dump", side_effect=fake_dump), \
         patch("builtins.input", side_effect=list(inputs)), \
         patch("getpass.getpass", side_effect=list(passwords)), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("urllib.request.Request", return_value=MagicMock()), \
         patch("builtins.print"):
        runpy.run_path(SCRIPT_PATH)

    return saved or None


class TestFreshSetup(unittest.TestCase):
    """No existing config — all fields are prompted."""

    def setUp(self):
        self.saved = _run_script(
            existing_config=None,
            inputs=["user@example.com", "HomeWifi", "shower", "9876543"],
            passwords=["secretpass", "wifipass123"],
        )

    def test_email_saved(self):
        self.assertEqual(self.saved["email"], "user@example.com")

    def test_password_saved(self):
        self.assertEqual(self.saved["moen_password"], "secretpass")

    def test_ssid_saved(self):
        self.assertEqual(self.saved["ssid"], "HomeWifi")

    def test_wifi_password_saved(self):
        self.assertEqual(self.saved["wifi_password"], "wifipass123")

    def test_shower_name_saved(self):
        self.assertEqual(self.saved["shower_name"], "shower")

    def test_serial_saved(self):
        self.assertEqual(self.saved["serial"], "9876543")

    def test_token_saved(self):
        self.assertEqual(self.saved["user_token"], "TESTTOKEN123")


class TestExistingConfig(unittest.TestCase):
    """Existing config with all fields — no prompts should fire."""

    FULL_CONFIG = {
        "email":          "existing@test.com",
        "moen_password":  "existingpass",
        "ssid":           "ExistingWifi",
        "wifi_password":  "existingwifi",
        "shower_name":    "my shower",
        "serial":         "1111111",
    }

    def test_no_input_prompts_when_all_fields_present(self):
        mock_input    = MagicMock()
        mock_getpass  = MagicMock()

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=json.dumps(self.FULL_CONFIG))), \
             patch("json.load", return_value=dict(self.FULL_CONFIG)), \
             patch("json.dump"), \
             patch("builtins.input", mock_input), \
             patch("getpass.getpass", mock_getpass), \
             patch("urllib.request.urlopen", return_value=MagicMock(
                 read=MagicMock(return_value=b'{"token":"NEWTOKEN"}')
             )), \
             patch("urllib.request.Request", return_value=MagicMock(
                 add_header=MagicMock()
             )), \
             patch("builtins.print"):
            runpy.run_path(SCRIPT_PATH)

        mock_input.assert_not_called()
        mock_getpass.assert_not_called()

    def test_existing_values_preserved_after_run(self):
        saved = _run_script(existing_config=self.FULL_CONFIG)
        self.assertEqual(saved["email"],         self.FULL_CONFIG["email"])
        self.assertEqual(saved["ssid"],          self.FULL_CONFIG["ssid"])
        self.assertEqual(saved["serial"],        self.FULL_CONFIG["serial"])

    def test_fresh_token_replaces_old_one(self):
        config_with_old_token = dict(self.FULL_CONFIG)
        config_with_old_token["user_token"] = "OLDTOKEN"
        saved = _run_script(
            existing_config=config_with_old_token,
            token_response=b'{"token":"FRESHTOKEN"}',
        )
        self.assertEqual(saved["user_token"], "FRESHTOKEN")


class TestTokenFetchFailure(unittest.TestCase):
    """Network error during token fetch — config should still be saved."""

    ALL_INPUTS = {
        "inputs":    ["fail@test.com", "BadWifi", "shower", "5555555"],
        "passwords": ["badpass", "badwifi"],
    }

    def test_config_saved_without_token(self):
        saved = _run_script(
            existing_config=None,
            token_error=Exception("connection refused"),
            **self.ALL_INPUTS,
        )
        self.assertEqual(saved["email"], "fail@test.com")
        self.assertNotIn("user_token", saved)

    def test_other_fields_intact_on_failure(self):
        saved = _run_script(
            existing_config=None,
            token_error=Exception("timeout"),
            **self.ALL_INPUTS,
        )
        self.assertEqual(saved["ssid"],   "BadWifi")
        self.assertEqual(saved["serial"], "5555555")


class TestDefaultShowerName(unittest.TestCase):
    """Blank shower name input should default to 'shower'."""

    def test_empty_input_defaults_to_shower(self):
        saved = _run_script(
            existing_config=None,
            inputs=["a@b.com", "wifi", "", "1234567"],  # blank shower name
            passwords=["pass", "wifipass"],
        )
        self.assertEqual(saved["shower_name"], "shower")


if __name__ == "__main__":
    unittest.main()
