# U by Moen WiFi Provisioner

A tool built with **Claude Sonnet 4.6** (Anthropic) to provision (connect to WiFi) your **U by Moen Smart Shower** when the official app fails — which it does, on most modern Android devices.

> ✅ Successfully tested on firmware **3.3.0**, serial **1234567**, Android 13/14

---

## Why the Official App Fails

The U by Moen Android app was **last updated in 2019** and is effectively abandoned. On Android 10+, provisioning silently breaks due to a fundamental networking conflict:

During setup, the app needs **two simultaneous network connections**:
- **Local WiFi** — talking to the shower controller at `192.168.10.1` (Moen AP)
- **Internet** — authenticating with Moen's cloud at `moen-iot.com`

Modern Android's `ConnectivityManager` automatically drops connections with no internet. The app uses a raw TCP socket to fight this, but Android 10+ changed the network binding APIs in ways that break this approach. The app never got updated to handle it.

**Result:** The app spinner hangs forever, or times out, no matter what you try.

### What This Tool Does Differently

This tool runs on your **computer terminal** (Linux/Mac/Windows), which doesn't have Android's network manager fighting it. It:

1. Fetches your Moen cloud auth token once and caches it
2. Connects directly to the controller via raw TCP socket
3. Sends the exact provisioning payload the app would send
4. Polls until the controller reports connected

---

## Requirements

- A computer with WiFi that can connect to the Moen AP — **Chromebook, Mac, Windows, or Linux**
- Python 3.8+
- Python package: `cryptography`
- A U by Moen Smart Shower controller (tested on firmware 3.3.0)
- Your Moen account credentials (email + password)

---

## Quick Start

### Step 1 — Install dependencies

**Chromebook / Debian / Ubuntu:**
```bash
sudo apt update && sudo apt install -y python3-pip python3-cryptography
```

**Mac:**
```bash
pip3 install cryptography
```

**Windows:**
```cmd
pip install cryptography
```

### Step 2 — Download the scripts

```bash
wegt https://raw.githubusercontent.com/mezl/moen-provisioner/refs/heads/master/setup_moen.py
wget https://raw.githubusercontent.com/mezl/moen-provisioner/refs/heads/master/moen_provision.py
```

Or clone the repo:

```bash
git clone https://github.com/mezl/moen-provisioner.git
cd moen-provisioner
```

### Step 3 — Save your credentials (run once, on home WiFi)

```bash
python3 setup_moen.py
```

This will prompt for:
- Moen account email
- Moen account password
- Home WiFi SSID (the network to connect the shower to)
- Home WiFi password
- Shower name (e.g. "Master Shower")
- Controller serial number (shown on controller Technical Information screen)

Your credentials are saved to `moen_config.json` and your cloud auth token is fetched and cached. **You only need to do this once.**

### Step 4 — Get the provisioning PIN

1. Walk to your shower controller
2. **Hold the temperature UP (hot) arrow button for 5 seconds**
3. Screen shows setup method selection → select **"Standard Set Up (Recommended)"** → press **Next**
4. The controller screen briefly displays a **PIN code** — note it down (e.g. `AB12`)

> ⚠️ The PIN disappears after ~5 seconds but remains valid for 2 minutes.

### Step 5 — Connect to Moen AP and provision

1. Connect your computer to the **Moen WiFi AP** (named `Moen_XXXXXX`)
   - **Windows users:** if Windows warns "No internet", click "Connect anyway"
   - **Mac users:** click "Join" even if it shows no internet warning
2. Have the command ready to paste — just replace `AB12` with your actual PIN:

```bash
python3 moen_provision.py --pin AB12
```

3. Press Enter **immediately** after connecting — you have a 2-minute window

### Expected output

```
[crypto] Auth-Hash: 4921c0be...
[cloud] Using cached token: XXXXXXXX...
[controller] Getting session key...
[controller] Session key: LS0tLS1CRUdJ...
[crypto] RSA-encrypting fields with session key...
[tcp] Connecting to 192.168.10.1:80...
[tcp] Response: HTTP/1.1 200 OK
[status] Polling provisioning status...
[status] 1/60: {'status': 'connecting', 'progress': 'connecting to network'}
[status] 4/60: {'status': 'connected'}

✅ SUCCESS! Shower connected to WiFi!
```

