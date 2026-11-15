# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The settings page: logging in with the PIN, and changing .piprayer from it."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pipray import __version__, service
from pipray.config import Config, ConfigError
from pipray.pin import COOKIE, Throttle, check_pin, hash_pin, is_hash, session_cookie
from pipray.settings import SettingsRoutes, per_prayer
from conftest import MELBOURNE_CONFIG
from test_status_page import page, request  # noqa: F401, the page fixture

MELBOURNE = ZoneInfo("Australia/Melbourne")
PIN = "4826"
CONFIG = "# my settings\n" + MELBOURNE_CONFIG + "reminder = 10\n# the end\n"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def settings(write_config, at_time):
    """Settings routes for a configuration with a PIN, and a login."""
    at_time(datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))

    class Settings:
        def __init__(self, text=CONFIG + "settings-pin = " + hash_pin(PIN, iterations=1000) + "\n"):
            self.path = write_config(text)
            self.clock = FakeClock()
            self.routes = SettingsRoutes(self.path, 1, Throttle(self.clock),
                                         speakers=lambda: [("AA:BB:CC:DD:EE:01", "Echo Pop", True)])
            self.cookies = {}

        def login(self, pin=PIN):
            response = self.routes.post("/settings/login", {"pin": [pin]}, {})
            match = re.search(COOKIE + r"=([^;]*)", (response.headers or {}).get("Set-Cookie", ""))
            if match:
                self.cookies = {COOKIE: match.group(1)}
            return response

        def get(self, path="/settings", query=None):
            return self.routes.get(path, query or {}, self.cookies)

        def post(self, section, **form):
            return self.routes.post("/settings/" + section, {k.replace("_", "-"): [v] for k, v in form.items()},
                                    self.cookies)

        def text(self):
            with open(self.path) as f:
                return f.read()

    return Settings


# ------------------------------------------------------------------ the PIN

def test_pin_is_saved_as_a_hash():
    saved = hash_pin(PIN)
    assert is_hash(saved) and PIN not in saved
    assert check_pin(PIN, saved) and not check_pin("4827", saved)
    assert check_pin(PIN, PIN)  # typed into .piprayer, not hashed yet


@pytest.mark.parametrize("value", ["123", "123456789", "12a4", "pbkdf2-sha256$1$zz$00"])
def test_invalid_pin(write_config, value):
    with pytest.raises(ConfigError, match="Invalid settings-pin: use 4 to 8 digits"):
        Config(write_config(MELBOURNE_CONFIG + "settings-pin = " + value + "\n"))


def test_service_saves_a_typed_pin_as_a_hash(write_config):
    path = write_config(CONFIG + "settings-pin = 4826\n")
    service.secure_pin(path, Config(path))
    saved = Config(path).settings_pin
    assert is_hash(saved) and check_pin(PIN, saved)
    assert open(path).read().startswith("# my settings\n")


def test_without_a_pin_the_page_says_how_to_set_one(settings):
    s = settings(CONFIG)
    assert "set a PIN on the Raspberry Pi" in s.get().body
    assert s.login("").status == 200
    assert s.post("times", lat="1").status == 303  # back to /settings, nothing changed
    assert s.text() == CONFIG


def test_asks_for_the_pin(settings):
    s = settings()
    assert "Enter the settings PIN" in s.get().body
    assert "<footer>PiPrayer " + __version__ + "</footer>" in s.get().body
    assert s.post("sound", reminder="5").location == "/settings"
    assert "reminder = 10" in s.text()


def test_logging_in(settings):
    s = settings()
    response = s.login()
    assert (response.status, response.location) == (303, "/settings")
    assert "Max-Age=2592000; Path=/; HttpOnly; SameSite=Strict" in response.headers["Set-Cookie"]
    assert '<section class="card settings" id="times">' in s.get().body
    assert "<footer>PiPrayer " + __version__ + "</footer>" in s.get().body


def test_wrong_pins_wait_longer_each_time(settings):
    s = settings()
    assert "Wrong PIN. Try again in 1 second." in s.login("1111").body
    assert "Too many wrong PINs. Try again in 1 second." in s.login(PIN).body  # even the right one waits
    s.clock.now += 1
    assert "Try again in 2 seconds" in s.login("2222").body
    s.clock.now += 2
    assert "Try again in 4 seconds" in s.login("3333").body
    s.clock.now += 4
    assert s.login().status == 303
    assert s.routes.throttle.wrong == 0


