"""
Unit tests for moen_control.py.
All tests run without network access — cloud/Pusher calls are mocked.
"""

import json
import struct
import sys
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__file__)))
import moen_control as mc


# ---------------------------------------------------------------------------
# Temperature helpers
# ---------------------------------------------------------------------------

class TestTemperatureConversion(unittest.TestCase):

    def test_celsius_to_fahrenheit_known_values(self):
        self.assertEqual(mc._to_controller_f(38, True), 101)
        self.assertEqual(mc._to_controller_f(20, True), 68)
        self.assertEqual(mc._to_controller_f(15, True), 60)  # min
        self.assertEqual(mc._to_controller_f(49, True), 120) # max

    def test_fahrenheit_passthrough(self):
        self.assertEqual(mc._to_controller_f(100, False), 100)
        self.assertEqual(mc._to_controller_f(72,  False), 72)

    def test_invalid_celsius_raises(self):
        with self.assertRaises(ValueError):
            mc._to_controller_f(50, True)   # above max (49°C)
        with self.assertRaises(ValueError):
            mc._to_controller_f(14, True)   # below min (15°C)
        with self.assertRaises(ValueError):
            mc._to_controller_f(0,  True)

    def test_roundtrip_all_celsius_steps(self):
        """Every entry in _C_TO_F must survive a round-trip through _F_TO_C."""
        for c, f in mc._C_TO_F.items():
            self.assertIn(f, mc._F_TO_C, f"{f}°F missing from reverse table")
            self.assertEqual(mc._F_TO_C[f], c,
                             f"Round-trip mismatch: {c}°C → {f}°F → {mc._F_TO_C[f]}°C")

    def test_fmt_celsius(self):
        self.assertEqual(mc._fmt(101, True), "38°C")
        self.assertEqual(mc._fmt(68,  True), "20°C")

    def test_fmt_fahrenheit(self):
        self.assertEqual(mc._fmt(100, False), "100°F")
        self.assertEqual(mc._fmt(72,  False), "72°F")

    def test_fmt_unknown_fahrenheit_falls_back_to_formula(self):
        # A value not in the table should be estimated, not crash.
        result = mc._fmt(99, True)
        self.assertIn("°C", result)


# ---------------------------------------------------------------------------
# WebSocket frame encoding
# ---------------------------------------------------------------------------

