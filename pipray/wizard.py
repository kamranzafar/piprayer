# SPDX-License-Identifier: GPL-3.0-or-later
"""piprayer.py config: asks for each setting, shows the resulting times and saves .piprayer."""
import configparser
import os
import socket

import praytimes

from . import bluetooth, clock
from .bluetooth import SCAN_SECONDS
from .config import (AZAAN, ASR_TIME, DEFAULT_HIGH_LATITUDE, DEFAULT_VOLUME, DST, FAJR_AZAAN, GMT_OFFSET,
                     HIGH_LATITUDE, LAT, LNG, MAC_ADDRESS, METHOD, PRAYERS, REMINDER, SPEAKER, STATUS_PORT, TIMEZONE,
                     VOLUME, Config, ConfigError, check_azaan_file, check_range, parse_prayers, parse_volume)
from .output import format_times
from .service import installed_azaan, service_file

# Answers in the speaker and method menus
PAIR = "pair"
SCAN_AGAIN = "scan"
MORE = "more"

PRAYER_NAMES = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
# The first seven keep their numbers from before more methods were added, and are always listed. The others are
# listed on request, except the one suggested for the timezone.
SHORT_METHOD_LIST = 7
METHODS = ["MWL", "ISNA", "Egypt", "Makkah", "Karachi", "Tehran", "Jafari", "Gulf", "Kuwait", "Qatar", "Dubai",
           "Singapore", "Malaysia", "Indonesia", "Turkey", "France", "Russia", "Morocco", "Algeria", "Tunisia",
           "Jordan", "Portugal"]
ASR_METHODS = ["Standard", "Hanafi"]

# Usual calculation method by timezone, MWL elsewhere
REGIONAL_METHODS = [
    ("America/", "ISNA"),
    ("Africa/Cairo", "Egypt"),
    ("Asia/Riyadh", "Makkah"), ("Asia/Aden", "Makkah"),
    ("Asia/Bahrain", "Gulf"), ("Asia/Muscat", "Gulf"),
    ("Asia/Kuwait", "Kuwait"), ("Asia/Qatar", "Qatar"), ("Asia/Dubai", "Dubai"),
    ("Asia/Karachi", "Karachi"), ("Asia/Kolkata", "Karachi"), ("Asia/Dhaka", "Karachi"), ("Asia/Kabul", "Karachi"),
    ("Asia/Tehran", "Tehran"),
    ("Asia/Singapore", "Singapore"),
    ("Asia/Kuala_Lumpur", "Malaysia"), ("Asia/Kuching", "Malaysia"),
    ("Asia/Jakarta", "Indonesia"), ("Asia/Pontianak", "Indonesia"), ("Asia/Makassar", "Indonesia"),
    ("Asia/Jayapura", "Indonesia"),
    ("Europe/Istanbul", "Turkey"),
    ("Europe/Paris", "France"),
    ("Europe/Moscow", "Russia"), ("Europe/Samara", "Russia"), ("Europe/Volgograd", "Russia"),
    ("Europe/Ulyanovsk", "Russia"), ("Europe/Saratov", "Russia"), ("Europe/Astrakhan", "Russia"),
    ("Europe/Kirov", "Russia"), ("Asia/Yekaterinburg", "Russia"), ("Asia/Omsk", "Russia"),
    ("Africa/Casablanca", "Morocco"), ("Africa/Algiers", "Algeria"), ("Africa/Tunis", "Tunisia"),
    ("Asia/Amman", "Jordan"),
    ("Europe/Lisbon", "Portugal"),
]

