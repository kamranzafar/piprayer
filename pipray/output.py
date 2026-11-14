# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The times as a table, as text, and as JSON."""
from datetime import datetime, time, timedelta

from .clock import NOT_SYNCHRONISED
from .schedule import schedule, timezone_name
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
    """e.g. "Asr at 18:03 today"."""
    return "{} at {} {}".format(entry.prayer.capitalize(), entry.at.strftime("%H:%M"),
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


def format_times(config, day, now, state=None, last=None, synchronised=None):
    """The day's schedule as a table, marking the next azaan and any that won't play.

    Below it, how the last azaan went, and a warning when the clock isn't set from the network (synchronised False).
    """
    entries = schedule(config, day, state)
    upcoming = next((e for e in entries if e.at > now), None) if day == now.date() else None

    header = ["Prayer", "Time", "Azaan", "Volume"] + (["Reminder"] if config.reminder else [])
    rows = []
    for entry in entries:
        row = [entry.prayer.capitalize(), entry.at.strftime("%H:%M"), str(entry.azaan), "{}%".format(entry.volume)]
        if entry.reminder:
            row.append(entry.reminder.strftime("%H:%M"))
        notes = (["◀ next"] if entry is upcoming else []) + ([entry.silenced] if entry.silenced else []) + \
                (["after midnight"] if entry.at.date() != day else [])
        rows.append((row, " · ".join(notes)))

    widths = [max([len(heading)] + [len(row[i]) for row, _ in rows]) for i, heading in enumerate(header)]

    def line(cells):
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths))

    lines = [
        "{:%A} {} {:%B %Y}".format(day, day.day, day),
        "{} · {} method · {} Asr".format(timezone_name(config, day), config.method, config.asr_time),
        "",
        line(header).rstrip(),
        "─" * len(line(header)),
    ]
    for row, notes in rows:
        lines.append(line(row) + "  " + notes if notes else line(row).rstrip())
    if not rows:
        lines.append("No prayer times on this day")
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

    details = "Azaan {} · {}% volume".format(entry.azaan, entry.volume)
    if entry.reminder:
        details += " · reminder at " + entry.reminder.strftime("%H:%M")

    lines = [
        "Next azaan: {}, in {}".format(describe_azaan(entry, now), format_duration(entry.at - now)),
        details,
        timezone_name(config, entry.at.date()),
    ]
    if entry.silenced == "muted":
        lines.append(describe_mute(config, state.mute_until, now) + ". " + RESUME_HINT)
    elif entry.silenced == "skipped":
        lines.append("Skipped. Run piprayer.py resume to play it.")
    return indent(lines + warning)


def format_plain(config, day):
    """fajr-04:21 lines."""
    return "\n".join(entry.prayer + "-" + entry.at.strftime("%H:%M") for entry in schedule(config, day))


def indent(lines):
    return "\n".join(" " + line if line else line for line in lines)


def muted_until_json(state, now):
    muted = state.muted_at(now) if state else None
    return muted.isoformat() if muted else None


def times_json(config, day, now, state=None, last=None, synchronised=None):
    entries = schedule(config, day, state)
    upcoming = next((e for e in entries if e.at > now), None) if day == now.date() else None
    return {
        "date": day.isoformat(),
        "timezone": getattr(config.tz, "key", None) or datetime.combine(day, time(12), config.tz).tzname(),
        "method": config.method,
        "asr": config.asr_time,
        "next": upcoming.prayer if upcoming else None,
        "muted_until": muted_until_json(state, now),
        "prayers": [entry.to_json() for entry in entries],
        "last": last.to_json() if last else None,
        "clock_synchronised": synchronised,
    }


def next_json(config, entry, now, state=None):
    if entry is None:
        return None
    result = entry.to_json()
    result["timezone"] = getattr(config.tz, "key", None) or entry.at.tzname()
    result["seconds_until"] = int((entry.at - now).total_seconds())
    result["muted_until"] = muted_until_json(state, now)
    return result
