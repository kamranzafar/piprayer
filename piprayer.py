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

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, ".piprayer")
PLAYER = os.path.join(SCRIPT_DIR, "play-azaan.sh")

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


def parse_prayers(value):
    """Parse "Fajr:-11, Dhuhr, ..." into prayer names and offsets in minutes (default 0)."""
    offsets = {}
    prayers = []

    for p in value.split(","):
        if not p.strip():
            continue

        name, _, offset = p.partition(":")
        prayer = name.strip().lower()

        if prayer not in praytimes.PrayTimes.timeNames:
            raise ConfigError("Unknown prayer: " + name.strip())

        try:
            offsets[prayer] = int(offset) if offset.strip() else 0
        except ValueError:
            raise ConfigError("Invalid offset for " + name.strip() + ": " + offset.strip())

        prayers.append(prayer)

    return prayers, offsets


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


def next_prayer(config, now):
    """Return (prayer, datetime) for the first prayer after now, or None."""
    for day in (now.date(), now.date() + timedelta(days=1)):
        for prayer, at in prayer_times(config, day):
            if at > now:
                return prayer, at
    return None


def config_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def play(azaan, prayer=None):
    log.info("Playing azaan %s%s", azaan, " for " + prayer if prayer else "")
    result = subprocess.run([PLAYER, str(azaan)])
    if result.returncode != 0:
        log.error("%s failed with exit code %d", os.path.basename(PLAYER), result.returncode)
    return result.returncode


def run(config_path, azaan):
    """Wait for each prayer time and play the azaan. Runs until stopped."""
    log.info("PiPrayer started with azaan %s and configuration %s", azaan, config_path)

    while True:
        mtime = config_mtime(config_path)
        config = Config(config_path)
        upcoming = next_prayer(config, datetime.now(config.tz))

        if upcoming is None:
            log.warning("No prayer times found for today or tomorrow, checking again in an hour")
            systime.sleep(3600)
            continue

        prayer, at = upcoming
        log.info("Next azaan: %s at %s", prayer, at.strftime("%Y-%m-%d %H:%M %Z"))

        config_changed = False
        while True:
            remaining = (at - datetime.now(config.tz)).total_seconds()
            if remaining <= 0:
                break
            if config_mtime(config_path) != mtime:
                config_changed = True
                break
            systime.sleep(min(remaining, MAX_SLEEP_SECONDS))

        if config_changed:
            log.info("Configuration changed, reloading")
            continue

        late = datetime.now(config.tz) - at
        if late > MAX_LATENESS:
            log.warning("Skipping %s, noticed %d minutes late (clock changed?)",
                        prayer, late.total_seconds() // 60)
            continue

        play(azaan, prayer)


def main():
    parser = argparse.ArgumentParser(description="PiPrayer - Azaan for Raspberry Pi")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                        help="configuration file (default: %(default)s)")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("times", help="print today's azaan times (default)")

    for name, description in (("run", "run as a service, playing the azaan at each prayer time"),
                              ("play", "play the azaan now, to test the speaker")):
        command = commands.add_parser(name, help=description)
        command.add_argument("-a", "--azaan", type=int, default=1, choices=range(1, 16),
                             metavar="1-15", help="which azaan to play (default: %(default)s)")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        if args.command == "run":
            run(args.config, args.azaan)
        elif args.command == "play":
            return play(args.azaan)
        else:
            config = Config(args.config)
            for prayer, at in prayer_times(config, datetime.now(config.tz).date()):
                print(prayer + "-" + at.strftime("%H:%M"))
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
