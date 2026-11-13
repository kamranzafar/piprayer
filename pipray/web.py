# SPDX-License-Identifier: GPL-3.0-or-later
"""The optional status page and its JSON API, turned on with status-port in .piprayer."""
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import clock
from .config import Config, ConfigError
from .output import describe_azaan, describe_day, describe_mute, format_duration, next_json, times_json
from .schedule import next_azaan, schedule, timezone_name
from .state import MAX_MUTE_DAYS, State, mute, next_playing, resume, skip_next, state_file

log = logging.getLogger("piprayer")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "status.css")) as _css:
    STATUS_STYLE = _css.read()


class StatusPage:
    """Serves the page from a background thread while the service runs."""

    def __init__(self, config_path, default_azaan):
        self.config_path = config_path
        self.default_azaan = default_azaan
        self.port = None
        self.server = None

    def serve(self, port):
        """Start, move or stop the page to match the configuration. None stops it."""
        if port == self.port:
            return
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            log.info("Status page stopped")

        # Remembered even if starting fails, so the error is logged once rather than every minute
        self.port = port
        if not port:
            return

        try:
            self.server = ThreadingHTTPServer(("", port), status_handler(self.config_path, self.default_azaan))
        except OSError as e:
            log.error("Can't start the status page on port %d: %s", port, e.strerror or e)
            return
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="status-page", daemon=True).start()
        log.info("Status page on port %d", port)


@dataclass
class Response:
    status: int
    content_type: str = "text/plain"
    body: str = ""
    location: str = None

    @classmethod
    def json(cls, data):
        return cls(200, "application/json", json.dumps(data, indent=2))

    @classmethod
    def html(cls, page):
        return cls(200, "text/html; charset=utf-8", page)

    @classmethod
    def back_to_page(cls):
        return cls(303, location="/")


NOT_FOUND = Response(404, body="Not found")


class StatusRoutes:
    """What each request does, without the HTTP plumbing."""

    def __init__(self, config_path, default_azaan):
        self.config_path = config_path
        self.state_path = state_file(config_path)
        self.default_azaan = default_azaan

    def get(self, path):
        config, now = self.load()
        state = State.load(self.state_path)
        if path == "/":
            return Response.html(status_html(config, now, state))
        if path == "/api/times":
            return Response.json(times_json(config, now.date(), now, state))
        if path == "/api/next":
            return Response.json(next_json(config, next_azaan(config, now, state), now, state))
        return NOT_FOUND

    def post(self, path, query):
        """/skip, /mute and /resume come from the page's buttons and go back to it. /api/... answer in JSON."""
        api = path.startswith("/api/")
        action = path[len("/api"):] if api else path
        config, now = self.load()

        if action == "/skip":
            entry = skip_next(config, self.state_path, now)
            result = {"skipped": entry.to_json() if entry else None}
        elif action == "/mute":
            days = query.get("days", ["1"])[0]
            if not days.isdigit() or not 1 <= int(days) <= MAX_MUTE_DAYS:
                return Response(400, body="days must be a whole number from 1 to {}".format(MAX_MUTE_DAYS))
            mute_until = mute(config, self.state_path, now, now.date() + timedelta(days=int(days) - 1))
            result = {"muted_until": mute_until.isoformat()}
        elif action == "/resume":
            resume(self.state_path)
            result = {"muted_until": None, "skip": []}
        else:
            return NOT_FOUND
        return Response.json(result) if api else Response.back_to_page()

    def load(self):
        config = Config(self.config_path, self.default_azaan)
        return config, clock.now(config.tz)


