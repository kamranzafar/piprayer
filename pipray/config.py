# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading and checking the configuration file, .piprayer."""
import configparser
import difflib
import math
import os
import re
import stat
import threading
from datetime import timedelta, timezone

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
HIGH_LATITUDE = "high-latitude"
MQTT_HOST = "mqtt-host"
MQTT_PORT = "mqtt-port"
MQTT_USERNAME = "mqtt-username"
MQTT_PASSWORD = "mqtt-password"
MQTT_TOPIC = "mqtt-topic"
FALLBACK_OUTPUT = "fallback-output"
FADE_IN = "fade-in"
HIJRI_ADJUST = "hijri-adjust"

OPTIONS = (LAT, LNG, TIMEZONE, DST, GMT_OFFSET, METHOD, ASR_TIME, PRAYERS, AZAAN, FAJR_AZAAN, VOLUME, FADE_IN, REMINDER,
           SPEAKER, FALLBACK_OUTPUT, STATUS_PORT, HIGH_LATITUDE, MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
           MQTT_TOPIC, HIJRI_ADJUST)

DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_TOPIC = "piprayer"
MQTT_TOPIC_NAME = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")

# How Fajr and Isha are set when the night is too short for their angles, as named by praytimes.py:
# the middle of the night, a portion of the night given by the angle, or one seventh of the night
HIGH_LATITUDE_RULES = ["NightMiddle", "AngleBased", "OneSeventh", "None"]
DEFAULT_HIGH_LATITUDE = "NightMiddle"

# The prayers an azaan can be played for, as named by praytimes.py
PRAYER_NAMES = ["fajr", "dhuhr", "asr", "maghrib", "isha"]

# Largest offset in prayers, in minutes either way
MAX_OFFSET = 180

ASR_TIMES = ["Standard", "Hanafi"]

MAC_ADDRESS = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")

# The project folder, which holds piprayer.py, play-azaan.sh and the media folder
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, ".piprayer")
MEDIA_DIR = os.path.join(SCRIPT_DIR, "media")

DEFAULT_AZAAN = 1
DEFAULT_VOLUME = 100

# Longest fade in at the start of an azaan, in seconds
MAX_FADE_IN = 30

# Most days the Hijri date can be shifted either way from the Umm al-Qura calendar
MAX_HIJRI_ADJUST = 2


class ConfigError(Exception):
    pass


class Config:
    """The settings in a configuration file.

    default_azaan is played when the file doesn't set azaan, e.g. the number setup-piprayer.sh was given.
    """

    def __init__(self, path, default_azaan=DEFAULT_AZAAN):
        section = read_section(path)

        try:
            self.lat = parse_coordinate(section[LAT], LAT, "latitude", 90, "-37.909499")
            self.lng = parse_coordinate(section[LNG], LNG, "longitude", 180, "144.751876")
            self.method = parse_method(section[METHOD])
            self.asr_time = parse_choice(section[ASR_TIME], ASR_TIME, ASR_TIMES)
            self.prayers, self.offsets = parse_prayers(section[PRAYERS])
        except KeyError as e:
            raise ConfigError("Missing configuration option: " + e.args[0])

        self.tz = parse_timezone(section)

        # Optional settings
        self.azaan = parse_azaan(section, AZAAN)
        self.fajr_azaan = parse_azaan(section, FAJR_AZAAN)
        self.volume, self.volumes = parse_volume(section.get(VOLUME, ""))
        self.fade_in, self.fade_ins = parse_fade_in(section.get(FADE_IN, ""))
        self.reminder = parse_number(section, REMINDER, 0, 0, 60)
        self.speaker = parse_speaker(section.get(SPEAKER, ""))
        self.fallback_local = parse_fallback(section.get(FALLBACK_OUTPUT, "off"))
        self.status_port = parse_number(section, STATUS_PORT, None, 1, 65535)
        self.high_latitude = parse_high_latitude(section.get(HIGH_LATITUDE, DEFAULT_HIGH_LATITUDE))
        self.mqtt = parse_mqtt(section)
        self.hijri_adjust = parse_number(section, HIJRI_ADJUST, 0, -MAX_HIJRI_ADJUST, MAX_HIJRI_ADJUST)
        # Not errors, as PiPrayer still works, but e.g. status_port for status-port would silently do nothing
        self.warnings = [unknown_option(option, path) for option in section if option not in OPTIONS]
        self.default_azaan = default_azaan

    def azaan_for(self, prayer):
        """The azaan to play for a prayer."""
        if prayer == "fajr" and self.fajr_azaan:
            return self.fajr_azaan
        return self.azaan or self.default_azaan

    def volume_for(self, prayer):
        return self.volumes.get(prayer, self.volume)

    def fade_in_for(self, prayer):
        """Seconds to fade the azaan in over, 0 for none."""
        return self.fade_ins.get(prayer, self.fade_in)

    @property
    def fades_in(self):
        """True if any azaan fades in."""
        return bool(self.fade_in or any(self.fade_ins.values()))


