# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The settings page, /settings: change .piprayer from a phone or computer, after entering the settings PIN.

Each section has its own Save, and saves only the options that changed, with save_options, so the comments and
order of .piprayer are kept. Settings are checked with the same code as .piprayer, and a mistake is shown next to
its field. Without JavaScript, the forms post and the page reloads. With htmx, each field is checked as it is typed,
and saving updates the section in place.
"""
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from html import escape
from typing import Callable, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import praytimes

from . import __version__, bluetooth, clock, update
from .config import (ASR_TIME, ASR_TIMES, AZAAN, DEFAULT_HIGH_LATITUDE, DEFAULT_MQTT_PORT, DEFAULT_MQTT_TOPIC,
                     DEFAULT_VOLUME, DST, DUA, FADE_IN, FAJR_AZAAN, FALLBACK_OUTPUT, GMT_OFFSET, HIGH_LATITUDE,
                     HIGH_LATITUDE_RULES, JUMMAH_TIME, KAHF, KAHF_CHOICES, KAHF_FILE, LAT, LNG, MAC_ADDRESS,
                     MEDIA_DIR, MAX_FADE_IN, MAX_OFFSET, MAX_SUHOOR, METHOD, MQTT_HOST, MQTT_PASSWORD, MQTT_PORT,
                     MQTT_TOPIC, MQTT_TOPIC_NAME, MQTT_USERNAME, PRAYER_NAMES, PRAYERS, RAMADAN, RAMADAN_MODES,
                     REMINDER, SPEAKER, SUHOOR, TIMEZONE, VOLUME, Config, ConfigError, azaan_file, changed_lines,
                     check_range, kahf_file_path, parse_choice, parse_coordinate, parse_high_latitude,
                     parse_jummah_time, parse_method, read_section, save_options)
from .pairing import PAIRING, Pairing
from .pin import COOKIE, SESSION_SECONDS, Throttle, check_pin, session_cookie, valid_session
from .schedule import schedule
from .state import Playing, playing_file
from .web import HTMX, STATUS_STYLE, Response, times_list, update_html

# The sound files the page can play, to hear them before choosing
MEDIA = re.compile(r"^/media/(azaan-[0-9]{1,2}\.opus|dua\.opus|suhoor\.opus|kahf\.opus|reminder\.wav)$")

HIGH_LATITUDE_LABELS = {
    "NightMiddle": "Middle of the night",
    "AngleBased": "A part of the night set by the angle",
    "OneSeventh": "One seventh of the night",
    "None": "No adjustment",
}
# Line breaks and other control characters, which a setting can't hold
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SINGLE_LINE = "Use a single line, without line breaks or tabs"

RAMADAN_LABELS = {"auto": "Ramadan, by the Hijri date", "on": "Every day", "off": "Off"}
KAHF_LABELS = {"off": "Off", "1h": "1 hour before", "2h": "2 hours before", "3h": "3 hours before"}


def azaan_numbers():
    return [number for number in range(1, 100) if os.path.isfile(azaan_file(number))]


# ------------------------------------------------------------------ fields

@dataclass
class Field:
    """A setting on the page, saved as an option in .piprayer."""
    option: str
    label: str
    check: Callable[[str], str]  # the value to save, from what was entered, or raises ConfigError
    default: str = ""  # used when the option isn't in .piprayer
    kind: str = "text"  # text, number, decimal, select or password
    choices: Callable[[], list] = None  # [(value, label)] for a select
    hint: str = ""
    listen: str = ""  # for a sound: the file to hear, with {} for the value chosen
    required: bool = False

    @property
    def name(self):
        return self.option


def checked(parse):
    """A check that parses the value, raising ConfigError if it is wrong, and saves it as entered."""

    def check(value):
        parse(value)
        return value

    return check


def number(option, label, minimum, maximum):
    """A whole number in the range, saved as one, e.g. 5 for +5 or 05."""
    return lambda value: str(check_range(value, label or option, minimum, maximum))


def timezone_check(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError("Unknown timezone: {}, use a name from the tz database, e.g. Australia/Melbourne"
                          .format(value))
    return value


def azaan_check(option):
    def check(value):
        number = check_range(value, option, 1, 99)
        if not os.path.isfile(azaan_file(number)):
            raise ConfigError("Invalid {}: {}, there is no azaan {}".format(option, value, number))
        return str(number)

    return check


def kahf_file_check(config_path):
    """kahf-file must be a file on the Raspberry Pi, from the folder of .piprayer when it is relative."""
    def check(value):
        if not os.path.isfile(kahf_file_path(config_path, value)):
            raise ConfigError("There is no file {} on the Raspberry Pi".format(value))
        return value

    return check


def mqtt_host_check(value):
    if any(c.isspace() for c in value) or "/" in value:
        raise ConfigError("Invalid {}: {}, use a hostname or address, e.g. homeassistant.local"
                          .format(MQTT_HOST, value))
    return value


def mqtt_topic_check(value):
    if not MQTT_TOPIC_NAME.match(value):
        raise ConfigError("Invalid {}: {}, use letters, numbers, - and _, with / between levels, e.g. piprayer"
                          .format(MQTT_TOPIC, value))
    return value


def speaker_check(value):
    if not MAC_ADDRESS.match(value.upper()):
        raise ConfigError("Invalid speaker: {}, use the Bluetooth address, e.g. AA:BB:CC:DD:EE:FF".format(value))
    return value.upper()


def method_choices():
    return [(method, "{} · {}".format(method, details["name"]))
            for method, details in praytimes.PrayTimes.methods.items()]


def azaan_choices():
    return [(str(n), "Azaan {}{}".format(n, " (Fajr)" if n >= 14 else "")) for n in azaan_numbers()]


# ------------------------------------------------------------------ sections

@dataclass
class Section:
    id: str
    title: str
    fields: List[Field] = field(default_factory=list)
    description: str = ""
    preview: bool = False  # shows the times the new settings give before saving


def sections(default_azaan, config_path):
    return [
        Section("times", "Prayer times", [
            Field(LAT, "Latitude", checked(lambda v: parse_coordinate(v, LAT, "latitude", 90, "-37.909499")),
                  kind="decimal", required=True),
            Field(LNG, "Longitude", checked(lambda v: parse_coordinate(v, LNG, "longitude", 180, "144.751876")),
                  kind="decimal", required=True),
            Field(TIMEZONE, "Timezone", timezone_check, required=True, hint="e.g. Australia/Melbourne"),
            Field(METHOD, "Calculation method", lambda v: parse_method(v), kind="select", choices=method_choices,
                  required=True),
            Field(ASR_TIME, "Asr time", lambda v: parse_choice(v, ASR_TIME, ASR_TIMES), kind="select",
                  choices=lambda: [(choice, choice) for choice in ASR_TIMES], required=True),
            Field(HIGH_LATITUDE, "Fajr and Isha on short summer nights", lambda v: parse_high_latitude(v),
                  default=DEFAULT_HIGH_LATITUDE, kind="select",
                  choices=lambda: [(rule, HIGH_LATITUDE_LABELS[rule]) for rule in HIGH_LATITUDE_RULES]),
        ], "From your coordinates, e.g. from Google Maps: right-click your home and click the numbers.", True),
        Section("prayers", "Prayers", description="The prayers to play the azaan for, the minutes to move each "
                "time by, and each one's volume and fade in."),
        Section("sound", "Azaan and sound", [
            Field(AZAAN, "Azaan", azaan_check(AZAAN), default=str(default_azaan), kind="select",
                  choices=azaan_choices, listen="azaan-{}.opus"),
            Field(FAJR_AZAAN, "Fajr azaan", azaan_check(FAJR_AZAAN), kind="select",
                  choices=lambda: [("", "The same as the others")] + azaan_choices(), listen="azaan-{}.opus",
                  hint="14 to 17 are Fajr azaans, with as-salatu khayrun min an-nawm"),
            Field(REMINDER, "Reminder chime, minutes before each azaan", number(REMINDER, None, 0, 60),
                  default="0", kind="number", hint="0 for none", listen="reminder.wav"),
            Field(FALLBACK_OUTPUT, "When the speaker doesn't connect",
                  checked(lambda v: parse_choice(v, FALLBACK_OUTPUT, ["off", "local"])), default="off",
                  kind="select", choices=lambda: [("off", "Don't play"),
                                                  ("local", "Play on the Raspberry Pi's own audio output")]),
        ]),
        Section("ramadan", "Ramadan", [
            Field(SUHOOR, "Suhoor alarm, minutes before Fajr", number(SUHOOR, None, 0, MAX_SUHOOR), default="0",
                  kind="number", hint="0 for none", listen="suhoor.opus"),
            Field(RAMADAN, "Days of fasting", lambda v: parse_choice(v, RAMADAN, RAMADAN_MODES), default="auto",
                  kind="select", choices=lambda: list(RAMADAN_LABELS.items())),
        ], "Counts down to iftar, and sounds the suhoor alarm, on the days of fasting."),
        Section("jummah", "Jumu'ah", [
            Field(JUMMAH_TIME, "Time of the khutbah", checked(parse_jummah_time),
                  hint="e.g. 13:30, as your mosque holds it. Empty for the time of Dhuhr"),
            Field(KAHF, "Surah Al-Kahf before Jumu'ah", lambda v: parse_choice(v, KAHF, KAHF_CHOICES),
                  default="off", kind="select",
                  choices=lambda: [(choice, KAHF_LABELS[choice]) for choice in KAHF_CHOICES],
                  hint="It stops 2 minutes before the reminder or azaan for Dhuhr", listen="kahf.opus"),
            Field(KAHF_FILE, "Recording of Surah Al-Kahf", kahf_file_check(config_path),
                  hint="A file on the Raspberry Pi, empty for Saad Al-Ghamdi's, which is 29 minutes"),
        ], "Plays Surah Al-Kahf on Fridays, before Jumu'ah."),
        Section("speaker", "Speaker", [
            Field(SPEAKER, "Speaker", speaker_check, kind="select"),
        ], "PiPrayer plays on this speaker. Pair a new one below."),
        Section("mqtt", "Home Assistant", [
            Field(MQTT_HOST, "MQTT broker", mqtt_host_check, hint="e.g. homeassistant.local, empty for off"),
            Field(MQTT_PORT, "MQTT port", number(MQTT_PORT, None, 1, 65535), default=str(DEFAULT_MQTT_PORT),
                  kind="number"),
            Field(MQTT_USERNAME, "MQTT username", lambda v: v),
            Field(MQTT_PASSWORD, "MQTT password", lambda v: v, kind="password"),
            Field(MQTT_TOPIC, "MQTT topic", mqtt_topic_check, default=DEFAULT_MQTT_TOPIC),
        ], "Shows PiPrayer in Home Assistant, over MQTT."),
    ]


# ------------------------------------------------------------------ the prayers section

PRAYER_COLUMNS = [("offset", "Offset, min"), ("volume", "Volume, %"), ("fade", "Fade in, s")]


def prayer_values(config):
    """The prayers section's fields from the configuration, e.g. {"on-fajr": "on", "volume-fajr": "40"}."""
    values = {}
    for prayer in PRAYER_NAMES:
        if config is None:
            values.update({"on-" + prayer: "on", "offset-" + prayer: "0", "volume-" + prayer: str(DEFAULT_VOLUME),
                           "fade-" + prayer: "0"})
            continue
        if prayer in config.prayers:
            values["on-" + prayer] = "on"
        values["offset-" + prayer] = str(config.offsets.get(prayer, 0))
        values["volume-" + prayer] = str(config.volume_for(prayer))
        values["fade-" + prayer] = str(config.fade_in_for(prayer))
        if config.dua_for(prayer):
            values["dua-" + prayer] = "on"
    return values


