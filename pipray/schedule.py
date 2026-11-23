# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prayer times, and the azaans, reminders, suhoor alarms and Surah Al-Kahf to play at them."""
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

# The kinds of event
AZAAN = "azaan"
REMINDER = "reminder"
SUHOOR = "suhoor"
KAHF = "kahf"

# Which plays first when two are due at the same time: the azaan, then the suhoor alarm, the reminder and Al-Kahf. A
# reminder due at the same time as another sound isn't played
PRIORITY = {AZAAN: 0, SUHOOR: 1, REMINDER: 2, KAHF: 3}

FRIDAY = 4  # as date.weekday() gives it

# Surah Al-Kahf stops this long before whatever plays next, so the speaker is free to connect for it
KAHF_MARGIN = timedelta(minutes=2)
# Al-Kahf isn't started with less time than this to play before it has to stop
MIN_KAHF = timedelta(minutes=1)


@dataclass
class Event:
    """Something to play: the azaan for a prayer, the reminder before it, the suhoor alarm before Fajr, or Surah
    Al-Kahf before Jumu'ah."""
    at: datetime
    prayer: str
    kind: str = AZAAN
    azaan_at: Optional[datetime] = None  # the time of the azaan, also for its reminder, the suhoor alarm and Al-Kahf
    until: Optional[datetime] = None  # when it has to stop, so the next event plays on time; None to play to the end

    def __post_init__(self):
        if self.azaan_at is None:
            self.azaan_at = self.at

    @property
    def key(self):
        """Identifies the event, e.g. to remember it has been played."""
        return self.at, self.prayer, self.kind

    def silenced(self, state):
        """Why it won't play: "muted", "skipped", or None if it will.

        Skipping an azaan skips its reminder too. The suhoor alarm and Al-Kahf are muted with the azaans, but
        skipping Fajr or Dhuhr doesn't skip them: the suhoor alarm is for waking up to eat, and Al-Kahf is for the day,
        rather than for the prayer.
        """
        if self.kind in (SUHOOR, KAHF):
            return "muted" if state.mute_until and self.at < state.mute_until else None
        return state.silenced(self.azaan_at)

    def __str__(self):
        at = self.at.strftime("%Y-%m-%d %H:%M %Z")
        if self.kind == SUHOOR:
            return "Suhoor alarm at " + at
        if self.kind == KAHF:
            return "Surah Al-Kahf at {}, until {}".format(at, self.until.strftime("%H:%M")) if self.until else \
                "Surah Al-Kahf at " + at
        return "{} for {} at {}".format("Reminder" if self.kind == REMINDER else "Azaan", self.prayer, at)


def events(config, day):
    """Return the events for the given day, in order."""
    result = []
    for prayer, at in prayer_times(config, day):
        if config.reminder:
            result.append(Event(at - timedelta(minutes=config.reminder), prayer, REMINDER, azaan_at=at))
        result.append(Event(at, prayer))
    fast = fast_times(config, day) if config.suhoor else None
    if fast and fast.suhoor_alarm:
        result.append(Event(fast.suhoor_alarm, "fajr", SUHOOR, azaan_at=fast.suhoor_ends))
    kahf = kahf_event(config, day, result)
    if kahf:
        result.append(kahf)
    return resolve(result)


def resolve(due):
    """The events in the order they play, with a reminder left out when another sound is due at the same time.

    Two sounds at the same time play one after the other, by PRIORITY. A reminder after another sound would come late,
    and the sound it is with does as well to remind.
    """
    result = []
    for event in sorted(due, key=lambda event: (event.at, PRIORITY[event.kind])):
        if event.kind == REMINDER and result and result[-1].at == event.at:
            continue
        result.append(event)
    return result


