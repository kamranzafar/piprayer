# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prayer times, and the azaans and reminders to play at them."""
import heapq
import logging
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional

import praytimes

log = logging.getLogger("piprayer")


def prayer_times(config, day):
    """Return [(prayer, datetime)] for the configured prayers on the given day, in order.

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
    for prayer in config.prayers:
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
    """Something to play: the azaan for a prayer, or the reminder before it."""
    at: datetime
    prayer: str
    reminder: bool = False
    azaan_at: Optional[datetime] = None  # the time of the azaan, also for its reminder

    def __post_init__(self):
        if self.azaan_at is None:
            self.azaan_at = self.at

    @property
    def key(self):
        """Identifies the event, e.g. to remember it has been played."""
        return self.at, self.prayer, self.reminder

    def __str__(self):
        return "{} for {} at {}".format("Reminder" if self.reminder else "Azaan", self.prayer,
                                        self.at.strftime("%Y-%m-%d %H:%M %Z"))


def events(config, day):
    """Return the events for the given day, in order."""
    result = []
    for prayer, at in prayer_times(config, day):
        if config.reminder:
            result.append(Event(at - timedelta(minutes=config.reminder), prayer, reminder=True, azaan_at=at))
        result.append(Event(at, prayer))
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

    @classmethod
    def of(cls, config, prayer, at, state=None):
        return cls(prayer, at, config.azaan_for(prayer), config.volume_for(prayer),
                   at - timedelta(minutes=config.reminder) if config.reminder else None,
                   state.silenced(at) if state else None)

    def to_json(self):
        return {
            "prayer": self.prayer,
            "time": self.at.isoformat(),
            "azaan": self.azaan,
            "volume": self.volume,
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
