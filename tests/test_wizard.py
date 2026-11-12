"""The configuration wizard: piprayer.py config."""
import configparser
from zoneinfo import ZoneInfo

import pytest

import piprayer
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
        return piprayer.Wizard(self.ask, self.say)


def saved(path):
    parser = configparser.ConfigParser()
    parser.read(path)
    return dict(parser["Default"])


@pytest.fixture(autouse=True)
def melbourne_system(monkeypatch):
    monkeypatch.setattr(piprayer, "system_timezone", lambda: "Australia/Melbourne")
    monkeypatch.setattr(piprayer, "installed_azaan", lambda: 1)
    monkeypatch.setattr(piprayer, "bluetoothctl", FakeBluetooth())

# Answers up to the speaker question, and after it
BEFORE_SPEAKER = [""] * 6
AFTER_SPEAKER = [""] * 5


def test_new_configuration_from_system_timezone(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*[""] * 11)

    assert piprayer.configure(path, answers.wizard()) == 0
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
    piprayer.Config(path)


def test_changes_an_existing_configuration(write_config):
    path = write_config(MELBOURNE_CONFIG + "gmt-offset = 10\ndst = 1\n")
    answers = Answers("", "-37.9", "", "", "", "Fajr:-5, Isha", "5", "3", "80, Fajr:40", "10", "y")

    assert piprayer.configure(path, answers.wizard()) == 0
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
    answers = Answers("America/New_York", *[""] * 10)

    assert piprayer.configure(path, answers.wizard()) == 0
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
])
def test_suggests_regional_method(tmp_path, tz, method, asr):
    path = str(tmp_path / ".piprayer")
    answers = Answers(tz, *[""] * 10)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert (saved(path)["method"], saved(path)["asr-time"]) == (method, asr)


def test_asks_again_after_invalid_answers(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers("Mars/Olympus", "", "100", "", "", "9", "", "", "Fajir", "", "16", "", "", "150", "",
                      "61", "", "")

    assert piprayer.configure(path, answers.wizard()) == 0
    for message in ["Unknown timezone: Mars/Olympus", "Invalid latitude: 100", "Choose a number from 1 to 7",
                    "Unknown prayer: Fajir", "there is no media/azaan-16.wav", "Invalid volume: 150",
                    "Invalid reminder: 61"]:
        assert any(message in line for line in answers.output), message
    config = saved(path)
    assert config["method"] == "MWL"
    assert config["asr-time"] == "Standard"


def test_method_by_name_or_number(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers("", "", "", "karachi", "2", *[""] * 6)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert (saved(path)["method"], saved(path)["asr-time"]) == ("Karachi", "Hanafi")


def test_shows_times_and_keeps_file_when_not_saved(write_config):
    path = write_config()
    answers = Answers(*[""] * 3, "MWL", *[""] * 6, "n")

    assert piprayer.configure(path, answers.wizard()) == 1
    assert open(path).read() == MELBOURNE_CONFIG
    assert any("MWL method" in line for line in answers.output)
    assert not (piprayer.os.path.exists(path + ".new"))


def test_none_removes_fajr_azaan(write_config):
    path = write_config(MELBOURNE_CONFIG + "fajr-azaan = 3\n")
    answers = Answers(*[""] * 7, "none", *[""] * 3)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert "fajr-azaan" not in saved(path)


def test_end_of_input_is_an_error(tmp_path):
    path = str(tmp_path / ".piprayer")
    with pytest.raises(EOFError):
        piprayer.configure(path, Answers("").wizard())
    assert not (tmp_path / ".piprayer").exists()


@pytest.mark.parametrize("name, lat, lng", [
    ("Australia/Melbourne", -37.8167, 144.9667),
    ("America/New_York", 40.7142, -74.0064),
])
def test_timezone_location(name, lat, lng):
    assert piprayer.timezone_location(name)[:2] == (lat, lng)


def test_unknown_timezone_has_no_location():
    assert piprayer.timezone_location("Etc/UTC") is None


def test_zone_coordinates_with_seconds():
    assert piprayer.parse_zone_coordinate("+404251", 2) == 40.7142
    assert piprayer.parse_zone_coordinate("-0740023", 3) == -74.0064


def test_system_timezone_from_tz_variable(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("TZ", "Europe/London")
    assert piprayer.system_timezone() == "Europe/London"
    ZoneInfo(piprayer.system_timezone())


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
    monkeypatch.setattr(piprayer, "bluetoothctl", fake)
    return fake


def test_lists_paired_speakers_and_saves_the_choice(tmp_path, bluetooth):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "2", *AFTER_SPEAKER)

    assert piprayer.configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == ECHO
    assert "  1. Kitchen Speaker ({})".format(KITCHEN) in answers.output
    assert "  2. Echo Pop ({}), connected".format(ECHO) in answers.output
    assert not any("Phone" in line for line in answers.output)
    assert "Speaker [0]: " in answers.prompts


def test_any_speaker_is_the_default(tmp_path, bluetooth):
    path = str(tmp_path / ".piprayer")
    assert piprayer.configure(path, Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER).wizard()) == 0
    assert "speaker" not in saved(path)


def test_keeps_or_removes_the_configured_speaker(write_config, bluetooth):
    path = write_config(MELBOURNE_CONFIG + "speaker = {}\n".format(KITCHEN))
    answers = Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == KITCHEN
    assert "Speaker [1]: " in answers.prompts

    assert piprayer.configure(path, Answers(*BEFORE_SPEAKER, "0", *AFTER_SPEAKER).wizard()) == 0
    assert "speaker" not in saved(path)


def test_configured_speaker_that_is_no_longer_paired_is_offered(write_config, bluetooth):
    path = write_config(MELBOURNE_CONFIG + "speaker = {}\n".format(SOUNDBAR))
    answers = Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == SOUNDBAR
    assert "  3. not paired now ({})".format(SOUNDBAR) in answers.output


def test_speaker_by_address_and_invalid_choices(tmp_path, bluetooth):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "3", "speaker", SOUNDBAR.lower(), *AFTER_SPEAKER)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == SOUNDBAR
    assert answers.output.count(
        "  Choose a number from 0 to 2, p to pair a new speaker, or enter a Bluetooth address") == 2