---

## Controlling the Shower

Once provisioned, use `moen_control.py` to control the shower from any machine with internet access. It communicates via Moen's Pusher cloud relay — **no local network access to the controller is required**.

Only `user_token` and `serial` from `moen_config.json` are needed (both saved by `setup_moen.py`).

### Commands

**Get current state:**
```bash
python3 moen_control.py status
```
Prints a JSON snapshot of the shower: current temperature, outlet states, running mode, etc.

**Turn on at a target temperature:**
```bash
python3 moen_control.py on --temp 105
```
`--temp` is in **Fahrenheit** (default: 38, which is really only useful if your controller is configured in Celsius — most US controllers use °F). Substitute your desired temperature.

**Turn off:**
```bash
python3 moen_control.py off
```

**Change temperature while running:**
```bash
python3 moen_control.py temp 100
```

**Run a saved preset:**
```bash
python3 moen_control.py preset 1
```
Presets 1–12 correspond to the presets configured in the Moen app.

### Notes

- **Internet required.** Commands are relayed through Moen's Pusher WebSocket service (`ws-*.pusher.com`). The controller must be online (home WiFi connected, Moen cloud reachable).
- **No extra packages needed.** `moen_control.py` uses only Python stdlib (`ssl`, `socket`, `struct`).
- **Token expiry.** If you get auth errors, re-run `python3 setup_moen.py` to refresh your `user_token`.

---

## Resetting WiFi (changing networks)

If you need to connect the shower to a different WiFi network:

1. On the controller: **hold the DOWN (cold) arrow** for 5 seconds → Technical Information screen → press the outlet button next to **"Reset WiFi Credentials"**
2. Update `moen_config.json` with your new SSID and password (or re-run `setup_moen.py`)
3. Follow Steps 4–5 above

---

## Troubleshooting

### `❌ 404 — Provisioning window closed`
The 2-minute window expired. Repeat Step 4 to get a new PIN and run faster.

### `❌ 400 — Bad request`
Your user token may have expired. Re-run `setup_moen.py --refresh` to get a fresh token.

### `[controller] Session failed: HTTP Error 404`
The controller AP timed out before the session request. Get a new PIN and try again immediately.

### `❌ connecting to network failed`
Wrong WiFi password or SSID. Check `moen_config.json` and make sure the SSID exactly matches (case-sensitive) and the password is correct.

### Chromebook Linux won't start (`Error starting crostini`)
Go to Settings → Advanced → Developers → Linux development environment → **Remove Linux**, then re-enable it. Takes ~5 minutes.

### Windows: can't reach 192.168.10.1
Windows sometimes routes traffic over the wrong adapter. Try disabling any VPN, and run this in cmd to confirm the route:
```cmd
ping 192.168.10.1
```
If it fails, go to Network Settings → WiFi → Moen AP → "Set as metered connection" OFF, and disconnect any ethernet cable temporarily.

### Token fetch fails with 406
Run `setup_moen.py` again — it handles the correct headers.

---

## Preset Sync Issues After Provisioning

If presets still don't work after provisioning:

1. In the Moen app → Settings → **Sign Out**
2. Sign back in with your email and password

This re-saves your auth token into the app's local storage. The provisioning process registers the controller with the cloud but the app's local token cache sometimes gets out of sync.

---

## Technical Details

### How Provisioning Works

> **Legal note:** This protocol was documented by analyzing network traffic generated by the official U by Moen app communicating with hardware the author owns. This is standard interoperability research permitted under the DMCA § 1201(f) exemption and consistent with the Computer Fraud and Abuse Act, as it involves only hardware and accounts owned by the researcher. No Moen servers were accessed beyond normal authenticated API calls any legitimate user would make.

