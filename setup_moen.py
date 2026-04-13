#!/usr/bin/env python3
"""
Run this once to save your Moen credentials and token.
Creates moen_config.json in the same directory.
"""
import json, urllib.request, urllib.parse, getpass, os

config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moen_config.json")

# Load existing config if present
config = {}
if os.path.exists(config_file):
    with open(config_file) as f:
        config = json.load(f)
    print(f"Loaded existing config from {config_file}")

# Prompt for any missing values
if not config.get("email"):
    config["email"] = input("Moen account email: ").strip()
if not config.get("moen_password"):
    config["moen_password"] = getpass.getpass("Moen account password: ")
if not config.get("ssid"):
    config["ssid"] = input("Home WiFi SSID: ").strip()
if not config.get("wifi_password"):
    config["wifi_password"] = getpass.getpass("Home WiFi password: ")
if not config.get("shower_name"):
    config["shower_name"] = input("Shower name [shower]: ").strip() or "shower"
if not config.get("serial"):
    config["serial"] = input("Controller serial (from Technical Info screen): ").strip()

# Fetch fresh token
print("\nFetching Moen cloud token...")
params = urllib.parse.urlencode({"email": config["email"], "password": config["moen_password"]})
req = urllib.request.Request(f"https://www.moen-iot.com/v2/authenticate?{params}")
req.add_header("Accept", "application/json")
try:
    r = urllib.request.urlopen(req, timeout=10)
    token = json.loads(r.read())["token"]
    config["user_token"] = token
    print(f"Got token: {token[:20]}...")
except Exception as e:
    print(f"Token fetch failed: {e}")
    print("You can still run moen_provision.py with --pin and it will retry login")

# Save config
with open(config_file, "w") as f:
    json.dump(config, f, indent=2)
print(f"\nSaved config to {config_file}")
print(f"\nNow run:")
print(f"  python3 moen_provision.py --pin YOURPIN")
