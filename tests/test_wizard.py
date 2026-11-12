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