The provisioning flow has two phases: **cloud authentication** and **local controller setup**.

#### Phase 1 — Cloud Authentication

Before touching the controller, the app authenticates with Moen's cloud to obtain a user token:

```
GET https://www.moen-iot.com/v2/authenticate?email=...&password=...
Accept: application/json

Response: {"token": "XXXXXXXXXXXXXXXX"}
```

This token identifies your Moen account and is passed to the controller during provisioning so it knows which cloud account to register with. The token is long-lived and can be cached — you only need to fetch it once unless you change your password.

All subsequent cloud API calls include this token as a `User-Token` header, which the app's HTTP interceptor adds automatically.

#### Phase 2 — Local Controller Setup (on Moen AP)

Once connected to the Moen AP (`192.168.10.x`), the provisioning flow has 4 steps:

**Step 1 — Get session key**
```
GET http://192.168.10.1/v1/prov/session
Auth-Hash: sha256(paddedPIN + ":" + serial + ":" + paddedPIN)

Response: {"session_key": "<base64-encoded RSA public key>"}
```

The `Auth-Hash` proves physical presence — only someone standing at the shower can read the PIN displayed on the controller screen. This is the security handshake that prevents remote attackers from hijacking provisioning.

**Computing the Auth-Hash — verify each step in your terminal:**

Say your PIN is `AB12` and your serial number is `7654321`.

**Step 1 — Pad the PIN to 16 characters** (left-aligned, zeros on the right):
```bash
printf "%-16s" "AB12" | tr ' ' '0'
# AB12000000000000
```

**Step 2 — Build the input string** (`paddedPIN:serial:paddedPIN`):
```bash
echo -n "AB12000000000000:7654321:AB12000000000000"
# AB12000000000000:7654321:AB12000000000000
```

**Step 3 — SHA-256 hash it:**
```bash
echo -n "AB12000000000000:7654321:AB12000000000000" | sha256sum
# 092758d920ac236c47c4c6e77f282ca1f61142cb574d40ca316e0fadc55aa50c
```

**All in one command** (substitute your own PIN and serial):
```bash
PIN="AB12"
SERIAL="7654321"
PADDED=$(printf "%-16s" "$PIN" | tr ' ' '0')
echo -n "${PADDED}:${SERIAL}:${PADDED}" | sha256sum
# 092758d920ac236c47c4c6e77f282ca1f61142cb574d40ca316e0fadc55aa50c
```

**Step 2 — Encrypt credentials**

The session key is an RSA-2048 public key. The app encrypts three fields before sending:
- `user_token` — your Moen account token
- `ssid` — your home WiFi network name
- `password` — your home WiFi password

Each field is encrypted as: `RSA_PKCS1v15_encrypt(field + ":" + SHA512(field))`

This ensures WiFi credentials are never sent in plaintext over the local network, even though it's a temporary AP with no external access.

**Encrypting a field — verify each step in your terminal:**

Say your WiFi SSID is `MyHomeWiFi`.

**Step 1 — SHA-512 hash the plaintext field:**
```bash
echo -n "MyHomeWiFi" | sha512sum
# 425f24ec45d58299a3029428cd51e9970836eba167e5edd2134e7e81dffc1e4c99b6f7f7549c342ecf5e4430fd39b7db9c73c66fa7fd052a885b7cf9652bffef  -
```

**Step 2 — Build the payload** by appending the hash to the plaintext with `:`:
```bash
FIELD="MyHomeWiFi"
SHA512=$(echo -n "$FIELD" | sha512sum | awk '{print $1}')
echo "${FIELD}:${SHA512}"
# MyHomeWiFi:425f24ec45d58299a3029428cd51e9970836eba167e5edd2134e7e81dffc1e4c...
```

**Step 3 — Measure the payload length** (useful for debugging RSA size limits):
```bash
FIELD="MyHomeWiFi"
SHA512=$(echo -n "$FIELD" | sha512sum | awk '{print $1}')
PAYLOAD="${FIELD}:${SHA512}"
echo "Payload length: ${#PAYLOAD} bytes"
# Payload length: 139 bytes
```

