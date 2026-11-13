# SPDX-License-Identifier: GPL-3.0-or-later
"""Prayer times, and the azaans and reminders to play at them."""
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

import praytimes

log = logging.getLogger("piprayer")


def prayer_times(config, day):
    """Return [(prayer, datetime)] for the configured prayers on the given day, in order."""
    calc = praytimes.PrayTimes(config.method, config.asr_time)
    calc.tune(config.offsets)

    # UTC offset for that day, measured at midday so a DST change in the early hours is applied
    utc_offset = datetime.combine(day, time(12), config.tz).utcoffset().total_seconds() / 3600
    times = calc.getTimes(day, (config.lat, config.lng), utc_offset)

    result = []
    for prayer in config.prayers:
        value = times[prayer]
        if value == calc.invalidTime:
            log.warning("No %s time on %s at this location", prayer, day)
            continue

        hour, minute = (int(v) for v in value.split(":"))
        result.append((prayer, datetime.combine(day, time(hour, minute), config.tz)))

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


def next_event(config, now):
    """Return the first event after now, or None."""
    upcoming = [event
                for day in (now.date(), now.date() + timedelta(days=1))
                for event in events(config, day)
                if event.at > now]
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
    """The azaans after now, over the given number of days starting today."""
    for offset in range(days):
        for entry in schedule(config, now.date() + timedelta(days=offset), state):
            if entry.at > now:
                yield entry


def next_azaan(config, now, state=None):
    """The first azaan after now, today or tomorrow, or None."""
    return next(upcoming_azaans(config, now, state), None)
