# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prayer times, and the azaans, reminders and suhoor alarms to play at them."""
import heapq
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import praytimes

from .hijri import fasting

log = logging.getLogger("piprayer")


def prayer_times(config, day, prayers=None):
    """Return [(prayer, datetime)] for the configured prayers, or the given ones, on the given day, in order.

    A time after midnight, such as Isha on a summer night far from the equator, is on the next day.
    """
    calc = praytimes.PrayTimes(config.method, config.asr_time)
    calc.adjust({"highLats": config.high_latitude})
    calc.tune(config.offsets)

    # praytimes.py works out the times of a solar day at the longitude. Where clocks are a day off from the sun,
    # e.g. east of the date line on UTC+13, the solar day whose prayers fall on this date is the day before.
    clock_ahead = datetime.combine(day, time(12), config.tz).utcoffset().total_seconds() / 3600 - config.lng / 15
    solar_day = day - timedelta(days=round(clock_ahead / 24))

    # Hours after midnight UTC at the start of the solar day. Unlike the clock times praytimes.py can return, these
    # don't wrap around at 24, and each one gets the UTC offset in force at that moment, daylight saving or not.
    times = calc.getTimes(solar_day, (config.lat, config.lng), 0, format="Float")
    midnight = datetime.combine(solar_day, time(0), timezone.utc)

    result = []
    for prayer in prayers or config.prayers:
        hours = times[prayer]
        if hours == calc.invalidTime:
            log.warning("No %s time on %s at this location", prayer, day)
            continue

        # Rounded to the nearest minute, as praytimes.py rounds clock times
        minutes = math.floor(hours * 60 + 0.5)
        result.append((prayer, (midnight + timedelta(minutes=minutes)).astimezone(config.tz)))

    return sorted(result, key=lambda item: item[1])


def timezone_name(config, day):
    """e.g. "Australia/Melbourne (AEDT)", or "UTC+11:00" for a legacy fixed offset."""
    abbreviation = datetime.combine(day, time(12), config.tz).tzname()
    name = getattr(config.tz, "key", None)
    return "{} ({})".format(name, abbreviation) if name else abbreviation


# ------------------------------------------------------------------ what the service plays

@dataclass
class Event:
    """Something to play: the azaan for a prayer, the reminder before it, or the suhoor alarm before Fajr."""
    at: datetime
    prayer: str
    reminder: bool = False
    azaan_at: Optional[datetime] = None  # the time of the azaan, also for its reminder and the suhoor alarm
    suhoor: bool = False

    def __post_init__(self):
        if self.azaan_at is None:
            self.azaan_at = self.at

    @property
    def key(self):
        """Identifies the event, e.g. to remember it has been played."""
        return self.at, self.prayer, self.reminder, self.suhoor

    def silenced(self, state):
        """Why it won't play: "muted", "skipped", or None if it will.

        Skipping an azaan skips its reminder too. The suhoor alarm is muted with the azaans, but skipping Fajr
        doesn't skip it, as it is for waking up to eat rather than for the prayer.
        """
        if self.suhoor:
            return "muted" if state.mute_until and self.at < state.mute_until else None
        return state.silenced(self.azaan_at)

    def __str__(self):
        if self.suhoor:
            return "Suhoor alarm at " + self.at.strftime("%Y-%m-%d %H:%M %Z")
        return "{} for {} at {}".format("Reminder" if self.reminder else "Azaan", self.prayer,
                                        self.at.strftime("%Y-%m-%d %H:%M %Z"))


def events(config, day):
    """Return the events for the given day, in order."""
    result = []
    for prayer, at in prayer_times(config, day):
        if config.reminder:
            result.append(Event(at - timedelta(minutes=config.reminder), prayer, reminder=True, azaan_at=at))
        result.append(Event(at, prayer))
    fast = fast_times(config, day) if config.suhoor else None
    if fast and fast.suhoor_alarm:
        result.append(Event(fast.suhoor_alarm, "fajr", azaan_at=fast.suhoor_ends, suhoor=True))
    return sorted(result, key=lambda event: event.at)


