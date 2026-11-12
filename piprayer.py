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
import logging
import os
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


def play(sound, volume=DEFAULT_VOLUME, description=None):
    """Play an azaan (by number) or a sound file through play-azaan.sh."""
    log.info("Playing %s at %d%% volume", description or "azaan " + str(sound), volume)
    result = subprocess.run([PLAYER, str(sound), str(volume)])
    if result.returncode != 0:
        log.error("%s failed with exit code %d", os.path.basename(PLAYER), result.returncode)
    return result.returncode


def play_event(config, event, default_azaan):
    volume = config.volume_for(event.prayer)
    if event.reminder:
        return play(REMINDER_SOUND, volume, "reminder for " + event.prayer)
    azaan = config.azaan_for(event.prayer, default_azaan)
    return play(azaan, volume, "azaan {} for {}".format(azaan, event.prayer))


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
    if args.reminder:
        return play(REMINDER_SOUND, volume, "reminder")

    if args.azaan:
        azaan = args.azaan
    else:
        azaan = config.azaan_for(prayer) if config else DEFAULT_AZAAN
    return play(azaan, volume)


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


def format_times(config, day, now, default_azaan):
    """Today's schedule as a table, marking the next azaan."""
    times = prayer_times(config, day)
    upcoming = next((prayer for prayer, at in times if at > now), None) if day == now.date() else None

    header = ["Prayer", "Time", "Azaan", "Volume"] + (["Reminder"] if config.reminder else [])
    rows = []
    for prayer, at in times:
        row = [prayer.capitalize(), at.strftime("%H:%M"),
               str(config.azaan_for(prayer, default_azaan)), "{}%".format(config.volume_for(prayer))]
        if config.reminder:
            row.append((at - timedelta(minutes=config.reminder)).strftime("%H:%M"))
        rows.append((row, prayer == upcoming))

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

    return "\n".join(" " + l if l else l for l in lines)


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
    times_command.add_argument("-d", "--date", type=parse_date, help="show another day, as YYYY-MM-DD")
    times_command.add_argument("-a", "--azaan", type=azaan_number, metavar="1-15",
                               help="azaan to show when the configuration doesn't set one "
                                    "(default: the one the service was set up with)")
    times_command.add_argument("--plain", action="store_true",
                               help="print one prayer-HH:MM line per prayer, for scripts")

    run_command = commands.add_parser("run", help="run as a service, playing the azaan at each prayer time")
    run_command.add_argument("-a", "--azaan", type=azaan_number, default=DEFAULT_AZAAN, metavar="1-15",
                             help="azaan to play when the configuration doesn't set one (default: %(default)s)")

    play_command = commands.add_parser("play", help="play the azaan now, to test the speaker")
    play_command.add_argument("-a", "--azaan", type=azaan_number, metavar="1-15",
                              help="azaan to play (default: the one in the configuration)")
    play_command.add_argument("-p", "--prayer", choices=["fajr", "dhuhr", "asr", "maghrib", "isha"],
                              type=str.lower, help="play the azaan and volume set for this prayer")
    play_command.add_argument("-r", "--reminder", action="store_true", help="play the reminder sound")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        if args.command == "run":
            run(args.config, args.azaan)
        elif args.command == "play":
            return test_play(args)
        else:
            config = Config(args.config)
            now = datetime.now(config.tz)
            day = getattr(args, "date", None) or now.date()

            if getattr(args, "plain", False):
                for prayer, at in prayer_times(config, day):
                    print(prayer + "-" + at.strftime("%H:%M"))
            else:
                print(format_times(config, day, now, getattr(args, "azaan", None) or installed_azaan()))
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
