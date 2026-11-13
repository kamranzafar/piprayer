# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading and checking the configuration file, .piprayer."""
import configparser
import difflib
import os
import re
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

OPTIONS = (LAT, LNG, TIMEZONE, DST, GMT_OFFSET, METHOD, ASR_TIME, PRAYERS, AZAAN, FAJR_AZAAN, VOLUME, REMINDER,
           SPEAKER, STATUS_PORT, HIGH_LATITUDE, MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, MQTT_TOPIC)

DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_TOPIC = "piprayer"
MQTT_TOPIC_NAME = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")

# How Fajr and Isha are set when the night is too short for their angles, as named by praytimes.py:
# the middle of the night, a portion of the night given by the angle, or one seventh of the night
HIGH_LATITUDE_RULES = ["NightMiddle", "AngleBased", "OneSeventh", "None"]
DEFAULT_HIGH_LATITUDE = "NightMiddle"

MAC_ADDRESS = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")

# The project folder, which holds piprayer.py, play-azaan.sh and the media folder
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, ".piprayer")
MEDIA_DIR = os.path.join(SCRIPT_DIR, "media")

DEFAULT_AZAAN = 1
DEFAULT_VOLUME = 100


class ConfigError(Exception):
    pass


class Config:
    """The settings in a configuration file.

    default_azaan is played when the file doesn't set azaan, e.g. the number setup-piprayer.sh was given.
    """

    def __init__(self, path, default_azaan=DEFAULT_AZAAN):
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
        self.high_latitude = parse_high_latitude(section.get(HIGH_LATITUDE, DEFAULT_HIGH_LATITUDE))
        self.mqtt = parse_mqtt(section)
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
