# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding, pairing and connecting Bluetooth speakers with bluetoothctl."""
import re
import subprocess

from .config import MAC_ADDRESS

SCAN_SECONDS = 15
ANSI_CODE = re.compile(r"\x1b\[[0-9;]*m|\x01|\x02")
DEVICE_LINE = re.compile(r"Device ((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}) (.+)$")


def bluetoothctl(*args, timeout=10, input=None, errors=False):
    """Run bluetoothctl and return its output, without colours. Empty when it isn't available.

    input is given to it as commands, e.g. "connect AA:BB:CC:DD:EE:FF\n". With errors, what it says on stderr is
    included.
    """
    try:
        output = subprocess.run(["bluetoothctl"] + list(args), input=input, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT if errors else subprocess.PIPE, text=True,
                                timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return ANSI_CODE.sub("", output)


def device_info(mac):
    return bluetoothctl("info", mac)


def is_connected(mac):
    return "Connected: yes" in device_info(mac)


def is_paired(mac):
    return "Paired: yes" in device_info(mac)


def not_paired(mac):
    """Whether bluetoothctl says the speaker isn't paired, e.g. after it was reset or paired with another device.

    False when it can't tell, e.g. when bluetoothctl doesn't run.
    """
    info = bluetoothctl("info", mac, errors=True)
    return "Paired: no" in info or "{} not available".format(mac) in info


def send(command, mac):
    """Give bluetoothctl a command, e.g. connect or disconnect, without waiting for it to finish."""
    bluetoothctl(input="{} {}\n".format(command, mac))


def device_addresses():
    """The addresses of the devices bluetoothctl knows, paired or not."""
    return [fields[1].upper() for fields in (line.split() for line in bluetoothctl("devices").splitlines())
            if len(fields) >= 2 and fields[0] == "Device" and MAC_ADDRESS.match(fields[1].upper())]


def bluetooth_available():
    return bluetoothctl("show").startswith("Controller")


def bluetooth_speakers():
    """Paired Bluetooth devices as [(mac, name, connected)], empty when bluetoothctl isn't available."""
    speakers = []
    for line in bluetoothctl("devices").splitlines():
        fields = line.split(" ", 2)
        if len(fields) < 2 or fields[0] != "Device" or not MAC_ADDRESS.match(fields[1].upper()):
            continue
        info = bluetoothctl("info", fields[1])
        if "Paired: yes" not in info:
            continue
        name = fields[2].strip() if len(fields) > 2 else fields[1]
        speakers.append((fields[1].upper(), name, "Connected: yes" in info))
    return speakers


def scan_speakers(seconds=SCAN_SECONDS):
    """Named devices nearby that aren't paired, as [(mac, name)]."""
    bluetoothctl("power", "on")
    scan = bluetoothctl("--timeout", str(seconds), "scan", "on", timeout=seconds + 10)
    lines = [line for line in scan.splitlines() if "[NEW]" in line] + bluetoothctl("devices").splitlines()

    paired = {mac for mac, _, _ in bluetooth_speakers()}
    found = {}
    for line in lines:
        match = DEVICE_LINE.search(line.strip())
        if not match:
            continue
        mac, name = match.group(1).upper(), match.group(2).strip()
        # devices that don't send a name show their address instead
        if mac in paired or name.upper().replace("-", ":") == mac:
            continue
        found.setdefault(mac, name)
    return list(found.items())


def pair_speaker(mac):
    """Pair and trust a device. Returns None, or why it failed."""
    output = bluetoothctl("pair", mac, timeout=40)
    if "Paired: yes" not in bluetoothctl("info", mac):
        reasons = [line.strip() for line in output.splitlines() if "fail" in line.lower() or "error" in line.lower()]
        return reasons[-1] if reasons else "no answer from the speaker"
    bluetoothctl("trust", mac)
    return None


def remove_speaker(mac):
    """Forget a paired device. Returns None, or why it failed."""
    output = bluetoothctl("remove", mac, errors=True)
    if "Paired: yes" in bluetoothctl("info", mac):
        reasons = [line.strip() for line in output.splitlines() if "fail" in line.lower() or "error" in line.lower()]
        return reasons[-1] if reasons else "bluetoothctl didn't remove it"
    return None


def pair_again(mac, seconds=SCAN_SECONDS):
    """Pair a speaker that was reset, or paired with another device: forget it, find it again, and pair it.

    Returns None, or why it failed.
    """
    bluetoothctl("remove", mac)
    if mac not in [found for found, _ in scan_speakers(seconds)]:
        return "not found, is it in pairing mode?"
    return pair_speaker(mac)
