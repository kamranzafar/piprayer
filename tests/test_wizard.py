# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The configuration wizard: piprayer.py config."""
import configparser
import os
from zoneinfo import ZoneInfo

import pytest

from pipray import bluetooth as bluetooth_module, wizard
from pipray.bluetooth import bluetooth_speakers
from pipray.config import Config
from pipray.wizard import Wizard, configure, parse_zone_coordinate, system_timezone, timezone_location
from conftest import MELBOURNE_CONFIG


class Answers:
    """Answers the wizard's questions in order, recording what was asked and said."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []
        self.output = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def say(self, text):
        self.output.append(text)

    def wizard(self):
        return Wizard(self.ask, self.say)


def saved(path):
    parser = configparser.RawConfigParser()  # as written, e.g. passwords with %
    parser.read(path)
    return dict(parser["Default"])


@pytest.fixture(autouse=True)
def melbourne_system(monkeypatch):
    monkeypatch.setattr(wizard, "system_timezone", lambda: "Australia/Melbourne")
    monkeypatch.setattr(wizard, "installed_azaan", lambda: 1)
    monkeypatch.setattr(bluetooth_module, "bluetoothctl", FakeBluetooth())

# Answers up to the speaker question, and after it
BEFORE_SPEAKER = [""] * 6
AFTER_SPEAKER = [""] * 8  # azaan, fajr azaan, volume, reminder, Hijri date, status page, MQTT, save


def test_new_configuration_from_system_timezone(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*[""] * 14)

    assert configure(path, answers.wizard()) == 0
    assert saved(path) == {
        "timezone": "Australia/Melbourne",
        "lat": "-37.8167",
        "lng": "144.9667",
        "method": "MWL",
        "asr-time": "Standard",
        "prayers": "Fajr, Dhuhr, Asr, Maghrib, Isha",
        "azaan": "1",
        "volume": "100",
        "reminder": "0",
    }
    Config(path)


def test_changes_an_existing_configuration(write_config):
    path = write_config(MELBOURNE_CONFIG + "gmt-offset = 10\ndst = 1\n")
    answers = Answers("", "-37.9", "", "", "", "Fajr:-5, Isha", "5", "3", "80, Fajr:40", "10", "", "", "", "y")

    assert configure(path, answers.wizard()) == 0
    assert saved(path) == {
        "timezone": "Australia/Melbourne",
        "lat": "-37.9",
        "lng": "144.751876",
        "method": "Jafari",
        "asr-time": "Hanafi",
        "prayers": "Fajr:-5, Isha",
        "azaan": "5",
        "fajr-azaan": "3",
        "volume": "80, Fajr:40",
        "reminder": "10",
    }
    assert "Latitude [-37.909499]: " in answers.prompts


def test_new_timezone_suggests_its_location_and_method(write_config):
    path = write_config()
    answers = Answers("America/New_York", *[""] * 13)

    assert configure(path, answers.wizard()) == 0
    config = saved(path)
    assert config["timezone"] == "America/New_York"
    assert (config["lat"], config["lng"]) == ("40.7142", "-74.0064")
    # the method of the existing configuration is kept
    assert config["method"] == "Jafari"


@pytest.mark.parametrize("tz, method, asr", [
    ("America/Chicago", "ISNA", "Standard"),
    ("Asia/Karachi", "Karachi", "Hanafi"),
    ("Asia/Riyadh", "Makkah", "Standard"),
    ("Europe/London", "MWL", "Standard"),
    ("Asia/Dubai", "Dubai", "Standard"),
    ("Europe/Istanbul", "Turkey", "Standard"),
    ("Asia/Jakarta", "Indonesia", "Standard"),
    ("Asia/Kuala_Lumpur", "Malaysia", "Standard"),
])
def test_suggests_regional_method(tmp_path, tz, method, asr):
    path = str(tmp_path / ".piprayer")
    answers = Answers(tz, *[""] * 14)
    assert configure(path, answers.wizard()) == 0
    assert (saved(path)["method"], saved(path)["asr-time"]) == (method, asr)


def test_asks_again_after_invalid_answers(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers("Mars/Olympus", "", "100", "", "", "99", "", "", "Fajir", "", "18", "", "", "150", "",
                      "61", "", "3", "", "70000", "", "", "")

    assert configure(path, answers.wizard()) == 0
    for message in ["Unknown timezone: Mars/Olympus", "Invalid latitude: 100", "Choose a number from 1 to 22",
                    "Unknown prayer: Fajir", "there is no media/azaan-18.opus", "Invalid volume: 150",
                    "Invalid reminder: 61", "Invalid hijri-adjust: 3",
                    "Invalid status-port: 70000"]:
        assert any(message in line for line in answers.output), message
    config = saved(path)
    assert config["method"] == "MWL"
    assert config["asr-time"] == "Standard"


def test_method_by_name_or_number(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers("", "", "", "karachi", "2", *[""] * 9)
    assert configure(path, answers.wizard()) == 0
    assert (saved(path)["method"], saved(path)["asr-time"]) == ("Karachi", "Hanafi")


def test_shows_times_and_keeps_file_when_not_saved(write_config):
    path = write_config()
    answers = Answers(*[""] * 3, "MWL", *[""] * 9, "n")

    assert configure(path, answers.wizard()) == 1
    assert open(path).read() == MELBOURNE_CONFIG
    assert any("MWL method" in line for line in answers.output)
    assert not (os.path.exists(path + ".new"))


def test_none_removes_fajr_azaan(write_config):
    path = write_config(MELBOURNE_CONFIG + "fajr-azaan = 3\n")
    answers = Answers(*[""] * 7, "none", *[""] * 6)
    assert configure(path, answers.wizard()) == 0
    assert "fajr-azaan" not in saved(path)


def test_end_of_input_is_an_error(tmp_path):
    path = str(tmp_path / ".piprayer")
    with pytest.raises(EOFError):
        configure(path, Answers("").wizard())
    assert not (tmp_path / ".piprayer").exists()


@pytest.mark.parametrize("name, lat, lng", [
    ("Australia/Melbourne", -37.8167, 144.9667),
    ("America/New_York", 40.7142, -74.0064),
])
def test_timezone_location(name, lat, lng):
    assert timezone_location(name)[:2] == (lat, lng)


def test_unknown_timezone_has_no_location():
    assert timezone_location("Etc/UTC") is None


def test_zone_coordinates_with_seconds():
    assert parse_zone_coordinate("+404251", 2) == 40.7142
    assert parse_zone_coordinate("-0740023", 3) == -74.0064


def test_system_timezone_from_tz_variable(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("TZ", "Europe/London")
    assert system_timezone() == "Europe/London"
    ZoneInfo(system_timezone())


# ---------------------------------------------------------------- speaker

KITCHEN, ECHO, SOUNDBAR, PHONE = "AA:AA:AA:AA:AA:01", "BB:BB:BB:BB:BB:02", "CC:CC:CC:CC:CC:03", "DD:DD:DD:DD:DD:04"


class FakeBluetooth:
    """Stands in for bluetoothctl: an adapter, known devices, and devices that appear when scanning."""

    def __init__(self, controller=False):
        self.controller = controller
        self.devices = {}  # mac: [name, paired, connected]
        self.nearby = {}  # mac: name, found by a scan
        self.refuse = set()
        self.commands = []

    def __call__(self, *args, timeout=10):
        self.commands.append(" ".join(args))
        if args == ("show",):
            return "Controller 00:11:22:33:44:55 (public)\n\tPowered: yes\n" if self.controller else ""
        if args == ("devices",):
            return "".join("Device {} {}\n".format(mac, d[0]) for mac, d in self.devices.items())
        if args[0] == "info":
            device = self.devices.get(args[1])
            if not device:
                return "Device {} not available\n".format(args[1])
            return "Device {}\n\tPaired: {}\n\tConnected: {}\n".format(
                args[1], "yes" if device[1] else "no", "yes" if device[2] else "no")
        if args[1:] == ("15", "scan", "on"):
            output = "Discovery started\n"
            for mac, name in self.nearby.items():
                self.devices.setdefault(mac, [name, False, False])
                output += "[NEW] Device {} {}\n[CHG] Device {} RSSI: -60\n".format(mac, name, mac)
            return output
        if args[0] == "pair":
            if args[1] in self.refuse:
                return "Attempting to pair with {}\nFailed to pair: org.bluez.Error.AuthenticationFailed\n".format(args[1])
            self.devices[args[1]][1] = True
            return "Attempting to pair with {}\nPairing successful\n".format(args[1])
        return ""


@pytest.fixture
def bluetooth(monkeypatch):
    fake = FakeBluetooth(controller=True)
    fake.devices = {KITCHEN: ["Kitchen Speaker", True, False], ECHO: ["Echo Pop", True, True],
                    PHONE: ["Phone", False, False]}
    monkeypatch.setattr(bluetooth_module, "bluetoothctl", fake)
    return fake


def test_lists_paired_speakers_and_saves_the_choice(tmp_path, bluetooth):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "2", *AFTER_SPEAKER)

    assert configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == ECHO
    assert "  1. Kitchen Speaker ({})".format(KITCHEN) in answers.output
    assert "  2. Echo Pop ({}), connected".format(ECHO) in answers.output
    assert not any("Phone" in line for line in answers.output)
    assert "Speaker [0]: " in answers.prompts


def test_any_speaker_is_the_default(tmp_path, bluetooth):
    path = str(tmp_path / ".piprayer")
    assert configure(path, Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER).wizard()) == 0
    assert "speaker" not in saved(path)


def test_keeps_or_removes_the_configured_speaker(write_config, bluetooth):
    path = write_config(MELBOURNE_CONFIG + "speaker = {}\n".format(KITCHEN))
    answers = Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER)
    assert configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == KITCHEN
    assert "Speaker [1]: " in answers.prompts

    assert configure(path, Answers(*BEFORE_SPEAKER, "0", *AFTER_SPEAKER).wizard()) == 0
    assert "speaker" not in saved(path)


def test_configured_speaker_that_is_no_longer_paired_is_offered(write_config, bluetooth):
    path = write_config(MELBOURNE_CONFIG + "speaker = {}\n".format(SOUNDBAR))
    answers = Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER)
    assert configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == SOUNDBAR
    assert "  3. not paired now ({})".format(SOUNDBAR) in answers.output


def test_speaker_by_address_and_invalid_choices(tmp_path, bluetooth):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "3", "speaker", SOUNDBAR.lower(), *AFTER_SPEAKER)
    assert configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == SOUNDBAR
    assert answers.output.count(
        "  Choose a number from 0 to 2, p to pair a new speaker, or enter a Bluetooth address") == 2


def test_pairs_a_new_speaker(tmp_path, bluetooth):
    bluetooth.nearby = {SOUNDBAR: "Soundbar", "EE:EE:EE:EE:EE:05": "EE-EE-EE-EE-EE-05"}
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "p", "", "1", *AFTER_SPEAKER)

    assert configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == SOUNDBAR
    assert "  1. Soundbar ({})".format(SOUNDBAR) in answers.output
    # devices without a name, and the paired ones, are not offered
    assert not any("EE-EE" in line or "Kitchen" in line for line in answers.output[answers.output.index("Speakers found:"):])
    assert "Paired Soundbar" in answers.output
    assert ["power on", "--timeout 15 scan on", "pair " + SOUNDBAR, "trust " + SOUNDBAR] == [
        c for c in bluetooth.commands if c.split()[0] in ("power", "--timeout", "pair", "trust")]


def test_pairing_failure_can_be_retried_or_abandoned(tmp_path, bluetooth):
    bluetooth.nearby = {SOUNDBAR: "Soundbar"}
    bluetooth.refuse.add(SOUNDBAR)
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "p", "", "1", "r", "", "1", *AFTER_SPEAKER)

    assert configure(path, answers.wizard()) == 0
    assert "Could not pair Soundbar: Failed to pair: org.bluez.Error.AuthenticationFailed" in answers.output
    assert sum(c.startswith("--timeout") for c in bluetooth.commands) == 2
    # back at the list of paired speakers, Kitchen Speaker was chosen
    assert saved(path)["speaker"] == KITCHEN


def test_offers_pairing_when_nothing_is_paired(tmp_path, bluetooth):
    bluetooth.devices = {}
    bluetooth.nearby = {SOUNDBAR: "Soundbar"}
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "", "", "1", *AFTER_SPEAKER)

    assert configure(path, answers.wizard()) == 0
    assert "No paired Bluetooth speakers found." in answers.output
    assert "Pair a speaker now? [Y/n]: " in answers.prompts
    assert saved(path)["speaker"] == SOUNDBAR


def test_nothing_found_when_scanning(tmp_path, bluetooth):
    bluetooth.devices = {}
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "", "", "", "n", *AFTER_SPEAKER)

    assert configure(path, answers.wizard()) == 0
    assert any(line.startswith("No new speakers found") for line in answers.output)
    assert "speaker" not in saved(path)


def test_without_a_bluetooth_adapter_the_speaker_is_kept(write_config):
    path = write_config(MELBOURNE_CONFIG + "speaker = {}\n".format(KITCHEN))
    answers = Answers(*BEFORE_SPEAKER, *AFTER_SPEAKER)
    assert configure(path, answers.wizard()) == 0
    assert not any(prompt.startswith("Speaker") for prompt in answers.prompts)
    assert any("No Bluetooth adapter found" in line for line in answers.output)
    assert saved(path)["speaker"] == KITCHEN


def test_bluetooth_speakers_reads_bluetoothctl(monkeypatch):
    monkeypatch.undo()

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, **kwargs):
        if cmd[1:] == ["devices"]:
            return Result("Device AA:AA:AA:AA:AA:01 Kitchen Speaker\nDevice BB:BB:BB:BB:BB:02 Echo Pop\n"
                          "Device CC:CC:CC:CC:CC:03 Phone\n")
        mac = cmd[2]
        return Result({
            "AA:AA:AA:AA:AA:01": "\tPaired: yes\n\tConnected: no\n",
            "BB:BB:BB:BB:BB:02": "\tPaired: yes\n\tConnected: yes\n",
            "CC:CC:CC:CC:CC:03": "\tPaired: no\n",
        }[mac])

    monkeypatch.setattr(bluetooth_module.subprocess, "run", fake_run)
    assert bluetooth_speakers() == [(KITCHEN, "Kitchen Speaker", False), (ECHO, "Echo Pop", True)]


def test_bluetooth_speakers_without_bluetoothctl(monkeypatch):
    monkeypatch.undo()

    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(bluetooth_module.subprocess, "run", missing)
    assert bluetooth_speakers() == []


# ---------------------------------------------------------------- after saving


@pytest.mark.parametrize("installed, message", [
    (True, "The PiPrayer service will use the new settings within a minute."),
    (False, "Run ./setup-piprayer.sh to install the PiPrayer service."),
])
def test_says_what_happens_next(tmp_path, monkeypatch, installed, message):
    service = tmp_path / "piprayer.service"
    if installed:
        service.write_text("[Service]\n")
    monkeypatch.setattr(wizard, "service_file", lambda: str(service))
    answers = Answers(*[""] * 14)

    assert configure(str(tmp_path / ".piprayer"), answers.wizard()) == 0
    assert answers.output[-1] == message


def test_no_next_step_when_run_by_the_setup(tmp_path):
    answers = Answers(*[""] * 14)
    path = str(tmp_path / ".piprayer")
    assert configure(path, answers.wizard(), next_step=False) == 0
    assert answers.output[-1] == "Saved " + path


def test_asks_about_short_nights_far_from_the_equator(tmp_path):
    path = str(tmp_path / ".piprayer")
    # timezone, latitude, longitude, method, Asr, then the rule for short nights: 2 is angle based
    answers = Answers("Europe/London", "51.5", "-0.12", "", "", "2", *[""] * 9)

    assert configure(path, answers.wizard()) == 0
    assert saved(path)["high-latitude"] == "AngleBased"
    assert any("too short for Fajr and Isha" in line for line in answers.output)


def test_doesnt_ask_about_short_nights_near_the_equator(write_config):
    path = write_config()
    answers = Answers(*[""] * 14)
    assert configure(path, answers.wizard()) == 0
    assert "high-latitude" not in saved(path)
    assert not any("too short for Fajr and Isha" in line for line in answers.output)


def test_status_page_port(write_config, monkeypatch):
    monkeypatch.setattr(wizard.socket, "gethostname", lambda: "raspberrypi")
    path = write_config()
    answers = Answers(*[""] * 11, "8080", "", "")

    assert configure(path, answers.wizard()) == 0
    assert saved(path)["status-port"] == "8080"
    assert "Status page: http://raspberrypi.local:8080 from another device on your network" in answers.output

    # kept when Enter is pressed, removed with none
    assert configure(path, Answers(*[""] * 14).wizard()) == 0
    assert saved(path)["status-port"] == "8080"
    assert configure(path, Answers(*[""] * 11, "none", "", "").wizard()) == 0
    assert "status-port" not in saved(path)


def test_method_menu_lists_the_first_seven_and_more_on_request(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers("", "", "", "m", "16", *[""] * 10)

    assert configure(path, answers.wizard()) == 0
    menus = [i for i, line in enumerate(answers.output) if line.startswith("   1. MWL")]
    first, full = answers.output[menus[0]:menus[1]], answers.output[menus[1]:]
    assert "   7. Jafari     Shia Ithna-Ashari, Leva Institute, Qum" in first
    assert not any(line.startswith("  15. Turkey") for line in first)
    assert any(line.startswith("   m. More methods") for line in first)
    assert "  22. Portugal   Comunidade Islamica de Lisboa, Portugal" in full
    assert not any(line.startswith("   m.") for line in full)
    assert saved(path)["method"] == "France"


def test_method_menu_includes_the_suggested_method(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers("Europe/Istanbul", *[""] * 13)

    assert configure(path, answers.wizard()) == 0
    assert "  15. Turkey     Diyanet Isleri Baskanligi, Turkey" in answers.output
    assert "Calculation method [Turkey]: " in answers.prompts
    assert saved(path)["method"] == "Turkey"


def test_any_method_can_be_chosen_without_listing_them_all(tmp_path):
    path = str(tmp_path / ".piprayer")
    assert configure(path, Answers("", "", "", "malaysia", *[""] * 10).wizard()) == 0
    assert saved(path)["method"] == "Malaysia"


# Answers up to the MQTT question for an existing Melbourne configuration
BEFORE_MQTT = [""] * 12


def test_mqtt_broker_with_a_login(write_config, monkeypatch):
    monkeypatch.setattr(wizard, "load_paho", lambda: object())
    path = write_config()
    answers = Answers(*BEFORE_MQTT, "homeassistant.local", "", "piprayer", "p%ss word", "")

    assert configure(path, answers.wizard()) == 0
    config = saved(path)
    assert (config["mqtt-host"], config["mqtt-port"], config["mqtt-username"]) == ("homeassistant.local", "1883",
                                                                                   "piprayer")
    settings = Config(path).mqtt
    assert settings.password == "p%ss word"  # exactly as typed
    assert os.stat(path).st_mode & 0o077 == 0  # only its owner can read the password
    assert "MQTT password (none for no password): " in answers.prompts
    assert not any("p%ss word" in prompt for prompt in answers.prompts)


def test_mqtt_password_is_kept_or_removed(write_config, monkeypatch):
    monkeypatch.setattr(wizard, "load_paho", lambda: object())
    path = write_config(MELBOURNE_CONFIG + "mqtt-host = broker\nmqtt-username = pi\nmqtt-password = secret\n")

    answers = Answers(*BEFORE_MQTT, "", "", "", "", "")
    assert configure(path, answers.wizard()) == 0
    assert Config(path).mqtt.password == "secret"
    assert "MQTT password (Enter to keep it, none for no password): " in answers.prompts
    assert not any("secret" in prompt for prompt in answers.prompts)

    assert configure(path, Answers(*BEFORE_MQTT, "", "", "", "none", "").wizard()) == 0
    assert "mqtt-password" not in saved(path)


def test_mqtt_off_removes_its_settings(write_config):
    path = write_config(MELBOURNE_CONFIG + "mqtt-host = broker\nmqtt-port = 1884\nmqtt-username = pi\n")
    assert configure(path, Answers(*BEFORE_MQTT, "none", "").wizard()) == 0
    assert not [option for option in saved(path) if option.startswith("mqtt-")]


def test_mqtt_without_paho_says_how_to_install_it(write_config, monkeypatch):
    monkeypatch.setattr(wizard, "load_paho", lambda: None)
    path = write_config()
    answers = Answers(*BEFORE_MQTT, "broker", "", "", "")

    assert configure(path, answers.wizard()) == 0
    assert "  MQTT needs paho-mqtt. Install it with: sudo apt install python3-paho-mqtt" in answers.output
    assert saved(path)["mqtt-host"] == "broker"


def test_invalid_mqtt_broker(write_config, monkeypatch):
    monkeypatch.setattr(wizard, "load_paho", lambda: object())
    path = write_config()
    answers = Answers(*BEFORE_MQTT, "http://broker", "broker", "", "", "")
    assert configure(path, answers.wizard()) == 0
    assert any("Invalid MQTT broker: http://broker" in line for line in answers.output)
