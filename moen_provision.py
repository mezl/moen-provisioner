#!/usr/bin/env python3
"""
U by Moen WiFi Provisioner
Run setup_moen.py first to create moen_config.json

Usage:
  python3 moen_provision.py --pin XXXX
"""

import argparse, base64, hashlib, json, os, socket, time, urllib.request, urllib.parse, urllib.error

CONTROLLER_IP   = "192.168.10.1"
CONTROLLER_PORT = 80
API_SERVER      = "https://www.moen-iot.com"
CONFIG_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moen_config.json")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"Config not found. Run setup_moen.py first.\nExpected: {CONFIG_FILE}")
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

# ---------------------------------------------------------------------------
# Crypto (from Crypto.java)
# ---------------------------------------------------------------------------

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def pad_pin(pin: str) -> str:
    """Pad PIN to 16 chars with zeros (from SetupActivity.getPin())"""
    return pin.upper().ljust(16, "0")

def auth_hash(pin: str, serial: str) -> str:
    return sha256_hex(f"{pin}:{serial}:{pin}")

def timestamp() -> str:
    return str(int(time.time() * 1000) // 100)

def rsa_encrypt_field(plaintext: str, pem_b64: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    pem_bytes = base64.b64decode(pem_b64)
    pub_key = serialization.load_pem_public_key(pem_bytes)
    sha512 = hashlib.sha512(plaintext.encode()).hexdigest()
    payload = f"{plaintext}:{sha512}".encode()
    encrypted = pub_key.encrypt(payload, padding.PKCS1v15())
    return base64.b64encode(encrypted).decode().replace("\n", "")

# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------

def cloud_login(email: str, password: str) -> str:
    params = urllib.parse.urlencode({"email": email, "password": password})
    req = urllib.request.Request(f"{API_SERVER}/v2/authenticate?{params}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.loads(r.read())
        return resp["token"]

def get_user_token(config: dict) -> str:
    """Use cached token or fetch a fresh one."""
    if config.get("user_token"):
        print(f"[cloud] Using cached token: {config['user_token'][:20]}...")
        return config["user_token"]
    print("[cloud] Fetching token from Moen cloud...")
    token = cloud_login(config["email"], config["moen_password"])
    config["user_token"] = token
    save_config(config)
    print(f"[cloud] Got token: {token[:20]}...")
    return token

# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

def controller_get(path: str, ahash: str) -> dict:
    url = f"http://{CONTROLLER_IP}{path}"
    req = urllib.request.Request(url)
    req.add_header("Auth-Hash", ahash)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def controller_post_tcp(path: str, ahash: str, ts: str, body: str) -> int:
    body_bytes = body.encode()
    request = (
        f"POST {path} HTTP/1.1\r\n"
        f"Auth-Hash: {ahash}\r\n"
        f"Timestamp: {ts}\r\n"
        f"Content-Type: application/json\r\n"
        f"Accept: */*\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"Host: {CONTROLLER_IP}\r\n"
        f"Connection: Keep-Alive\r\n"
        f"\r\n"
    ) + body
    print(f"[tcp] Connecting to {CONTROLLER_IP}:{CONTROLLER_PORT}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    sock.connect((CONTROLLER_IP, CONTROLLER_PORT))
    sock.sendall(request.encode())
    response = b""
    sock.settimeout(10)
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    except socket.timeout:
        pass
    sock.close()
    resp_str = response.decode(errors="replace")
    print(f"[tcp] Response: {resp_str[:300]}")
    for code in [200, 400, 404, 500]:
        if f"HTTP/1.1 {code}" in resp_str:
            return code
    return -1

def poll_status(ahash: str, max_attempts: int = 60) -> bool:
    print("\n[status] Polling provisioning status...")
    for i in range(max_attempts):
        try:
            resp = controller_get("/v1/prov/status", ahash)
            print(f"[status] {i+1}/{max_attempts}: {resp}")
            if resp.get("status") == "connected":
                return True
            if resp.get("progress") == "connecting to network failed":
                print("[status] Wrong WiFi password or SSID")
                return False
        except Exception as e:
            print(f"[status] {i+1}/{max_attempts}: {e}")
        time.sleep(2)
    return False

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def wait_for_controller(timeout: int = 60) -> bool:
    """Wait until 192.168.10.1 is reachable, up to timeout seconds."""
    print(f"\n[network] Waiting for controller at {CONTROLLER_IP}...", flush=True)
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((CONTROLLER_IP, CONTROLLER_PORT))
            sock.close()
            if result == 0:
                print(f"[network] ✅ Controller reachable after {attempt} attempt(s)")
                return True
        except Exception:
            pass
        print(f"[network] Attempt {attempt}: not reachable, retrying...", flush=True)
        time.sleep(1)
    print(f"[network] ❌ Controller not reachable after {timeout}s")
    print(f"[network] Make sure Chromebook is connected to Moen AP (192.168.10.x)")
    return False

def provision(pin: str, config: dict):
    serial  = config.get("serial", "")
    ssid    = config["ssid"]
    wifi_pw = config["wifi_password"]
    name    = config.get("shower_name", "shower")
    pin     = pad_pin(pin)
    ahash   = auth_hash(pin, serial)

    print(f"\n[crypto] Auth-Hash: {ahash}")

    # Check controller is reachable first
    if not wait_for_controller(timeout=60):
        return

    # Get user token (cached or fresh)
    user_token = get_user_token(config)

    # Get session key from controller
    print(f"\n[controller] Getting session key...")
    session_key = None
    try:
        session = controller_get("/v1/prov/session", ahash)
        session_key = session.get("session_key")
        print(f"[controller] Session key: {session_key[:40] if session_key else 'None'}...")
    except Exception as e:
        print(f"[controller] Session failed: {e} — using plaintext fallback")

    # Build body
    ts = timestamp()
    if session_key:
        print("[crypto] RSA-encrypting fields with session key...")
        enc_token = rsa_encrypt_field(user_token, session_key)
        enc_ssid  = rsa_encrypt_field(ssid, session_key)
        enc_pw    = rsa_encrypt_field(wifi_pw, session_key)
        body = json.dumps({
            "user_token":  enc_token,
            "shower_name": name,
            "ssid":        enc_ssid,
            "password":    enc_pw,
            "api_server":  API_SERVER
        })
    else:
        print("[crypto] Plaintext fallback...")
        body = json.dumps({
            "user_token":  user_token,
            "shower_name": name,
            "ssid":        ssid,
            "password":    wifi_pw,
            "api_server":  API_SERVER
        })

    print(f"\n[tcp] Sending registration...")
    status = controller_post_tcp("/v2/prov/registration", ahash, ts, body)
    print(f"[tcp] HTTP {status}")

    if status == 200:
        if poll_status(ahash):
            print("\n✅ SUCCESS! Shower connected to WiFi!")
        else:
            print("\n❌ Registration sent but controller did not report connected")
    elif status == 404:
        print("\n❌ 404 — Provisioning window closed. Trigger Reset WiFi Credentials and re-run faster.")
    elif status == 400:
        print("\n❌ 400 — Bad request (wrong payload or invalid token)")
    elif status == 500:
        print("\n❌ 500 — Controller error")
    else:
        print(f"\n❌ Unexpected: {status}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="U by Moen Provisioner")
    parser.add_argument("--pin", required=True, help="PIN from controller (hold hot arrow 5s → red screen)")
    parser.add_argument("--refresh-token", action="store_true", help="Force refresh cloud token")
    args = parser.parse_args()

    config = load_config()
    if args.refresh_token:
        config.pop("user_token", None)

    provision(args.pin, config)
