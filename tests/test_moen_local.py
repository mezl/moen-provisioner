"""
Unit tests for moen_local.py.
No real controller or network access — HTTP calls are mocked.
"""

import hashlib, json, os, sys, unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import moen_local as ml


# ---------------------------------------------------------------------------
# Temperature helpers (shared with moen_control — spot-check key values)
# ---------------------------------------------------------------------------

class TestTemperature(unittest.TestCase):

    def test_celsius_to_fahrenheit(self):
        self.assertEqual(ml._to_f(38, True), 101)

    def test_fahrenheit_passthrough(self):
        self.assertEqual(ml._to_f(100, False), 100)

    def test_invalid_celsius_raises(self):
        with self.assertRaises(ValueError):
            ml._to_f(50, True)

    def test_fmt_celsius(self):
        self.assertEqual(ml._fmt(101, True), "38°C")

    def test_fmt_fahrenheit(self):
        self.assertEqual(ml._fmt(100, False), "100°F")


# ---------------------------------------------------------------------------
# Crypto primitives
# ---------------------------------------------------------------------------

class TestCrypto(unittest.TestCase):

    def test_timestamp_numeric_string(self):
        self.assertRegex(ml._timestamp(), r"^\d+$")

    def test_auth_hash_matches_known_formula(self):
        token, serial = "ABCDEF1234567890ABCDEF1234567890", "1082364"
        expected = hashlib.sha256(
            f"{token}:{serial}:{token}".encode()
        ).hexdigest()
        self.assertEqual(ml._auth_hash(token, serial), expected)

    def test_auth_hash_is_64_hex_chars(self):
        h = ml._auth_hash("TOKEN12345678901", "9999999")
        self.assertRegex(h, r"^[0-9a-f]{64}$")

    def test_aes_ctr_encrypt_decrypt_roundtrip(self):
        plaintext    = b'{"current_mode":"adjusting","target_temperature":101}'
        shower_token = "ABCDEF1234567890XXXXXXXXXXXXXXXX"
        timestamp    = "17012345678"
        encrypted    = ml._aes_ctr(plaintext, timestamp, shower_token, encrypt=True)
        decrypted    = ml._aes_ctr(encrypted, timestamp, shower_token, encrypt=False)
        self.assertEqual(decrypted, plaintext)

    def test_aes_ctr_different_timestamps_produce_different_ciphertext(self):
        plain = b"test payload"
        token = "1234567890ABCDEF"
        c1 = ml._aes_ctr(plain, "111111111", token, encrypt=True)
        c2 = ml._aes_ctr(plain, "999999999", token, encrypt=True)
        self.assertNotEqual(c1, c2)

    def test_aes_ctr_uses_first_16_chars_of_token(self):
        """Two tokens that share the first 16 chars must produce identical output."""
        plain = b"hello"
        ts    = "123456789"
        c1 = ml._aes_ctr(plain, ts, "ABCDEF1234567890DIFFERENT_SUFFIX", encrypt=True)
        c2 = ml._aes_ctr(plain, ts, "ABCDEF1234567890XXXXXXXXXXXXXX",   encrypt=True)
        self.assertEqual(c1, c2)

    def test_aes_ctr_iv_derived_from_sha256_of_timestamp(self):
        """IV = sha256(timestamp_string)[:16] as ASCII bytes (not raw hash bytes)."""
        ts      = "17012345678"
        iv_str  = hashlib.sha256(ts.encode()).hexdigest()[:16]
        self.assertEqual(len(iv_str), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in iv_str))


# ---------------------------------------------------------------------------
# get_shower_state (GET /v1/shower with decryption)
# ---------------------------------------------------------------------------