def test_wait_is_at_most_15_minutes():
    throttle = Throttle(FakeClock())
    for _ in range(30):
        throttle.failed()
    assert throttle.wait() == 15 * 60


def test_changing_the_pin_logs_out(settings):
    s = settings()
    s.login()
    text = s.text().replace(re.search(r"settings-pin = (.*)", s.text()).group(1), hash_pin("9999", iterations=1000))
    with open(s.path, "w") as f:
        f.write(text)
    assert "Enter the settings PIN" in s.get().body


def test_expired_login(settings):
    s = settings()
    saved = re.search(r"settings-pin = (.*)", s.text()).group(1)
    s.cookies = {COOKIE: session_cookie(saved, now=0)}
    assert "Enter the settings PIN" in s.get().body


def test_logging_out(settings):
    s = settings()
    s.login()
    response = s.routes.post("/settings/logout", {}, s.cookies)
    assert (response.location, response.headers["Set-Cookie"].split(";")[0]) == ("/", COOKIE + "=")


def test_htmx_is_sent_to_the_login(settings):
    response = settings().routes.post("/settings/sound", {"reminder": ["5"]}, {}, htmx=True)
    assert response.headers == {"HX-Redirect": "/settings"}


# ------------------------------------------------------------------ the page

def test_page_shows_the_settings(settings):
    s = settings()
    s.login()
    body = s.get().body
    for section in ("Prayer times", "Prayers", "Azaan and sound", "Ramadan", "Speaker", "Home Assistant"):
        assert '<h2 class="label">{}</h2>'.format(section) in body
    assert 'name="lat" id="field-lat" inputmode="decimal" value="-37.909499"' in body
    assert '<option value="Jafari" selected>' in body
    assert 'name="reminder" id="field-reminder" inputmode="numeric" value="10"' in body
    assert "Preview the times" in body
    assert 'hx-get="/settings/speaker"' in body  # listed once the page has loaded, as it takes a few seconds
    assert "status-port" not in body and "settings-pin" not in body


def test_prayer_rows(settings):
    s = settings(CONFIG.replace("Isha:19", "Isha:19\nvolume = 80, Fajr:40") +
                 "settings-pin = " + hash_pin(PIN, iterations=1000) + "\n")
    s.login()
    body = s.get().body
    assert 'name="on-fajr" checked' in body
    assert 'name="offset-fajr" id="field-offset-fajr" value="-11"' in body
    assert 'name="volume-fajr" id="field-volume-fajr" value="40"' in body
    assert 'name="volume-isha" id="field-volume-isha" value="80"' in body


def test_speakers_are_listed(settings):
    s = settings()
    s.login()
    body = s.get("/settings/speaker").body
    assert '<option value="" selected>Any paired speaker</option>' in body
    assert '<option value="AA:BB:CC:DD:EE:01">Echo Pop (AA:BB:CC:DD:EE:01), connected</option>' in body


def test_sounds_can_be_heard(settings):
    s = settings()
    response = s.get("/media/azaan-5.opus")
    assert (response.status, response.content_type) == (200, "audio/ogg")
    assert s.get("/media/azaan-99.opus").status == 404
    assert s.get("/media/../.piprayer") is None  # not a sound, so not found


# ------------------------------------------------------------------ saving

def test_saves_only_what_changed(settings):
    s = settings()
    s.login()
    response = s.post("sound", azaan="5", fajr_azaan="", reminder="10", fallback_output="off")
    assert (response.status, response.location) == (303, "/settings?saved=sound#sound")
    assert "azaan = 5\n" in s.text() and "fajr-azaan" not in s.text() and "fallback-output" not in s.text()
    assert s.text().startswith("# my settings\n")
    assert "Saved. PiPrayer uses it within a minute." in s.get(query={"saved": ["sound"]}).body


def test_clearing_an_option_removes_it(settings):
    s = settings()
    s.login()
    s.post("sound", azaan="1", fajr_azaan="", reminder="", fallback_output="off")
    assert "reminder" not in s.text()