def prayer_changes(values, config, dua_available):
    """The options the prayers section changes, and the errors, by field."""
    errors = {}
    offsets, volumes, fades, duas = {}, {}, {}, {}
    for prayer in PRAYER_NAMES:
        name = prayer.capitalize()
        for column, store, label, minimum, maximum in (
                ("offset", offsets, name + " offset", -MAX_OFFSET, MAX_OFFSET),
                ("volume", volumes, name + " volume", 0, 100),
                ("fade", fades, name + " fade in", 0, MAX_FADE_IN)):
            value = values.get(column + "-" + prayer, "").strip().rstrip("%")
            if not value and column == "volume":  # rather than 0, which would play it silently
                errors[column + "-" + prayer] = "Enter the {} volume, from 0 to 100".format(name)
                continue
            value = value or "0"
            try:
                store[prayer] = check_range(value, label, minimum, maximum)
            except ConfigError as e:
                errors[column + "-" + prayer] = str(e)
        duas[prayer] = values.get("dua-" + prayer) == "on"

    chosen = [prayer for prayer in PRAYER_NAMES if values.get("on-" + prayer) == "on"]
    if not chosen:
        errors["prayers"] = "Choose at least one prayer"
    if errors:
        return {}, errors

    changes = {}
    if config is None or sorted((p, config.offsets.get(p, 0)) for p in config.prayers) != \
            sorted((p, offsets[p]) for p in chosen):
        changes[PRAYERS] = ", ".join(p.capitalize() + (":{}".format(offsets[p]) if offsets[p] else "")
                                     for p in chosen)
    if config is None or [config.volume_for(p) for p in PRAYER_NAMES] != [volumes[p] for p in PRAYER_NAMES]:
        changes[VOLUME] = per_prayer(volumes, DEFAULT_VOLUME)
    if config is None or [config.fade_in_for(p) for p in PRAYER_NAMES] != [fades[p] for p in PRAYER_NAMES]:
        changes[FADE_IN] = per_prayer(fades, 0)
    if dua_available and (config is None or [config.dua_for(p) for p in PRAYER_NAMES] !=
                          [duas[p] for p in PRAYER_NAMES]):
        changes[DUA] = per_prayer({p: "on" if on else "off" for p, on in duas.items()}, "off")
    return changes, {}