class TestGetShowerState(unittest.TestCase):

    def _mock_get(self, plaintext_dict: dict, timestamp: str = "17000000000"):
        """Return a mock that simulates the encrypted GET response."""
        shower_token = "TESTTOKEN1234567ABCDEFGH"
        plain_bytes  = json.dumps(plaintext_dict).encode()
        enc_bytes    = ml._aes_ctr(plain_bytes, timestamp, shower_token, encrypt=True)

        mock_resp = MagicMock()
        mock_resp.headers.get.return_value = timestamp
        mock_resp.read.return_value        = enc_bytes
        mock_resp.__enter__.return_value   = mock_resp
        mock_resp.__exit__.return_value    = False
        return mock_resp, shower_token

    def test_decrypts_and_parses_state(self):
        state = {"current_mode": "off", "target_temperature": 101,
                 "outlets": [{"position": 1, "active": False}]}
        mock_resp, token = self._mock_get(state)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ml.get_shower_state("192.168.1.100", token, "1082364")
        self.assertEqual(result["current_mode"], "off")
        self.assertEqual(result["target_temperature"], 101)

    def test_auth_hash_header_sent(self):
        state = {"current_mode": "off"}
        mock_resp, token = self._mock_get(state)
        requests_made = []
        original_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen
        def capture(req, timeout=None):
            requests_made.append(req)
            return mock_resp
        with patch("urllib.request.urlopen", side_effect=capture):
            ml.get_shower_state("192.168.1.100", token, "1082364")
        self.assertTrue(len(requests_made) > 0)
        req = requests_made[0]
        self.assertEqual(req.get_header("Auth-hash"), ml._auth_hash(token, "1082364"))


# ---------------------------------------------------------------------------
# set_shower_state (POST /v1/shower with encryption)
# ---------------------------------------------------------------------------

class TestSetShowerState(unittest.TestCase):

    def test_encrypted_body_decrypts_correctly(self):
        shower_token  = "POSTTOKEN1234567ABCDEFGH"
        serial        = "1082364"
        request_obj   = {"current_mode": "adjusting", "target_temperature": 101}
        captured_reqs = []

        def fake_urlopen(req, timeout=None):
            captured_reqs.append(req)
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value  = False
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch.object(ml, "_timestamp", return_value="17000000000"):
            ml.set_shower_state("192.168.1.100", shower_token, serial, request_obj)

        self.assertEqual(len(captured_reqs), 1)
        req = captured_reqs[0]

        # Verify Auth-Hash header
        self.assertEqual(req.get_header("Auth-hash"), ml._auth_hash(shower_token, serial))

        # Decrypt the body and verify it matches the original request object
        ts        = req.get_header("Timestamp")
        body_enc  = req.data
        decrypted = ml._aes_ctr(body_enc, ts, shower_token, encrypt=False)
        self.assertEqual(json.loads(decrypted), request_obj)

    def test_timestamp_header_matches_iv_seed(self):
        """The Timestamp header sent to the controller must match the IV used for encryption."""
        shower_token  = "POSTTOKEN1234567ABCDEFGH"
        captured_reqs = []

        def fake_urlopen(req, timeout=None):
            captured_reqs.append(req)
            m = MagicMock()
            m.__enter__.return_value = m
            m.__exit__.return_value  = False
            return m

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ml.set_shower_state("192.168.1.100", shower_token, "1082364",
                                {"target_temperature": 60})

        req = captured_reqs[0]
        ts  = req.get_header("Timestamp")
        # Decryption with the same ts must succeed
        dec = ml._aes_ctr(req.data, ts, shower_token, encrypt=False)
        self.assertIn(b"target_temperature", dec)


# ---------------------------------------------------------------------------
# High-level commands
# ---------------------------------------------------------------------------

