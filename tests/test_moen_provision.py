"""
Unit tests for moen_provision.py.
All network/socket calls are mocked — no real controller or cloud access needed.
"""

import base64, hashlib, json, os, socket, sys, tempfile, time, unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import moen_provision as mp


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------

class TestCrypto(unittest.TestCase):

    def test_sha256_hex_known_value(self):
        # echo -n "hello" | sha256sum
        self.assertEqual(
            mp.sha256_hex("hello"),
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )

    def test_pad_pin_uppercases_and_pads(self):
        self.assertEqual(mp.pad_pin("ab12"), "AB12000000000000")

    def test_pad_pin_already_16_chars(self):
        p = "ABCD1234EFGH5678"
        self.assertEqual(mp.pad_pin(p), p)

    def test_pad_pin_short_pin(self):
        self.assertEqual(mp.pad_pin("A1"), "A1" + "0" * 14)

    def test_auth_hash_structure(self):
        # auth_hash(pin, serial) == sha256("{pin}:{serial}:{pin}")
        pin, serial = "AB12000000000000", "1234567"
        expected = hashlib.sha256(f"{pin}:{serial}:{pin}".encode()).hexdigest()
        self.assertEqual(mp.auth_hash(pin, serial), expected)

    def test_auth_hash_is_64_hex_chars(self):
        result = mp.auth_hash("AB12000000000000", "9999999")
        self.assertRegex(result, r"^[0-9a-f]{64}$")

    def test_timestamp_is_numeric_string(self):
        ts = mp.timestamp()
        self.assertRegex(ts, r"^\d+$")

    def test_timestamp_roughly_now(self):
        before = int(time.time() * 1000) // 100
        ts = int(mp.timestamp())
        after  = int(time.time() * 1000) // 100
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after + 1)

    def test_rsa_encrypt_field_produces_base64(self):
        """Generate a throwaway RSA key and verify the output is valid base64."""
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub  = priv.public_key()
        pem  = pub.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        pem_b64 = base64.b64encode(pem).decode()
        result = mp.rsa_encrypt_field("testvalue", pem_b64)
        # Must be a non-empty base64 string with no newlines
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        self.assertNotIn("\n", result)
        base64.b64decode(result)  # raises if not valid base64

    def test_rsa_encrypt_includes_sha512_in_payload(self):
        """The encrypted payload embeds sha512 of the plaintext — verify decrypt path."""
        from cryptography.hazmat.primitives.asymmetric import rsa, padding as asym_padding
        from cryptography.hazmat.primitives import serialization
        plaintext = "mytoken"
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub  = priv.public_key()
        pem_b64 = base64.b64encode(
            pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        ).decode()
        encrypted_b64 = mp.rsa_encrypt_field(plaintext, pem_b64)
        raw = priv.decrypt(base64.b64decode(encrypted_b64), asym_padding.PKCS1v15())
        decoded = raw.decode()
        value, sha = decoded.rsplit(":", 1)
        self.assertEqual(value, plaintext)
        self.assertEqual(sha, hashlib.sha512(plaintext.encode()).hexdigest())


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------

