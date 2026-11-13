"""
    PiPrayer - Azaan for Raspberry Pi
    Copyright (C) 2021  Kamran Zafar

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import argparse
import configparser
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time as systime
from html import escape
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, time, timedelta, timezone

import praytimes

LAT = "lat"
LNG = "lng"
TIMEZONE = "timezone"
DST = "dst"
GMT_OFFSET = "gmt-offset"
METHOD = "method"
ASR_TIME = "asr-time"
PRAYERS = "prayers"
AZAAN = "azaan"
FAJR_AZAAN = "fajr-azaan"
VOLUME = "volume"
REMINDER = "reminder"
SPEAKER = "speaker"
STATUS_PORT = "status-port"

# Longest mute, in days
MAX_MUTE_DAYS = 30

MAC_ADDRESS = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, ".piprayer")
PLAYER = os.path.join(SCRIPT_DIR, "play-azaan.sh")
MEDIA_DIR = os.path.join(SCRIPT_DIR, "media")
REMINDER_SOUND = os.path.join(MEDIA_DIR, "reminder.wav")

DEFAULT_AZAAN = 1
DEFAULT_VOLUME = 100

# Skip an azaan if we only notice its time after this long, e.g. when the
# clock jumps forward after boot (the Pi has no hardware clock)
MAX_LATENESS = timedelta(minutes=5)

# Longest single sleep while waiting, so clock changes and config edits are noticed
MAX_SLEEP_SECONDS = 60

log = logging.getLogger("piprayer")


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, path):
        parser = configparser.ConfigParser()

        if not parser.read(path):
            raise ConfigError("File not found or is inaccessible: " + path)

        if not parser.has_section("Default"):
            raise ConfigError("Prayer configuration is missing")

        section = parser["Default"]

        try:
            self.lat = float(section[LAT])
            self.lng = float(section[LNG])
            self.method = section[METHOD]
            self.asr_time = section[ASR_TIME]
            self.prayers, self.offsets = parse_prayers(section[PRAYERS])
        except KeyError as e:
            raise ConfigError("Missing configuration option: " + e.args[0])
        except ValueError as e:
            raise ConfigError("Invalid configuration value: " + str(e))

        self.tz = parse_timezone(section)

        # Optional settings
        self.azaan = parse_azaan(section, AZAAN)
        self.fajr_azaan = parse_azaan(section, FAJR_AZAAN)
        self.volume, self.volumes = parse_volume(section.get(VOLUME, ""))
        self.reminder = parse_number(section, REMINDER, 0, 0, 60)
        self.speaker = parse_speaker(section.get(SPEAKER, ""))
        self.status_port = parse_number(section, STATUS_PORT, None, 1, 65535)

    def azaan_for(self, prayer, default=DEFAULT_AZAAN):
        """The azaan to play for a prayer. default is used when the configuration doesn't set one."""
        if prayer == "fajr" and self.fajr_azaan:
            return self.fajr_azaan
        return self.azaan or default

    def volume_for(self, prayer):
        return self.volumes.get(prayer, self.volume)


def parse_prayers(value):
    """Parse "Fajr:-11, Dhuhr, ..." into prayer names and offsets in minutes (default 0)."""
    offsets = {}
    prayers = []

    for name, offset in parse_list(value):
        try:
            offsets[name] = int(offset) if offset else 0
        except ValueError:
            raise ConfigError("Invalid offset for " + name.capitalize() + ": " + offset)
        prayers.append(name)

    return prayers, offsets


def parse_volume(value):
    """Parse "80, Fajr:40" into a default volume and per-prayer volumes, in percent."""
    default = DEFAULT_VOLUME
    volumes = {}

    for name, volume in parse_list(value, allow_bare_value=True):
        what = "volume" if name is None else name.capitalize() + " volume"
        if name is not None and not volume:
            raise ConfigError("Missing " + what + ", e.g. " + name.capitalize() + ":50")
        number = check_range(volume, what, 0, 100)
        if name is None:
            default = number
        else:
            volumes[name] = number

    return default, volumes


def parse_list(value, allow_bare_value=False):
    """Split "Name:value, ..." into (prayer, value) pairs.

    With allow_bare_value, an item that isn't a prayer name, e.g. "80", is returned as (None, "80").
    """
    result = []

    for item in value.split(","):
        if not item.strip():
            continue

        name, colon, setting = item.partition(":")
        name = name.strip()
        prayer = name.lower()

        if allow_bare_value and not colon and prayer not in praytimes.PrayTimes.timeNames:
            result.append((None, name))
            continue

        if prayer not in praytimes.PrayTimes.timeNames:
            raise ConfigError("Unknown prayer: " + name)
        result.append((prayer, setting.strip()))

    return result


def parse_azaan(section, option):
    if option not in section:
        return None

    azaan = parse_number(section, option, None, 1, 99)
    if not os.path.isfile(azaan_file(azaan)):
        raise ConfigError("Invalid {}: {}, there is no {}".format(
            option, azaan, os.path.relpath(azaan_file(azaan), SCRIPT_DIR)))
    return azaan


def parse_speaker(value):
    """A Bluetooth MAC address, or None to use any paired speaker."""
    value = value.strip().upper()
    if not value:
        return None
    if not MAC_ADDRESS.match(value):
        raise ConfigError("Invalid speaker: {}, use the Bluetooth address, e.g. AA:BB:CC:DD:EE:FF".format(value))
    return value


def parse_number(section, option, default, minimum, maximum):
    if option not in section:
        return default
    return check_range(section[option], option, minimum, maximum)


def check_range(value, what, minimum, maximum):
    try:
        number = int(value)
    except ValueError:
        raise ConfigError("Invalid {}: {}, use a whole number from {} to {}".format(what, value, minimum, maximum))
    if not minimum <= number <= maximum:
        raise ConfigError("Invalid {}: {}, use a number from {} to {}".format(what, value, minimum, maximum))
    return number


def azaan_file(azaan):
    return os.path.join(MEDIA_DIR, "azaan-{}.opus".format(azaan))