> RSA-2048 with PKCS#1 v1.5 padding can encrypt up to 245 bytes. The payload is always
> `len(field) + 1 + 128` bytes (128 hex chars from SHA-512), so fields up to 116 chars fit.
> WiFi passwords are max 63 chars — well within limit.

**Step 4 — RSA encrypt and Base64 encode:**

First, fetch the session key from the controller (must be connected to Moen AP):
```bash
# Auth-Hash here uses the example values from Step 1:
# PIN=AB12 → padded to AB12000000000000, serial=7654321
# sha256("AB12000000000000:7654321:AB12000000000000") = 092758d9...
# Replace with your own computed hash from Step 1 above

curl -s http://192.168.10.1/v1/prov/session   -H "Auth-Hash: 092758d920ac236c47c4c6e77f282ca1f61142cb574d40ca316e0fadc55aa50c"
```

The response looks like this:
```json
{
  "session_key": "<base64-encoded string returned live from your controller>"
}
```

> 📝 The `session_key` value is Moen's RSA-2048 public key, returned fresh by your controller
> during provisioning. It is not reproduced here out of respect for Moen's intellectual property.
> You will see the real value when you run the `curl` command above against your own controller.

The `session_key` value is **base64 of a PEM public key**. Decode it to see the actual key:
```bash
# Replace with the actual value returned by your controller
SESSION_KEY="<value from curl response>"
echo "$SESSION_KEY" | base64 -d
# Output will be a standard PEM block:
# -----BEGIN PUBLIC KEY-----
# MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAh8Jicf0vRIdAdrcKCDmu
# bKt0aMNNukkl UdZEHpacSnozs Iu+/3EKdXLAHdW7GOCX5A2AsIfSbj6BE3OVy8f1
# Zwd0h6hcV2l1n14/gqd3AyZeHdQpnY8PrCK8ZK/lQWLbpSIrBsKJION3HUpcT3pn
# HER5UhvxIkuScqpPTdDr2unqrIkbRVih0HV+X6IHjNeQ5oYW0HSzJeLk3/JQ2ssF
# zFovODfUGfC2ph5c2Wx2rgAQNoxbXQx1iEbcBWPXyFd3eW1+sFkcOS8lsVPo0slD
# pvXCeS5QbwGz/TYy8V2lJIjna4k+GheX5CDW5L+IPD0HxKSp8r/jFn4Nwri6WyRx
# 5QIDAQAB
# -----END PUBLIC KEY-----


```

Save it as a PEM file for use with openssl:
```bash
echo "$SESSION_KEY" | base64 -d > session_key_pem.txt
cat session_key_pem.txt  # verify it shows -----BEGIN PUBLIC KEY-----
```

Then encrypt your field:
```bash
FIELD="MyHomeWiFi"
SHA512=$(echo -n "$FIELD" | sha512sum | awk '{print $1}')
PAYLOAD="${FIELD}:${SHA512}"

echo -n "$PAYLOAD" | openssl rsautl -encrypt -pkcs -pubin -inkey session_key_pem.txt | base64
# Output: base64-encoded ciphertext — this is what goes in the JSON body
# e.g. "a3Fk9mN2pQ...Xz8="
```

All three fields (`user_token`, `ssid`, `password`) go through the exact same four steps independently.

**Why this design?**

The SHA-512 hash appended to the payload acts as an integrity check — the controller decrypts the payload and verifies the hash matches the plaintext, confirming nothing was corrupted or tampered with in transit. The RSA layer ensures only the controller (holding the private key) can read the credentials.

**Step 3 — Send registration (raw TCP)**

This is the most unusual part. Instead of a normal HTTP POST, the app opens a **raw TCP socket** and manually writes the HTTP request.

The `api_server` field tells the controller which cloud endpoint to connect to after provisioning — this is how the controller knows where to phone home.

The reason for raw TCP (rather than a standard HTTP library) is that the controller doesn't respond to HTTP OPTIONS preflight requests, which modern HTTP clients send automatically for requests with custom headers. Raw TCP bypasses this entirely.

