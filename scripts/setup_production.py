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
  python3 scripts/setup_production.py setup            # ESP on its own USB, not wired to the FC
  python3 scripts/setup_production.py connect         # join the drone network
  python3 scripts/setup_production.py disconnect       # back to the usual Wi-Fi (SEAME)
  python3 scripts/setup_production.py status
  python3 scripts/setup_production.py reboot-test       # S1-04: 20 resets, time to reconnect
"""

import argparse
import csv
import datetime
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time

import bridge_config as cfg
import esp_at

RESULTS_DIR = esp_at.RESULTS_DIR
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
    settings = [
        "802-11-wireless.ssid",
        ssid,
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk-flags",
        "2",
        "ipv4.method",
        "manual",
        "ipv4.addresses",
        f"{PC_IP}/24",
        "ipv4.never-default",
        "yes",
        "ipv6.method",
        "disabled",
        "connection.autoconnect",
        "no",
    ]
    names = nmcli("-t", "-f", "NAME", "connection", "show").splitlines()
    if NM_NAME in names:
        nmcli("connection", "modify", NM_NAME, *settings)
    else:
        nmcli(
            "connection",
            "add",
            "type",
            "wifi",
            "con-name",
            NM_NAME,
            "ifname",
            wifi_device(),
            "ssid",
            ssid,
            *settings[2:],
        )
    print(
        f"  NetworkManager connection '{NM_NAME}': static {PC_IP}/24, no default route, no autoconnect"
    )


def cmd_setup(args):
    ser = esp_at.open_port(args.port or esp_at.find_port())
    try:
        print("1. ESP in AT mode")
        esp_at.ensure_at_mode(ser)
        mac = re.search(r'APMAC,"([0-9a-f:]+)"', esp_at.at(ser, "AT+CIFSR", 2))
        ssid = (
            args.ssid
            or f"seame-drone-{mac.group(1).replace(':', '')[-4:] if mac else 'esp'}"
        )
        password = cfg.get_secret(
            f"wifi:{ssid}",
            f"New WPA2 password for the drone network '{ssid}': ",
            ask_again=args.ask_password,
        )
        print(
            f"2. SoftAP '{ssid}', WPA2, channel {args.channel}, max {args.max_clients} client(s)"
        )
        esp_at.at_ok(ser, "AT+CWMODE_DEF=2")
        esp_at.at_ok(
            ser,
            f'AT+CWSAP_DEF="{ssid}","{password}",{args.channel},3,{args.max_clients}',
            secret=password,
        )
        esp_at.at_ok(ser, f'AT+CIPAP_DEF="{AP_IP}","{AP_IP}","255.255.255.0"')
        esp_at.at_ok(ser, "AT+CWDHCP_DEF=0,1")
        print("3. Transparent UDP bridge at boot")
        esp_at.at_ok(
            ser,
            f'AT+SAVETRANSLINK=1,"{PC_IP}",{cfg.BRIDGE_PORT},"UDP",{cfg.BRIDGE_PORT}',
        )
        esp_at.reboot(ser)
    finally:
        ser.close()
    print("4. This PC")
    setup_pc_connection(ssid)
    cfg.save_profile(
        {
            "scenario": "production",
            "ssid": ssid,
            "esp_ip": AP_IP,
            "pc_ip": PC_IP,
            "esp_port": cfg.BRIDGE_PORT,
            "pc_port": cfg.BRIDGE_PORT,
            "channel": args.channel,
            "configured": datetime.datetime.now().isoformat(timespec="seconds"),
        }
    )
    print(f"\nReady. Scenario PRODUCTION saved in {cfg.PROFILE}.")
    print(
        "Next:  python3 scripts/setup_production.py connect   (this PC loses internet until disconnect)"
    )


def nm_up(password: str, wait: int = 0):
    """Bring the drone connection up; the password goes through a temporary passwd-file."""
    fd, path = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(f"802-11-wireless-security.psk:{password}\n")
        extra = ["--wait", str(wait)] if wait else []
        nmcli(*extra, "connection", "up", NM_NAME, "passwd-file", path)
    finally:
        os.unlink(path)


def ping_once(ip: str) -> bool:
    return (
        subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip], capture_output=True
        ).returncode
        == 0
    )


def production_password() -> tuple[dict, str]:
    p = cfg.load_profile()
    if p.get("scenario") != "production":
        sys.exit("Run setup_production.py setup first.")
    return p, cfg.get_secret(f"wifi:{p['ssid']}", f"Password for '{p['ssid']}': ")


def cmd_connect(args):
    p, password = production_password()
    nmcli("device", "wifi", "rescan", check=False)
    time.sleep(3)
    nm_up(password)
    print(
        f"Connected to '{p['ssid']}' as {PC_IP}. Test with: python3 scripts/msp_check.py info"
    )


def cmd_disconnect(args):
    nmcli("connection", "down", NM_NAME, check=False)
    print(
        "Left the drone network. NetworkManager reconnects to the usual Wi-Fi (e.g. SEAME) by itself."
    )


def cmd_status(args):
    p = cfg.load_profile()
    if p.get("scenario") != "production":
        sys.exit(f"Active scenario: {p.get('scenario', 'none')}.")
    active = (
        NM_NAME
        in nmcli("-t", "-f", "NAME", "connection", "show", "--active").splitlines()
    )
    print(
        f"Scenario PRODUCTION, network '{p['ssid']}', channel {p.get('channel')} (configured {p['configured']})"
    )
    print(f"  ESP {p['esp_ip']}:{p['esp_port']}  ↔  PC {p['pc_ip']}:{p['pc_port']}")
    print(f"  this PC on the drone network: {'yes' if active else 'no'}")


def reboot_once(ser, password: str, wait: int, clock=time.monotonic) -> dict:
    """Reset the ESP, rejoin its network and time it. Times are from the reset."""
    nmcli("connection", "down", NM_NAME, check=False)
    esp_at.hard_reset(ser)
    t0 = clock()
    row = {"connected": False, "connect_s": "", "reachable_s": ""}
    try:
        nm_up(password, wait)
    except esp_at.EspError:
        return row
    row["connected"], row["connect_s"] = True, f"{clock() - t0:.2f}"
    while clock() - t0 < wait:
        if ping_once(AP_IP):
            row["reachable_s"] = f"{clock() - t0:.2f}"
            break
    return row


def cmd_reboot_test(args):
    p, password = production_password()
    ser = esp_at.open_port(args.port or esp_at.find_port())
    rows = []
    try:
        print(
            f"S1-04: {args.reboots} resets of the ESP SoftAP '{p['ssid']}'. Ctrl+C to stop."
        )
        for i in range(1, args.reboots + 1):
            row = {"reboot": i, **reboot_once(ser, password, args.wait)}
            rows.append(row)
            print(
                f"  {i:2d}/{args.reboots}  "
                + (
                    f"connected {row['connect_s']} s, ping {row['reachable_s'] or 'no answer'} s"
                    if row["connected"]
                    else f"NOT connected within {args.wait} s"
                )
            )
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
    out = RESULTS_DIR / args.csv
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=("reboot", "connected", "connect_s", "reachable_s")
        )
        writer.writeheader()
        writer.writerows(rows)
    ok = [r for r in rows if r["connected"] and r["reachable_s"]]
    print(f"\nSaved {out}\nConnected and reachable: {len(ok)}/{len(rows)}")
    if ok:
        times = [float(r["reachable_s"]) for r in ok]
        print(
            f"Time to reach the ESP: min {min(times):.1f} s, "
            f"median {statistics.median(times):.1f} s, max {max(times):.1f} s"
        )
    passed = len(rows) == args.reboots and len(ok) == len(rows)
    print(
        f"S1-04 {'PASS' if passed else 'FAIL'}  ({args.reboots}/{args.reboots} connects)"
    )
    print("Back to the usual Wi-Fi: python3 scripts/setup_production.py disconnect")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", help="ESP serial port (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "setup", help="configure the ESP and this PC for the PRODUCTION scenario"
    )
    p.add_argument(
        "--ssid", help="network name (default: seame-drone-<last 4 MAC digits>)"
    )
    p.add_argument(
        "--channel", type=int, default=6, choices=range(1, 14), metavar="1-13"
    )
    p.add_argument(
        "--max-clients", type=int, default=1, choices=range(1, 5), metavar="1-4"
    )
    p.add_argument(
        "--ask-password", action="store_true", help="choose a new network password"
    )
    sub.add_parser("connect", help="join the drone network (no internet meanwhile)")
    sub.add_parser("disconnect", help="leave the drone network")
    sub.add_parser("status", help="show the saved PRODUCTION scenario")
    p = sub.add_parser(
        "reboot-test",
        help="S1-04: reset the ESP N times (ESP on USB) and time each reconnect",
    )
    p.add_argument("--reboots", type=int, default=20)
    p.add_argument(
        "--wait", type=int, default=30, help="seconds allowed for each reconnect"
    )
    p.add_argument(
        "--csv", default="s1-04_softap_reboots.csv", help="file name in results/"
    )
    ap.set_defaults(ssid=None, channel=6, max_clients=1, ask_password=False)
    args = ap.parse_args()
    try:
        {
            "setup": cmd_setup,
            "connect": cmd_connect,
            "disconnect": cmd_disconnect,
            "status": cmd_status,
            "reboot-test": cmd_reboot_test,
        }[args.cmd](args)
    except esp_at.EspError as e:
        sys.exit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