def parse_timezone(section):
    if TIMEZONE in section:
        # IANA timezone, daylight saving is applied automatically
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            return ZoneInfo(section[TIMEZONE].strip())
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError("Unknown timezone: " + section[TIMEZONE])

    # Legacy configuration: fixed offset and manual daylight saving flag
    try:
        hours = float(section[GMT_OFFSET]) + (1 if int(section.get(DST, "0")) else 0)
    except KeyError:
        raise ConfigError("Missing configuration option: timezone")
    except ValueError as e:
        raise ConfigError("Invalid configuration value: " + str(e))

    return timezone(timedelta(hours=hours))


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


class Event:
    """Something to play: the azaan for a prayer, or the reminder before it."""

    def __init__(self, at, prayer, reminder=False, azaan_at=None):
        self.at = at
        self.prayer = prayer
        self.reminder = reminder
        self.azaan_at = azaan_at or at  # the time of the azaan, also for its reminder

    def __repr__(self):
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


def config_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def play(sound, volume=DEFAULT_VOLUME, description=None, speaker=None):
    """Play an azaan (by number) or a sound file through play-azaan.sh, on the given speaker or any paired one."""
    log.info("Playing %s at %d%% volume%s", description or "azaan " + str(sound), volume,
             " on " + speaker if speaker else "")
    result = subprocess.run([PLAYER, str(sound), str(volume)] + ([speaker] if speaker else []))
    if result.returncode != 0:
        log.error("%s failed with exit code %d", os.path.basename(PLAYER), result.returncode)
    return result.returncode


def play_event(config, event, default_azaan):
    volume = config.volume_for(event.prayer)
    if event.reminder:
        return play(REMINDER_SOUND, volume, "reminder for " + event.prayer, config.speaker)
    azaan = config.azaan_for(event.prayer, default_azaan)
    return play(azaan, volume, "azaan {} for {}".format(azaan, event.prayer), config.speaker)


def run(config_path, default_azaan):
    """Wait for each event and play it. Runs until stopped."""
    log.info("PiPrayer started with configuration %s", config_path)
    state_path = state_file(config_path)
    status_page = StatusPage(config_path, default_azaan)

    try:
        while True:
            mtime = config_mtime(config_path)
            config = Config(config_path)
            status_page.serve(config.status_port)
            tidy_state(state_path, datetime.now(config.tz))
            if not wait_and_play(config_path, config, mtime, state_path, default_azaan):
                log.info("Configuration changed, reloading")
    finally:
        status_page.serve(None)


def wait_and_play(config_path, config, mtime, state_path, default_azaan):
    """Wait for the next event and play it. Returns False if the configuration changed while waiting."""
    event = next_event(config, datetime.now(config.tz))

    if event is None:
        log.warning("No prayer times found for today or tomorrow, checking again in an hour")
        systime.sleep(3600)
        return True

    log.info("Next: %s", event)

    while True:
        remaining = (event.at - datetime.now(config.tz)).total_seconds()
        if remaining <= 0:
            break
        if config_mtime(config_path) != mtime:
            return False
        systime.sleep(min(remaining, MAX_SLEEP_SECONDS))

    late = datetime.now(config.tz) - event.at
    if late > MAX_LATENESS:
        log.warning("Skipping %s, noticed %d minutes late (clock changed?)",
                    event, late.total_seconds() // 60)
        return True

    # Read just before playing, so skip and mute apply however recently they were set
    silenced = State.load(state_path).silenced(event.azaan_at)
    if silenced:
        log.info("Not playing %s, it is %s", event, silenced)
        return True

    play_event(config, event, default_azaan)
    return True


def test_play(args):
    """Play what the service would play, using the configuration if there is one."""
    config = Config(args.config) if os.path.exists(args.config) else None
    prayer = args.prayer.lower() if args.prayer else None

    volume = config.volume_for(prayer) if config else DEFAULT_VOLUME
    speaker = config.speaker if config else None
    if args.reminder:
        return play(REMINDER_SOUND, volume, "reminder", speaker)

    if args.azaan:
        azaan = args.azaan
    else:
        azaan = config.azaan_for(prayer) if config else DEFAULT_AZAAN
    return play(azaan, volume, speaker=speaker)


def service_file():
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "systemd", "user", "piprayer.service")


