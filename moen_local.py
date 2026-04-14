#!/usr/bin/env python3
"""
U by Moen Local Shower Controller
Controls your shower directly over the local network — no internet required.

⚠️  FIRMWARE COMPATIBILITY
    This script only works with pre-Pusher (legacy) firmware that does NOT have
    the 'hmi_supports_pusher' capability.  Firmware 3.x controllers route all
    control through Pusher and return "File not_found" for every /v1/shower
    request.

    For firmware 3.x the recommended offline path is HomeKit:
      python3 moen_control.py homekit on   # one-time, requires internet
      Then pair in the Apple Home app — after that all control is local.

Usage:
  python3 moen_local.py discover          # find controller IP via mDNS
  python3 moen_local.py status
  python3 moen_local.py on [--temp 38]
  python3 moen_local.py off
  python3 moen_local.py temp 40
  python3 moen_local.py outlet 1 on|off

Requires:
  moen_config.json with controller_ip and shower_token  (set by setup_moen.py)
  cryptography >= 3.0  (pip install cryptography)
"""

import argparse, hashlib, json, os, socket, sys, time
import urllib.request, urllib.error

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CONFIG_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moen_config.json")
CONTROLLER_PORT = 80

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError("Config not found. Run setup_moen.py first.")
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ---------------------------------------------------------------------------
# Temperature helpers (same lookup table as moen_control.py)
# ---------------------------------------------------------------------------

_C_TO_F = {
    15:60,  16:61,  17:63,  18:65,  19:67,  20:68,  21:70,  22:72,  23:74,
    24:76,  25:77,  26:79,  27:81,  28:83,  29:85,  30:86,  31:88,  32:90,
    33:92,  34:94,  35:95,  36:97,  37:100, 38:101, 39:103, 40:104, 41:105,
    42:107, 43:109, 44:111, 45:113, 46:114, 47:116, 48:118, 49:120,
}
_F_TO_C = {v: k for k, v in _C_TO_F.items()}

def _to_f(temp: int, celsius: bool) -> int:
    if not celsius:
        return temp
    f = _C_TO_F.get(temp)
    if f is None:
        raise ValueError(
            f"{temp}°C is not a valid controller step "
            f"(valid range {min(_C_TO_F)}–{max(_C_TO_F)}°C)"
        )
    return f

def _fmt(temp_f, celsius: bool) -> str:
    if not celsius:
        return f"{int(temp_f)}°F"
    c = _F_TO_C.get(int(temp_f), round((temp_f - 32) * 5 / 9))
    return f"{c}°C"

# ---------------------------------------------------------------------------
# Crypto  (mirrors Android Crypto.java)
#
# Auth-Hash : sha256(shower_token + ":" + serial + ":" + shower_token)
# AES key   : shower_token[:16]  encoded as ASCII bytes
# AES IV    : sha256(timestamp_string)[:16]  encoded as ASCII bytes
# Mode      : AES-128-CTR  (no padding)
# ---------------------------------------------------------------------------