class TestConfig(unittest.TestCase):

    def test_load_config_missing_raises(self):
        with patch("os.path.exists", return_value=False):
            with self.assertRaises(FileNotFoundError):
                mp.load_config()

    def test_load_config_reads_json(self):
        data = {"email": "a@b.com", "serial": "123"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            fname = f.name
        try:
            with patch.object(mp, "CONFIG_FILE", fname):
                with patch("os.path.exists", return_value=True):
                    result = mp.load_config()
            self.assertEqual(result, data)
        finally:
            os.unlink(fname)

    def test_save_config_round_trip(self):
        data = {"email": "x@y.com", "serial": "456", "user_token": "ABCD"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            fname = f.name
        try:
            with patch.object(mp, "CONFIG_FILE", fname):
                mp.save_config(data)
                with patch("os.path.exists", return_value=True):
                    loaded = mp.load_config()
            self.assertEqual(loaded, data)
        finally:
            os.unlink(fname)


# ---------------------------------------------------------------------------
# get_user_token
# ---------------------------------------------------------------------------

class TestGetUserToken(unittest.TestCase):

    def test_returns_cached_token_without_network_call(self):
        cfg = {"user_token": "CACHEDTOKEN"}
        with patch.object(mp, "cloud_login") as mock_login:
            result = mp.get_user_token(cfg)
        self.assertEqual(result, "CACHEDTOKEN")
        mock_login.assert_not_called()

    def test_fetches_fresh_token_when_missing(self):
        cfg = {"email": "a@b.com", "moen_password": "pw"}
        with patch.object(mp, "cloud_login", return_value="FRESHTOKEN") as mock_login, \
             patch.object(mp, "save_config"):
            result = mp.get_user_token(cfg)
        self.assertEqual(result, "FRESHTOKEN")
        mock_login.assert_called_once_with("a@b.com", "pw")

    def test_fresh_token_saved_to_config(self):
        cfg = {"email": "a@b.com", "moen_password": "pw"}
        saved = {}
        def capture_save(c):
            saved.update(c)
        with patch.object(mp, "cloud_login", return_value="SAVEDTOKEN"), \
             patch.object(mp, "save_config", side_effect=capture_save):
            mp.get_user_token(cfg)
        self.assertEqual(saved.get("user_token"), "SAVEDTOKEN")


# ---------------------------------------------------------------------------
# controller_post_tcp
# ---------------------------------------------------------------------------

class TestControllerPostTcp(unittest.TestCase):

    def _run(self, response_bytes):
        """Run controller_post_tcp with a mocked socket returning response_bytes."""
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [response_bytes, b""]
        with patch("moen_provision.socket.socket", return_value=mock_sock):
            return mp.controller_post_tcp("/v2/prov/registration", "AHASH", "TS123", '{"key":"val"}')

    def test_returns_200(self):
        self.assertEqual(self._run(b"HTTP/1.1 200 OK\r\n\r\n"), 200)

    def test_returns_404(self):
        self.assertEqual(self._run(b"HTTP/1.1 404 Not Found\r\n\r\n"), 404)

    def test_returns_400(self):
        self.assertEqual(self._run(b"HTTP/1.1 400 Bad Request\r\n\r\n"), 400)

    def test_returns_500(self):
        self.assertEqual(self._run(b"HTTP/1.1 500 Internal Server Error\r\n\r\n"), 500)

    def test_returns_minus_one_for_unknown(self):
        self.assertEqual(self._run(b"HTTP/1.1 302 Redirect\r\n\r\n"), -1)

    def test_request_contains_required_headers(self):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"HTTP/1.1 200 OK\r\n\r\n", b""]
        sent_data = bytearray()
        mock_sock.sendall.side_effect = lambda b: sent_data.extend(b)
        with patch("moen_provision.socket.socket", return_value=mock_sock):
            mp.controller_post_tcp("/v2/prov/registration", "TESTHASH", "99999", '{"x":1}')
        request_str = sent_data.decode(errors="replace")
        self.assertIn("POST /v2/prov/registration HTTP/1.1", request_str)
        self.assertIn("Auth-Hash: TESTHASH", request_str)
        self.assertIn("Timestamp: 99999", request_str)
        self.assertIn("Content-Type: application/json", request_str)
        self.assertIn('{"x":1}', request_str)

    def test_socket_timeout_handled_gracefully(self):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"HTTP/1.1 200 OK\r\n", socket.timeout]
        with patch("moen_provision.socket.socket", return_value=mock_sock):
            code = mp.controller_post_tcp("/path", "hash", "ts", "{}")
        self.assertEqual(code, 200)


# ---------------------------------------------------------------------------
# poll_status
# ---------------------------------------------------------------------------

class TestPollStatus(unittest.TestCase):

    def test_returns_true_on_connected(self):
        with patch.object(mp, "controller_get", return_value={"status": "connected"}), \
             patch("time.sleep"):
            self.assertTrue(mp.poll_status("AHASH", max_attempts=5))

    def test_returns_false_on_wrong_password(self):
        with patch.object(mp, "controller_get",
                          return_value={"status": "failed",
                                        "progress": "connecting to network failed"}), \
             patch("time.sleep"):
            self.assertFalse(mp.poll_status("AHASH", max_attempts=5))

    def test_returns_false_after_max_attempts(self):
        with patch.object(mp, "controller_get", return_value={"status": "connecting"}), \
             patch("time.sleep"):
            self.assertFalse(mp.poll_status("AHASH", max_attempts=3))

    def test_network_errors_are_tolerated(self):
        """Transient errors should not abort polling — only max_attempts stops it."""
        calls = [Exception("timeout"), Exception("timeout"), {"status": "connected"}]
        with patch.object(mp, "controller_get", side_effect=calls), \
             patch("time.sleep"):
            self.assertTrue(mp.poll_status("AHASH", max_attempts=5))


# ---------------------------------------------------------------------------
# CLI argparse
# ---------------------------------------------------------------------------

class TestArgParse(unittest.TestCase):

    def _parse(self, argv):
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--pin", required=True)
        p.add_argument("--refresh-token", action="store_true")
        return p.parse_args(argv)

    def test_pin_required(self):
        with self.assertRaises(SystemExit):
            self._parse([])

    def test_pin_parsed(self):
        args = self._parse(["--pin", "AB12"])
        self.assertEqual(args.pin, "AB12")

    def test_refresh_token_default_false(self):
        args = self._parse(["--pin", "AB12"])
        self.assertFalse(args.refresh_token)

    def test_refresh_token_flag(self):
        args = self._parse(["--pin", "AB12", "--refresh-token"])
        self.assertTrue(args.refresh_token)


if __name__ == "__main__":
    unittest.main()