# Ask how to set Fajr and Isha on short summer nights from this latitude, north or south. The usual angles
# (15 to 20 degrees) stop being reached at some point in summer from about 46.5 to 51.5 degrees.
HIGH_LATITUDE_FROM = 45
HIGH_LATITUDE_CHOICES = [
    ("NightMiddle", "Middle of the night: Isha and Fajr at the midpoint between sunset and sunrise"),
    ("AngleBased", "Angle based: a part of the night set by the angle, e.g. 18/60 of it for 18 degrees"),
    ("OneSeventh", "One seventh of the night after sunset and before sunrise"),
    ("None", "No adjustment: no Fajr or Isha when the sun doesn't reach the angle"),
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
        if what.endswith("azaan"):
            check_azaan_file(what, number)
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




def pair_new_speaker(wizard):
    """Find and pair a speaker. Returns its address, or "" when none was paired."""
    wizard.say("")
    wizard.ask_input("Put the speaker in pairing mode, then press Enter ")
    scan = True
    while True:
        if scan:
            wizard.say("Looking for speakers for {} seconds...".format(SCAN_SECONDS))
            found = bluetooth.scan_speakers()
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
        failure = bluetooth.pair_speaker(mac)
        if failure:
            wizard.say("Could not pair {}: {}".format(name, failure))
            continue
        wizard.say("Paired {}".format(name))
        return mac


def choose_speaker(wizard, current):
    """Ask which paired speaker to use, offering to pair one. Returns its address, or "" for any paired speaker."""
    wizard.say("")
    if not bluetooth.bluetooth_available():
        wizard.say("No Bluetooth adapter found, so the speaker can't be chosen now.")
        return current

    while True:
        speakers = bluetooth.bluetooth_speakers()
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


def choose_method(wizard, default):
    """Ask for the calculation method. Lists the first few and the default, and the rest on request."""
    shown = METHODS[:SHORT_METHOD_LIST] + ([default] if default in METHODS[SHORT_METHOD_LIST:] else [])
    parse_method = choose(METHODS)

    def parse(value):
        return MORE if value.lower() == "m" and len(shown) < len(METHODS) else parse_method(value)

    while True:
        wizard.say("")
        for method in shown:
            wizard.say("  {:>2}. {:10} {}".format(METHODS.index(method) + 1, method,
                                                 praytimes.PrayTimes.methods[method]["name"]))
        if len(shown) < len(METHODS):
            wizard.say("   m. More methods: the Gulf, Southeast Asia, Turkey, France, Russia, North Africa and more")
        choice = wizard.ask("Calculation method", default, parse)
        if choice != MORE:
            return choice
        shown = METHODS


def status_page_address(port):
    """e.g. http://raspberrypi.local:8080, using the name the Raspberry Pi announces on the local network."""
    host = socket.gethostname().split(".")[0] or "raspberrypi"
    return "http://{}.local:{}".format(host, port)


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

    method = choose_method(wizard, current.get(METHOD, regional_method(tz_name)))

    asr_default = current.get(ASR_TIME, "Hanafi" if tz_name in HANAFI_TIMEZONES else "Standard")
    asr = wizard.ask("Asr time (1. Standard, 2. Hanafi)", asr_default, choose(ASR_METHODS))

    # Far from the equator, summer nights can be too short for the Fajr and Isha angles
    high_latitude = ""
    if abs(float(lat)) >= HIGH_LATITUDE_FROM or HIGH_LATITUDE in current:
        wizard.say("")
        wizard.say("Some summer nights here are too short for Fajr and Isha by their angles. Instead, use:")
        for number, (rule, description) in enumerate(HIGH_LATITUDE_CHOICES, 1):
            wizard.say("  {}. {}".format(number, description))
        high_latitude = wizard.ask("Fajr and Isha on short nights",
                                   current.get(HIGH_LATITUDE, DEFAULT_HIGH_LATITUDE),
                                   choose([rule for rule, _ in HIGH_LATITUDE_CHOICES]))

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

    wizard.say("")
    wizard.say("The status page shows the times in a browser, with buttons to skip or mute the azaan.")
    wizard.say("It has no password, so anyone on your network can use it.")
    status_port = wizard.ask("Status page port, e.g. 8080 (none for off)", current.get(STATUS_PORT, ""),
                             number_option(STATUS_PORT, 1, 65535, blank_ok=True))

    settings = [(TIMEZONE, tz_name), (LAT, lat), (LNG, lng), (METHOD, method), (ASR_TIME, asr),
                (PRAYERS, prayers), (AZAAN, azaan), (FAJR_AZAAN, fajr_azaan), (VOLUME, volume),
                (REMINDER, reminder), (SPEAKER, speaker), (HIGH_LATITUDE, high_latitude),
                (STATUS_PORT, status_port)]

    # Keep any other options, but timezone replaces the legacy ones
    result = configparser.ConfigParser()
    result.add_section("Default")
    for option, value in settings:
        if value != "":
            result["Default"][option] = value
    for option, value in current.items():
        if option not in result["Default"] and option not in (GMT_OFFSET, DST, FAJR_AZAAN, SPEAKER, STATUS_PORT):
            result["Default"][option] = value

    preview = path + ".new"
    with open(preview, "w") as f:
        result.write(f)
    try:
        config = Config(preview)
        now = clock.now(config.tz)
        wizard.say("")
        wizard.say(format_times(config, now.date(), now))
        wizard.say("")
        if not wizard.confirm("Save to " + path + "?"):
            wizard.say("Not saved")
            return 1
        os.replace(preview, path)
    finally:
        if os.path.exists(preview):
            os.remove(preview)

    wizard.say("Saved " + path)
    if status_port:
        wizard.say("Status page: {} from another device on your network".format(status_page_address(status_port)))
    if next_step:
        if os.path.exists(service_file()):
            wizard.say("The PiPrayer service will use the new settings within a minute.")
        else:
            wizard.say("Run ./setup-piprayer.sh to install the PiPrayer service.")
    return 0
