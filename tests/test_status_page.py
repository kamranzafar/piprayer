"""The status page and its JSON API, served over real HTTP on a free local port."""
import json
import socket
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pipray.config import Config, ConfigError
from pipray.state import State, state_file
from pipray.web import StatusPage
from conftest import MELBOURNE_CONFIG

MELBOURNE = ZoneInfo("Australia/Melbourne")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def page(write_config, at_time, tmp_path, monkeypatch):
    """A running status page for the Melbourne configuration at 14:00 on Thursday 12 November 2026."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    at_time(datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))
    path = write_config()
    status_page = StatusPage(path, 1)
    status_page.serve(free_port())
    status_page.config_path = path
    yield status_page
    status_page.serve(None)


def request(page, path, method="GET", data=None, headers=None):
    """Returns (status, headers, body) without following redirects."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    url = "http://127.0.0.1:{}{}".format(page.port, path)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def test_page_shows_the_next_azaan_and_todays_times(page):
    status, headers, body = request(page, "/")

    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert '<span class="hero-prayer">Asr</span><span class="hero-time">18:03</span>' in body
    assert 'data-at="2026-11-12T18:03:00+11:00">Today, in <span class="in">4 h 3 min</span>' in body
    assert 'Skip Asr<span class="sub">18:03 today</span>' in body
    assert "Mute today" in body
    assert "Resume" not in body
    assert body.count("<li class=") == 5
    assert body.count('<li class="is-past">') == 2  # Fajr and Dhuhr
    assert '<li class="is-next">' in body


def test_api_next_and_times(page):
    status, headers, body = request(page, "/api/next")
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body)["prayer"] == "asr"

    status, headers, body = request(page, "/api/times")
    assert json.loads(body)["next"] == "asr"


def test_api_skip_mute_and_resume(page):
    path = state_file(page.config_path)

    status, _, body = request(page, "/api/skip", "POST")
    assert status == 200
    assert json.loads(body)["skipped"]["prayer"] == "asr"
    assert json.loads(request(page, "/api/next")[2])["skipped"] is True

    status, _, body = request(page, "/api/mute?days=2", "POST")
    assert json.loads(body) == {"muted_until": "2026-11-14T00:00:00+11:00"}
    assert State.load(path).mute_until == datetime(2026, 11, 14, tzinfo=MELBOURNE)

    status, _, body = request(page, "/api/resume", "POST")
    assert json.loads(body) == {"muted_until": None, "skip": []}
    assert State.load(path).skip == []


def test_buttons_post_and_return_to_the_page(page):
    status, headers, _ = request(page, "/skip", "POST", data=b"")
    assert (status, headers["Location"]) == (303, "/")

    body = request(page, "/")[2]
    assert "This azaan will be skipped." in body
    assert 'Skip Maghrib<span class="sub">20:02 today</span>' in body
    assert "Resume" in body

    request(page, "/mute", "POST", data=b"days=1")
    body = request(page, "/")[2]
    assert "Muted until the end of today." in body
    assert "Mute today" not in body


def test_mute_rejects_invalid_days(page):
    status, _, body = request(page, "/api/mute?days=99", "POST")
    assert status == 400
    assert "from 1 to 30" in body


def test_forms_from_other_sites_are_refused(page):
    status, _, _ = request(page, "/skip", "POST", data=b"", headers={"Origin": "http://evil.example"})
    assert status == 403
    assert State.load(state_file(page.config_path)).skip == []

    host = "127.0.0.1:{}".format(page.port)
    status, _, _ = request(page, "/skip", "POST", data=b"", headers={"Origin": "http://" + host})
    assert status == 303


def test_unknown_paths(page):
    assert request(page, "/nope")[0] == 404
    assert request(page, "/api/nope", "POST")[0] == 404
    assert request(page, "/api/skip")[0] == 404  # actions need POST


def test_configuration_error(page):
    with open(page.config_path, "w") as f:
        f.write("[Default]\n")
    status, _, body = request(page, "/")
    assert status == 500
    assert "PiPrayer configuration error: Missing configuration option: lat" in body


def test_status_port_option(write_config):
    assert Config(write_config()).status_port is None
    assert Config(write_config(MELBOURNE_CONFIG + "status-port = 8080\n")).status_port == 8080
    with pytest.raises(ConfigError, match="Invalid status-port: 70000"):
        Config(write_config(MELBOURNE_CONFIG + "status-port = 70000\n"))


def test_port_in_use_is_logged(caplog):
    with socket.socket() as taken:
        taken.bind(("", 0))
        taken.listen()
        status_page = StatusPage("unused", 1)
        status_page.serve(taken.getsockname()[1])
    assert status_page.server is None
    assert "Can't start the status page" in caplog.text


def test_service_serves_the_page_while_it_runs(write_config, fake_clock):
    port = free_port()
    path = write_config(MELBOURNE_CONFIG + "status-port = {}\n".format(port))
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    seen = []

    def fetch():
        with urllib.request.urlopen("http://127.0.0.1:{}/api/next".format(port), timeout=5) as response:
            seen.append(json.loads(response.read())["prayer"])

    clock.at(datetime(2026, 11, 12, 16, 0), fetch)
    clock.run_service(path, hours=2)

    assert seen == ["asr"]
    with pytest.raises(OSError):  # stopped with the service
        urllib.request.urlopen("http://127.0.0.1:{}/".format(port), timeout=2)


def test_page_works_as_a_home_screen_app(page):
    body = request(page, "/")[2]
    for tag in ['<meta name="apple-mobile-web-app-capable" content="yes">',
                '<link rel="apple-touch-icon" href="/icon-180.png">',
                '<link rel="manifest" href="/manifest.webmanifest">',
                'content="width=device-width, initial-scale=1, viewport-fit=cover"']:
        assert tag in body

    status, headers, manifest = request(page, "/manifest.webmanifest")
    assert headers["Content-Type"] == "application/manifest+json"
    manifest = json.loads(manifest)
    assert (manifest["display"], manifest["start_url"]) == ("standalone", "/")
    assert [icon["src"] for icon in manifest["icons"]] == ["/icon-192.png", "/icon-512.png"]


@pytest.mark.parametrize("path, size", [("/icon-180.png", 180), ("/icon-192.png", 192), ("/icon-512.png", 512),
                                        ("/favicon.ico", 192)])
def test_icons(page, path, size):
    url = "http://127.0.0.1:{}{}".format(page.port, path)
    with urllib.request.urlopen(url, timeout=5) as response:
        data = response.read()
        assert response.headers["Content-Type"] == "image/png"
        assert response.headers["Cache-Control"] == "max-age=86400"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")) == (size, size)


def test_page_is_never_cached(page):
    assert request(page, "/")[1]["Cache-Control"] == "no-store"


def test_after_midnight_is_shown(tmp_path, at_time):
    from pipray.web import status_html
    path = tmp_path / "london.ini"
    path.write_text("[Default]\nlat = 51.5074\nlng = -0.1278\ntimezone = Europe/London\nmethod = MWL\n"
                    "asr-time = Standard\nprayers = Fajr, Dhuhr, Asr, Maghrib, Isha\n")
    london = ZoneInfo("Europe/London")
    body = status_html(Config(str(path)), datetime(2026, 6, 21, 22, 30, tzinfo=london), State())
    assert 'Isha<span class="detail">Azaan 1 · 100%</span><span class="detail">After midnight</span>' in body
    assert "Tomorrow, in" in body
