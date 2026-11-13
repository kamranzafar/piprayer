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
    assert '<div class="next">Asr 18:03</div>' in body
    assert "Today, in 4 h 3 min" in body
    assert "Skip Asr at 18:03 today" in body
    assert "Mute today" in body
    assert "Resume" not in body
    assert body.count("<tr class=") == 5


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
    assert "Skip Maghrib at 20:02 today" in body
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
