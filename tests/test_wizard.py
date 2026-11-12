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
    monkeypatch.setattr(piprayer, "bluetooth_speakers", lambda: [])


SPEAKERS = [("AA:AA:AA:AA:AA:01", "Kitchen Speaker", False), ("BB:BB:BB:BB:BB:02", "Echo Pop", True)]

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


@pytest.fixture
def paired(monkeypatch):
    monkeypatch.setattr(piprayer, "bluetooth_speakers", lambda: list(SPEAKERS))


def test_lists_paired_speakers_and_saves_the_choice(tmp_path, paired):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "2", *AFTER_SPEAKER)

    assert piprayer.configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == "BB:BB:BB:BB:BB:02"
    assert "  1. Kitchen Speaker (AA:AA:AA:AA:AA:01)" in answers.output
    assert "  2. Echo Pop (BB:BB:BB:BB:BB:02), connected" in answers.output
    assert "Speaker [0]: " in answers.prompts


def test_any_speaker_is_the_default(tmp_path, paired):
    path = str(tmp_path / ".piprayer")
    assert piprayer.configure(path, Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER).wizard()) == 0
    assert "speaker" not in saved(path)


def test_keeps_or_removes_the_configured_speaker(write_config, paired):
    path = write_config(MELBOURNE_CONFIG + "speaker = AA:AA:AA:AA:AA:01\n")
    answers = Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == "AA:AA:AA:AA:AA:01"
    assert "Speaker [1]: " in answers.prompts

    assert piprayer.configure(path, Answers(*BEFORE_SPEAKER, "0", *AFTER_SPEAKER).wizard()) == 0
    assert "speaker" not in saved(path)


def test_configured_speaker_that_is_no_longer_paired_is_offered(write_config, paired):
    path = write_config(MELBOURNE_CONFIG + "speaker = CC:CC:CC:CC:CC:03\n")
    answers = Answers(*BEFORE_SPEAKER, "", *AFTER_SPEAKER)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == "CC:CC:CC:CC:CC:03"
    assert "  3. not paired now (CC:CC:CC:CC:CC:03)" in answers.output


def test_speaker_by_address_and_invalid_choices(tmp_path, paired):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, "3", "speaker", "dd:dd:dd:dd:dd:04", *AFTER_SPEAKER)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert saved(path)["speaker"] == "DD:DD:DD:DD:DD:04"
    assert answers.output.count("  Choose a number from 0 to 2, or enter a Bluetooth address") == 2


def test_no_paired_speakers_skips_the_question(tmp_path):
    path = str(tmp_path / ".piprayer")
    answers = Answers(*BEFORE_SPEAKER, *AFTER_SPEAKER)
    assert piprayer.configure(path, answers.wizard()) == 0
    assert not any(prompt.startswith("Speaker") for prompt in answers.prompts)
    assert any("No paired Bluetooth speakers found" in line for line in answers.output)


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
    assert piprayer.bluetooth_speakers() == SPEAKERS


def test_bluetooth_speakers_without_bluetoothctl(monkeypatch):
    monkeypatch.undo()

    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(piprayer.subprocess, "run", missing)
    assert piprayer.bluetooth_speakers() == []