def status_handler(config_path, default_azaan):
    """A request handler class for the status page of this configuration."""
    routes = StatusRoutes(config_path, default_azaan)

    class Handler(BaseHTTPRequestHandler):
        server_version = "PiPrayer"

        def log_message(self, format, *args):
            log.debug("Status page: " + format, *args)

        def do_GET(self):
            self.answer(lambda: routes.get(urlsplit(self.path).path))

        def do_POST(self):
            # Refuse forms submitted from other sites. Home Assistant and curl don't send Origin
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.send(Response(403, body="Forbidden"))

            url = urlsplit(self.path)
            query = parse_qs(url.query)
            length = int(self.headers.get("Content-Length") or 0)
            query.update(parse_qs(self.rfile.read(length).decode("utf-8", "replace")))
            self.answer(lambda: routes.post(url.path, query))

        def answer(self, route):
            try:
                response = route()
            except ConfigError as e:
                response = Response(500, body="PiPrayer configuration error: {}".format(e))
            self.send(response)

        def send(self, response):
            data = response.body.encode("utf-8")
            self.send_response(response.status)
            if response.location:
                self.send_header("Location", response.location)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def status_html(config, now, state):
    """The status page: the next azaan, buttons to skip or mute, and today's times."""
    upcoming = next_azaan(config, now, state)
    playing = next_playing(config, now, state)
    muted = state.muted_at(now)

    if upcoming:
        next_card = ('<div class="label">Next azaan</div><div class="next">{} {}</div>'
                     '<div class="label">{}, in {}</div>').format(
            escape(upcoming.prayer.capitalize()), upcoming.at.strftime("%H:%M"),
            describe_day(upcoming.at.date(), now.date()).capitalize(), format_duration(upcoming.at - now))
    else:
        next_card = '<div class="next">No azaan in the next two days</div>'

    if muted:
        next_card += '<div class="notice">{}.</div>'.format(escape(describe_mute(config, muted, now)))
    elif upcoming and upcoming.silenced == "skipped":
        next_card += '<div class="notice">This azaan will be skipped.</div>'

    buttons = []
    if playing:
        buttons.append(("/skip", "primary", "Skip " + describe_azaan(playing, now)))
    if not muted:
        buttons.append(("/mute", "", "Mute today"))
    if muted or state.skip:
        buttons.append(("/resume", "", "Resume"))
    next_card += '<div class="actions">{}</div>'.format("".join(
        '<form method="post" action="{}"><button class="{}">{}</button></form>'.format(action, cls, escape(text))
        for action, cls, text in buttons))

    return PAGE.format(style=STATUS_STYLE, next_card=next_card,
                       table=times_table(schedule(config, now.date(), state), upcoming, now),
                       date="{:%A} {} {:%B %Y}".format(now, now.day, now),
                       details=escape("{} · {} method · {} Asr".format(
                           timezone_name(config, now.date()), config.method, config.asr_time)),
                       updated=now.strftime("%H:%M"))


def times_table(entries, upcoming, now):
    if not entries:
        return "No prayer times today"

    rows = []
    for entry in entries:
        if entry.at <= now:
            tag, cls = "", "past"
        elif entry.silenced:
            tag, cls = '<span class="tag">{}</span>'.format(entry.silenced), ""
        elif upcoming and entry.at == upcoming.at:
            tag, cls = '<span class="tag next">next</span>', "next"
        else:
            tag, cls = "", ""
        rows.append('<tr class="{}"><td>{}</td><td>{}</td><td>{}</td><td>{}%</td><td>{}</td></tr>'.format(
            cls, escape(entry.prayer.capitalize()), entry.at.strftime("%H:%M"), entry.azaan, entry.volume, tag))
    return ('<table><thead><tr><th>Prayer</th><th>Time</th><th>Azaan</th><th>Volume</th><th></th></tr></thead>'
            '<tbody>{}</tbody></table>'.format("".join(rows)))


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>PiPrayer</title>
<style>{style}</style>
</head>
<body>
<main>
<h1>PiPrayer</h1>
<section class="card">{next_card}</section>
<section class="card">
<div class="label" style="margin-bottom:12px">{date}</div>
{table}
</section>
<footer>{details} · updated {updated}</footer>
</main>
</body>
</html>
"""