def read_section(path):
    """The [Default] section of the configuration file. Any problem reading it is a ConfigError."""
    # Without % substitution, so a value such as "volume = 80%" or a password is read as it is written
    parser = configparser.ConfigParser(interpolation=None)
    try:
        if not parser.read(path, encoding="utf-8"):
            raise ConfigError("File not found or is inaccessible: " + path)
    except configparser.DuplicateOptionError as e:
        raise ConfigError("{} is set twice in {}, on line {}. Remove one of them".format(e.option, path, e.lineno))
    except configparser.DuplicateSectionError as e:
        raise ConfigError("[{}] appears twice in {}, on line {}. Put all the options in one [{}] section".format(
            e.section, path, e.lineno, e.section))
    except configparser.MissingSectionHeaderError as e:
        raise ConfigError("{} must start with [Default], found on line {}: {}".format(
            path, e.lineno, e.line.strip()))
    except configparser.ParsingError as e:
        # The line is read from the file, as ParsingError holds it as its repr() before Python 3.13
        with open(path, encoding="utf-8") as f:
            text = f.read().splitlines()
        lines = ", ".join("line {}: {}".format(number, text[number - 1].strip() if number <= len(text) else "")
                          for number, _ in e.errors)
        raise ConfigError("Can't read {}, use option = value on each line. Check {}".format(path, lines))
    except configparser.Error as e:
        raise ConfigError("Can't read {}: {}".format(path, " ".join(str(e).split())))
    except UnicodeDecodeError:
        raise ConfigError("Can't read {}, it isn't a text file in UTF-8".format(path))

    if not parser.has_section("Default"):
        raise ConfigError("Prayer configuration is missing: {} needs a [Default] section".format(path))
    return parser["Default"]


# Saving from the status page and MQTT at once would lose one of the changes
_saving = threading.Lock()

# A section header, and an option's name at the start of its line, as configparser reads them
SECTION_LINE = re.compile(r"\[(?P<name>.+)\]")
OPTION_LINE = re.compile(r"(?P<name>[^\s=:][^=:]*?)\s*[=:]")


def save_option(path, option, value):
    """Set an option in the configuration file, changing only its line, so comments and the order are kept.

    configparser would drop the comments when writing the file. The option is added at the end of [Default] if it
    isn't there. The running service picks the change up within a minute, as for any edit.
    """
    with _saving:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines(keepends=True)
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError("Can't read {}: {}".format(path, getattr(e, "strerror", None) or e))

        new_line = "{} = {}\n".format(option, value)
        section = None
        end_of_default = None  # after the last line of [Default] that isn't blank or a comment
        found = False
        result = []
        replacing = False  # skipping the indented lines that continue the old value
        for line in lines:
            if replacing and line.strip() and line[0].isspace():
                continue
            replacing = False
            header = SECTION_LINE.match(line)
            if header:
                section = header.group("name")
            elif section == "Default":
                name = OPTION_LINE.match(line)
                if name and name.group("name").strip().lower() == option and not found:
                    found = replacing = True
                    line = new_line
            result.append(line)
            if section == "Default" and line.strip() and not line.lstrip().startswith(("#", ";")):
                end_of_default = len(result)

        if end_of_default is None:
            raise ConfigError("Prayer configuration is missing: {} needs a [Default] section".format(path))
        if not found:
            if not result[end_of_default - 1].endswith("\n"):
                result[end_of_default - 1] += "\n"
            result.insert(end_of_default, new_line)

        # Write then rename, so the service never reads a half written file, keeping who can read it, as it can
        # hold the MQTT password
        saving = path + ".saving"
        with open(saving, "w", encoding="utf-8") as f:
            os.chmod(saving, mode)
            f.writelines(result)
        os.replace(saving, path)


