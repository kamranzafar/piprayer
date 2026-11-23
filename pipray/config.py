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
import tempfile
import threading
from datetime import timedelta, timezone

import praytimes

from .pin import is_hash, valid_pin

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
RAMADAN = "ramadan"
SUHOOR = "suhoor"
DUA = "dua"
SETTINGS_PIN = "settings-pin"
UPDATE_CHECK = "update-check"
AUTO_UPDATE = "auto-update"
KAHF = "kahf"
KAHF_FILE = "kahf-file"

OPTIONS = (LAT, LNG, TIMEZONE, DST, GMT_OFFSET, METHOD, ASR_TIME, PRAYERS, AZAAN, FAJR_AZAAN, VOLUME, FADE_IN, REMINDER,
           SPEAKER, FALLBACK_OUTPUT, STATUS_PORT, HIGH_LATITUDE, MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
           MQTT_TOPIC, HIJRI_ADJUST, RAMADAN, SUHOOR, DUA, SETTINGS_PIN, UPDATE_CHECK, AUTO_UPDATE, KAHF, KAHF_FILE)

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

# The project folder, which holds piprayer.py, the pipray package and the media folder
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, ".piprayer")
MEDIA_DIR = os.path.join(SCRIPT_DIR, "media")
DUA_FILE = os.path.join(MEDIA_DIR, "dua.opus")
# Surah Al-Kahf, played before Jumu'ah when kahf-file doesn't give another recording
KAHF_SOUND = os.path.join(MEDIA_DIR, "kahf.opus")

DEFAULT_AZAAN = 1
DEFAULT_VOLUME = 100

# Longest fade in at the start of an azaan, in seconds
MAX_FADE_IN = 30

# Most days the Hijri date can be shifted either way from the Umm al-Qura calendar
MAX_HIJRI_ADJUST = 2

# auto: the days of Ramadan by the Hijri date are days of fasting. on: every day is, e.g. when Ramadan starts on another
# day where you are, or to fast outside Ramadan. off: none are
RAMADAN_MODES = ["auto", "on", "off"]

# Longest time the suhoor alarm sounds before Fajr, in minutes
MAX_SUHOOR = 180

# The hours before Jumu'ah that Surah Al-Kahf can start, as kahf takes them, e.g. 1h. Whole hours leave the
# recitation, about half an hour, time to finish before the reminder and the azaan
KAHF_HOURS = ["1h", "2h", "3h"]
KAHF_CHOICES = ["off"] + KAHF_HOURS


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
        self.dua, self.duas = parse_dua(section.get(DUA, ""))
        self.reminder = parse_number(section, REMINDER, 0, 0, 60)
        self.speaker = parse_speaker(section.get(SPEAKER, ""))
        self.fallback_local = parse_fallback(section.get(FALLBACK_OUTPUT, "off"))
        self.status_port = parse_number(section, STATUS_PORT, None, 1, 65535)
        self.high_latitude = parse_high_latitude(section.get(HIGH_LATITUDE, DEFAULT_HIGH_LATITUDE))
        self.mqtt = parse_mqtt(section)
        self.hijri_adjust = parse_number(section, HIJRI_ADJUST, 0, -MAX_HIJRI_ADJUST, MAX_HIJRI_ADJUST)
        self.ramadan = parse_choice(section.get(RAMADAN, "auto"), RAMADAN, RAMADAN_MODES)
        self.suhoor = parse_number(section, SUHOOR, 0, 0, MAX_SUHOOR)  # minutes before Fajr, 0 for no alarm
        self.settings_pin = parse_settings_pin(section.get(SETTINGS_PIN, ""))
        # Check GitHub once a day for a new version, and update by itself
        self.update_check = parse_choice(section.get(UPDATE_CHECK, "on"), UPDATE_CHECK, ["on", "off"]) == "on"
        self.auto_update = parse_choice(section.get(AUTO_UPDATE, "off"), AUTO_UPDATE, ["on", "off"]) == "on"
        # Not errors, as PiPrayer still works, but e.g. status_port for status-port would silently do nothing
        self.warnings = [unknown_option(option, path) for option in section if option not in OPTIONS]
        # Surah Al-Kahf on Fridays: minutes before Jumu'ah, 0 for none, and the recording to play
        self.kahf = parse_kahf(section.get(KAHF, "off"))  # in minutes, 0 for off
        self.kahf_file = parse_kahf_file(section.get(KAHF_FILE, ""), path, self.kahf, self.warnings)
        if self.kahf and self.kahf <= self.reminder:
            self.warnings.append("{} = 1h starts Surah Al-Kahf with the reminder for Dhuhr, 60 minutes before it, so "
                                 "it doesn't play. Use {} = 2h, or a shorter reminder".format(KAHF, KAHF))
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

    def dua_for(self, prayer):
        """True to play the dua after the prayer's azaan."""
        return self.duas.get(prayer, self.dua)

    @property
    def plays_dua(self):
        """True if the dua is played after any azaan."""
        return bool(self.dua or any(self.duas.values()))

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
    save_options(path, {option: value})


