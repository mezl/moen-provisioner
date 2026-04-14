# U by Moen WiFi Provisioner

A tool to provision (connect to WiFi) your **U by Moen Smart Shower** when the official app fails — which it does on most modern Android devices.

> ✅ Successfully tested on firmware **3.3.0**, serial **1234567**, Android 13/14

The official app was last updated in 2019. On Android 10+, provisioning silently breaks because the app needs two simultaneous network connections (local Moen AP + internet), and Android's `ConnectivityManager` drops connections with no internet. This tool runs on a computer terminal, which has no such restriction.

---

## Requirements

- A computer with WiFi — **Chromebook, Mac, Windows, or Linux**
- Python 3.8+
- Python package: `cryptography`
- Your Moen account credentials (email + password)
- Controller serial number (shown on controller Technical Information screen)

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
wget https://raw.githubusercontent.com/mezl/moen-provisioner/refs/heads/master/setup_moen.py
wget https://raw.githubusercontent.com/mezl/moen-provisioner/refs/heads/master/moen_provision.py
wget https://raw.githubusercontent.com/mezl/moen-provisioner/refs/heads/master/moen_control.py
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

Prompts for email, password, home WiFi SSID/password, shower name, and serial number. Saves everything to `moen_config.json` and fetches a cloud auth token. **Run once — you don't need to repeat this.**

### Step 4 — Get the provisioning PIN

1. Walk to your shower controller
2. **Hold the temperature UP (hot) arrow for 5 seconds**
3. Select **"Standard Set Up (Recommended)"** → press **Next**
4. Note the **PIN code** shown on screen (e.g. `AB12`)

> ⚠️ The PIN disappears after ~5 seconds but stays valid for 2 minutes.

### Step 5 — Connect to Moen AP and provision

1. Connect your computer to the **Moen WiFi AP** (`Moen_XXXXXX`)
   - Windows: click "Connect anyway" if warned about no internet
   - Mac: click "Join" despite the no-internet warning
2. Have the command ready — replace `AB12` with your PIN:

```bash
python3 moen_provision.py --pin AB12
```

3. Press Enter **immediately** — you have a 2-minute window

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

Only `user_token` and `serial` from `moen_config.json` are needed (both saved by `setup_moen.py`). Temperature units (°C or °F) are detected automatically from your cloud account settings.

### Commands

**Get current state:**
```bash
python3 moen_control.py status
```
Prints a JSON snapshot: current temperature, outlet states, running mode, etc. Temperatures are shown in your controller's configured unit.

**Turn on:**
```bash
# Celsius controller
python3 moen_control.py on --temp 38

# Fahrenheit controller
python3 moen_control.py on --temp 100
```
Omit `--temp` to use the default (38°C or 100°F, auto-detected).

**Turn off:**
```bash
python3 moen_control.py off
```

**Change temperature while running:**
```bash
python3 moen_control.py temp 40    # °C
python3 moen_control.py temp 104   # °F
```

**Run a saved preset:**
```bash
python3 moen_control.py preset 1
```
Presets 1–12 match the presets configured in the Moen app.

**Turn a specific outlet on or off:**
```bash
python3 moen_control.py outlet 1 on
python3 moen_control.py outlet 2 off
```
Controls individual water outlets (1–4 depending on your controller). Useful when the shower is already running and you want to redirect flow.

**Identify the controller (flash/beep):**
```bash
python3 moen_control.py identify
```
Sends the `identify` RPC to the controller — handy for confirming the cloud connection is live without changing shower state.

### Notes

- **Internet required.** Commands relay through Moen's Pusher WebSocket service. The controller must be online.
- **No extra packages.** `moen_control.py` uses Python stdlib only.
- **Token expiry.** If you get auth errors, re-run `python3 setup_moen.py` to refresh `user_token`.

---

## Resetting WiFi (changing networks)

1. On the controller: **hold the DOWN (cold) arrow** for 5 seconds → Technical Information → press the outlet button next to **"Reset WiFi Credentials"**
2. Update `moen_config.json` with the new SSID and password (or re-run `setup_moen.py`)
3. Follow Steps 4–5 above

---

## Troubleshooting

### `❌ 404 — Provisioning window closed`
The 2-minute window expired. Repeat Step 4 to get a new PIN and run faster.

### `❌ 400 — Bad request`
Your user token may have expired. Re-run `setup_moen.py` to get a fresh token.

### `[controller] Session failed: HTTP Error 404`
The controller AP timed out. Get a new PIN and try again immediately.

### `❌ connecting to network failed`
Wrong WiFi password or SSID. Check `moen_config.json` — SSID is case-sensitive.

### Chromebook Linux won't start (`Error starting crostini`)
Settings → Advanced → Developers → Linux development environment → **Remove Linux**, then re-enable it (~5 minutes).

### Windows: can't reach 192.168.10.1
Disable any VPN. Try: Settings → WiFi → Moen AP → "Set as metered connection" OFF, and disconnect ethernet temporarily.

### Token fetch fails with 406
Re-run `setup_moen.py`.

---

## Preset Sync Issues After Provisioning

If presets don't work after provisioning: in the Moen app → Settings → **Sign Out**, then sign back in. This re-syncs the app's local token cache with the newly provisioned controller.

---

## Files

| File | Description |
|------|-------------|
| `setup_moen.py` | One-time setup: saves credentials and fetches auth token |
| `moen_provision.py` | Provisioner — run with `--pin XXXX` |
| `moen_control.py` | Shower controller — on/off/temp/preset via Pusher cloud relay |
| `moen_config.json` | Auto-generated config (created by `setup_moen.py`, gitignored) |

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

> ⚠️ `moen_config.json` contains your credentials — never commit it to version control.

For protocol internals, API tables, and pre-Pusher legacy firmware details see [TECHNICAL.md](TECHNICAL.md).

---

## Contributing

If you test this on a different firmware version or controller model, please open an issue. Known working:
- Firmware 3.3.0 ✅

---

## Disclaimer

This tool was created for personal use and interoperability purposes. Not affiliated with or endorsed by Moen. Use at your own risk.

---

## Credits

Built with **Claude Sonnet 4.6** by Anthropic — protocol analysis, Python scripting, and documentation. Special thanks to the Home Assistant community for earlier API work.