def set_hijri_adjust(path, value):
    """Save hijri-adjust, the days to shift the Hijri date by. Raises ValueError when it is out of range."""
    try:
        days = check_range(str(value).strip(), HIJRI_ADJUST, -MAX_HIJRI_ADJUST, MAX_HIJRI_ADJUST)
    except ConfigError as e:
        raise ValueError(str(e))
    save_option(path, HIJRI_ADJUST, days)
    return days


def parse_coordinate(value, option, what, limit, example):
    """A latitude or longitude, in decimal degrees."""
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not -limit <= number <= limit:  # also false for nan
        raise ConfigError("Invalid {}: {}, use the {} in decimal degrees from -{} to {}, e.g. {}".format(
            option, shown(value), what, limit, limit, example))
    return number


def parse_method(value):
    """A calculation method, as praytimes.py names it, in any case."""
    methods = list(praytimes.PrayTimes.methods)
    for method in methods:
        if method.lower() == value.strip().lower():
            return method
    close = difflib.get_close_matches(value.strip().lower(), [m.lower() for m in methods], n=1)
    hint = " Did you mean {}?".format(next(m for m in methods if m.lower() == close[0])) if close else ""
    raise ConfigError("Unknown {}: {}, use one of: {}.{}".format(METHOD, shown(value), ", ".join(methods), hint))


def parse_choice(value, option, choices):
    """One of the choices, in any case."""
    for choice in choices:
        if choice.lower() == value.strip().lower():
            return choice
    raise ConfigError("Invalid {}: {}, use {}".format(option, shown(value), " or ".join(choices)))


def shown(value):
    """A value as written, for a message."""
    return value.strip() or "(empty)"


def parse_prayers(value):
    """Parse "Fajr:-11, Dhuhr, ..." into prayer names and offsets in minutes (default 0)."""
    offsets = {}
    prayers = []

    for name, offset in parse_list(value):
        what = "offset for " + name.capitalize()
        offsets[name] = check_range(offset, what, -MAX_OFFSET, MAX_OFFSET) if offset else 0
        prayers.append(name)

    if not prayers:
        raise ConfigError("No prayers in {}, list the prayers to play the azaan for, e.g. {}".format(
            PRAYERS, ", ".join(p.capitalize() for p in PRAYER_NAMES)))
    return prayers, offsets


def parse_volume(value):
    """Parse "80, Fajr:40" into a default volume and per-prayer volumes, in percent."""
    default = DEFAULT_VOLUME
    volumes = {}

    for name, volume in parse_list(value, allow_bare_value=True):
        what = "volume" if name is None else name.capitalize() + " volume"
        if name is not None and not volume:
            raise ConfigError("Missing " + what + ", e.g. " + name.capitalize() + ":50")
        number = check_range(volume[:-1] if volume.endswith("%") else volume, what, 0, 100)
        if name is None:
            default = number
        else:
            volumes[name] = number

    return default, volumes


def parse_fade_in(value):
    """Parse "5, Fajr:15" into a default fade in and per-prayer fade ins, in seconds. None is 0."""
    default = 0
    fades = {}

    for name, seconds in parse_list(value, allow_bare_value=True):
        what = FADE_IN if name is None else name.capitalize() + " " + FADE_IN
        if name is not None and not seconds:
            raise ConfigError("Missing " + what + ", e.g. " + name.capitalize() + ":10")
        number = check_range(seconds, what, 0, MAX_FADE_IN)
        if name is None:
            default = number
        else:
            fades[name] = number

    return default, fades


def parse_list(value, allow_bare_value=False):
    """Split "Name:value, ..." into (prayer, value) pairs. Each prayer, and the bare value, can be given once.

    With allow_bare_value, an item that isn't a prayer name, e.g. "80", is returned as (None, "80").
    """
    result = []

    for item in value.split(","):
        if not item.strip():
            continue

        name, colon, setting = item.partition(":")
        name = name.strip()
        prayer = name.lower()

        if allow_bare_value and not colon and prayer not in PRAYER_NAMES:
            if any(given is None for given, _ in result):
                raise ConfigError("Two values without a prayer in {!r}, give one, and Prayer:value for the others"
                                  .format(value.strip()))
            result.append((None, name))
            continue

        if prayer not in PRAYER_NAMES:
            raise ConfigError("Unknown prayer: {}, use {}".format(
                name, ", ".join(p.capitalize() for p in PRAYER_NAMES[:-1]) + " or Isha"))
        if any(given == prayer for given, _ in result):
            raise ConfigError("{} is listed twice in {!r}".format(name.capitalize(), value.strip()))
        result.append((prayer, setting.strip()))

    return result


