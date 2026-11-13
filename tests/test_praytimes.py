"""Prayer time calculations, checked against an independent implementation."""
import json
import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

import praytimes

DATA = os.path.join(os.path.dirname(__file__), "data")


def load_reference(name):
    """Reference times from AlAdhan, each case with the date of its file."""
    with open(os.path.join(DATA, name)) as f:
        reference = json.load(f)
    return [dict(case, date=reference["date"]) for case in reference["cases"]]


# The original 7 methods, the methods added since with AlAdhan's minute adjustments, and the high latitude rules
REFERENCE = (load_reference("aladhan-2026-05-15.json") + load_reference("aladhan-methods-2026-05-15.json") +
             load_reference("aladhan-high-latitude-2026-06-21.json"))


def minutes(hhmm):
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def case_id(case):
    return "-".join([case["location"], case["method"], case["asr"], case.get("high_latitude", "")]).rstrip("-")


@pytest.mark.parametrize("case", REFERENCE, ids=case_id)
def test_matches_aladhan_reference(case):
    day = date.fromisoformat(case["date"])
    tz = ZoneInfo(case["timezone"])
    utc_offset = datetime.combine(day, time(12), tz).utcoffset().total_seconds() / 3600

    calc = praytimes.PrayTimes(case["method"], case["asr"])
    if "high_latitude" in case:
        calc.adjust({"highLats": case["high_latitude"]})
    times = calc.getTimes(day, (case["lat"], case["lng"]), utc_offset)

    for prayer, expected in case["times"].items():
        # Allow a minute for rounding differences between the implementations. Clock times wrap at midnight
        difference = abs(minutes(times[prayer]) - minutes(expected)) % (24 * 60)
        assert min(difference, 24 * 60 - difference) <= 1, \
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


def test_method_offsets_add_to_tuned_offsets():
    coords, day = (41.0082, 28.9784), (2026, 5, 15)
    plain = praytimes.PrayTimes("Turkey").getTimes(day, coords, 3)

    tuned = praytimes.PrayTimes("Turkey")
    tuned.tune({"asr": 2, "sunrise": 1})
    times = tuned.getTimes(day, coords, 3)

    # Turkey's own +4 minutes for Asr and -7 for sunrise still apply
    assert minutes(times["asr"]) == minutes(plain["asr"]) + 2
    assert minutes(times["sunrise"]) == minutes(plain["sunrise"]) + 1


def test_changing_method_drops_the_previous_adjustments():
    coords, day = (41.0082, 28.9784), (2026, 5, 15)
    calc = praytimes.PrayTimes("Turkey")
    calc.setMethod("MWL")
    assert calc.getTimes(day, coords, 3) == praytimes.PrayTimes("MWL").getTimes(day, coords, 3)


def test_times_as_hours_continue_past_midnight():
    # London on the longest day: with the middle of the night rule, Isha is just after midnight UTC
    calc = praytimes.PrayTimes("MWL")
    times = calc.getTimes((2026, 6, 21), (51.5074, -0.1278), 0, format="Float")
    assert 23.9 < times["isha"] < 24.1
    assert times["isha"] > times["maghrib"]