def per_prayer(values, neutral):
    """e.g. "80, Fajr:40" for {"fajr": 40, "dhuhr": 80, ...}: the most common value, and the others by prayer.

    None, to remove the option, when every prayer has the neutral value, its default.
    """
    common = Counter(values.values()).most_common(1)[0][0]
    items = [] if common == neutral else [str(common)]
    items += ["{}:{}".format(p.capitalize(), value) for p, value in values.items() if value != common]
    return ", ".join(items) or None


# ------------------------------------------------------------------ the routes

class SettingsRoutes:
    """GET and POST /settings/..., for the status page's handler."""

    def __init__(self, config_path, default_azaan, throttle=None, speakers=None, pairing=None):
        self.config_path = config_path
        self.default_azaan = default_azaan
        self.sections = {section.id: section for section in sections(default_azaan, config_path)}
        self.throttle = throttle or Throttle()
        self.speakers = speakers or bluetooth.bluetooth_speakers
        self.pairing = pairing or Pairing(playing=self.playing)

    def playing(self):
        """Whether an Azaan, reminder or suhoor alarm plays now."""
        playing = Playing.load(playing_file(self.config_path))
        return bool(playing and not playing.stopped)

    # -------------------------------------------------------------- reading

    def raw(self):
        """The options as written in .piprayer."""
        return dict(read_section(self.config_path))

    def config(self):
        try:
            return Config(self.config_path, self.default_azaan)
        except ConfigError:
            return None

    def saved_pin(self):
        """The PIN as saved, or None. Read from the file as written, so a mistake elsewhere doesn't lock it."""
        try:
            pin = self.raw().get("settings-pin", "").strip()
        except ConfigError:
            return None
        return pin or None

    def logged_in(self, cookies):
        pin = self.saved_pin()
        return bool(pin and valid_session(cookies.get(COOKIE), pin))

    # -------------------------------------------------------------- requests

    def get(self, path, query, cookies, htmx=False):
        if path.startswith("/media/"):
            match = MEDIA.match(path)
            return media(match.group(1)) if match else None
        pin = self.saved_pin()
        if pin is None:
            return Response.html(settings_page("Settings", NO_PIN))
        if not valid_session(cookies.get(COOKIE), pin):
            return Response.html(settings_page("Settings", login_form()))
        if path == "/settings/speaker":
            return Response.html(self.section_html(self.sections["speaker"], load_speakers=True))
        if path != "/settings":
            return None
        saved = query.get("saved", [""])[0]
        return Response.html(self.page(saved=saved, load_speakers=query.get("speakers") == ["1"]))

    def post(self, path, form, cookies, htmx=False):
        if path == "/settings/login":
            return self.login(form.get("pin", [""])[0].strip())
        if path == "/settings/logout":
            return Response(303, location="/", headers={"Set-Cookie": cookie_header("", 0)})
        if not self.logged_in(cookies):
            # e.g. the login has expired, or the PIN was changed
            return Response(303, location="/settings", headers={"HX-Redirect": "/settings"} if htmx else None)

        values = {name: values[0].strip() for name, values in form.items()}
        if path.startswith("/settings/speaker/"):
            return self.speaker_action(path[len("/settings/speaker/"):], values)
        parts = path.split("/")  # "", "settings", ["check",] section
        if len(parts) == 4 and parts[2] == "check" and parts[3] in self.sections:
            return Response.html(self.check(self.sections[parts[3]], values))
        if len(parts) == 3 and parts[2] in self.sections:
            return self.save(self.sections[parts[2]], values)
        return None

    def login(self, pin):
        saved = self.saved_pin()
        if saved is None:
            return Response.html(settings_page("Settings", NO_PIN))
        wait = self.throttle.wait()
        if wait:
            return Response.html(settings_page("Settings", login_form(
                "Too many wrong PINs. Try again in {}.".format(describe_wait(wait)))))
        if not check_pin(pin, saved):
            self.throttle.failed()
            return Response.html(settings_page("Settings", login_form("Wrong PIN. Try again in {}.".format(
                describe_wait(self.throttle.wait())))))
        self.throttle.succeeded()
        return Response(303, location="/settings",
                        headers={"Set-Cookie": cookie_header(session_cookie(saved), SESSION_SECONDS)})

    # -------------------------------------------------------------- changing

    def changes(self, section, values):
        """The options the section changes, as {option: value, or None to remove it}, and errors by field."""
        # A line break would add a line to .piprayer, so e.g. a username could set options not on this page
        lines = [name for name, value in values.items() if CONTROL.search(value)]
        if lines:
            names = self.field_names(section)
            return {}, {name if name in names else "section-" + section.id: SINGLE_LINE for name in lines}
        if section.id == "prayers":
            return prayer_changes(values, self.config(), os.path.isfile(os.path.join(MEDIA_DIR, "dua.opus")))

        raw = self.raw()
        changes, errors = {}, {}
        for item in section.fields:
            value = values.get(item.name, "")
            if item.kind == "password":
                if values.get("remove-" + item.name) == "on":
                    if item.option in raw:
                        changes[item.option] = None
                elif value:
                    changes[item.option] = value
                continue
            if not value:
                if item.required:
                    errors[item.name] = "Enter the " + item.label.lower()
                elif item.option in raw:
                    changes[item.option] = None
                continue
            try:
                value = item.check(value)
            except ConfigError as e:
                errors[item.name] = str(e)
                continue
            if value != raw.get(item.option, item.default).strip():
                changes[item.option] = value

        # timezone replaces the fixed offset of an old configuration
        if TIMEZONE in changes and changes[TIMEZONE]:
            for option in (GMT_OFFSET, DST):
                if option in raw:
                    changes[option] = None
        return changes, errors

    def preview(self, changes):
        """The configuration the changes would give, read from a copy of .piprayer. Raises ConfigError."""
        with open(self.config_path, encoding="utf-8") as f:
            lines = changed_lines(self.config_path, f.read().splitlines(keepends=True), changes)
        handle, path = tempfile.mkstemp(suffix=".preview")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return Config(path, self.default_azaan)
        finally:
            os.remove(path)

    def field_names(self, section):
        if section.id == "prayers":
            return ["{}-{}".format(column, prayer) for prayer in PRAYER_NAMES for column, _ in PRAYER_COLUMNS] + \
                ["prayers"]
        return [item.name for item in section.fields]

    def check(self, section, values):
        """The section's errors, to show as each field is typed, replacing those shown."""
        changes, errors = self.changes(section, values)
        if not errors and changes:
            try:
                self.preview(changes)
            except ConfigError as e:
                errors = self.place_error(section, str(e))
        names = self.field_names(section) + ["section-" + section.id]
        return "".join(error_span(name, errors.get(name), oob=True) for name in names)

    def save(self, section, values):
        changes, errors = self.changes(section, values)
        times = None
        if not errors:
            try:
                if section.preview and values.get("save") != "1":
                    times = self.preview(changes)
                elif changes:
                    save_options(self.config_path, changes, check=lambda path: Config(path, self.default_azaan))
            except ConfigError as e:
                errors = self.place_error(section, str(e))
        if errors or times:
            return Response.html(self.page(section.id, values, errors, preview=times))
        return Response(303, location="/settings?saved={0}#{0}".format(section.id))

    def speaker_action(self, action, values):
        """Find, pair, forget or use a speaker, from the buttons below the speaker section's list."""
        mac = values.get("mac", "").upper()
        if action != "scan" and not MAC_ADDRESS.match(mac):
            return None
        name = values.get("name") or None
        if action == "scan":
            self.pairing.scan()
        elif action == "pair":
            self.pairing.pair(mac, name, again=values.get("again") == "1")
        elif action == "forget":
            if mac != self.raw().get(SPEAKER, "").strip().upper():  # not the one PiPrayer plays on
                self.pairing.forget(mac, name)
        elif action == "use":
            save_options(self.config_path, {SPEAKER: mac}, check=lambda path: Config(path, self.default_azaan))
            return Response(303, location="/settings?speakers=1&saved=speaker#speaker")
        else:
            return None
        return Response(303, location="/settings?speakers=1#speaker")

    def place_error(self, section, message):
        """Show an error from Config next to the field it is about, or at the top of the section."""
        for item in section.fields:
            if re.search(r"(^|\W){}(\W|$)".format(re.escape(item.option)), message):
                return {item.name: message}
        return {"section-" + section.id: message}

    # -------------------------------------------------------------- showing

    def page(self, editing=None, values=None, errors=None, saved=None, preview=None, load_speakers=False):
        body = "".join(
            self.section_html(section, values if section.id == editing else None,
                              errors if section.id == editing else None, saved == section.id,
                              preview if section.id == editing else None, load_speakers)
            for section in self.sections.values())
        body += ('<form method="post" action="/settings/logout" class="logout">'
                 '<button class="secondary">Log out</button></form>')
        config = self.config()
        return settings_page("Settings", body, update.available(self.config_path, config) if config else None)

    def section_html(self, section, values=None, errors=None, saved=False, preview=None, load_speakers=False):
        errors = errors or {}
        if section.id == "prayers":
            fields = self.prayers_html(values or prayer_values(self.config()), errors)
        elif section.id == "speaker" and not load_speakers:
            fields = ('<div hx-get="/settings/speaker" hx-trigger="load" hx-target="closest section" '
                      'hx-select="#speaker" hx-swap="outerHTML"><p class="detail">Looking for paired speakers...</p>'
                      '<noscript><a href="/settings?speakers=1#speaker">List the paired speakers</a></noscript>'
                      '</div>')
        else:
            raw = self.raw()
            fields = "".join(self.field_html(item, raw, values, errors.get(item.name)) for item in section.fields)

        buttons = '<button class="primary" name="save" value="1">Save</button>'
        if section.preview and not preview:
            buttons = '<button class="primary" name="preview" value="1">Preview the times</button>'
        times = ""
        if preview:
            now = clock.now(preview.tz)
            times = ('<div class="preview"><p class="label">The times today with these settings</p>{}</div>'
                     .format(times_list(schedule(preview, now.date()), None, now)))
            buttons += '<a class="button" href="/settings#{}">Cancel</a>'.format(section.id)
        extra = ""
        if section.id == "speaker":
            if load_speakers:
                extra = self.pairing_html()
            else:
                buttons = ""

        return ('<section class="card settings" id="{id}"><h2 class="label">{title}</h2>{description}'
                '<form method="post" action="/settings/{id}" hx-post="/settings/{id}" hx-target="#{id}" '
                'hx-select="#{id}" hx-swap="outerHTML" hx-disabled-elt="find button">'
                '<div hx-post="/settings/check/{id}" hx-trigger="input delay:600ms, change" '
                'hx-include="closest form" hx-swap="none" hx-disabled-elt="unset">{section_error}{fields}</div>{times}'
                '{saved}<div class="actions">{buttons}</div></form>{extra}</section>').format(
            id=section.id, title=escape(section.title),
            description='<p class="detail">{}</p>'.format(escape(section.description)) if section.description else "",
            section_error=error_span("section-" + section.id, errors.get("section-" + section.id)),
            fields=fields, times=times, buttons=buttons, extra=extra,
            saved='<p class="saved" role="status">Saved. PiPrayer uses it within a minute.</p>' if saved else "")

    def field_html(self, item, raw, values, error):
        if values is not None:
            value = values.get(item.name, "")
        else:
            value = raw.get(item.option, item.default).strip()
        attributes = 'name="{}" id="field-{}"'.format(item.name, item.name)
        if error:
            attributes += ' aria-invalid="true" aria-describedby="error-{}"'.format(item.name)

        if item.kind == "password":
            has = bool(raw.get(item.option))
            control = ('<input type="password" {} autocomplete="new-password" placeholder="{}">'
                       '{}').format(attributes, "Unchanged" if has else "None",
                                    '<label class="check"><input type="checkbox" name="remove-{}"> Remove it</label>'
                                    .format(item.name) if has else "")
        elif item.kind == "select":
            choices = self.speaker_choices(value) if item.option == SPEAKER else item.choices()
            if value and value not in [choice for choice, _ in choices]:
                choices = choices + [(value, value)]
            control = '<select {}>{}</select>'.format(attributes, "".join(
                '<option value="{}"{}>{}</option>'.format(escape(choice), " selected" if choice == value else "",
                                                          escape(label)) for choice, label in choices))
        else:
            extra = {"number": ' inputmode="numeric"', "decimal": ' inputmode="decimal"'}.get(item.kind, "")
            if item.option == TIMEZONE:
                extra += ' list="timezones" autocomplete="off" autocapitalize="off"'
            control = '<input type="text" {}{} value="{}">'.format(attributes, extra, escape(value))
            if item.option == TIMEZONE:
                control += '<datalist id="timezones">{}</datalist>'.format("".join(
                    '<option value="{}">'.format(name) for name in sorted(available_timezones())))

        listen = ""
        if item.listen:
            sound = item.listen.format(value or "")
            if "{}" not in item.listen or value:
                listen = ' <a class="listen" href="/media/{}" target="_blank" data-listen="{}" data-for="{}">' \
                         'Listen</a>'.format(escape(sound), escape(item.listen), item.name)
        return ('<div class="field"><div class="field-head"><label for="field-{name}">{label}</label>{listen}</div>'
                '{control}{hint}{error}</div>'
                .format(name=item.name, label=escape(item.label), listen=listen, control=control,
                        hint='<p class="hint">{}</p>'.format(escape(item.hint)) if item.hint else "",
                        error=error_span(item.name, error)))

    def speaker_choices(self, current):
        speakers = self.speakers()
        choices = [("", "Any paired speaker")]
        choices += [(mac, "{} ({}){}".format(name, mac, ", connected" if connected else ""))
                    for mac, name, connected in speakers]
        if current and current.upper() not in [mac for mac, _, _ in speakers]:
            choices.append((current.upper(), "{} (not paired now)".format(current.upper())))
        return choices

    def pairing_html(self):
        """Below the speaker list: find and pair a new speaker, forget one, or pair again one that was reset."""
        pairing = self.pairing
        current = self.raw().get(SPEAKER, "").strip().upper()
        speakers = self.speakers()
        paired = [mac for mac, _, _ in speakers]
        parts = []

        if pairing.message:
            parts.append('<p class="{}" role="status">{}</p>'.format(
                "error" if pairing.error else "saved", escape(pairing.message)))
        if pairing.paired and pairing.paired != current and pairing.paired in paired:
            parts.append(speaker_button("use", pairing.paired, "Use {} for the Azaan".format(pairing.name),
                                        "primary"))

        if pairing.busy:
            doing = ("Pairing {}...".format(pairing.name) if pairing.task == PAIRING
                     else "Looking for speakers, for about {} seconds...".format(bluetooth.SCAN_SECONDS))
            parts.append('<p class="detail" hx-get="/settings/speaker" hx-trigger="every 2s" '
                         'hx-target="closest section" hx-select="#speaker" hx-swap="outerHTML">{}'
                         '<noscript> <a href="/settings?speakers=1#speaker">Refresh</a></noscript></p>'
                         .format(escape(doing)))
            return '<div class="pairing">{}</div>'.format("".join(parts))

        rows = []
        if current and current not in paired and MAC_ADDRESS.match(current):
            rows.append(speaker_row("{} isn't paired any more, e.g. after it was reset".format(current),
                                    speaker_button("pair", current, "Pair again", extra={"again": "1"})))
        for mac, name, _ in speakers:
            if mac != current:
                rows.append(speaker_row("{} ({})".format(name, mac),
                                        speaker_button("forget", mac, "Forget", extra={"name": name})))
        for mac, name in pairing.found:
            if mac not in paired:
                rows.append(speaker_row("{} ({})".format(name, mac),
                                        speaker_button("pair", mac, "Pair", "primary", extra={"name": name})))
        if pairing.scanned and pairing.found and not any(mac not in paired for mac, _ in pairing.found):
            rows.append('<p class="detail">No other speakers found.</p>')
        if rows:
            parts.append('<div class="speakers">{}</div>'.format("".join(rows)))

        parts.append(PAIRING_HINT)
        parts.append(speaker_button("scan", None, "Find speakers"))
        return '<div class="pairing">{}</div>'.format("".join(parts))

    def prayers_html(self, values, errors):
        dua = os.path.isfile(os.path.join(MEDIA_DIR, "dua.opus"))
        head = "".join('<span>{}</span>'.format(label) for _, label in PRAYER_COLUMNS) + \
            ('<span>Dua<a class="listen" href="/media/dua.opus" target="_blank" aria-label="Listen to the dua">'
             '&#9654;</a></span>' if dua else "")
        rows = []
        for prayer in PRAYER_NAMES:
            cells = "".join(
                '<input type="text" inputmode="numeric" name="{0}-{1}" id="field-{0}-{1}" value="{2}" '
                'aria-label="{3} {4}"{5}>'.format(column, prayer, escape(values.get(column + "-" + prayer, "")),
                                                  prayer.capitalize(), label.split(",")[0].lower(),
                                                  ' aria-invalid="true"' if column + "-" + prayer in errors else "")
                for column, label in PRAYER_COLUMNS)
            if dua:
                cells += '<input type="checkbox" name="dua-{0}" aria-label="Dua after {1}"{2}>'.format(
                    prayer, prayer.capitalize(), " checked" if values.get("dua-" + prayer) == "on" else "")
            rows.append('<div class="prayer-row{4}"><label class="check"><input type="checkbox" name="on-{0}"{1}> '
                        '{2}</label>{3}</div>'.format(prayer, " checked" if values.get("on-" + prayer) == "on" else "",
                                                      prayer.capitalize(), cells, " with-dua" if dua else ""))
            rows.append("".join(error_span("{}-{}".format(column, prayer), errors.get("{}-{}".format(column, prayer)))
                                for column, _ in PRAYER_COLUMNS))
        return ('<div class="prayer-table"><div class="prayer-row head{}"><span>Prayer</span>{}</div>{}</div>{}'
                .format(" with-dua" if dua else "", head, "".join(rows), error_span("prayers", errors.get("prayers"))))