def parse_azaan(section, option):
    if option not in section:
        return None

    azaan = parse_number(section, option, None, 1, 99)
    check_azaan_file(option, azaan)
    return azaan


def check_azaan_file(what, azaan):
    if not os.path.isfile(azaan_file(azaan)):
        raise ConfigError("Invalid {}: {}, there is no {}".format(
            what, azaan, os.path.relpath(azaan_file(azaan), SCRIPT_DIR)))


def parse_speaker(value):
    """A Bluetooth MAC address, or None to use any paired speaker."""
    value = value.strip().upper()
    if not value:
        return None
    if not MAC_ADDRESS.match(value):
        raise ConfigError("Invalid speaker: {}, use the Bluetooth address, e.g. AA:BB:CC:DD:EE:FF".format(value))
    return value


def parse_fallback(value):
    """True to play on the Raspberry Pi's own audio output when no speaker connects."""
    value = value.strip().lower()
    if value not in ("local", "off"):
        raise ConfigError("Invalid {}: {}, use local or off".format(FALLBACK_OUTPUT, value))
    return value == "local"


def unknown_option(option, path):
    message = "Unknown option {} in {}, it is ignored".format(option, path)
    close = difflib.get_close_matches(option.replace("_", "-"), OPTIONS, n=1)
    return message + (". Did you mean {}?".format(close[0]) if close else "")


class MqttSettings:
    """Where to publish for Home Assistant. MQTT is off unless mqtt-host is set."""

    def __init__(self, host, port, username, password, topic):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.topic = topic

    def __eq__(self, other):
        return isinstance(other, MqttSettings) and vars(self) == vars(other)


def parse_mqtt(section):
    host = section.get(MQTT_HOST, "").strip()
    if not host:
        return None
    topic = section.get(MQTT_TOPIC, DEFAULT_MQTT_TOPIC).strip()
    if not MQTT_TOPIC_NAME.match(topic):
        raise ConfigError("Invalid {}: {}, use letters, numbers, - and _, with / between levels, e.g. piprayer"
                          .format(MQTT_TOPIC, topic))
    return MqttSettings(host, parse_number(section, MQTT_PORT, DEFAULT_MQTT_PORT, 1, 65535),
                        section.get(MQTT_USERNAME, "").strip() or None,
                        # exactly as written, as passwords can contain %, which configparser would substitute
                        section.get(MQTT_PASSWORD, "", raw=True) or None, topic)


def parse_high_latitude(value):
    for rule in HIGH_LATITUDE_RULES:
        if rule.lower() == value.strip().lower():
            return rule
    raise ConfigError("Invalid {}: {}, use one of: {}".format(HIGH_LATITUDE, value, ", ".join(HIGH_LATITUDE_RULES)))


def parse_number(section, option, default, minimum, maximum):
    if option not in section:
        return default
    return check_range(section[option], option, minimum, maximum)


def check_range(value, what, minimum, maximum):
    try:
        number = int(value)
    except ValueError:
        raise ConfigError("Invalid {}: {}, use a whole number from {} to {}".format(
            what, shown(value), minimum, maximum))
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
            raise ConfigError("Unknown timezone: {}, use a name from the tz database, e.g. Australia/Melbourne"
                              .format(shown(section[TIMEZONE])))

    # Legacy configuration: fixed offset and manual daylight saving flag
    if GMT_OFFSET not in section:
        raise ConfigError("Missing configuration option: timezone")
    value = section[GMT_OFFSET].strip()
    try:
        hours = float(value)
    except ValueError:
        hours = math.nan
    if not -12 <= hours <= 14:  # also false for nan
        raise ConfigError("Invalid {}: {}, use the hours from GMT from -12 to 14, e.g. 10 or 5.5. "
                          "Better, set timezone instead".format(GMT_OFFSET, shown(value)))
    dst = section.get(DST, "0").strip()
    if dst not in ("0", "1"):
        raise ConfigError("Invalid {}: {}, use 1 during daylight saving, otherwise 0".format(DST, shown(dst)))

    return timezone(timedelta(hours=hours + int(dst)))
