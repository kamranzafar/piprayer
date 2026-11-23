# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The times as a table, as text, and as JSON."""
from datetime import datetime, time, timedelta

from .clock import NOT_SYNCHRONISED
from .hijri import hijri_day, hijri_now
from .schedule import fast_times, next_fast_time, prayer_name, schedule, timezone_name
from .state import FAILED, MISSED, PLAYED_LOCALLY

RESUME_HINT = "Run piprayer.py resume to play again."


def describe_day(day, today):
    """e.g. "today", "tomorrow", "yesterday" or "Friday 14 November"."""
    if day == today:
        return "today"
    if day == today - timedelta(days=1):
        return "yesterday"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return "{:%A} {} {:%B}".format(day, day.day, day)


def describe_azaan(entry, now):
    """e.g. "Asr at 18:03 today", or "Jumu'ah at 12:58 today"."""
    return "{} at {} {}".format(prayer_name(entry.prayer, entry.at.date()), entry.at.strftime("%H:%M"),
                                describe_day(entry.at.date(), now.date()))


def describe_last(last, now):
    """How the last azaan went, e.g. "Asr at 18:03 today played", or "... didn't play: <why>"."""
    what = describe_azaan(last, now)
    if last.result == FAILED:
        return "{} didn't play: {}".format(what, last.error or "unknown error")
    if last.result == MISSED:
        return "{} was missed: {}".format(what, last.error or "unknown reason")
    if last.result == PLAYED_LOCALLY:
        return "{} played on the Raspberry Pi's own audio output, as the speaker couldn't be reached".format(what)
    return "{} {}".format(what, last.result if last.result == "played" else "was " + last.result)


def describe_mute(config, mute_until, now):
    """e.g. "Muted until the end of today"."""
    last_day = (mute_until.astimezone(config.tz) - timedelta(seconds=1)).date()
    return "Muted until the end of " + describe_day(last_day, now.date())


def format_duration(delta):
    """Time until something, rounded up to the minute, e.g. "3 h 12 min"."""
    minutes = max(1, -(-int(delta.total_seconds()) // 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return "{} h {} min".format(hours, minutes)
    return "{} h".format(hours) if hours else "{} min".format(minutes)


def describe_kahf(kahf):
    """e.g. "Surah Al-Kahf 11:28-12:56"."""
    return "Surah Al-Kahf {}-{}".format(kahf.at.strftime("%H:%M"), kahf.until.strftime("%H:%M"))


def describe_fast(config, fast):
    """e.g. "Ramadan: suhoor alarm 04:20 · suhoor ends 04:50 · iftar 17:42"."""
    times = [("suhoor alarm", fast.suhoor_alarm), ("suhoor ends", fast.suhoor_ends), ("iftar", fast.iftar)]
    return "{}: {}".format(fast_name(config), " · ".join(
        "{} {}".format(what, at.strftime("%H:%M")) for what, at in times if at))


def fast_name(config):
    """"Ramadan", or "Fasting" when ramadan = on, as it may not be Ramadan."""
    return "Ramadan" if config.ramadan == "auto" else "Fasting"


def describe_next_fast(config, now):
    """e.g. "Iftar at 17:42 today, in 3 h 5 min", or None outside a fast."""
    upcoming = next_fast_time(config, now)
    if upcoming is None:
        return None
    what, at = upcoming
    return "{} at {} {}, in {}".format(what, at.strftime("%H:%M"), describe_day(at.date(), now.date()),
                                       format_duration(at - now))


def format_times(config, day, now, state=None, last=None, synchronised=None):
    """The day's schedule as a table, marking the next azaan and any that won't play, and in Ramadan, the end of
    suhoor and iftar.

    Below it, the times of the fast, how the last azaan went, and a warning when the clock isn't set from the
    network (synchronised False).
    """
    entries = schedule(config, day, state)
    upcoming = next((e for e in entries if e.at > now), None) if day == now.date() else None
    fast = fast_times(config, day)

    header = ["Prayer", "Time", "Azaan", "Volume"] + (["Fade"] if config.fades_in else []) + \
        (["Dua"] if config.plays_dua else []) + \
        (["Reminder"] if config.reminder else [])
    rows = []
    for entry in entries:
        row = [entry.name, entry.at.strftime("%H:%M"), str(entry.azaan), "{}%".format(entry.volume)]
        if config.fades_in:
            row.append("{} s".format(entry.fade_in) if entry.fade_in else "-")
        if config.plays_dua:
            row.append("yes" if entry.dua else "-")
        if entry.reminder:
            row.append(entry.reminder.strftime("%H:%M"))
        notes = (["◀ next"] if entry is upcoming else []) + \
                ([fast.label(entry.prayer).lower()] if fast and fast.label(entry.prayer) else []) + \
                (["khutbah " + entry.khutbah.strftime("%H:%M")] if entry.khutbah else []) + \
                ([describe_kahf(entry.kahf)] if entry.kahf else []) + \
                ([entry.silenced] if entry.silenced else []) + (["after midnight"] if entry.at.date() != day else [])
        rows.append((row, " · ".join(notes)))

    widths = [max([len(heading)] + [len(row[i]) for row, _ in rows]) for i, heading in enumerate(header)]

    def line(cells):
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths))

    hijri = hijri_for(config, day, now)
    lines = [
        "{:%A} {} {:%B %Y}".format(day, day.day, day) + (" · {}".format(hijri) if hijri else ""),
        "{} · {} method · {} Asr".format(timezone_name(config, day), config.method, config.asr_time),
        "",
        line(header).rstrip(),
        "─" * len(line(header)),
    ]
    for row, notes in rows:
        lines.append(line(row) + "  " + notes if notes else line(row).rstrip())
    if not rows:
        lines.append("No prayer times on this day")
    if fast:
        lines += ["", describe_fast(config, fast)]
    if state and state.muted_at(now):
        lines += ["", describe_mute(config, state.mute_until, now) + ". " + RESUME_HINT]
    if last and day == now.date():
        lines += ["", "Last azaan: " + describe_last(last, now)]
    if synchronised is False:
        lines += ["", "Warning: " + NOT_SYNCHRONISED]

    return indent(lines)


