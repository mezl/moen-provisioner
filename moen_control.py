#!/usr/bin/env python3
"""
U by Moen Cloud Shower Controller
Controls your shower via Pusher (cloud WebSocket relay).
Requires a Pusher-enabled controller (hmi_supports_pusher capability).

Usage:
  python3 moen_control.py status
  python3 moen_control.py on --temp 38
  python3 moen_control.py off
  python3 moen_control.py preset 1
  python3 moen_control.py temp 40

Requires moen_config.json with user_token and serial.
Run setup_moen.py first if you haven't already.
"""

import argparse, json, os, random, ssl, socket, struct, sys, time
import urllib.request, urllib.error, urllib.parse

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moen_config.json")
API_SERVER  = "https://www.moen-iot.com"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError("Config not found. Run setup_moen.py first.")
    with open(CONFIG_FILE) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Cloud API
# ---------------------------------------------------------------------------

def get_credentials(user_token: str, serial: str) -> dict:
    params = urllib.parse.urlencode({"user_token": user_token, "serial_number": serial})
    req = urllib.request.Request(f"{API_SERVER}/v2/credentials?{params}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def get_pusher_auth(user_token: str, serial: str, channel_name: str, socket_id: str) -> str:
    """Authenticate private channel. Custom params in query string, Pusher params in POST body."""
    query = urllib.parse.urlencode({"user_token": user_token, "serial_number": serial})
    body  = urllib.parse.urlencode({"channel_name": channel_name, "socket_id": socket_id}).encode()
    req   = urllib.request.Request(f"{API_SERVER}/v2/pusher-auth?{query}", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["auth"]

# ---------------------------------------------------------------------------
# Minimal WebSocket client (stdlib only, TLS)
# ---------------------------------------------------------------------------

def _ws_read_exactly(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("WebSocket connection closed")
        buf += chunk
    return buf

def ws_connect(host: str, path: str) -> ssl.SSLSocket:
    import base64
    key = base64.b64encode(os.urandom(16)).decode()
    raw = socket.create_connection((host, 443), timeout=15)
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(raw, server_hostname=host)
    sock.sendall((
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(1024)
    if b"101" not in resp:
        raise ConnectionError(f"WebSocket handshake failed: {resp[:200]}")
    sock.settimeout(30)
    return sock

def ws_send(sock, text: str):
    payload = text.encode()
    mask    = os.urandom(4)
    masked  = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    if n <= 125:
        header = bytes([0x81, 0x80 | n]) + mask
    elif n <= 65535:
        header = bytes([0x81, 0xFE]) + struct.pack(">H", n) + mask
    else:
        header = bytes([0x81, 0xFF]) + struct.pack(">Q", n) + mask
    sock.sendall(header + masked)

def ws_recv(sock) -> str:
    """Read one WebSocket text frame, transparently handle ping/close."""
    while True:
        hdr    = _ws_read_exactly(sock, 2)
        opcode = hdr[0] & 0x0F
        n      = hdr[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", _ws_read_exactly(sock, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _ws_read_exactly(sock, 8))[0]
        payload = _ws_read_exactly(sock, n) if n > 0 else b""
        if opcode == 0x8:  # close
            raise ConnectionError("Server closed WebSocket")
        if opcode == 0x9:  # ping → pong
            sock.sendall(bytes([0x8A, len(payload)]) + payload)
            continue
        if opcode == 0xA:  # pong
            continue
        return payload.decode()

# ---------------------------------------------------------------------------
# Pusher protocol
# ---------------------------------------------------------------------------

def pusher_send(sock, event: str, data, channel: str = None):
    """Send a Pusher event.
    Pusher protocol events (pusher:*) use raw dict data.
    Client-triggered events use JSON-encoded string data, per Pusher wire format.
    """
    msg = {"event": event}
    msg["data"] = data if event.startswith("pusher:") else json.dumps(data)
    if channel:
        msg["channel"] = channel
    ws_send(sock, json.dumps(msg))

def pusher_connect(app_key: str, cluster: str):
    """Connect to Pusher and return (sock, socket_id)."""
    host = f"ws-{cluster}.pusher.com"
    path = f"/app/{app_key}?protocol=7&client=python&version=1.0"
    sock = ws_connect(host, path)
    deadline = time.time() + 15
    while time.time() < deadline:
        msg = json.loads(ws_recv(sock))
        if msg.get("event") == "pusher:connection_established":
            data = json.loads(msg["data"])
            return sock, data["socket_id"]
    raise TimeoutError("Timed out waiting for Pusher connection_established")

def pusher_subscribe(sock, channel_name: str, auth: str):
    """Subscribe to a private channel and wait for confirmation."""
    pusher_send(sock, "pusher:subscribe", {"channel": channel_name, "auth": auth})
    deadline = time.time() + 15
    while time.time() < deadline:
        msg = json.loads(ws_recv(sock))
        evt = msg.get("event", "")
        if "subscription_succeeded" in evt and msg.get("channel") == channel_name:
            return
        if msg.get("event") == "pusher:error":
            raise ConnectionError(f"Pusher subscription error: {msg}")
    raise TimeoutError("Timed out waiting for Pusher subscription_succeeded")

def open_channel(user_token: str, serial: str):
    """Fetch credentials, connect, authenticate, subscribe. Returns (sock, channel_name)."""
    creds        = get_credentials(user_token, serial)
    channel_name = f"private-{creds['channel']}"
    sock, socket_id = pusher_connect(creds["app_key"], creds["cluster"])
    auth = get_pusher_auth(user_token, serial, channel_name, socket_id)
    pusher_subscribe(sock, channel_name, auth)
    return sock, channel_name

def trigger_control(sock, channel: str, action: str, params=None):
    """Trigger a client-state-desired control action."""
    data = {"action": action}
    if params is not None:
        data["params"] = params
    pusher_send(sock, "client-state-desired", {"type": "control", "data": data}, channel=channel)

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(user_token: str, serial: str):
    sock, channel = open_channel(user_token, serial)
    try:
        rpc_id = random.randint(1, 999)
        pusher_send(sock, "client-command",
                    {"jsonrpc": "2.0", "method": "do_shower_report", "id": rpc_id},
                    channel=channel)
        deadline = time.time() + 20
        while time.time() < deadline:
            msg = json.loads(ws_recv(sock))
            if (msg.get("event") == "client-state-reported"
                    and msg.get("channel") == channel):
                data = msg["data"]
                if isinstance(data, str):
                    data = json.loads(data)
                print(json.dumps(data, indent=2))
                return
        print("Timeout: no shower report received")
    finally:
        sock.close()

def cmd_on(user_token: str, serial: str, temp_c: float):
    sock, channel = open_channel(user_token, serial)
    try:
        trigger_control(sock, channel, "shower_on", {})
        time.sleep(0.3)
        trigger_control(sock, channel, "temperature_set", {"target_temperature": int(temp_c)})
        time.sleep(0.3)
        print(f"Turn ON {int(temp_c)}°C → sent")
    finally:
        sock.close()

def cmd_off(user_token: str, serial: str):
    sock, channel = open_channel(user_token, serial)
    try:
        trigger_control(sock, channel, "shower_off")
        time.sleep(0.3)
        print("Turn OFF → sent")
    finally:
        sock.close()

def cmd_temp(user_token: str, serial: str, temp_c: float):
    sock, channel = open_channel(user_token, serial)
    try:
        trigger_control(sock, channel, "temperature_set", {"target_temperature": int(temp_c)})
        time.sleep(0.3)
        print(f"Set temp {int(temp_c)}°C → sent")
    finally:
        sock.close()

def cmd_preset(user_token: str, serial: str, position: int):
    sock, channel = open_channel(user_token, serial)
    try:
        trigger_control(sock, channel, "shower_on", {"preset": position})
        time.sleep(0.3)
        print(f"Run preset {position} → sent")
    finally:
        sock.close()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="U by Moen Cloud Controller")
    sub    = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Get current shower state")
    sub.add_parser("off",    help="Turn shower off")

    p_on = sub.add_parser("on", help="Turn shower on")
    p_on.add_argument("--temp", type=float, default=38.0, help="Target °C (default 38)")

    p_temp = sub.add_parser("temp", help="Set temperature")
    p_temp.add_argument("degrees", type=float, help="Target °C")

    p_preset = sub.add_parser("preset", help="Run a preset")
    p_preset.add_argument("position", type=int, help="Preset number (1-12)")

    args       = parser.parse_args()
    cfg        = load_config()
    user_token = cfg.get("user_token")
    serial     = cfg.get("serial", "")

    if not user_token:
        print("Missing user_token in moen_config.json. Run setup_moen.py first.")
        sys.exit(1)

    if   args.command == "status": cmd_status(user_token, serial)
    elif args.command == "on":     cmd_on(user_token, serial, args.temp)
    elif args.command == "off":    cmd_off(user_token, serial)
    elif args.command == "temp":   cmd_temp(user_token, serial, args.degrees)
    elif args.command == "preset": cmd_preset(user_token, serial, args.position)
