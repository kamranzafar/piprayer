"""The service loop, run against a simulated clock so a day takes milliseconds."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MELBOURNE = ZoneInfo("Australia/Melbourne")


def played(clock):
    return [(at.strftime("%a %H:%M %Z"), sound) for at, sound, _ in clock.played]


def test_plays_every_prayer_through_the_day(write_config, fake_clock):
    clock = fake_clock(datetime(2026, 11, 12, 3, 0), MELBOURNE)
    clock.run_service(write_config(), hours=26, azaan=7)

    assert played(clock) == [
        ("Thu 04:23 AEDT", "7"),
        ("Thu 13:06 AEDT", "7"),
        ("Thu 18:03 AEDT", "7"),
        ("Thu 20:02 AEDT", "7"),
        ("Thu 21:43 AEDT", "7"),
        ("Fri 04:22 AEDT", "7"),
    ]


def test_starting_mid_day_waits_for_next_prayer(write_config, fake_clock):
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    clock.run_service(write_config(), hours=4)
    assert played(clock) == [("Thu 18:03 AEDT", "1")]


def test_daylight_saving_change_overnight(write_config, fake_clock):
    # Clocks go forward at 2am on Sunday 4 October 2026, between Isha and Fajr
    clock = fake_clock(datetime(2026, 10, 3, 21, 0), MELBOURNE)
    clock.run_service(write_config(), hours=10)

    assert played(clock) == [("Sun 05:23 AEDT", "1")]


def test_daylight_saving_end_overnight(write_config, fake_clock):
    # Clocks go back at 3am on Sunday 5 April 2026
    clock = fake_clock(datetime(2026, 4, 4, 22, 0), MELBOURNE)
    clock.run_service(write_config(), hours=9)

    # Saturday's Fajr was 06:09 AEDT
    assert played(clock) == [("Sun 05:10 AEST", "1")]


def test_config_change_is_picked_up(write_config, fake_clock):
    path = write_config()
    clock = fake_clock(datetime(2026, 11, 12, 20, 30), MELBOURNE)

    def edit_isha_offset():
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace("Isha:19", "Isha:30"))
        later = clock.now.timestamp() + 10  # make sure the modification time changes
        os.utime(path, (later, later))

    clock.at(datetime(2026, 11, 12, 21, 0), edit_isha_offset)
    clock.run_service(path, hours=2)

    assert played(clock) == [("Thu 21:54 AEDT", "1")]


def test_late_azaan_is_skipped_after_clock_jump(write_config, fake_clock):
    clock = fake_clock(datetime(2026, 11, 12, 3, 0), MELBOURNE)

    def jump_forward():
        # e.g. the clock is set from the network after booting
        clock.now += timedelta(hours=3)

    clock.at(datetime(2026, 11, 12, 3, 30), jump_forward)
    clock.run_service(write_config(), hours=11)

    # Fajr at 04:23 is noticed at 06:30 and skipped, Dhuhr plays normally
    assert played(clock) == [("Thu 13:06 AEDT", "1")]


def test_azaan_noticed_slightly_late_still_plays(write_config, fake_clock):
    clock = fake_clock(datetime(2026, 11, 12, 4, 0), MELBOURNE)

    def jump_forward():
        clock.now += timedelta(minutes=25)  # lands at 04:26, 3 minutes late

    clock.at(datetime(2026, 11, 12, 4, 1), jump_forward)
    clock.run_service(write_config(), hours=1)

    assert [sound for _, sound, _ in clock.played] == ["1"]
