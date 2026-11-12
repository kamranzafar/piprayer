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
import time as systime
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
    return os.path.join(MEDIA_DIR, "azaan-{}.wav".format(azaan))


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

    def __init__(self, at, prayer, reminder=False):
        self.at = at
        self.prayer = prayer
        self.reminder = reminder

    def __repr__(self):
        return "{} for {} at {}".format("Reminder" if self.reminder else "Azaan", self.prayer,
                                        self.at.strftime("%Y-%m-%d %H:%M %Z"))


def events(config, day):
    """Return the events for the given day, in order."""
    result = []
    for prayer, at in prayer_times(config, day):
        if config.reminder:
            result.append(Event(at - timedelta(minutes=config.reminder), prayer, reminder=True))
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

    while True:
        mtime = config_mtime(config_path)
        config = Config(config_path)
        event = next_event(config, datetime.now(config.tz))

        if event is None:
            log.warning("No prayer times found for today or tomorrow, checking again in an hour")
            systime.sleep(3600)
            continue

        log.info("Next: %s", event)

        config_changed = False
        while True:
            remaining = (event.at - datetime.now(config.tz)).total_seconds()
            if remaining <= 0:
                break
            if config_mtime(config_path) != mtime:
                config_changed = True
                break
            systime.sleep(min(remaining, MAX_SLEEP_SECONDS))

        if config_changed:
            log.info("Configuration changed, reloading")
            continue

        late = datetime.now(config.tz) - event.at
        if late > MAX_LATENESS:
            log.warning("Skipping %s, noticed %d minutes late (clock changed?)",
                        event, late.total_seconds() // 60)
            continue

        play_event(config, event, default_azaan)


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


def installed_azaan():
    """The azaan the installed service was set up with, or the default."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    try:
        with open(os.path.join(config_home, "systemd", "user", "piprayer.service")) as f:
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

    def __init__(self, config, prayer, at, default_azaan):
        self.prayer = prayer
        self.at = at
        self.azaan = config.azaan_for(prayer, default_azaan)
        self.volume = config.volume_for(prayer)
        self.reminder = at - timedelta(minutes=config.reminder) if config.reminder else None

    def to_json(self):
        return {
            "prayer": self.prayer,
            "time": self.at.isoformat(),
            "azaan": self.azaan,
            "volume": self.volume,
            "reminder": self.reminder.isoformat() if self.reminder else None,
        }


def schedule(config, day, default_azaan):
    return [Entry(config, prayer, at, default_azaan) for prayer, at in prayer_times(config, day)]


def next_azaan(config, now, default_azaan):
    """The first azaan after now, today or tomorrow, or None."""
    for day in (now.date(), now.date() + timedelta(days=1)):
        for entry in schedule(config, day, default_azaan):
            if entry.at > now:
                return entry
    return None


def format_duration(delta):
    """Time until something, rounded up to the minute, e.g. "3 h 12 min"."""
    minutes = max(1, -(-int(delta.total_seconds()) // 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return "{} h {} min".format(hours, minutes)
    return "{} h".format(hours) if hours else "{} min".format(minutes)


def format_times(config, day, now, default_azaan):
    """The day's schedule as a table, marking the next azaan."""
    entries = schedule(config, day, default_azaan)
    upcoming = next((e for e in entries if e.at > now), None) if day == now.date() else None

    header = ["Prayer", "Time", "Azaan", "Volume"] + (["Reminder"] if config.reminder else [])
    rows = []
    for entry in entries:
        row = [entry.prayer.capitalize(), entry.at.strftime("%H:%M"), str(entry.azaan), "{}%".format(entry.volume)]
        if entry.reminder:
            row.append(entry.reminder.strftime("%H:%M"))
        rows.append((row, entry is upcoming))

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
    for row, is_next in rows:
        lines.append(line(row) + "  ◀ next" if is_next else line(row).rstrip())
    if not rows:
        lines.append("No prayer times on this day")

    return indent(lines)


def format_next(config, entry, now):
    """The next azaan, e.g. "Next azaan: Asr at 18:03 today, in 3 h 12 min"."""
    if entry is None:
        return indent(["No azaan in the next two days"])

    when = "today" if entry.at.date() == now.date() else "tomorrow"
    details = "Azaan {} · {}% volume".format(entry.azaan, entry.volume)
    if entry.reminder:
        details += " · reminder at " + entry.reminder.strftime("%H:%M")

    return indent([
        "Next azaan: {} at {} {}, in {}".format(entry.prayer.capitalize(), entry.at.strftime("%H:%M"), when,
                                                 format_duration(entry.at - now)),
        details,
        timezone_name(config, entry.at.date()),
    ])


def indent(lines):
    return "\n".join(" " + line if line else line for line in lines)


def times_json(config, day, now, default_azaan):
    entries = schedule(config, day, default_azaan)
    upcoming = next((e for e in entries if e.at > now), None) if day == now.date() else None
    return {
        "date": day.isoformat(),
        "timezone": getattr(config.tz, "key", None) or datetime.combine(day, time(12), config.tz).tzname(),
        "method": config.method,
        "asr": config.asr_time,
        "next": upcoming.prayer if upcoming else None,
        "prayers": [entry.to_json() for entry in entries],
    }


def next_json(config, entry, now):
    if entry is None:
        return None
    result = entry.to_json()
    result["timezone"] = getattr(config.tz, "key", None) or entry.at.tzname()
    result["seconds_until"] = int((entry.at - now).total_seconds())
    return result


def show_times(args):
    config = Config(args.config)
    now = datetime.now(config.tz)
    default_azaan = args.azaan or installed_azaan()

    if args.which == "next":
        if args.date:
            raise ConfigError("--date can't be used with next")
        entry = next_azaan(config, now, default_azaan)
        if args.format == "json":
            print(json.dumps(next_json(config, entry, now), indent=2))
        elif args.format == "plain":
            if entry:
                print(entry.prayer + "-" + entry.at.strftime("%H:%M"))
        else:
            print(format_next(config, entry, now))
        return 0

    day = args.date or now.date()
    if args.format == "json":
        print(json.dumps(times_json(config, day, now, default_azaan), indent=2))
    elif args.format == "plain":
        for prayer, at in prayer_times(config, day):
            print(prayer + "-" + at.strftime("%H:%M"))
    else:
        print(format_times(config, day, now, default_azaan))
    return 0


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


def bluetooth_speakers():
    """Paired Bluetooth devices as [(mac, name, connected)], empty when bluetoothctl isn't available."""

    def bluetoothctl(*args):
        try:
            return subprocess.run(["bluetoothctl"] + list(args), capture_output=True, text=True,
                                  timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

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


def choose_speaker(wizard, current):
    """Ask which paired speaker to use. Returns its address, or "" for any paired speaker."""
    speakers = bluetooth_speakers()
    if current and current.upper() not in [mac for mac, _, _ in speakers]:
        speakers.append((current.upper(), "not paired now", False))

    wizard.say("")
    if not speakers:
        wizard.say("No paired Bluetooth speakers found. Pair one with bluetoothctl, see the README, then run")
        wizard.say("python3 piprayer.py config again to choose it. PiPrayer uses any paired speaker until then.")
        return ""

    wizard.say("Bluetooth speakers:")
    wizard.say("  0. Any paired speaker: one that is connected, otherwise the first that connects")
    for number, (mac, name, connected) in enumerate(speakers, 1):
        wizard.say("  {}. {} ({}){}".format(number, name, mac, ", connected" if connected else ""))

    default = "0"
    for number, (mac, _, _) in enumerate(speakers, 1):
        if current and mac == current.upper():
            default = str(number)

    def parse(value):
        if value.isdigit() and int(value) <= len(speakers):
            return speakers[int(value) - 1][0] if int(value) else ""
        if MAC_ADDRESS.match(value.upper()):
            return value.upper()
        raise ConfigError("Choose a number from 0 to {}, or enter a Bluetooth address".format(len(speakers)))

    return wizard.ask("Speaker", default, parse)


def read_existing(path):
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error:
        return configparser.ConfigParser()
    return parser


def configure(path, wizard=None):
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
    wizard.say("Azaan recordings are numbered 1 to 15. Try one with: python3 piprayer.py play -a 5")
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
    return 0


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("use the format YYYY-MM-DD, e.g. 2026-11-12")


def azaan_number(value):
    azaan = int(value)
    if not os.path.isfile(azaan_file(azaan)):
        raise argparse.ArgumentTypeError("no azaan {}, choose from 1 to 15".format(value))
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
    times_command.add_argument("-a", "--azaan", type=azaan_number, metavar="1-15",
                               help="azaan to show when the configuration doesn't set one "
                                    "(default: the one the service was set up with)")

    run_command = commands.add_parser("run", help="run as a service, playing the azaan at each prayer time")
    run_command.add_argument("-a", "--azaan", type=azaan_number, default=DEFAULT_AZAAN, metavar="1-15",
                             help="azaan to play when the configuration doesn't set one (default: %(default)s)")

    play_command = commands.add_parser("play", help="play the azaan now, to test the speaker")
    play_command.add_argument("-a", "--azaan", type=azaan_number, metavar="1-15",
                              help="azaan to play (default: the one in the configuration)")
    play_command.add_argument("-p", "--prayer", choices=["fajr", "dhuhr", "asr", "maghrib", "isha"],
                              type=str.lower, help="play the azaan and volume set for this prayer")
    play_command.add_argument("-r", "--reminder", action="store_true", help="play the reminder sound")

    commands.add_parser("config", help="create or change the configuration, asking for each setting")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        if args.command == "config":
            return configure(args.config)
        if args.command == "run":
            run(args.config, args.azaan)
        elif args.command == "play":
            return test_play(args)
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