def test_mistakes_are_shown_next_to_their_field_and_not_saved(settings):
    s = settings()
    s.login()
    before = s.text()
    response = s.post("sound", azaan="1", reminder="90", fallback_output="off")
    assert response.status == 200
    assert '<span class="error" id="error-reminder" role="alert">Invalid reminder: 90, use a number from 0 to 60' \
        in response.body
    assert 'name="reminder" id="field-reminder" aria-invalid="true"' in response.body
    assert s.text() == before


def test_fields_are_checked_as_they_are_typed(settings):
    s = settings()
    s.login()
    body = s.post("check/times", lat="100", lng="144.75", timezone="Australia/Melbourne", method="Jafari",
                  asr_time="Hanafi").body
    assert '<span class="error" id="error-lat" role="alert" hx-swap-oob="true">Invalid lat: 100' in body
    assert '<span class="error" id="error-lng" role="alert" hx-swap-oob="true"></span>' in body


def test_prayer_times_are_previewed_before_saving(settings):
    s = settings()
    s.login()
    before = s.text()
    form = dict(lat="-37.9", lng="144.75", timezone="Australia/Melbourne", method="MWL", asr_time="Standard",
                high_latitude="NightMiddle")
    response = s.post("times", preview="1", **form)
    assert "The times today with these settings" in response.body
    assert '<button class="primary" name="save" value="1">Save</button>' in response.body
    assert s.text() == before

    s.post("times", save="1", **form)
    assert "lat = -37.9\n" in s.text() and "method = MWL\n" in s.text() and "asr-time = Standard\n" in s.text()
    assert "high-latitude" not in s.text()  # the default, as before


def test_timezone_replaces_a_fixed_offset(settings):
    s = settings(CONFIG.replace("timezone = Australia/Melbourne", "gmt-offset = 10\ndst = 1") +
                 "settings-pin = " + hash_pin(PIN, iterations=1000) + "\n")
    s.login()
    s.post("times", save="1", lat="-37.909499", lng="144.751876", timezone="Australia/Melbourne", method="Jafari",
           asr_time="Hanafi", high_latitude="NightMiddle")
    assert "timezone = Australia/Melbourne" in s.text()
    assert "gmt-offset" not in s.text() and "dst" not in s.text()


def test_prayers_section(settings):
    s = settings()
    s.login()
    form = {}
    for prayer, offset in (("fajr", "-11"), ("dhuhr", "1"), ("asr", "0"), ("maghrib", "-23"), ("isha", "19")):
        form.update({"on_" + prayer: "on", "offset_" + prayer: offset, "volume_" + prayer: "80",
                     "fade_" + prayer: "0"})
    form.update(volume_fajr="40", fade_fajr="15")
    del form["on_isha"]
    s.post("prayers", **form)
    text = s.text()
    assert "prayers = Fajr:-11, Dhuhr:1, Asr, Maghrib:-23\n" in text
    assert "volume = 80, Fajr:40\n" in text
    assert "fade-in = Fajr:15\n" in text
    config = Config(s.path)
    assert (config.volume_for("fajr"), config.volume_for("isha"), config.fade_in_for("fajr")) == (40, 80, 15)


def test_prayers_section_needs_a_prayer_and_valid_numbers(settings):
    s = settings()
    s.login()
    body = s.post("prayers", offset_fajr="200", volume_fajr="80").body
    assert "Invalid Fajr offset: 200, use a number from -180 to 180" in body
    assert "reminder = 10" in s.text() and "volume" not in s.text()


def test_per_prayer():
    assert per_prayer({"fajr": 40, "dhuhr": 80, "asr": 80}, 100) == "80, Fajr:40"
    assert per_prayer({"fajr": 15, "dhuhr": 0, "asr": 0}, 0) == "Fajr:15"
    assert per_prayer({"fajr": 0, "dhuhr": 0}, 0) is None