def next_event(config, after, exclude=()):
    """Return the first event after the given time, leaving out the keys in exclude, or None.

    Yesterday's Isha may still be to come, after midnight.
    """
    upcoming = [event
                for offset in (-1, 0, 1)
                for event in events(config, after.date() + timedelta(days=offset))
                if event.at > after and event.key not in exclude]
    return min(upcoming, key=lambda event: event.at) if upcoming else None


# ------------------------------------------------------------------ what is shown

@dataclass
class Entry:
    """A prayer in the schedule, with what will be played for it."""
    prayer: str
    at: datetime
    azaan: int
    volume: int
    reminder: Optional[datetime]
    silenced: Optional[str] = None  # "skipped" or "muted" when it won't play
    fade_in: int = 0  # seconds

    @classmethod
    def of(cls, config, prayer, at, state=None):
        return cls(prayer, at, config.azaan_for(prayer), config.volume_for(prayer),
                   at - timedelta(minutes=config.reminder) if config.reminder else None,
                   state.silenced(at) if state else None, config.fade_in_for(prayer))

    def to_json(self):
        return {
            "prayer": self.prayer,
            "time": self.at.isoformat(),
            "azaan": self.azaan,
            "volume": self.volume,
            "fade_in": self.fade_in,
            "reminder": self.reminder.isoformat() if self.reminder else None,
            "skipped": self.silenced == "skipped",
            "muted": self.silenced == "muted",
        }


def schedule(config, day, state=None):
    return [Entry.of(config, prayer, at, state) for prayer, at in prayer_times(config, day)]


def upcoming_azaans(config, now, state=None, days=2):
    """The azaans after now, in order, over the given number of days starting today.

    Yesterday's are included too, as its Isha may still be to come after midnight.
    """
    days = [schedule(config, now.date() + timedelta(days=offset), state) for offset in range(-1, days)]
    for entry in heapq.merge(*days, key=lambda entry: entry.at):
        if entry.at > now:
            yield entry


def next_azaan(config, now, state=None):
    """The first azaan after now, today or tomorrow, or None."""
    return next(upcoming_azaans(config, now, state), None)


# ------------------------------------------------------------------ Ramadan

SUHOOR_ENDS = "Suhoor ends"
IFTAR = "Iftar"


@dataclass
class FastTimes:
    """A day of fasting: the suhoor alarm, the end of suhoor at Fajr, and iftar at Maghrib.

    The times are as the azaans', with any offsets in prayers, even when no azaan is played for Fajr or Maghrib.
    """
    day: date
    suhoor_ends: Optional[datetime]  # None where there is no Fajr time, far from the equator
    iftar: Optional[datetime]
    suhoor_alarm: Optional[datetime] = None

    def label(self, prayer):
        """What the prayer's time means for the fast: "Suhoor ends" at Fajr, "Iftar" at Maghrib, otherwise None."""
        return {"fajr": SUHOOR_ENDS, "maghrib": IFTAR}.get(prayer)

    def to_json(self):
        def iso(at):
            return at.isoformat() if at else None

        return {"suhoor_alarm": iso(self.suhoor_alarm), "suhoor_ends": iso(self.suhoor_ends), "iftar": iso(self.iftar)}


def fast_times(config, day):
    """The times of the fast on the given day, or None if it isn't a day of fasting."""
    if not fasting(config, day):
        return None
    times = dict(prayer_times(config, day, ["fajr", "maghrib"]))
    fajr = times.get("fajr")
    return FastTimes(day, fajr, times.get("maghrib"),
                     fajr - timedelta(minutes=config.suhoor) if fajr and config.suhoor else None)


def upcoming_fast(config, now):
    """The next end of suhoor and the next iftar, today or tomorrow, as {SUHOOR_ENDS: at, IFTAR: at}.

    Either is left out when there isn't one, e.g. after Maghrib on the last day of Ramadan.
    """
    result = {}
    for offset in (0, 1):
        fast = fast_times(config, now.date() + timedelta(days=offset))
        if fast:
            for what, at in ((SUHOOR_ENDS, fast.suhoor_ends), (IFTAR, fast.iftar)):
                if at and at > now and what not in result:
                    result[what] = at
    return result


def next_fast_time(config, now):
    """The next end of suhoor or iftar, as ("Suhoor ends" or "Iftar", at), or None."""
    upcoming = upcoming_fast(config, now)
    return min(upcoming.items(), key=lambda item: item[1]) if upcoming else None