**Build and send via bash using `nc` (netcat):**

```bash
# Set variables (use your real values)
AUTH_HASH="092758d920ac236c47c4c6e77f282ca1f61142cb574d40ca316e0fadc55aa50c"
TIMESTAMP=$(date +%s%3N | head -c -3)   # unix ms / 100
ENC_TOKEN="<base64 from Step 2>"
ENC_SSID="<base64 from Step 2>"
ENC_PASS="<base64 from Step 2>"

# Build JSON body
BODY=$(printf '{"user_token":"%s","shower_name":"Master Shower","ssid":"%s","password":"%s","api_server":"https://www.moen-iot.com"}'   "$ENC_TOKEN" "$ENC_SSID" "$ENC_PASS")

CONTENT_LENGTH=${#BODY}

# Build full raw HTTP request
REQUEST=$(printf "POST /v2/prov/registration HTTP/1.1
Auth-Hash: %s
Timestamp: %s
Content-Type: application/json
Accept: */*
Content-Length: %d
Host: 192.168.10.1
Connection: Keep-Alive

%s"   "$AUTH_HASH" "$TIMESTAMP" "$CONTENT_LENGTH" "$BODY")

# Send via netcat and print response
echo -e "$REQUEST" | nc -q 3 192.168.10.1 80
```

Expected response on success:
```
HTTP/1.1 200 OK
Server: Marvell-WM
Connection: close
Content-Length: 0
```

> ⚠️ `nc` flag `-q 3` (wait 3s before closing) is required on Linux. On Mac use `-G 3` instead:
> ```bash
> echo -e "$REQUEST" | nc -G 3 192.168.10.1 80
> ```

> ⚠️ Content-Length must be **exact**. If it's wrong the controller returns 400.
> Always recompute it after changing the body: `CONTENT_LENGTH=${#BODY}`

**Step 4 — Poll for completion**

After sending registration, the controller attempts to connect to the specified WiFi network. The app polls every 2 seconds:

```
GET http://192.168.10.1/v1/prov/status
Auth-Hash: <same as above>

Response (connecting): {"status": "connecting", "progress": "connecting to network"}
Response (success):    {"status": "connected"}
Response (failure):    {"status": "error", "progress": "connecting to network failed"}
```

Once `connected` is returned, the controller has joined your home WiFi and registered with Moen's cloud. The Moen AP shuts down and the controller is reachable via your home network going forward.

#### PIN Details

The PIN displayed on the controller screen is not stored anywhere — it is generated fresh each provisioning session. It serves as proof of physical presence (you must be standing at the shower to read it).

The app internally pads the PIN to 16 characters before use:
```
padded_pin = pin.upper().ljust(16, '0')
# e.g. "AB12" → "AB120000000000000"
```

This padded value is what gets used in the Auth-Hash and as the encryption key in the fallback (non-session-key) path.

#### Fallback Path (older firmware)

If the controller doesn't support the session key endpoint (older firmware), the app falls back to encrypting fields with AES-CTR using the PIN itself as the key, and sends the registration to the older `/v1/prov/registration` endpoint. Our script handles both paths automatically.

### Cloud API

Base URL: `https://www.moen-iot.com`

| Endpoint | Description |
|----------|-------------|
| `GET /v2/authenticate?email=&password=` | Login, returns `{"token":"..."}` |
| `GET v5/showers/{serial}` | Get shower details |
| `PATCH v4/showers/{serial}` | Update shower/presets |

### Controller Local API

The controller exposes two groups of endpoints depending on its connection state.

---

#### 📡 Before Provisioning — Moen AP mode (`http://192.168.10.1`)

These are only available while the controller is broadcasting its own AP (`Moen_XXXXXX`).
Your computer must be connected to the Moen AP to reach them.

