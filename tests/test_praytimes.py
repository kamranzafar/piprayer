"""Prayer time calculations, checked against an independent implementation."""
import json
import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

import praytimes

REFERENCE_FILE = os.path.join(os.path.dirname(__file__), "data", "aladhan-2026-05-15.json")

with open(REFERENCE_FILE) as f:
    REFERENCE = json.load(f)


def minutes(hhmm):
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


@pytest.mark.parametrize(
    "case", REFERENCE["cases"],
    ids=lambda c: "{}-{}-{}".format(c["location"], c["method"], c["asr"]))
def test_matches_aladhan_reference(case):
    day = date.fromisoformat(REFERENCE["date"])
    tz = ZoneInfo(case["timezone"])
    utc_offset = datetime.combine(day, time(12), tz).utcoffset().total_seconds() / 3600

    calc = praytimes.PrayTimes(case["method"], case["asr"])
    times = calc.getTimes(day, (case["lat"], case["lng"]), utc_offset)

    for prayer, expected in case["times"].items():
        # Allow a minute for rounding differences between the implementations
        assert abs(minutes(times[prayer]) - minutes(expected)) <= 1, \
            "{} is {}, expected {}".format(prayer, times[prayer], expected)


def test_praytimes_documented_example():
    # Sample usage from the praytimes.py documentation
    times = praytimes.PrayTimes("ISNA").getTimes((2011, 2, 9), (43, -80), -5)
    assert times["sunrise"] == "07:26"


@pytest.mark.parametrize("method", sorted(praytimes.PrayTimes.methods))
def test_uses_requested_method(method):
    calc = praytimes.PrayTimes(method)
    assert calc.getMethod() == method
    for name, value in praytimes.PrayTimes.methods[method]["params"].items():
        assert calc.getSettings()[name] == value


def test_unknown_method_falls_back_to_mwl():
    assert praytimes.PrayTimes("Unknown").getMethod() == "MWL"


@pytest.mark.parametrize("asr", ["Standard", "Hanafi"])
def test_uses_requested_asr_method(asr):
    assert praytimes.PrayTimes("MWL", asr).getSettings()["asr"] == asr


def test_hanafi_asr_is_later_than_standard():
    coords, day = (21.4225, 39.8262), (2026, 5, 15)
    standard = praytimes.PrayTimes("MWL", "Standard").getTimes(day, coords, 3)
    hanafi = praytimes.PrayTimes("MWL", "Hanafi").getTimes(day, coords, 3)
    assert minutes(hanafi["asr"]) > minutes(standard["asr"])


def test_set_method():
    calc = praytimes.PrayTimes("MWL")
    calc.setMethod("Karachi")
    assert calc.getMethod() == "Karachi"
    assert calc.getSettings()["isha"] == 18


def test_instances_do_not_share_state():
    first = praytimes.PrayTimes("ISNA", "Hanafi")
    first.tune({"fajr": 5})
    second = praytimes.PrayTimes("MWL")

    assert second.getSettings()["fajr"] == 18
    assert second.getSettings()["asr"] == "Standard"
    assert second.getOffsets()["fajr"] == 0


def test_tune_shifts_times_by_offset():
    coords, day = (21.4225, 39.8262), (2026, 5, 15)
    plain = praytimes.PrayTimes("MWL").getTimes(day, coords, 3)

    tuned = praytimes.PrayTimes("MWL")
    tuned.tune({"fajr": -10, "isha": 15})
    times = tuned.getTimes(day, coords, 3)

    assert minutes(times["fajr"]) == minutes(plain["fajr"]) - 10
    assert minutes(times["isha"]) == minutes(plain["isha"]) + 15
    assert times["dhuhr"] == plain["dhuhr"]


@pytest.mark.parametrize("setting, base, expected_gap", [
    ("maghrib", "sunset", 5),
    ("isha", "maghrib", 90),
])
def test_minute_settings_are_after_base_time(setting, base, expected_gap):
    calc = praytimes.PrayTimes("MWL")
    calc.adjust({setting: "{} min".format(expected_gap)})
    times = calc.getTimes((2026, 5, 15), (21.4225, 39.8262), 3)
    assert minutes(times[setting]) - minutes(times[base]) == expected_gap