PAIRING_HINT = ('<p class="detail">To pair a new speaker, put it in pairing mode first. Amazon Echo: say "Alexa, pair '
                'Bluetooth". Google Home or Nest: in the Google Home app, open the speaker\'s settings, then Audio, '
                'Paired Bluetooth devices, Enable pairing mode. Soundbar: switch its input to Bluetooth, then hold '
                'its Bluetooth button until the light flashes.</p>')


def speaker_button(action, mac, label, style="secondary", extra=None):
    """A button that posts to /settings/speaker/<action>, and updates the speaker section in place."""
    fields = dict(extra or {})
    if mac:
        fields["mac"] = mac
    hidden = "".join('<input type="hidden" name="{}" value="{}">'.format(name, escape(value))
                     for name, value in fields.items())
    return ('<form method="post" action="/settings/speaker/{0}" hx-post="/settings/speaker/{0}" '
            'hx-target="#speaker" hx-select="#speaker" hx-swap="outerHTML" hx-disabled-elt="find button" '
            'class="inline">{1}<button class="{2}">{3}</button></form>').format(action, hidden, style, escape(label))


def speaker_row(label, button):
    return '<div class="speaker-row"><span>{}</span>{}</div>'.format(escape(label), button)


def error_span(name, message, oob=False):
    return '<span class="error" id="error-{}" role="alert"{}>{}</span>'.format(
        name, ' hx-swap-oob="true"' if oob else "", escape(message or ""))


