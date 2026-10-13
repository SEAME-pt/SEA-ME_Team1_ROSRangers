#!/usr/bin/env python3
"""PRODUCTION scenario: the ESP creates its own WPA2 network; the ground PC joins it.

  ground PC (192.168.4.2) ──UDP 5760── ESP SoftAP (192.168.4.1) ──UART 115200── FC UART4 (MSP)

`setup` configures everything automatically (ESP on its own USB, FC wires OFF):
  1. leaves transparent mode if needed
  2. SoftAP with WPA2 (password asked once, stored in ~/.config/seame-drone/), fixed channel,
     1 client by default, AP address 192.168.4.1, DHCP on
  3. transparent UDP bridge at boot towards 192.168.4.2:5760, then reboots the ESP
  4. creates the NetworkManager connection 'seame-drone' on this PC (static 192.168.4.2/24,
     no default route, no autoconnect)
  5. saves the active scenario so msp_check.py finds the ESP by itself

While this PC is on the drone network it has no internet (unless it also has a cable).

Examples:
  python3 scripts/setup_production.py                  # = setup
  python3 scripts/setup_production.py connect          # join the drone network
  python3 scripts/setup_production.py disconnect       # back to the usual Wi-Fi (SEAME)
  python3 scripts/setup_production.py status
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile
import time

import bridge_config as cfg
import esp_at

AP_IP = "192.168.4.1"
PC_IP = "192.168.4.2"
NM_NAME = "seame-drone"


def nmcli(*args, check=True) -> str:
    r = subprocess.run(["nmcli", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise esp_at.EspError(f"nmcli {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def wifi_device() -> str:
    for line in nmcli("-t", "-f", "DEVICE,TYPE", "device").splitlines():
        dev, typ = line.split(":", 1)
        if typ == "wifi":
            return dev
    raise esp_at.EspError("No Wi-Fi interface found on this PC.")


def setup_pc_connection(ssid: str):
    """Create or update the NetworkManager profile. The password is not stored in it:
    it is handed over with a temporary passwd-file at each `connect`."""
    settings = ["802-11-wireless.ssid", ssid,
                "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk-flags", "2",
                "ipv4.method", "manual", "ipv4.addresses", f"{PC_IP}/24",
                "ipv4.never-default", "yes", "ipv6.method", "disabled",
                "connection.autoconnect", "no"]
    names = nmcli("-t", "-f", "NAME", "connection", "show").splitlines()
    if NM_NAME in names:
        nmcli("connection", "modify", NM_NAME, *settings)
    else:
        nmcli("connection", "add", "type", "wifi", "con-name", NM_NAME, "ifname", wifi_device(),
              "ssid", ssid, *settings[2:])
    print(f"  NetworkManager connection '{NM_NAME}': static {PC_IP}/24, no default route, no autoconnect")


def cmd_setup(args):
    ser = esp_at.open_port(args.port or esp_at.find_port())
    try:
        print("1. ESP in AT mode")
        esp_at.ensure_at_mode(ser)
        mac = re.search(r'APMAC,"([0-9a-f:]+)"', esp_at.at(ser, "AT+CIFSR", 2))
        ssid = args.ssid or f"seame-drone-{mac.group(1).replace(':', '')[-4:] if mac else 'esp'}"
        password = cfg.get_secret(f"wifi:{ssid}", f"New WPA2 password for the drone network '{ssid}': ",
                                  ask_again=args.ask_password)
        print(f"2. SoftAP '{ssid}', WPA2, channel {args.channel}, max {args.max_clients} client(s)")
        esp_at.at_ok(ser, "AT+CWMODE_DEF=2")
        esp_at.at_ok(ser, f'AT+CWSAP_DEF="{ssid}","{password}",{args.channel},3,{args.max_clients}',
                     secret=password)
        esp_at.at_ok(ser, f'AT+CIPAP_DEF="{AP_IP}","{AP_IP}","255.255.255.0"')
        esp_at.at_ok(ser, "AT+CWDHCP_DEF=0,1")
        print("3. Transparent UDP bridge at boot")
        esp_at.at_ok(ser, f'AT+SAVETRANSLINK=1,"{PC_IP}",{cfg.BRIDGE_PORT},"UDP",{cfg.BRIDGE_PORT}')
        esp_at.reboot(ser)
    finally:
        ser.close()
    print("4. This PC")
    setup_pc_connection(ssid)
    cfg.save_profile({
        "scenario": "production", "ssid": ssid, "esp_ip": AP_IP, "pc_ip": PC_IP,
        "esp_port": cfg.BRIDGE_PORT, "pc_port": cfg.BRIDGE_PORT, "channel": args.channel,
        "configured": datetime.datetime.now().isoformat(timespec="seconds"),
    })
    print(f"\nReady. Scenario PRODUCTION saved in {cfg.PROFILE}.")
    print("Next:  python3 scripts/setup_production.py connect   (this PC loses internet until disconnect)")


def cmd_connect(args):
    p = cfg.load_profile()
    if p.get("scenario") != "production":
        sys.exit("Run setup_production.py setup first.")
    password = cfg.get_secret(f"wifi:{p['ssid']}", f"Password for '{p['ssid']}': ")
    nmcli("device", "wifi", "rescan", check=False)
    time.sleep(3)
    fd, path = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(f"802-11-wireless-security.psk:{password}\n")
        nmcli("connection", "up", NM_NAME, "passwd-file", path)
    finally:
        os.unlink(path)
    print(f"Connected to '{p['ssid']}' as {PC_IP}. Test with: python3 scripts/msp_check.py info")


def cmd_disconnect(args):
    nmcli("connection", "down", NM_NAME, check=False)
    print("Left the drone network. NetworkManager reconnects to the usual Wi-Fi (e.g. SEAME) by itself.")


def cmd_status(args):
    p = cfg.load_profile()
    if p.get("scenario") != "production":
        sys.exit(f"Active scenario: {p.get('scenario', 'none')}.")
    active = NM_NAME in nmcli("-t", "-f", "NAME", "connection", "show", "--active").splitlines()
    print(f"Scenario PRODUCTION, network '{p['ssid']}', channel {p.get('channel')} (configured {p['configured']})")
    print(f"  ESP {p['esp_ip']}:{p['esp_port']}  ↔  PC {p['pc_ip']}:{p['pc_port']}")
    print(f"  this PC on the drone network: {'yes' if active else 'no'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="ESP serial port (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("setup", help="configure the ESP and this PC for the PRODUCTION scenario")
    p.add_argument("--ssid", help="network name (default: seame-drone-<last 4 MAC digits>)")
    p.add_argument("--channel", type=int, default=6, choices=range(1, 14), metavar="1-13")
    p.add_argument("--max-clients", type=int, default=1, choices=range(1, 5), metavar="1-4")
    p.add_argument("--ask-password", action="store_true", help="choose a new network password")
    sub.add_parser("connect", help="join the drone network (no internet meanwhile)")
    sub.add_parser("disconnect", help="leave the drone network")
    sub.add_parser("status", help="show the saved PRODUCTION scenario")
    ap.set_defaults(ssid=None, channel=6, max_clients=1, ask_password=False)
    args = ap.parse_args()
    args.cmd = args.cmd or "setup"
    try:
        {"setup": cmd_setup, "connect": cmd_connect, "disconnect": cmd_disconnect,
         "status": cmd_status}[args.cmd](args)
    except esp_at.EspError as e:
        sys.exit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