def test_mqtt_password_is_kept_unless_changed(settings):
    s = settings(CONFIG + "mqtt-host = broker\nmqtt-password = s3cret\nsettings-pin = " +
                 hash_pin(PIN, iterations=1000) + "\n")
    s.login()
    assert "s3cret" not in s.get().body
    form = dict(mqtt_host="broker", mqtt_port="1883", mqtt_username="", mqtt_topic="piprayer")
    s.post("mqtt", mqtt_password="", **form)
    assert "mqtt-password = s3cret" in s.text()
    s.post("mqtt", mqtt_password="n3w", **form)
    assert "mqtt-password = n3w" in s.text()
    s.post("mqtt", mqtt_password="", remove_mqtt_password="on", **form)
    assert "mqtt-password" not in s.text()


def test_speaker(settings):
    s = settings()
    s.login()
    s.post("speaker", speaker="aa:bb:cc:dd:ee:01")
    assert "speaker = AA:BB:CC:DD:EE:01" in s.text()
    s.post("speaker", speaker="")
    assert "speaker" not in s.text()


def test_ramadan(settings):
    s = settings()
    s.login()
    s.post("ramadan", suhoor="30", ramadan="auto")
    assert "suhoor = 30" in s.text() and "ramadan" not in s.text()


# ------------------------------------------------------------------ over HTTP

def test_settings_over_http(page):  # noqa: F811
    with open(page.config_path, "a") as f:
        f.write("settings-pin = {}\n".format(PIN))
    assert "Settings</a>" in request(page, "/")[2]

    status, headers, _ = request(page, "/settings/login", "POST", data=b"pin=" + PIN.encode())
    assert status == 303
    cookie = headers["Set-Cookie"].split(";")[0]
    status, _, body = request(page, "/settings", headers={"Cookie": cookie})
    assert '<section class="card settings" id="times">' in body

    status, _, _ = request(page, "/settings/sound", "POST", data=b"azaan=7&reminder=&fallback-output=off",
                           headers={"Cookie": cookie, "Origin": "http://evil.example"})
    assert status == 403
    status, headers, _ = request(page, "/settings/sound", "POST", data=b"azaan=7&reminder=&fallback-output=off",
                                 headers={"Cookie": cookie})
    assert (status, headers["Location"]) == (303, "/settings?saved=sound#sound")
    assert Config(page.config_path).azaan == 7


# ------------------------------------------------------------------ what can't be saved

@pytest.mark.parametrize("value", ["x\nstatus-port = 9", "x\r\nsettings-pin = 1111", "a\tb"])
def test_line_breaks_cant_add_options(settings, value):
    s = settings(CONFIG + "mqtt-host = broker\nsettings-pin = " + hash_pin(PIN, iterations=1000) + "\n")
    s.login()
    before = s.text()
    body = s.post("mqtt", mqtt_host="broker", mqtt_port="1883", mqtt_username=value, mqtt_topic="piprayer").body
    assert '<span class="error" id="error-mqtt-username" role="alert">Use a single line' in body
    body = s.post("mqtt", mqtt_host="broker", mqtt_port="1883", mqtt_password=value, mqtt_topic="piprayer").body
    assert "Use a single line" in body
    assert s.text() == before


def test_save_option_refuses_a_line_break(write_config):
    from pipray.config import save_option
    path = write_config()
    with pytest.raises(ConfigError, match="Invalid mqtt-username: it can't have a line break"):
        save_option(path, "mqtt-username", "x\nstatus-port = 9")
    assert open(path).read() == MELBOURNE_CONFIG


def test_an_empty_volume_isnt_saved_as_silent(settings):
    s = settings()
    s.login()
    form = {}
    for prayer in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
        form.update({"on_" + prayer: "on", "volume_" + prayer: "80", "offset_" + prayer: "", "fade_" + prayer: ""})
    form["volume_asr"] = ""
    body = s.post("prayers", **form).body
    assert "Enter the Asr volume, from 0 to 100" in body
    assert "volume" not in s.text()


def test_numbers_are_saved_as_numbers(settings):
    s = settings()
    s.login()
    s.post("sound", azaan="1", reminder="+05", fallback_output="off")
    assert "reminder = 5\n" in s.text()


def test_large_requests_are_refused(page):  # noqa: F811
    status, _, _ = request(page, "/settings/login", "POST", data=b"pin=" + b"1" * 70000)
    assert status == 413