def installed_azaan():
    """The azaan the installed service was set up with, or the default."""
    try:
        with open(service_file()) as f:
            for line in f:
                if line.startswith("ExecStart=") and "--azaan" in line:
                    return int(line.split("--azaan")[1].split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return DEFAULT_AZAAN


def timezone_name(config, day):
    abbreviation = datetime.combine(day, time(12), config.tz).tzname()
    name = getattr(config.tz, "key", None)
    return "{} ({})".format(name, abbreviation) if name else abbreviation


class Entry:
    """A prayer in the schedule, with what will be played for it."""

    def __init__(self, config, prayer, at, default_azaan, state=None):
        self.prayer = prayer
        self.at = at
        self.azaan = config.azaan_for(prayer, default_azaan)
        self.volume = config.volume_for(prayer)
        self.reminder = at - timedelta(minutes=config.reminder) if config.reminder else None
        # "skipped" or "muted" when it won't play
        self.silenced = state.silenced(at) if state else None

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


def schedule(config, day, default_azaan, state=None):
    return [Entry(config, prayer, at, default_azaan, state) for prayer, at in prayer_times(config, day)]


def upcoming_azaans(config, now, default_azaan, state=None, days=2):
    """The azaans after now, over the given number of days starting today."""
    for offset in range(days):
        for entry in schedule(config, now.date() + timedelta(days=offset), default_azaan, state):
            if entry.at > now:
                yield entry


def next_azaan(config, now, default_azaan, state=None):
    """The first azaan after now, today or tomorrow, or None."""
    return next(upcoming_azaans(config, now, default_azaan, state), None)


# ------------------------------------------------------------------ skip and mute

def state_file(config_path):
    """Skip and mute are saved next to the configuration, e.g. .piprayer-state, so they survive restarts."""
    return config_path + "-state"


class State:
    """Azaans to skip, and the time until which every azaan is muted."""

    def __init__(self, skip=(), mute_until=None):
        self.skip = sorted(skip)
        self.mute_until = mute_until

    @classmethod
    def load(cls, path):
        try:
            with open(path) as f:
                data = json.load(f)
            mute_until = data.get("mute_until")
            return cls([datetime.fromisoformat(at) for at in data.get("skip", [])],
                       datetime.fromisoformat(mute_until) if mute_until else None)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log.warning("Ignoring %s, it can't be read: %s", path, e)
            return cls()

    def save(self, path):
        """Write the state, or remove the file when nothing is skipped or muted."""
        if not self.skip and not self.mute_until:
            if os.path.exists(path):
                os.remove(path)
            return
        data = {
            "skip": [at.isoformat() for at in sorted(self.skip)],
            "mute_until": self.mute_until.isoformat() if self.mute_until else None,
        }
        # Write then rename, so the service never reads a half written file
        with open(path + ".new", "w") as f:
            json.dump(data, f, indent=2)
        os.replace(path + ".new", path)

    def silenced(self, azaan_at):
        """Why the azaan at this time won't play: "muted", "skipped", or None if it will."""
        if self.mute_until and azaan_at < self.mute_until:
            return "muted"
        if azaan_at in self.skip:
            return "skipped"
        return None

    def tidy(self, now):
        """Forget skips and mutes that are over. Returns True if anything changed."""
        skip = [at for at in self.skip if at > now]
        mute_until = self.mute_until if self.mute_until and self.mute_until > now else None
        changed = skip != self.skip or mute_until != self.mute_until
        self.skip, self.mute_until = skip, mute_until
        return changed


def tidy_state(path, now):
    state = State.load(path)
    if state.tidy(now):
        state.save(path)


def next_playing(config, now, default_azaan, state):
    """The next azaan that isn't skipped or muted, or None."""
    days = (state.mute_until.date() - now.date()).days + 2 if state.mute_until else 2
    return next((e for e in upcoming_azaans(config, now, default_azaan, state, days) if not e.silenced), None)


def skip_next(config, path, now, default_azaan):
    """Skip the next azaan that would play, with its reminder. Returns its Entry, or None if there is none."""
    state = State.load(path)
    state.tidy(now)
    entry = next_playing(config, now, default_azaan, state)
    if entry:
        state.skip.append(entry.at)
        state.save(path)
    return entry


def mute(config, path, now, last_day):
    """Mute every azaan and reminder up to the end of last_day. Returns when the mute ends."""
    state = State.load(path)
    state.tidy(now)
    state.mute_until = datetime.combine(last_day + timedelta(days=1), time(0), config.tz)
    state.save(path)
    return state.mute_until


def resume(path):
    """Clear every skip and mute."""
    State().save(path)


def describe_day(day, today):
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return "{:%A} {} {:%B}".format(day, day.day, day)


def describe_azaan(entry, now):
    """e.g. "Asr at 18:03 today"."""
    return "{} at {} {}".format(entry.prayer.capitalize(), entry.at.strftime("%H:%M"),
                                describe_day(entry.at.date(), now.date()))


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


def format_times(config, day, now, default_azaan, state=None):
    """The day's schedule as a table, marking the next azaan and any that won't play."""
    entries = schedule(config, day, default_azaan, state)
    upcoming = next((e for e in entries if e.at > now), None) if day == now.date() else None

    header = ["Prayer", "Time", "Azaan", "Volume"] + (["Reminder"] if config.reminder else [])
    rows = []
    for entry in entries:
        row = [entry.prayer.capitalize(), entry.at.strftime("%H:%M"), str(entry.azaan), "{}%".format(entry.volume)]
        if entry.reminder:
            row.append(entry.reminder.strftime("%H:%M"))
        notes = (["◀ next"] if entry is upcoming else []) + ([entry.silenced] if entry.silenced else [])
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
    if state and state.mute_until and state.mute_until > now:
        lines += ["", describe_mute(config, state.mute_until, now) + ". Run piprayer.py resume to play again."]

    return indent(lines)


def format_next(config, entry, now, state=None):
    """The next azaan, e.g. "Next azaan: Asr at 18:03 today, in 3 h 12 min"."""
    if entry is None:
        return indent(["No azaan in the next two days"])

    details = "Azaan {} · {}% volume".format(entry.azaan, entry.volume)
    if entry.reminder:
        details += " · reminder at " + entry.reminder.strftime("%H:%M")

    lines = [
        "Next azaan: {}, in {}".format(describe_azaan(entry, now), format_duration(entry.at - now)),
        details,
        timezone_name(config, entry.at.date()),
    ]
    if entry.silenced == "muted":
        lines.append(describe_mute(config, state.mute_until, now) + ". Run piprayer.py resume to play again.")
    elif entry.silenced == "skipped":
        lines.append("Skipped. Run piprayer.py resume to play it.")
    return indent(lines)


def indent(lines):
    return "\n".join(" " + line if line else line for line in lines)


def muted_until_json(state, now):
    return state.mute_until.isoformat() if state and state.mute_until and state.mute_until > now else None


def times_json(config, day, now, default_azaan, state=None):
    entries = schedule(config, day, default_azaan, state)
    upcoming = next((e for e in entries if e.at > now), None) if day == now.date() else None
    return {
        "date": day.isoformat(),
        "timezone": getattr(config.tz, "key", None) or datetime.combine(day, time(12), config.tz).tzname(),
        "method": config.method,
        "asr": config.asr_time,
        "next": upcoming.prayer if upcoming else None,
        "muted_until": muted_until_json(state, now),
        "prayers": [entry.to_json() for entry in entries],
    }


def next_json(config, entry, now, state=None):
    if entry is None:
        return None
    result = entry.to_json()
    result["timezone"] = getattr(config.tz, "key", None) or entry.at.tzname()
    result["seconds_until"] = int((entry.at - now).total_seconds())
    result["muted_until"] = muted_until_json(state, now)
    return result


def show_times(args):
    config = Config(args.config)
    now = datetime.now(config.tz)
    default_azaan = args.azaan or installed_azaan()
    state = State.load(state_file(args.config))

    if args.which == "next":
        if args.date:
            raise ConfigError("--date can't be used with next")
        entry = next_azaan(config, now, default_azaan, state)
        if args.format == "json":
            print(json.dumps(next_json(config, entry, now, state), indent=2))
        elif args.format == "plain":
            if entry:
                print(entry.prayer + "-" + entry.at.strftime("%H:%M"))
        else:
            print(format_next(config, entry, now, state))
        return 0

    day = args.date or now.date()
    if args.format == "json":
        print(json.dumps(times_json(config, day, now, default_azaan, state), indent=2))
    elif args.format == "plain":
        for prayer, at in prayer_times(config, day):
            print(prayer + "-" + at.strftime("%H:%M"))
    else:
        print(format_times(config, day, now, default_azaan, state))
    return 0


def skip_command(args):
    config = Config(args.config)
    now = datetime.now(config.tz)
    entry = skip_next(config, state_file(args.config), now, installed_azaan())
    if entry is None:
        print("No azaan to skip")
        return 1
    print("Skipping the azaan for {}{}. Run piprayer.py resume to play it after all."
          .format(describe_azaan(entry, now), " and its reminder" if entry.reminder else ""))
    return 0


def mute_command(args):
    config = Config(args.config)
    now = datetime.now(config.tz)
    today = now.date()
    if args.until:
        last_day = args.until
        if not today <= last_day <= today + timedelta(days=MAX_MUTE_DAYS - 1):
            raise ConfigError("--until must be from today to {} days ahead".format(MAX_MUTE_DAYS - 1))
    else:
        last_day = today + timedelta(days=args.days - 1)
    mute_until = mute(config, state_file(args.config), now, last_day)
    print(describe_mute(config, mute_until, now) + ". Run piprayer.py resume to play again.")
    return 0


def resume_command(args):
    config = Config(args.config)
    now = datetime.now(config.tz)
    resume(state_file(args.config))
    entry = next_azaan(config, now, installed_azaan())
    print("Skip and mute cleared." + (" Next azaan: " + describe_azaan(entry, now) + "." if entry else ""))
    return 0


# ------------------------------------------------------------------ status page

class StatusPage:
    """The status page and its JSON API, served from a background thread while the service runs."""

    def __init__(self, config_path, default_azaan):
        self.config_path = config_path
        self.default_azaan = default_azaan
        self.port = None
        self.server = None

    def serve(self, port):
        """Start, move or stop the page to match the configuration. None stops it."""
        if port == self.port:
            return
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            log.info("Status page stopped")

        # Remembered even if starting fails, so the error is logged once rather than every minute
        self.port = port
        if not port:
            return

        from http.server import ThreadingHTTPServer
        try:
            self.server = ThreadingHTTPServer(("", port), status_handler(self.config_path, self.default_azaan))
        except OSError as e:
            log.error("Can't start the status page on port %d: %s", port, e.strerror or e)
            return
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="status-page", daemon=True).start()
        log.info("Status page on port %d", port)