def cookie_header(value, seconds):
    return "{}={}; Max-Age={}; Path=/; HttpOnly; SameSite=Strict".format(COOKIE, value, seconds)


def describe_wait(seconds):
    seconds = int(-(-seconds // 1))
    if seconds < 60:
        return "{} second{}".format(seconds, "" if seconds == 1 else "s")
    minutes = -(-seconds // 60)
    return "{} minute{}".format(minutes, "" if minutes == 1 else "s")


def media(name):
    path = os.path.join(MEDIA_DIR, name)
    if not os.path.isfile(path):
        return Response(404, body="Not found")
    with open(path, "rb") as f:
        return Response(200, "audio/wav" if name.endswith(".wav") else "audio/ogg", f.read(),
                        cache_seconds=24 * 3600)


def login_form(error=None):
    return ('<section class="card settings"><h2 class="label">Enter the settings PIN</h2>'
            '<form method="post" action="/settings/login">{}'
            '<div class="field"><label for="pin">PIN</label>'
            '<input type="password" id="pin" name="pin" inputmode="numeric" pattern="[0-9]*" '
            'autocomplete="current-password" autofocus></div>'
            '<div class="actions"><button class="primary">Log in</button></div></form></section>').format(
        '<div class="notice" role="alert">{}</div>'.format(escape(error)) if error else "")


NO_PIN = ('<section class="card settings"><h2 class="label">Settings</h2>'
          '<p>To change the settings here, set a PIN on the Raspberry Pi, so only you can:</p>'
          '<pre>python3 piprayer.py config</pre>'
          '<p>or add it to <code>.piprayer</code>, which PiPrayer then saves as a hash:</p>'
          '<pre>settings-pin = 4826</pre>'
          '<p class="detail">Use 4 to 8 digits. The page asks for it once on each phone or computer.</p></section>')

# Keeps each sound's Listen link playing the one chosen, and says when PiPrayer can't be reached
SCRIPT = """
(function () {
  document.addEventListener("change", function (event) {
    var links = document.querySelectorAll('[data-for="' + event.target.name + '"]');
    for (var i = 0; i < links.length; i++) {
      links[i].href = "/media/" + links[i].getAttribute("data-listen").replace("{}", event.target.value);
      links[i].hidden = !event.target.value;
    }
  });
  function problem(text) {
    var notice = document.getElementById("problem");
    notice.textContent = text;
    notice.hidden = false;
  }
  document.addEventListener("htmx:responseError", function (event) {
    problem(event.detail.xhr.responseText || "PiPrayer answered with error " + event.detail.xhr.status);
  });
  document.addEventListener("htmx:sendError", function () {
    problem("Can't reach PiPrayer. Check the Raspberry Pi is on and connected, and try again.");
  });
})();
"""


def settings_page(title, body, release=None):
    return SETTINGS_PAGE.format(title=escape(title), style=STATUS_STYLE, body=body, update=update_html(release))


SETTINGS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f6f5f1" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#151614" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="PiPrayer settings">
<link rel="apple-touch-icon" href="/icon-180.png">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<title>PiPrayer {title}</title>
<style>{style}</style>
</head>
<body>
<main>
<header class="top"><a class="brand" href="/">PiPrayer</a><span class="date">{title}</span></header>
<div class="notice" id="problem" role="alert" hidden></div>
{body}
<footer>PiPrayer {version}{update}</footer>
</main>
<script src="/{htmx}"></script>
<script>{script}</script>
</body>
</html>
""".replace("{script}", SCRIPT.replace("{", "{{").replace("}", "}}")).replace("{htmx}", HTMX) \
    .replace("{version}", __version__)