| Endpoint | Method | Auth-Hash key | Description |
|----------|--------|---------------|-------------|
| `/v1/prov/session` | GET | `sha256(paddedPIN:serial:paddedPIN)` | Get RSA session key for encrypting credentials |
| `/v2/prov/registration` | POST (raw TCP) | `sha256(paddedPIN:serial:paddedPIN)` | Send encrypted WiFi credentials to controller |
| `/v1/prov/status` | GET | `sha256(paddedPIN:serial:paddedPIN)` | Poll provisioning status (`connecting` → `connected`) |
| `/v1/prov/networks` | GET | none | List nearby WiFi networks the controller can see |
| `/v1/prov/acknowledge` | POST | `sha256(paddedPIN:serial:paddedPIN)` | Acknowledge provisioning (used in older firmware path) |
| `/v2/ping` | GET | none | Check controller is reachable |

Example — check controller is reachable before provisioning:
```bash
curl http://192.168.10.1/v2/ping
```

Example — list WiFi networks visible to the controller:
```bash
curl http://192.168.10.1/v1/prov/networks
```

---

#### 🏠 After Provisioning — Home WiFi mode (`http://<controller-local-ip>`) *(legacy / pre-Pusher firmware only)*

> ⚠️ **Firmware 3.x controllers with `hmi_supports_pusher` capability do NOT register these local HTTP routes.** All `/v1/shower` requests will return `File /path not_found`. Use `moen_control.py` (Pusher cloud relay) instead — see [Controlling the Shower](#controlling-the-shower) above.

For **older firmware** (pre-Pusher), once provisioned the controller joins your home WiFi and is reachable at its local IP (e.g. `192.168.1.x`), discovered via mDNS as `moen-dolphin._http._tcp.`.

> ⚠️ Auth-Hash for post-provisioning endpoints uses the **shower's own token** (from the cloud),
> not the PIN. Formula: `sha256(showerToken:serial:showerToken)`

| Endpoint | Method | Auth-Hash key | Description |
|----------|--------|---------------|-------------|
| `/v1/shower` | GET | `sha256(showerToken:serial:showerToken)` | Get current shower state (temp, outlets, mode) |
| `/v1/shower` | POST | `sha256(showerToken:serial:showerToken)` | Set shower state (turn on/off, set temp, run preset) |
| `/v1/refresh` | POST | `sha256(showerToken:serial:showerToken)` | Push updated settings to controller display |
| `config` | POST | `sha256(showerToken:serial:showerToken)` | Update controller config |

Example — get current shower state (replace IP and hash with your values):
```bash
curl http://192.168.1.50/v1/shower   -H "Auth-Hash: <sha256(showerToken:serial:showerToken)>"
```

> 💡 The `showerToken` is different from your Moen account token. It is assigned by the cloud
> during provisioning and returned in `GET v5/showers/{serial}` as the `token` field.

---

## Files

| File | Description |
|------|-------------|
| `setup_moen.py` | One-time setup: saves credentials and fetches auth token |
| `moen_provision.py` | Main provisioner script — run with `--pin XXXX` |
| `moen_control.py` | Shower controller — on/off/temp/preset via Pusher cloud relay |
| `moen_config.json` | Auto-generated config file (created by setup_moen.py) |

**Example `moen_config.json`:**
```json
{
  "email": "you@example.com",
  "moen_password": "yourMoenPassword",
  "ssid": "YourHomeWiFi",
  "wifi_password": "yourWiFiPassword",
  "shower_name": "Master Shower",
  "serial": "1234567",
  "user_token": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
}
```

> ⚠️ `moen_config.json` contains your credentials — it is listed in `.gitignore` and should never be committed to version control or shared.

---

## Contributing

If you test this on a different firmware version or controller model, please open an issue with your results. Known working:
- Firmware 3.3.0 ✅

---

## Disclaimer

This tool was created by Anthropic for personal use and interoperability purposes. It is not affiliated with or endorsed by Moen. Use at your own risk.

---

## Credits

Built entirely with **Claude Sonnet 4.6** by Anthropic — including protocol analysis, Python scripting, and documentation. Special thanks to the Home Assistant community for documenting earlier API work.