def status_handler(config_path, default_azaan):
    """A request handler class for the status page of this configuration."""
    from http.server import BaseHTTPRequestHandler

    state_path = state_file(config_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = "PiPrayer"

        def log_message(self, format, *args):
            log.debug("Status page: " + format, *args)

        def do_GET(self):
            self.handle_request(post=False)

        def do_POST(self):
            # Refuse forms submitted from other sites. Home Assistant and curl don't send Origin
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.send(403, "text/plain", "Forbidden")
            self.handle_request(post=True)

        def handle_request(self, post):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if post:
                length = int(self.headers.get("Content-Length") or 0)
                query.update(parse_qs(self.rfile.read(length).decode("utf-8", "replace")))

            try:
                config = Config(config_path)
                now = datetime.now(config.tz)
                result = self.route(url.path, post, query, config, now)
            except ConfigError as e:
                return self.send(500, "text/plain", "PiPrayer configuration error: {}".format(e))
            except ValueError as e:
                return self.send(400, "text/plain", str(e))

            if result is None:
                self.send(404, "text/plain", "Not found")
            elif result == "redirect":
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
            elif isinstance(result, str):
                self.send(200, "text/html; charset=utf-8", result)
            else:
                self.send(200, "application/json", json.dumps(result["json"], indent=2))

        def route(self, path, post, query, config, now):
            action = path[len("/api"):] if path.startswith("/api/") else path
            from_page = not path.startswith("/api/")

            if not post:
                state = State.load(state_path)
                if path == "/":
                    return status_html(config, now, default_azaan, state)
                if path == "/api/times":
                    return {"json": times_json(config, now.date(), now, default_azaan, state)}
                if path == "/api/next":
                    return {"json": next_json(config, next_azaan(config, now, default_azaan, state), now, state)}
                return None

            if action == "/skip":
                entry = skip_next(config, state_path, now, default_azaan)
                result = {"skipped": entry.to_json() if entry else None}
            elif action == "/mute":
                days = query.get("days", ["1"])[0]
                if not days.isdigit() or not 1 <= int(days) <= MAX_MUTE_DAYS:
                    raise ValueError("days must be a whole number from 1 to {}".format(MAX_MUTE_DAYS))
                mute_until = mute(config, state_path, now, now.date() + timedelta(days=int(days) - 1))
                result = {"muted_until": mute_until.isoformat()}
            elif action == "/resume":
                resume(state_path)
                result = {"muted_until": None, "skip": []}
            else:
                return None
            return "redirect" if from_page else {"json": result}

        def send(self, status, content_type, body):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


STATUS_STYLE = """
:root { --bg: #f6f5f1; --card: #ffffff; --text: #1d1d1b; --muted: #6b6a65; --line: #e4e2db;
        --accent: #1f7a4d; --accent-text: #ffffff; --warn: #9a5b00; --warn-bg: #fff3dc; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #151614; --card: #1f201d; --text: #ecebe6; --muted: #9c9b94; --line: #33342f;
          --accent: #3fb07a; --accent-text: #0d1a12; --warn: #f3b552; --warn-bg: #3a2c12; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 32rem; margin: 0 auto; padding: 24px 16px 40px; }
h1 { font-size: 1rem; font-weight: 600; color: var(--muted); margin: 0 0 16px; letter-spacing: .02em; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px; margin-bottom: 16px; }
.label { color: var(--muted); font-size: .875rem; }
.next { font-size: 2.25rem; font-weight: 700; line-height: 1.2; margin: 4px 0; font-variant-numeric: tabular-nums; }
.notice { background: var(--warn-bg); color: var(--warn); border-radius: 10px; padding: 10px 14px; margin-top: 12px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.actions form { margin: 0; }
button { font: inherit; font-size: .9375rem; padding: 9px 14px; border-radius: 10px; cursor: pointer;
         border: 1px solid var(--line); background: var(--card); color: var(--text); }
button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); font-weight: 600; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th { text-align: left; font-weight: 500; color: var(--muted); font-size: .8125rem; padding: 0 0 8px; }
td { padding: 8px 0; border-top: 1px solid var(--line); }
th:not(:last-child), td:not(:last-child) { padding-right: 12px; }
tr.past td { color: var(--muted); }
tr.next td { font-weight: 600; }
.tag { font-size: .75rem; padding: 2px 8px; border-radius: 999px; background: var(--warn-bg); color: var(--warn); }
.tag.next { background: var(--accent); color: var(--accent-text); }
footer { color: var(--muted); font-size: .8125rem; text-align: center; }
"""


def status_html(config, now, default_azaan, state):
    """The status page: the next azaan, buttons to skip or mute, and today's times."""
    today = schedule(config, now.date(), default_azaan, state)
    upcoming = next_azaan(config, now, default_azaan, state)
    playing = next_playing(config, now, default_azaan, state)
    muted = state.mute_until and state.mute_until > now

    if upcoming:
        next_card = ('<div class="label">Next azaan</div><div class="next">{} {}</div>'
                     '<div class="label">{}, in {}</div>').format(
            escape(upcoming.prayer.capitalize()), upcoming.at.strftime("%H:%M"),
            describe_day(upcoming.at.date(), now.date()).capitalize(), format_duration(upcoming.at - now))
    else:
        next_card = '<div class="next">No azaan in the next two days</div>'

    if muted:
        next_card += '<div class="notice">{}.</div>'.format(escape(describe_mute(config, state.mute_until, now)))
    elif upcoming and upcoming.silenced == "skipped":
        next_card += '<div class="notice">This azaan will be skipped.</div>'

    buttons = []
    if playing:
        buttons.append(('/skip', "primary", "Skip " + describe_azaan(playing, now)))
    if not muted:
        buttons.append(('/mute', "", "Mute today"))
    if muted or state.skip:
        buttons.append(('/resume', "", "Resume"))
    next_card += '<div class="actions">{}</div>'.format("".join(
        '<form method="post" action="{}"><button class="{}">{}</button></form>'.format(action, cls, escape(text))
        for action, cls, text in buttons))

    rows = []
    for entry in today:
        if entry.at <= now:
            tag, cls = "", "past"
        elif entry.silenced:
            tag, cls = '<span class="tag">{}</span>'.format(entry.silenced), ""
        elif upcoming and entry.at == upcoming.at:
            tag, cls = '<span class="tag next">next</span>', "next"
        else:
            tag, cls = "", ""
        rows.append('<tr class="{}"><td>{}</td><td>{}</td><td>{}</td><td>{}%</td><td>{}</td></tr>'.format(
            cls, escape(entry.prayer.capitalize()), entry.at.strftime("%H:%M"), entry.azaan, entry.volume, tag))
    table = ('<table><thead><tr><th>Prayer</th><th>Time</th><th>Azaan</th><th>Volume</th><th></th></tr></thead>'
             '<tbody>{}</tbody></table>'.format("".join(rows)) if rows else "No prayer times today")

    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>PiPrayer</title>
<style>{style}</style>
</head>
<body>
<main>
<h1>PiPrayer</h1>
<section class="card">{next_card}</section>
<section class="card">
<div class="label" style="margin-bottom:12px">{date}</div>
{table}
</section>
<footer>{details} · updated {updated}</footer>
</main>
</body>
</html>
""".format(style=STATUS_STYLE, next_card=next_card, table=table,
           date="{:%A} {} {:%B %Y}".format(now, now.day, now),
           details=escape("{} · {} method · {} Asr".format(timezone_name(config, now.date()), config.method,
                                                            config.asr_time)),
           updated=now.strftime("%H:%M"))


# ------------------------------------------------------------------ configuration wizard

PRAYER_NAMES = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
METHODS = ["MWL", "ISNA", "Egypt", "Makkah", "Karachi", "Tehran", "Jafari"]
ASR_METHODS = ["Standard", "Hanafi"]

# Usual calculation method by timezone, MWL elsewhere
REGIONAL_METHODS = [
    ("America/", "ISNA"),
    ("Africa/Cairo", "Egypt"),
    ("Asia/Riyadh", "Makkah"), ("Asia/Aden", "Makkah"), ("Asia/Qatar", "Makkah"), ("Asia/Bahrain", "Makkah"),
    ("Asia/Kuwait", "Makkah"), ("Asia/Dubai", "Makkah"), ("Asia/Muscat", "Makkah"),
    ("Asia/Karachi", "Karachi"), ("Asia/Kolkata", "Karachi"), ("Asia/Dhaka", "Karachi"), ("Asia/Kabul", "Karachi"),
    ("Asia/Tehran", "Tehran"),
]
# Hanafi Asr is usual in South Asia
HANAFI_TIMEZONES = ("Asia/Karachi", "Asia/Kolkata", "Asia/Dhaka", "Asia/Kabul")


def valid_timezone(name):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def system_timezone():
    """The system's IANA timezone name, or None."""
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    try:
        with open("/etc/timezone") as f:
            candidates.append(f.read().strip())
    except OSError:
        pass
    # /etc/localtime links to e.g. /usr/share/zoneinfo/Australia/Melbourne
    link = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in link:
        candidates.append(link.rsplit("zoneinfo/", 1)[1])

    for name in candidates:
        if name and "/" in name and valid_timezone(name):
            return name
    return None


def parse_zone_coordinate(value, degree_digits):
    """Parse ISO 6709 "-3749" or "+1445812" (sign, degrees, minutes, optional seconds) into degrees."""
    sign = -1 if value[0] == "-" else 1
    digits = value[1:]
    degrees = int(digits[:degree_digits])
    minutes = int(digits[degree_digits:degree_digits + 2])
    seconds = int(digits[degree_digits + 2:] or 0)
    return round(sign * (degrees + minutes / 60 + seconds / 3600), 4)


def timezone_location(name):
    """(lat, lng, place) of the timezone's main city from the tz database, or None."""
    from zoneinfo import TZPATH

    for directory in TZPATH:
        for table in ("zone1970.tab", "zone.tab"):
            try:
                with open(os.path.join(directory, table), encoding="utf-8") as f:
                    for line in f:
                        fields = line.rstrip("\n").split("\t")
                        if line.startswith("#") or len(fields) < 3 or fields[2] != name:
                            continue
                        coordinates = fields[1]
                        split = max(coordinates.rfind("+"), coordinates.rfind("-"))
                        lat = parse_zone_coordinate(coordinates[:split], 2)
                        lng = parse_zone_coordinate(coordinates[split:], 3)
                        return lat, lng, name.rsplit("/", 1)[1].replace("_", " ")
            except (OSError, ValueError, IndexError):
                continue
    return None


def regional_method(tz_name):
    for prefix, method in REGIONAL_METHODS:
        if tz_name.startswith(prefix):
            return method
    return "MWL"


def choose(options):
    """Parser for a choice by number or name."""

    def parse(value):
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1]
        for option in options:
            if option.lower() == value.lower():
                return option
        raise ConfigError("Choose a number from 1 to {}, or one of: {}".format(len(options), ", ".join(options)))

    return parse


