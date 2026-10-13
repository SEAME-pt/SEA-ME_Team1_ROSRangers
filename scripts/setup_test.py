#!/usr/bin/env python3
"""TEST scenario: the ESP joins the SEAME Wi-Fi as a station, so laptops keep internet.

  ground PC (SEAME, DHCP) ──UDP 5760── ESP (SEAME, DHCP) ──UART 115200── FC UART4 (MSP)

`setup` configures everything automatically (ESP on its own USB, FC wires OFF):
  1. leaves transparent mode if needed
  2. station mode + DHCP, joins SEAME (password asked once, stored in ~/.config/seame-drone/)
  3. reads the ESP IP and this PC's IP, checks the PC can reach the ESP
  4. sets the transparent UDP bridge at boot towards this PC (port 5760) and reboots the ESP
  5. saves the active scenario so msp_check.py finds the ESP by itself

Only for bench work, props off: anybody on the SEAME network can send packets to the bridge.
If this PC gets a new IP (another day, another laptop), run `setup` again.

Examples:
  python3 scripts/setup_test.py                # = setup
  python3 scripts/setup_test.py status
  python3 scripts/setup_test.py setup --ask-password
"""

import argparse
import datetime
import re
import subprocess
import sys
import time

import bridge_config as cfg
import esp_at


def pc_ip_towards(ip: str) -> str:
    out = subprocess.run(
        ["ip", "-4", "route", "get", ip], capture_output=True, text=True
    ).stdout
    m = re.search(r"\bsrc ([\d.]+)", out)
    return m.group(1) if m else ""


def pc_wifi_ssid() -> str:
    out = subprocess.run(
        ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"],
        capture_output=True,
        text=True,
    ).stdout
    for line in out.splitlines():
        if line.startswith("yes:"):
            return line[4:]
    return ""


def reachable(ip: str) -> bool:
    return (
        subprocess.run(
            ["ping", "-c", "3", "-W", "1", ip], capture_output=True
        ).returncode
        == 0
    )


def cmd_setup(args):
    ssid_now = pc_wifi_ssid()
    if ssid_now != args.ssid:
        print(
            f"WARNING: this PC is on Wi-Fi '{ssid_now or 'none'}', not '{args.ssid}'."
        )
    password = cfg.get_secret(
        f"wifi:{args.ssid}",
        f"Password for Wi-Fi '{args.ssid}': ",
        ask_again=args.ask_password,
    )
    ser = esp_at.open_port(args.port or esp_at.find_port())
    try:
        print("1. ESP in AT mode")
        esp_at.ensure_at_mode(ser)
        print("2. Station mode, DHCP, join the network")
        esp_at.at_ok(ser, "AT+CWMODE_DEF=1")
        esp_at.at_ok(ser, "AT+CWDHCP_DEF=1,1")
        esp_at.join_wifi(ser, args.ssid, password)
        esp_at.at_ok(ser, "AT+CWAUTOCONN=1")
        esp_ip = ""
        for _ in range(10):
            esp_ip = esp_at.station_ip(ser)
            if esp_ip:
                break
            time.sleep(1)
        if not esp_ip:
            raise esp_at.EspError("The ESP joined but got no IP address.")
        pc_ip = pc_ip_towards(esp_ip)
        print(f"3. ESP {esp_ip}, this PC {pc_ip}")
        if not reachable(esp_ip):
            raise esp_at.EspError(
                f"This PC cannot ping the ESP at {esp_ip}. The network may isolate "
                "clients from each other: use setup_production.py instead."
            )
        print("   ping from this PC to the ESP: OK")
        print("4. Transparent UDP bridge at boot")
        esp_at.at_ok(
            ser,
            f'AT+SAVETRANSLINK=1,"{pc_ip}",{cfg.BRIDGE_PORT},"UDP",{cfg.BRIDGE_PORT}',
        )
        cfg.save_profile(
            {
                "scenario": "test",
                "ssid": args.ssid,
                "esp_ip": esp_ip,
                "pc_ip": pc_ip,
                "esp_port": cfg.BRIDGE_PORT,
                "pc_port": cfg.BRIDGE_PORT,
                "configured": datetime.datetime.now().isoformat(timespec="seconds"),
            }
        )
        print("5. Rebooting the ESP into the bridge")
        esp_at.reboot(ser)
    finally:
        ser.close()
    time.sleep(4)
    print(f"\nReady. Scenario TEST saved in {cfg.PROFILE}.")
    print("Next (cycle 1a, ESP still on USB):  python3 scripts/msp_check.py cycle1a")


def cmd_status(args):
    p = cfg.load_profile()
    if p.get("scenario") != "test":
        sys.exit(
            f"Active scenario: {p.get('scenario', 'none')}. Run setup_test.py setup."
        )
    print(f"Scenario TEST on '{p['ssid']}' (configured {p['configured']})")
    print(f"  ESP {p['esp_ip']}:{p['esp_port']}  →  PC {p['pc_ip']}:{p['pc_port']}")
    now = pc_ip_towards(p["esp_ip"])
    if now != p["pc_ip"]:
        print(
            f"  WARNING: this PC is now {now or 'offline'}; the ESP still sends to {p['pc_ip']}. Run setup again."
        )
    print(f"  ping ESP: {'OK' if reachable(p['esp_ip']) else 'no answer'}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", help="ESP serial port (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser(
        "setup", help="configure the ESP and this PC for the TEST scenario"
    )
    p.add_argument("--ssid", default="SEAME")
    p.add_argument(
        "--ask-password", action="store_true", help="ask for the Wi-Fi password again"
    )
    sub.add_parser("status", help="show the saved TEST scenario and check the ESP")
    ap.set_defaults(ssid="SEAME", ask_password=False)
    args = ap.parse_args()
    args.cmd = args.cmd or "setup"
    try:
        {"setup": cmd_setup, "status": cmd_status}[args.cmd](args)
    except esp_at.EspError as e:
        sys.exit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