def test_pairs_a_new_speaker(tmp_path, bluetooth):
    bluetooth.nearby = {SOUNDBAR: "Soundbar", "EE:EE:EE:EE:EE:05": "EE-EE-EE-EE-EE-05"}
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "p", "", "1", *AFTER_SPEAKER)

    assert piprayer.configure(path, answers.wizard()) == 0
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

    assert piprayer.configure(path, answers.wizard()) == 0
    assert "Could not pair Soundbar: Failed to pair: org.bluez.Error.AuthenticationFailed" in answers.output
    assert sum(c.startswith("--timeout") for c in bluetooth.commands) == 2
    # back at the list of paired speakers, Kitchen Speaker was chosen
    assert saved(path)["speaker"] == KITCHEN


def test_offers_pairing_when_nothing_is_paired(tmp_path, bluetooth):
    bluetooth.devices = {}
    bluetooth.nearby = {SOUNDBAR: "Soundbar"}
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "", "", "1", *AFTER_SPEAKER)

    assert piprayer.configure(path, answers.wizard()) == 0
    assert "No paired Bluetooth speakers found." in answers.output
    assert "Pair a speaker now? [Y/n]: " in answers.prompts
    assert saved(path)["speaker"] == SOUNDBAR


def test_nothing_found_when_scanning(tmp_path, bluetooth):
    bluetooth.devices = {}
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "", "", "", "n", *AFTER_SPEAKER)

    assert piprayer.configure(path, answers.wizard()) == 0
    assert any(line.startswith("No new speakers found") for line in answers.output)
    assert "speaker" not in saved(path)


def test_without_a_bluetooth_adapter_the_speaker_is_kept(write_config):
    path = write_config(MELBOURNE_CONFIG + "speaker = {}\n".format(KITCHEN))
    answers = Answers(*BEFORE_SPEAKER, *AFTER_SPEAKER)
    assert piprayer.configure(path, answers.wizard()) == 0
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

    monkeypatch.setattr(piprayer.subprocess, "run", fake_run)
    assert piprayer.bluetooth_speakers() == [(KITCHEN, "Kitchen Speaker", False), (ECHO, "Echo Pop", True)]


def test_bluetooth_speakers_without_bluetoothctl(monkeypatch):
    monkeypatch.undo()

    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(piprayer.subprocess, "run", missing)
    assert piprayer.bluetooth_speakers() == []