def coordinate(what, limit):
    def parse(value):
        try:
            number = float(value)
        except ValueError:
            raise ConfigError("Invalid {}: {}, use decimal degrees, e.g. -37.9095".format(what, value))
        if not -limit <= number <= limit:
            raise ConfigError("Invalid {}: {}, use a number from {} to {}".format(what, value, -limit, limit))
        return value

    return parse


def timezone_option(value):
    if not valid_timezone(value):
        raise ConfigError("Unknown timezone: {}, use a name such as Europe/London".format(value))
    return value


def prayers_option(value):
    parse_prayers(value)
    if not value.strip():
        raise ConfigError("Choose at least one prayer")
    return ", ".join(item.strip() for item in value.split(",") if item.strip())


def volume_option(value):
    parse_volume(value)
    return ", ".join(item.strip() for item in value.split(",") if item.strip())


def number_option(what, minimum, maximum, blank_ok=False):
    def parse(value):
        if blank_ok and value.lower() in ("", "none"):
            return ""
        number = check_range(value, what, minimum, maximum)
        if what.endswith("azaan") and not os.path.isfile(azaan_file(number)):
            raise ConfigError("Invalid {}: {}, there is no {}".format(
                what, number, os.path.relpath(azaan_file(number), SCRIPT_DIR)))
        return str(number)

    return parse