def _timestamp() -> str:
    return str(int(time.time() * 1000) // 100)

def _auth_hash(shower_token: str, serial: str) -> str:
    return hashlib.sha256(
        f"{shower_token}:{serial}:{shower_token}".encode()
    ).hexdigest()

def _aes_ctr(data: bytes, timestamp: str, shower_token: str, *, encrypt: bool) -> bytes:
    key = shower_token[:16].encode()                                   # 16 ASCII bytes
    iv  = hashlib.sha256(timestamp.encode()).hexdigest()[:16].encode() # 16 ASCII bytes
    c   = Cipher(algorithms.AES(key), modes.CTR(iv))
    op  = c.encryptor() if encrypt else c.decryptor()
    return op.update(data) + op.finalize()

# ---------------------------------------------------------------------------
# Local HTTP helpers
# ---------------------------------------------------------------------------

def _get(ip: str, path: str, ahash: str) -> tuple[dict, str]:
    """GET request to controller. Returns (parsed_json, response_timestamp)."""
    req = urllib.request.Request(f"http://{ip}{path}")
    req.add_header("Auth-Hash", ahash)
    req.add_header("Accept", "*/*")
    with urllib.request.urlopen(req, timeout=10) as r:
        ts   = r.headers.get("Timestamp", "")
        body = r.read()
    return body, ts

def _post(ip: str, path: str, ahash: str, ts: str, body_bytes: bytes):
    """POST encrypted body to controller."""
    req = urllib.request.Request(
        f"http://{ip}{path}",
        data=body_bytes,
        method="POST",
    )
    req.add_header("Auth-Hash", ahash)
    req.add_header("Timestamp", ts)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Content-Length", str(len(body_bytes)))
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status

def get_shower_state(ip: str, shower_token: str, serial: str) -> dict:
    """GET /v1/shower — returns decrypted JSON shower state."""
    ahash       = _auth_hash(shower_token, serial)
    enc_body, ts = _get(ip, "/v1/shower", ahash)
    plaintext   = _aes_ctr(enc_body, ts, shower_token, encrypt=False)
    return json.loads(plaintext)

def set_shower_state(ip: str, shower_token: str, serial: str, request_obj: dict):
    """POST /v1/shower — encrypts request_obj and sends it."""
    ahash    = _auth_hash(shower_token, serial)
    ts       = _timestamp()
    payload  = json.dumps(request_obj, separators=(",", ":"))
    enc_body = _aes_ctr(payload.encode(), ts, shower_token, encrypt=True)
    return _post(ip, "/v1/shower", ahash, ts, enc_body)

# ---------------------------------------------------------------------------
# mDNS discovery
# ---------------------------------------------------------------------------

def discover_controller(timeout: int = 5) -> str | None:
    """
    Attempt to resolve the controller's mDNS hostname 'moen-dolphin.local'.
    Works on Linux (Avahi), macOS (mDNSResponder), and Windows (mDNS enabled).
    Returns the IP string, or None if not found.
    """
    try:
        result = socket.getaddrinfo("moen-dolphin.local", CONTROLLER_PORT,
                                    socket.AF_INET, socket.SOCK_STREAM)
        return result[0][4][0]
    except OSError:
        return None

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_discover(cfg: dict):
    print("Searching for controller via mDNS (moen-dolphin.local)...")
    ip = discover_controller()
    if ip:
        print(f"Found controller at {ip}")
        cfg["controller_ip"] = ip
        save_config(cfg)
        print(f"Saved controller_ip to {CONFIG_FILE}")
    else:
        print("Not found. Make sure you're on the same WiFi as the controller.")
        print("Alternatively, check your router's DHCP table for the controller IP")
        print("and add  \"controller_ip\": \"<IP>\"  to moen_config.json manually.")

def cmd_status(ip: str, shower_token: str, serial: str, celsius: bool):
    state = get_shower_state(ip, shower_token, serial)
    for key in ("current_temperature", "target_temperature"):
        if state.get(key) is not None:
            state[key] = _fmt(state[key], celsius)
    print(json.dumps(state, indent=2))

def cmd_on(ip: str, shower_token: str, serial: str, temp: float, celsius: bool):
    temp_f = _to_f(int(temp), celsius)
    # Fetch current outlet layout so we can activate the right outlet positions
    try:
        state   = get_shower_state(ip, shower_token, serial)
        outlets = state.get("outlets", [{"position": 1, "active": False}])
    except Exception:
        outlets = [{"position": 1, "active": False}]

    # Activate outlet 1 (default), keep others as-is
    for o in outlets:
        o["active"] = (o.get("position", 0) == 1)

    req = {
        "current_mode":    "adjusting",
        "mode":            "ready",
        "target_temperature": temp_f,
        "active_preset":   0,
        "outlets":         outlets,
    }
    set_shower_state(ip, shower_token, serial, req)
    print(f"Turn ON {_fmt(temp_f, celsius)} → sent")

def cmd_off(ip: str, shower_token: str, serial: str):
    try:
        state   = get_shower_state(ip, shower_token, serial)
        outlets = state.get("outlets", [{"position": 1, "active": True}])
    except Exception:
        outlets = [{"position": 1, "active": True}]

    for o in outlets:
        o["active"] = False

    req = {"current_mode": "off", "outlets": outlets}
    set_shower_state(ip, shower_token, serial, req)
    print("Turn OFF → sent")

def cmd_temp(ip: str, shower_token: str, serial: str, temp: float, celsius: bool):
    temp_f = _to_f(int(temp), celsius)
    req    = {"target_temperature": temp_f}
    set_shower_state(ip, shower_token, serial, req)
    print(f"Set temp {_fmt(temp_f, celsius)} → sent")

def cmd_outlet(ip: str, shower_token: str, serial: str, position: int, active: bool):
    try:
        state   = get_shower_state(ip, shower_token, serial)
        outlets = state.get("outlets", [])
    except Exception:
        outlets = []

    # Update the specified outlet, or append a new entry if not present
    found = False
    for o in outlets:
        if o.get("position") == position:
            o["active"] = active
            found = True
    if not found:
        outlets.append({"position": position, "active": active})

    all_off = not any(o.get("active") for o in outlets)
    req = {
        "outlets": outlets,
        **({"mode": "paused"} if all_off else {}),
    }
    set_shower_state(ip, shower_token, serial, req)
    state_str = "ON" if active else "OFF"
    print(f"Outlet {position} {state_str} → sent")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="U by Moen Local Controller (legacy firmware only — see module docstring)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="Find controller IP via mDNS and save to config")
    sub.add_parser("status",   help="Get current shower state")
    sub.add_parser("off",      help="Turn shower off")

    p_on = sub.add_parser("on", help="Turn shower on")
    p_on.add_argument("--temp", type=float, default=None,
                      help="Target temperature (°C or °F; default 38°C / 100°F)")

    p_temp = sub.add_parser("temp", help="Set temperature while running")
    p_temp.add_argument("degrees", type=float)

    p_outlet = sub.add_parser("outlet", help="Turn a specific outlet on or off")
    p_outlet.add_argument("position", type=int, help="Outlet number (1-4)")
    p_outlet.add_argument("state",    choices=["on", "off"])

    args = parser.parse_args()
    cfg  = load_config()

    shower_token = cfg.get("shower_token", "")
    serial       = cfg.get("serial", "")
    ip           = cfg.get("controller_ip", "")

    if args.command == "discover":
        cmd_discover(cfg)
        sys.exit(0)

    if not shower_token:
        print("Missing shower_token in moen_config.json.")
        print("This value is stored by setup_moen.py when it fetches shower details.")
        sys.exit(1)
    if not ip:
        print("Missing controller_ip in moen_config.json.")
        print("Run: python3 moen_local.py discover")
        sys.exit(1)

    # Detect temperature units from config (0=Celsius, 1=Fahrenheit)
    celsius = (cfg.get("temperature_units", 1) == 0)

    if args.command == "status":
        cmd_status(ip, shower_token, serial, celsius)
    elif args.command == "on":
        temp = args.temp if args.temp is not None else (38.0 if celsius else 100.0)
        cmd_on(ip, shower_token, serial, temp, celsius)
    elif args.command == "off":
        cmd_off(ip, shower_token, serial)
    elif args.command == "temp":
        cmd_temp(ip, shower_token, serial, args.degrees, celsius)
    elif args.command == "outlet":
        cmd_outlet(ip, shower_token, serial, args.position, args.state == "on")