def next_event(config, after, exclude=()):
    """Return the first event after the given time, leaving out the keys in exclude, or None.

    Yesterday's Isha may still be to come, after midnight.
    """
    upcoming = resolve([event
                        for offset in (-1, 0, 1)
                        for event in events(config, after.date() + timedelta(days=offset))
                        if event.at > after])
    return next((event for event in upcoming if event.key not in exclude), None)


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
    dua: bool = False  # the dua plays after the azaan
    kahf: Optional[Event] = None  # Surah Al-Kahf before Jumu'ah
    khutbah: Optional[datetime] = None  # on Fridays with jummah-time, the time of the khutbah

    @classmethod
    def of(cls, config, prayer, at, state=None):
        jummah = is_jummah(prayer, at.date())
        return cls(prayer, at, config.azaan_for(prayer), config.volume_for(prayer),
                   at - timedelta(minutes=config.reminder) if config.reminder else None,
                   state.silenced(at) if state else None, config.fade_in_for(prayer), config.dua_for(prayer),
                   kahf_event(config, at.date()) if jummah else None,
                   jummah_time(config, at.date()) if jummah and config.jummah_time else None)

    @property
    def name(self):
        """e.g. "Asr", or "Jumu'ah" for Dhuhr on a Friday."""
        return prayer_name(self.prayer, self.at.date())

    def to_json(self):
        return {
            "prayer": self.prayer,
            "time": self.at.isoformat(),
            "azaan": self.azaan,
            "volume": self.volume,
            "fade_in": self.fade_in,
            "dua": self.dua,
            "reminder": self.reminder.isoformat() if self.reminder else None,
            "jummah": is_jummah(self.prayer, self.at.date()),
            "kahf": {"start": self.kahf.at.isoformat(), "until": self.kahf.until.isoformat()} if self.kahf else None,
            "khutbah": self.khutbah.isoformat() if self.khutbah else None,
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


# ------------------------------------------------------------------ Jumu'ah

JUMMAH = "Jumu'ah"


def is_jummah(prayer, day):
    """Whether the prayer is Jumu'ah: Dhuhr on a Friday."""
    return prayer == "dhuhr" and day.weekday() == FRIDAY


def prayer_name(prayer, day):
    """The prayer's name to show, e.g. "Asr", and "Jumu'ah" for Dhuhr on a Friday."""
    return JUMMAH if is_jummah(prayer, day) else prayer.capitalize()


def jummah_time(config, day):
    """The time of Jumu'ah on the given day, or None if it isn't a Friday: the khutbah at jummah-time, or without it,
    the calculated Dhuhr with its offset."""
    if day.weekday() != FRIDAY:
        return None
    if config.jummah_time:
        return datetime.combine(day, config.jummah_time, config.tz)
    times = prayer_times(config, day, ["dhuhr"])
    return times[0][1] if times else None


def upcoming_jummah(config, now):
    """The next Jumu'ah, this Friday's until it is over, or None where there is no Dhuhr time."""
    friday = now.date() + timedelta(days=(FRIDAY - now.date().weekday()) % 7)
    for day in (friday, friday + timedelta(days=7)):
        at = jummah_time(config, day)
        if at and at > now:
            return at
    return None


def kahf_event(config, day, others=None):
    """Surah Al-Kahf before Jumu'ah, as an Event, or None when it doesn't play on the given day.

    It plays kahf minutes before Jumu'ah, and has to stop KAHF_MARGIN before the next of the other events, e.g. the
    reminder or azaan for Dhuhr, and before Jumu'ah, so they always play on time. others are the day's other events,
    worked out from the configuration when not given.
    """
    if not config.kahf:
        return None
    jummah = jummah_time(config, day)
    if jummah is None:
        return None
    start = jummah - timedelta(minutes=config.kahf)
    if others is None:
        others = [event for event in events(config, day) if event.kind != KAHF]
    # Something else playing at the same time, e.g. the reminder 60 minutes before, leaves it no time
    until = min([event.at for event in others if event.at >= start] + [jummah]) - KAHF_MARGIN
    if until - start < MIN_KAHF:
        return None  # the configuration warns about it
    return Event(start, "dhuhr", KAHF, azaan_at=jummah, until=until)


def upcoming_kahf(config, now):
    """The next Surah Al-Kahf, as an Event, including one playing now, or None when kahf is off."""
    if not config.kahf:
        return None
    friday = now.date() + timedelta(days=(FRIDAY - now.date().weekday()) % 7)
    for day in (friday, friday + timedelta(days=7)):  # this Friday's may be over
        kahf = kahf_event(config, day)
        if kahf and kahf.until > now:
            return kahf
    return None


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