class Wizard:
    def __init__(self, ask=input, say=print):
        self.ask_input = ask
        self.say = say

    def ask(self, question, default, parse, hint=None):
        """Ask until the answer parses. An empty answer takes the default."""
        if hint:
            self.say(hint)
        while True:
            prompt = "{} [{}]: ".format(question, default) if default != "" else "{}: ".format(question)
            answer = self.ask_input(prompt).strip()
            if not answer:
                answer = default
            try:
                return parse(answer)
            except ConfigError as e:
                self.say("  " + str(e))

    def confirm(self, question, default=True):
        answer = self.ask_input("{} [{}]: ".format(question, "Y/n" if default else "y/N")).strip().lower()
        return default if not answer else answer in ("y", "yes")


SCAN_SECONDS = 15
ANSI_CODE = re.compile(r"\x1b\[[0-9;]*m|\x01|\x02")
DEVICE_LINE = re.compile(r"Device ((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}) (.+)$")
PAIR = "pair"
SCAN_AGAIN = "scan"


def bluetoothctl(*args, timeout=10):
    """Run bluetoothctl and return its output, without colours. Empty when it isn't available."""
    try:
        output = subprocess.run(["bluetoothctl"] + list(args), capture_output=True, text=True,
                                timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return ANSI_CODE.sub("", output)


def bluetooth_available():
    return bluetoothctl("show").startswith("Controller")


def bluetooth_speakers():
    """Paired Bluetooth devices as [(mac, name, connected)], empty when bluetoothctl isn't available."""
    speakers = []
    for line in bluetoothctl("devices").splitlines():
        fields = line.split(" ", 2)
        if len(fields) < 2 or fields[0] != "Device" or not MAC_ADDRESS.match(fields[1].upper()):
            continue
        info = bluetoothctl("info", fields[1])
        if "Paired: yes" not in info:
            continue
        name = fields[2].strip() if len(fields) > 2 else fields[1]
        speakers.append((fields[1].upper(), name, "Connected: yes" in info))
    return speakers


def scan_speakers(seconds=SCAN_SECONDS):
    """Named devices nearby that aren't paired, as [(mac, name)]."""
    bluetoothctl("power", "on")
    scan = bluetoothctl("--timeout", str(seconds), "scan", "on", timeout=seconds + 10)
    lines = [line for line in scan.splitlines() if "[NEW]" in line] + bluetoothctl("devices").splitlines()

    paired = {mac for mac, _, _ in bluetooth_speakers()}
    found = {}
    for line in lines:
        match = DEVICE_LINE.search(line.strip())
        if not match:
            continue
        mac, name = match.group(1).upper(), match.group(2).strip()
        # devices that don't send a name show their address instead
        if mac in paired or name.upper().replace("-", ":") == mac:
            continue
        found.setdefault(mac, name)
    return list(found.items())


def pair_speaker(mac):
    """Pair and trust a device. Returns None, or why it failed."""
    output = bluetoothctl("pair", mac, timeout=40)
    if "Paired: yes" not in bluetoothctl("info", mac):
        reasons = [line.strip() for line in output.splitlines() if "fail" in line.lower() or "error" in line.lower()]
        return reasons[-1] if reasons else "no answer from the speaker"
    bluetoothctl("trust", mac)
    return None


def pair_new_speaker(wizard):
    """Find and pair a speaker. Returns its address, or "" when none was paired."""
    wizard.say("")
    wizard.ask_input("Put the speaker in pairing mode, then press Enter ")
    scan = True
    while True:
        if scan:
            wizard.say("Looking for speakers for {} seconds...".format(SCAN_SECONDS))
            found = scan_speakers()
            scan = False
        wizard.say("")
        if found:
            wizard.say("Speakers found:")
            for number, (mac, name) in enumerate(found, 1):
                wizard.say("  {}. {} ({})".format(number, name, mac))
        else:
            wizard.say("No new speakers found. Check it is in pairing mode and close to the Raspberry Pi.")

        def parse(value):
            if value == "":
                return ""
            if value.lower() == "r":
                return SCAN_AGAIN
            if value.isdigit() and 1 <= int(value) <= len(found):
                return found[int(value) - 1]
            raise ConfigError("Choose a number from 1 to {}, r to look again, or Enter to go back".format(
                len(found)) if found else "Enter r to look again, or Enter to go back")

        prompt = "Speaker to pair (r to look again, Enter to go back)" if found else "r to look again, Enter to go back"
        choice = wizard.ask(prompt, "", parse)
        if choice == "":
            return ""
        if choice == SCAN_AGAIN:
            scan = True
            continue

        mac, name = choice
        wizard.say("Pairing {}...".format(name))
        failure = pair_speaker(mac)
        if failure:
            wizard.say("Could not pair {}: {}".format(name, failure))
            continue
        wizard.say("Paired {}".format(name))
        return mac


def choose_speaker(wizard, current):
    """Ask which paired speaker to use, offering to pair one. Returns its address, or "" for any paired speaker."""
    wizard.say("")
    if not bluetooth_available():
        wizard.say("No Bluetooth adapter found, so the speaker can't be chosen now.")
        return current

    while True:
        speakers = bluetooth_speakers()
        if current and current.upper() not in [mac for mac, _, _ in speakers]:
            speakers.append((current.upper(), "not paired now", False))

        if not speakers:
            wizard.say("No paired Bluetooth speakers found.")
            if not wizard.confirm("Pair a speaker now?"):
                wizard.say("PiPrayer will use any paired speaker. Run python3 piprayer.py config to choose one later.")
                return ""
            mac = pair_new_speaker(wizard)
            if mac:
                return mac
            wizard.say("")
            continue

        wizard.say("Bluetooth speakers:")
        wizard.say("  0. Any paired speaker: one that is connected, otherwise the first that connects")
        for number, (mac, name, connected) in enumerate(speakers, 1):
            wizard.say("  {}. {} ({}){}".format(number, name, mac, ", connected" if connected else ""))
        wizard.say("  p. Pair a new speaker")

        default = "0"
        for number, (mac, _, _) in enumerate(speakers, 1):
            if current and mac == current.upper():
                default = str(number)

        def parse(value):
            if value.lower() == "p":
                return PAIR
            if value.isdigit() and int(value) <= len(speakers):
                return speakers[int(value) - 1][0] if int(value) else ""
            if MAC_ADDRESS.match(value.upper()):
                return value.upper()
            raise ConfigError("Choose a number from 0 to {}, p to pair a new speaker, or enter a Bluetooth address"
                              .format(len(speakers)))

        choice = wizard.ask("Speaker", default, parse)
        if choice != PAIR:
            return choice
        mac = pair_new_speaker(wizard)
        if mac:
            return mac
        wizard.say("")


def read_existing(path):
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error:
        return configparser.ConfigParser()
    return parser


def configure(path, wizard=None, next_step=True):
    """Ask for each setting, show the resulting times and save them to path."""
    wizard = wizard or Wizard()
    parser = read_existing(path)
    if not parser.has_section("Default"):
        parser.add_section("Default")
    current = parser["Default"]

    wizard.say("PiPrayer configuration: " + path)
    wizard.say("Press Enter to keep the value in brackets.")
    wizard.say("")

    detected = system_timezone()
    hint = None
    if detected and TIMEZONE not in current:
        hint = "The timezone is detected from the system."
    elif detected and detected != current[TIMEZONE]:
        hint = "The system timezone is " + detected + "."
    tz_name = wizard.ask("Timezone", current.get(TIMEZONE, detected or ""), timezone_option, hint=hint)

    lat, lng = current.get(LAT, ""), current.get(LNG, "")
    location = timezone_location(tz_name)
    if location and (not lat or tz_name != current.get(TIMEZONE)):
        lat, lng = str(location[0]), str(location[1])
        wizard.say("")
        wizard.say("The location defaults to {}. For accurate times, enter your own coordinates,".format(location[2]))
        wizard.say("e.g. from Google Maps: right-click your home and click the numbers.")
    lat = wizard.ask("Latitude", lat, coordinate("latitude", 90))
    lng = wizard.ask("Longitude", lng, coordinate("longitude", 180))

    wizard.say("")
    for number, method in enumerate(METHODS, 1):
        wizard.say("  {}. {:8} {}".format(number, method, praytimes.PrayTimes.methods[method]["name"]))
    method = wizard.ask("Calculation method", current.get(METHOD, regional_method(tz_name)), choose(METHODS))

    asr_default = current.get(ASR_TIME, "Hanafi" if tz_name in HANAFI_TIMEZONES else "Standard")
    asr = wizard.ask("Asr time (1. Standard, 2. Hanafi)", asr_default, choose(ASR_METHODS))

    prayers = wizard.ask("Prayers", current.get(PRAYERS, ", ".join(PRAYER_NAMES)), prayers_option,
                         hint="\nPrayers to play the azaan for. Add :minutes to shift a time, e.g. Fajr:-10, Dhuhr:5")

    speaker = choose_speaker(wizard, current.get(SPEAKER, ""))

    wizard.say("")
    wizard.say("Azaan recordings are numbered 1 to 17. Try one with: python3 piprayer.py play -a 5")
    wizard.say("14 to 17 are Fajr azaans, with as-salatu khayrun min an-nawm. Use them for Fajr only.")
    azaan = wizard.ask("Azaan", current.get(AZAAN, str(installed_azaan())), number_option("azaan", 1, 99))
    fajr_azaan = wizard.ask("Fajr azaan (none for the same as the others)", current.get(FAJR_AZAAN, ""),
                            number_option("fajr-azaan", 1, 99, blank_ok=True))
    volume = wizard.ask("Volume in percent, e.g. 80 or 80, Fajr:40", current.get(VOLUME, str(DEFAULT_VOLUME)),
                        volume_option)
    reminder = wizard.ask("Reminder chime, minutes before each azaan (0 for none)", current.get(REMINDER, "0"),
                          number_option("reminder", 0, 60))

    settings = [(TIMEZONE, tz_name), (LAT, lat), (LNG, lng), (METHOD, method), (ASR_TIME, asr),
                (PRAYERS, prayers), (AZAAN, azaan), (FAJR_AZAAN, fajr_azaan), (VOLUME, volume),
                (REMINDER, reminder), (SPEAKER, speaker)]

    # Keep any other options, but timezone replaces the legacy ones
    result = configparser.ConfigParser()
    result.add_section("Default")
    for option, value in settings:
        if value != "":
            result["Default"][option] = value
    for option, value in current.items():
        if option not in result["Default"] and option not in (GMT_OFFSET, DST, FAJR_AZAAN, SPEAKER):
            result["Default"][option] = value

    preview = path + ".new"
    with open(preview, "w") as f:
        result.write(f)
    try:
        config = Config(preview)
        now = datetime.now(config.tz)
        wizard.say("")
        wizard.say(format_times(config, now.date(), now, config.azaan or DEFAULT_AZAAN))
        wizard.say("")
        if not wizard.confirm("Save to " + path + "?"):
            wizard.say("Not saved")
            return 1
        os.replace(preview, path)
    finally:
        if os.path.exists(preview):
            os.remove(preview)

    wizard.say("Saved " + path)
    if next_step:
        if os.path.exists(service_file()):
            wizard.say("The PiPrayer service will use the new settings within a minute.")
        else:
            wizard.say("Run ./setup-piprayer.sh to install the PiPrayer service.")
    return 0


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("use the format YYYY-MM-DD, e.g. 2026-11-12")


def mute_days(value):
    try:
        days = int(value)
    except ValueError:
        days = 0
    if not 1 <= days <= MAX_MUTE_DAYS:
        raise argparse.ArgumentTypeError("use a whole number from 1 to {}".format(MAX_MUTE_DAYS))
    return days


def azaan_number(value):
    azaan = int(value)
    if not os.path.isfile(azaan_file(azaan)):
        raise argparse.ArgumentTypeError("no azaan {}, choose from 1 to 17".format(value))
    return azaan


def main():
    parser = argparse.ArgumentParser(description="PiPrayer - Azaan for Raspberry Pi")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                        help="configuration file (default: %(default)s)")
    commands = parser.add_subparsers(dest="command")

    times_command = commands.add_parser("times", help="print today's azaan times (default)")
    times_command.add_argument("which", nargs="?", choices=["next"], help="show only the next azaan")
    times_command.add_argument("-d", "--date", type=parse_date, help="show another day, as YYYY-MM-DD")
    times_command.add_argument("-f", "--format", choices=["table", "json", "plain"], default="table",
                               help="output format: table (default), json, or plain prayer-HH:MM lines")
    times_command.add_argument("-a", "--azaan", type=azaan_number, metavar="1-17",
                               help="azaan to show when the configuration doesn't set one "
                                    "(default: the one the service was set up with)")

    run_command = commands.add_parser("run", help="run as a service, playing the azaan at each prayer time")
    run_command.add_argument("-a", "--azaan", type=azaan_number, default=DEFAULT_AZAAN, metavar="1-17",
                             help="azaan to play when the configuration doesn't set one (default: %(default)s)")

    play_command = commands.add_parser("play", help="play the azaan now, to test the speaker")
    play_command.add_argument("-a", "--azaan", type=azaan_number, metavar="1-17",
                              help="azaan to play (default: the one in the configuration)")
    play_command.add_argument("-p", "--prayer", choices=["fajr", "dhuhr", "asr", "maghrib", "isha"],
                              type=str.lower, help="play the azaan and volume set for this prayer")
    play_command.add_argument("-r", "--reminder", action="store_true", help="play the reminder sound")

    commands.add_parser("skip", help="don't play the next azaan or its reminder")
    mute_command_parser = commands.add_parser("mute", help="don't play any azaan until the end of today, "
                                                           "or for a number of days")
    mute_for = mute_command_parser.add_mutually_exclusive_group()
    mute_for.add_argument("-d", "--days", type=mute_days, default=1, metavar="DAYS",
                          help="days to mute, counting today (default: 1, up to {})".format(MAX_MUTE_DAYS))
    mute_for.add_argument("-u", "--until", type=parse_date, metavar="YYYY-MM-DD", help="last day to mute")
    commands.add_parser("resume", help="clear skip and mute, so every azaan plays again")

    config_command = commands.add_parser("config", help="create or change the configuration, asking for each setting")
    # setup-piprayer.sh installs the service next, so it doesn't need saying
    config_command.add_argument("--no-next-step", dest="next_step", action="store_false", help=argparse.SUPPRESS)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        if args.command == "config":
            return configure(args.config, next_step=args.next_step)
        if args.command == "run":
            run(args.config, args.azaan)
        elif args.command == "play":
            return test_play(args)
        elif args.command == "skip":
            return skip_command(args)
        elif args.command == "mute":
            return mute_command(args)
        elif args.command == "resume":
            return resume_command(args)
        else:
            if args.command is None:
                # "piprayer.py" on its own is the same as "piprayer.py times"
                args = parser.parse_args(sys.argv[1:] + ["times"])
            return show_times(args)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if args.command == "config":
            print("\nNot saved", file=sys.stderr)
            return 1
    except EOFError:
        print("\nNot saved, no answer to read", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