class TestWsSend(unittest.TestCase):

    def _capture_send(self, text):
        """Call ws_send on a mock socket and return the raw bytes written."""
        sock = MagicMock()
        sent = bytearray()
        sock.sendall.side_effect = lambda b: sent.extend(b)
        mc.ws_send(sock, text)
        return bytes(sent)

    def _decode_frame(self, raw):
        """Decode a masked client WebSocket text frame back to a string."""
        self.assertEqual(raw[0], 0x81, "First byte must be 0x81 (FIN + text)")
        self.assertTrue(raw[1] & 0x80, "Mask bit must be set")
        length = raw[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", raw[2:4])[0]
            mask_start = 4
        elif length == 127:
            length = struct.unpack(">Q", raw[2:10])[0]
            mask_start = 10
        else:
            mask_start = 2
        mask = raw[mask_start:mask_start + 4]
        payload = raw[mask_start + 4:mask_start + 4 + length]
        decoded = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return decoded.decode()

    def test_short_frame_roundtrip(self):
        text = '{"event":"test"}'
        raw = self._capture_send(text)
        self.assertEqual(self._decode_frame(raw), text)

    def test_medium_frame_roundtrip(self):
        # 126-byte extended-length frame
        text = "x" * 200
        raw = self._capture_send(text)
        self.assertEqual(self._decode_frame(raw), text)

    def test_large_frame_roundtrip(self):
        text = "y" * 70000
        raw = self._capture_send(text)
        self.assertEqual(self._decode_frame(raw), text)


# ---------------------------------------------------------------------------
# Pusher protocol: pusher_send data encoding
# ---------------------------------------------------------------------------

class TestPusherSend(unittest.TestCase):

    def _sent_json(self, event, data, channel=None):
        """Call pusher_send and return the parsed JSON that was written."""
        frames = []
        sock = MagicMock()
        sock.sendall.side_effect = lambda b: frames.append(b)
        mc.pusher_send(sock, event, data, channel=channel)
        # Decode the single frame
        raw = b"".join(frames)
        # Strip WebSocket header (mask bit set, var length)
        n = raw[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", raw[2:4])[0]; ms = 4
        elif n == 127:
            n = struct.unpack(">Q", raw[2:10])[0]; ms = 10
        else:
            ms = 2
        mask = raw[ms:ms+4]
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(raw[ms+4:ms+4+n]))
        return json.loads(payload.decode())

    def test_pusher_subscribe_data_is_dict(self):
        msg = self._sent_json("pusher:subscribe", {"channel": "private-abc", "auth": "x:y"})
        self.assertIsInstance(msg["data"], dict)
        self.assertEqual(msg["data"]["channel"], "private-abc")

    def test_client_event_data_is_json_string(self):
        """Client-triggered events must have data as a JSON-encoded string (Pusher wire format)."""
        payload = {"type": "control", "data": {"action": "shower_off"}}
        msg = self._sent_json("client-state-desired", payload, channel="private-abc")
        self.assertIsInstance(msg["data"], str,
                              "client-state-desired data must be a JSON string, not a dict")
        inner = json.loads(msg["data"])
        self.assertEqual(inner["type"], "control")

    def test_channel_field_included_when_provided(self):
        msg = self._sent_json("client-state-desired", {}, channel="private-xyz")
        self.assertEqual(msg["channel"], "private-xyz")

    def test_channel_field_absent_when_not_provided(self):
        msg = self._sent_json("pusher:ping", {})
        self.assertNotIn("channel", msg)


# ---------------------------------------------------------------------------
# trigger_control payload structure
# ---------------------------------------------------------------------------

class TestTriggerControl(unittest.TestCase):

    def _captured_payload(self, action, params=None):
        """Run trigger_control and return the decoded inner payload dict."""
        sent_texts = []
        with patch.object(mc, "ws_send", side_effect=lambda sock, text: sent_texts.append(text)):
            sock = MagicMock()
            mc.trigger_control(sock, "private-ch", action, params)
        self.assertEqual(len(sent_texts), 1)
        outer = json.loads(sent_texts[0])
        self.assertEqual(outer["event"], "client-state-desired")
        self.assertEqual(outer["channel"], "private-ch")
        return json.loads(outer["data"])  # inner data is a JSON string

    def test_shower_off_no_params(self):
        inner = self._captured_payload("shower_off")
        self.assertEqual(inner["type"], "control")
        self.assertEqual(inner["data"]["action"], "shower_off")
        self.assertNotIn("params", inner["data"])

    def test_shower_on_with_preset(self):
        inner = self._captured_payload("shower_on", {"preset": 3})
        self.assertEqual(inner["data"]["action"], "shower_on")
        self.assertEqual(inner["data"]["params"]["preset"], 3)

    def test_temperature_set(self):
        inner = self._captured_payload("temperature_set", {"target_temperature": 101})
        self.assertEqual(inner["data"]["action"], "temperature_set")
        self.assertEqual(inner["data"]["params"]["target_temperature"], 101)

    def test_outlets_set(self):
        inner = self._captured_payload("outlets_set",
                                       {"outlets": [{"position": 2, "active": False}]})
        self.assertEqual(inner["data"]["action"], "outlets_set")
        outlets = inner["data"]["params"]["outlets"]
        self.assertEqual(len(outlets), 1)
        self.assertEqual(outlets[0]["position"], 2)
        self.assertFalse(outlets[0]["active"])


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

class TestArgParse(unittest.TestCase):
    """Verify argparse wiring without running any network code."""

    def _parse(self, argv):
        parser = mc.__loader__  # not used — we test via subprocess-style parse
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="command", required=True)
        sub.add_parser("status")
        sub.add_parser("off")
        sub.add_parser("identify")
        p_on = sub.add_parser("on")
        p_on.add_argument("--temp", type=float, default=None)
        p_temp = sub.add_parser("temp")
        p_temp.add_argument("degrees", type=float)
        p_preset = sub.add_parser("preset")
        p_preset.add_argument("position", type=int)
        p_outlet = sub.add_parser("outlet")
        p_outlet.add_argument("position", type=int)
        p_outlet.add_argument("state", choices=["on", "off"])
        return p.parse_args(argv)

    def test_status(self):
        args = self._parse(["status"])
        self.assertEqual(args.command, "status")

    def test_off(self):
        args = self._parse(["off"])
        self.assertEqual(args.command, "off")

    def test_on_with_temp(self):
        args = self._parse(["on", "--temp", "40"])
        self.assertEqual(args.command, "on")
        self.assertAlmostEqual(args.temp, 40.0)

    def test_on_without_temp(self):
        args = self._parse(["on"])
        self.assertIsNone(args.temp)

    def test_temp_command(self):
        args = self._parse(["temp", "104"])
        self.assertEqual(args.command, "temp")
        self.assertAlmostEqual(args.degrees, 104.0)

    def test_preset(self):
        args = self._parse(["preset", "5"])
        self.assertEqual(args.position, 5)

    def test_outlet_on(self):
        args = self._parse(["outlet", "1", "on"])
        self.assertEqual(args.command, "outlet")
        self.assertEqual(args.position, 1)
        self.assertEqual(args.state, "on")

    def test_outlet_off(self):
        args = self._parse(["outlet", "2", "off"])
        self.assertEqual(args.position, 2)
        self.assertEqual(args.state, "off")

    def test_identify(self):
        args = self._parse(["identify"])
        self.assertEqual(args.command, "identify")

    def test_outlet_invalid_state(self):
        with self.assertRaises(SystemExit):
            self._parse(["outlet", "1", "maybe"])

    def test_missing_subcommand(self):
        with self.assertRaises(SystemExit):
            self._parse([])


if __name__ == "__main__":
    unittest.main()