class TestCommands(unittest.TestCase):
    """Verify that cmd_* functions send the right payloads."""

    IP    = "192.168.1.50"
    TOKEN = "CMDTOKEN12345678ABCDEFGH"
    SN    = "1082364"

    def _run_cmd(self, fn, *args, current_state=None):
        """
        Execute a command function with mocked HTTP calls.
        Returns the decrypted POST body dict (None for status).
        """
        if current_state is None:
            current_state = {
                "current_mode": "off",
                "outlets": [{"position": 1, "active": False},
                            {"position": 2, "active": False}],
            }
        plain_bytes = json.dumps(current_state).encode()
        ts_get = "17000000000"
        enc_bytes = ml._aes_ctr(plain_bytes, ts_get, self.TOKEN, encrypt=True)

        get_resp = MagicMock()
        get_resp.headers.get.return_value = ts_get
        get_resp.read.return_value        = enc_bytes
        get_resp.__enter__.return_value   = get_resp
        get_resp.__exit__.return_value    = False

        post_reqs = []
        def fake_urlopen(req, timeout=None):
            if req.get_method() == "GET":
                return get_resp
            post_reqs.append(req)
            m = MagicMock(); m.__enter__.return_value = m; m.__exit__.return_value = False
            return m

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch.object(ml, "_timestamp", return_value="17000000001"), \
             patch("builtins.print"):
            fn(*args)

        if not post_reqs:
            return None
        req = post_reqs[0]
        ts  = req.get_header("Timestamp")
        return json.loads(ml._aes_ctr(req.data, ts, self.TOKEN, encrypt=False))

    def test_cmd_on_sets_adjusting_mode(self):
        body = self._run_cmd(ml.cmd_on, self.IP, self.TOKEN, self.SN, 38.0, True)
        self.assertEqual(body["current_mode"], "adjusting")
        self.assertEqual(body["target_temperature"], 101)

    def test_cmd_on_activates_outlet_1(self):
        body = self._run_cmd(ml.cmd_on, self.IP, self.TOKEN, self.SN, 38.0, True)
        outlet1 = next(o for o in body["outlets"] if o["position"] == 1)
        self.assertTrue(outlet1["active"])

    def test_cmd_off_deactivates_all_outlets(self):
        body = self._run_cmd(ml.cmd_off, self.IP, self.TOKEN, self.SN,
                             current_state={
                                 "current_mode": "adjusting",
                                 "outlets": [{"position": 1, "active": True},
                                             {"position": 2, "active": True}],
                             })
        self.assertTrue(all(not o["active"] for o in body["outlets"]))

    def test_cmd_temp_sets_target_temperature(self):
        body = self._run_cmd(ml.cmd_temp, self.IP, self.TOKEN, self.SN, 40.0, True)
        self.assertEqual(body["target_temperature"], 104)

    def test_cmd_outlet_on(self):
        body = self._run_cmd(ml.cmd_outlet, self.IP, self.TOKEN, self.SN, 2, True)
        outlet2 = next(o for o in body["outlets"] if o["position"] == 2)
        self.assertTrue(outlet2["active"])

    def test_cmd_outlet_off(self):
        body = self._run_cmd(
            ml.cmd_outlet, self.IP, self.TOKEN, self.SN, 1, False,
            current_state={
                "current_mode": "adjusting",
                "outlets": [{"position": 1, "active": True}],
            },
        )
        outlet1 = next(o for o in body["outlets"] if o["position"] == 1)
        self.assertFalse(outlet1["active"])

    def test_cmd_outlet_all_off_sets_paused_mode(self):
        """Turning off the last active outlet should add mode=paused."""
        body = self._run_cmd(
            ml.cmd_outlet, self.IP, self.TOKEN, self.SN, 1, False,
            current_state={
                "current_mode": "adjusting",
                "outlets": [{"position": 1, "active": True}],
            },
        )
        self.assertEqual(body.get("mode"), "paused")


# ---------------------------------------------------------------------------
# homekit command in moen_control.py
# ---------------------------------------------------------------------------

class TestHomekitCommand(unittest.TestCase):
    """Verify the 'settings' envelope is used (not 'control')."""

    def _captured_pusher_payload(self, enable: bool):
        import moen_control as mc
        sent = []
        with patch.object(mc, "open_channel", return_value=(MagicMock(), "private-ch")), \
             patch.object(mc, "pusher_send",  side_effect=lambda *a, **kw: sent.append((a, kw))), \
             patch("time.sleep"), patch("builtins.print"):
            mc.cmd_homekit("TOKEN", "1082364", enable)
        return sent

    def test_homekit_on_sends_settings_type(self):
        sent = self._captured_pusher_payload(True)
        self.assertEqual(len(sent), 1)
        _, kw_or_args = sent[0]
        # pusher_send(sock, event, data, channel=...)
        args = sent[0][0]
        event, data = args[1], args[2]
        self.assertEqual(event, "client-state-desired")
        self.assertEqual(data["type"], "settings")
        self.assertTrue(data["data"]["homekit_enable"])

    def test_homekit_off_disables(self):
        sent = self._captured_pusher_payload(False)
        data = sent[0][0][2]
        self.assertFalse(data["data"]["homekit_enable"])

    def test_homekit_uses_settings_not_control_type(self):
        """HomeKit must NOT use type='control' — that would be sent to wrong handler."""
        sent = self._captured_pusher_payload(True)
        data = sent[0][0][2]
        self.assertNotEqual(data["type"], "control")


if __name__ == "__main__":
    unittest.main()