def format_next(config, entry, now, state=None, synchronised=None):
    """The next azaan, e.g. "Next azaan: Asr at 18:03 today, in 3 h 12 min"."""
    warning = ["", "Warning: " + NOT_SYNCHRONISED] if synchronised is False else []
    if entry is None:
        return indent(["No azaan in the next two days"] + warning)

    details = "Azaan {} · {}% volume".format(entry.azaan, entry.volume) + (" · then the dua" if entry.dua else "")
    if entry.reminder:
        details += " · reminder at " + entry.reminder.strftime("%H:%M")
    if entry.khutbah:
        details += " · khutbah at " + entry.khutbah.strftime("%H:%M")
    if entry.kahf:
        details += " · " + describe_kahf(entry.kahf)

    lines = [
        "Next azaan: {}, in {}".format(describe_azaan(entry, now), format_duration(entry.at - now)),
        details,
        timezone_name(config, entry.at.date()),
    ]
    fast = describe_next_fast(config, now)
    if fast:
        lines.append(fast)
    if entry.silenced == "muted":
        lines.append(describe_mute(config, state.mute_until, now) + ". " + RESUME_HINT)
    elif entry.silenced == "skipped":
        lines.append("Skipped. Run piprayer.py resume to play it.")
    return indent(lines + warning)


def hijri_for(config, day, now):
    """The Hijri date to show with a day's times: now's for today, which changes at Maghrib."""
    return hijri_now(config, now) if day == now.date() else hijri_day(config, day)


def format_plain(config, day):
    """fajr-04:21 lines."""
    return "\n".join(entry.prayer + "-" + entry.at.strftime("%H:%M") for entry in schedule(config, day))


def indent(lines):
    return "\n".join(" " + line if line else line for line in lines)


def muted_until_json(state, now):
    muted = state.muted_at(now) if state else None
    return muted.isoformat() if muted else None


def times_json(config, day, now, state=None, last=None, synchronised=None, playing=None):
    entries = schedule(config, day, state)
    upcoming = next((e for e in entries if e.at > now), None) if day == now.date() else None
    hijri = hijri_for(config, day, now)
    fast = fast_times(config, day)
    return {
        "date": day.isoformat(),
        "hijri": hijri.to_json() if hijri else None,
        "ramadan": fast.to_json() if fast else None,
        "timezone": getattr(config.tz, "key", None) or datetime.combine(day, time(12), config.tz).tzname(),
        "method": config.method,
        "asr": config.asr_time,
        "next": upcoming.prayer if upcoming else None,
        "muted_until": muted_until_json(state, now),
        "prayers": [entry.to_json() for entry in entries],
        "last": last.to_json() if last else None,
        "clock_synchronised": synchronised,
        "playing": playing.to_json() if playing else None,
    }


def next_json(config, entry, now, state=None):
    if entry is None:
        return None
    result = entry.to_json()
    result["timezone"] = getattr(config.tz, "key", None) or entry.at.tzname()
    result["seconds_until"] = int((entry.at - now).total_seconds())
    result["muted_until"] = muted_until_json(state, now)
    return result