def save_options(path, changes, check=None):
    """Change options in the configuration file at once, keeping its comments and order.

    changes maps each option to its new value, or to None to remove it, so its default applies. check, if given, is
    called with the path of the changed file before it replaces this one, e.g. to read it with Config: if it raises,
    nothing is saved.
    """
    with _saving:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines(keepends=True)
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError("Can't read {}: {}".format(path, getattr(e, "strerror", None) or e))

        result = changed_lines(path, lines, changes)

        # Write then rename, so the service never reads a half written file, keeping who can read it, as it can
        # hold the MQTT password
        directory = os.path.dirname(os.path.abspath(path))
        handle, saving = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".saving", dir=directory)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                os.chmod(saving, mode)
                f.writelines(result)
            if check:
                check(saving)
            os.replace(saving, path)
        finally:
            if os.path.exists(saving):
                os.remove(saving)


def changed_lines(path, lines, changes):
    """The lines of the configuration file with the changes made. path is for messages."""
    changes = {option.lower(): value for option, value in changes.items()}
    for option, value in changes.items():
        if value is not None and re.search(r"[\r\n]", str(value)):
            raise ConfigError("Invalid {}: it can't have a line break, which would start another option".format(
                option))
    section = None
    end_of_default = None  # after the last line of [Default] that isn't blank or a comment
    found = set()
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
            option = name.group("name").strip().lower() if name else None
            if option in changes and option not in found:
                found.add(option)
                replacing = True
                if changes[option] is None:
                    continue
                line = "{} = {}\n".format(option, changes[option])
        result.append(line)
        if section == "Default" and line.strip() and not line.lstrip().startswith(("#", ";")):
            end_of_default = len(result)

    if end_of_default is None:
        raise ConfigError("Prayer configuration is missing: {} needs a [Default] section".format(path))
    added = ["{} = {}\n".format(option, value) for option, value in changes.items()
             if option not in found and value is not None]
    if added:
        if not result[end_of_default - 1].endswith("\n"):
            result[end_of_default - 1] += "\n"
        result[end_of_default:end_of_default] = added
    return result


def set_hijri_adjust(path, value):
    """Save hijri-adjust, the days to shift the Hijri date by. Raises ValueError when it is out of range."""
    try:
        days = check_range(str(value).strip(), HIJRI_ADJUST, -MAX_HIJRI_ADJUST, MAX_HIJRI_ADJUST)
    except ConfigError as e:
        raise ValueError(str(e))
    save_option(path, HIJRI_ADJUST, days)
    return days


def set_kahf(path, value):
    """Save kahf, the hours before Jumu'ah to start Surah Al-Kahf, e.g. 1h, or off. Returns it as saved. Raises
    ValueError when it isn't one of KAHF_CHOICES, or the configuration can't play it."""
    try:
        parse_kahf(str(value))
        choice = str(value).strip().lower()
        save_options(path, {KAHF: choice}, check=Config)
    except ConfigError as e:
        raise ValueError(str(e))
    return choice


def parse_kahf(value):
    """kahf, e.g. 1h, as the minutes before Jumu'ah to start Surah Al-Kahf, 0 for off."""
    choice = value.strip().lower()
    if choice not in KAHF_CHOICES:
        raise ConfigError("Invalid {}: {}, use {} or off, the hours before Jumu'ah to start Surah Al-Kahf".format(
            KAHF, shown(value), ", ".join(KAHF_HOURS)))
    return int(choice[:-1]) * 60 if choice != "off" else 0


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


def parse_dua(value):
    """Parse "on, Fajr:off" into whether to play the dua after the azaan, and for which prayers. Off unless set."""
    default = False
    duas = {}

    for name, setting in parse_list(value, allow_bare_value=True):
        what = DUA if name is None else name.capitalize() + " " + DUA
        if name is not None and not setting:
            raise ConfigError("Missing " + what + ", e.g. " + name.capitalize() + ":on")
        if setting.lower() not in ("on", "off"):
            raise ConfigError("Invalid {}: {}, use on or off, e.g. on, Fajr:off".format(what, shown(setting)))
        if name is None:
            default = setting.lower() == "on"
        else:
            duas[name] = setting.lower() == "on"

    if (default or any(duas.values())) and not os.path.isfile(DUA_FILE):
        raise ConfigError("{} is on, but there is no {}. Add a recording of the dua there, or turn it off".format(
            DUA, os.path.relpath(DUA_FILE, SCRIPT_DIR)))
    return default, duas


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


def parse_kahf_file(value, path, kahf, warnings):
    """The recording of Surah Al-Kahf to play: kahf-file, or the one PiPrayer comes with.

    A relative path is from the folder of the configuration file. When kahf-file can't be found, the one PiPrayer
    comes with plays instead, with a warning, so a mistake doesn't stop it.
    """
    value = value.strip()
    if value:
        given = os.path.join(os.path.dirname(os.path.abspath(path)), os.path.expanduser(value))
        if os.path.isfile(given):
            return given
        warnings.append("{} {} isn't found, playing the recording of Surah Al-Kahf PiPrayer comes with".format(
            KAHF_FILE, value))
    if kahf and not os.path.isfile(KAHF_SOUND):
        raise ConfigError("{} is on, but there is no {}. Set {} to a recording of Surah Al-Kahf, or turn it off"
                          .format(KAHF, os.path.relpath(KAHF_SOUND, SCRIPT_DIR), KAHF_FILE))
    return KAHF_SOUND


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


def parse_settings_pin(value):
    """The settings page PIN as saved: a hash, or digits not hashed yet. None when there is none."""
    value = value.strip()
    if not value:
        return None
    if not (is_hash(value) or valid_pin(value)):
        raise ConfigError("Invalid {}: use 4 to 8 digits, e.g. 4826. Set it with python3 piprayer.py config, which "
                          "saves it as a hash".format(SETTINGS_PIN))
    return value


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
