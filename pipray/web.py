# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""The optional status page and its JSON API, turned on with status-port in .piprayer."""
import json
import logging
import os
import threading
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import clock
from .config import Config, ConfigError
from .output import describe_day, describe_last, describe_mute, format_duration, next_json, times_json
from .schedule import next_azaan, schedule, timezone_name
from .player import stop
from .state import (LastAzaan, Playing, State, last_file, mute_days, next_playing, playing_file, resume, skip_next,
                    state_file)

log = logging.getLogger("piprayer")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

with open(os.path.join(STATIC_DIR, "status.css")) as _css:
    STATUS_STYLE = _css.read()

# Files served as they are, for adding the page to a phone's home screen
STATIC_FILES = {
    "/icon-180.png": ("icon-180.png", "image/png"),  # iOS home screen
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/favicon.ico": ("icon-192.png", "image/png"),
}

# Android and desktop browsers read this when the page is added to the home screen or installed
MANIFEST = {
    "name": "PiPrayer",
    "short_name": "PiPrayer",
    "description": "The next azaan, today's times, and buttons to skip or mute",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f6f5f1",
    "theme_color": "#1f7a4d",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
}


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
    body: object = ""  # text, or bytes for an image
    location: str = None
    cache_seconds: int = 0  # the page and the API are never cached, so they're always up to date

    @classmethod
    def json(cls, data):
        return cls(200, "application/json", json.dumps(data, indent=2))

    @classmethod
    def html(cls, page):
        return cls(200, "text/html; charset=utf-8", page)

    @classmethod
    def static(cls, path):
        name, content_type = STATIC_FILES[path]
        with open(os.path.join(STATIC_DIR, name), "rb") as f:
            return cls(200, content_type, f.read(), cache_seconds=24 * 3600)

    @classmethod
    def back_to_page(cls):
        return cls(303, location="/")


NOT_FOUND = Response(404, body="Not found")


class StatusRoutes:
    """What each request does, without the HTTP plumbing."""

    def __init__(self, config_path, default_azaan):
        self.config_path = config_path
        self.state_path = state_file(config_path)
        self.last_path = last_file(config_path)
        self.playing_path = playing_file(config_path)
        self.default_azaan = default_azaan

    def get(self, path):
        if path in STATIC_FILES:
            return Response.static(path)
        if path == "/manifest.webmanifest":
            return Response(200, "application/manifest+json", json.dumps(MANIFEST, indent=2), cache_seconds=3600)
        config, now = self.load()
        state = State.load(self.state_path)
        if path == "/":
            return Response.html(status_html(config, now, state, LastAzaan.load(self.last_path),
                                             clock.synchronised(), Playing.load(self.playing_path)))
        if path == "/api/times":
            return Response.json(times_json(config, now.date(), now, state, LastAzaan.load(self.last_path),
                                            clock.synchronised(), Playing.load(self.playing_path)))
        if path == "/api/next":
            return Response.json(next_json(config, next_azaan(config, now, state), now, state))
        return NOT_FOUND

    def post(self, path, query):
        """/stop, /skip, /mute and /resume come from the page's buttons and go back to it. /api/... answer in JSON."""
        api = path.startswith("/api/")
        action = path[len("/api"):] if api else path
        config, now = self.load()

        if action == "/stop":
            playing = stop(self.config_path)
            result = {"stopped": playing.to_json() if playing else None}
        elif action == "/skip":
            entry = skip_next(config, self.state_path, now)
            result = {"skipped": entry.to_json() if entry else None}
        elif action == "/mute":
            try:
                mute_until = mute_days(config, self.state_path, now, query.get("days", ["1"])[0])
            except ValueError as e:
                return Response(400, body=str(e))
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
            data = response.body if isinstance(response.body, bytes) else response.body.encode("utf-8")
            self.send_response(response.status)
            if response.location:
                self.send_header("Location", response.location)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control",
                             "max-age={}".format(response.cache_seconds) if response.cache_seconds else "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def status_html(config, now, state, last=None, synchronised=None, playing=None):
    """The status page: the next azaan, buttons to skip or mute, and today's times.

    Problems are shown at the top: the last azaan didn't play as it should have, or the clock isn't set from the
    network (synchronised False). While an azaan or reminder plays, there is a button to stop it.
    """
    upcoming = next_azaan(config, now, state)
    next_to_play = next_playing(config, now, state)
    muted = state.muted_at(now)

    if upcoming:
        hero = ('<p class="label">Next azaan</p>'
                '<div class="hero-row"><span class="hero-prayer">{}</span><span class="hero-time">{}</span></div>'
                '<div class="countdown" data-at="{}">{}, in <span class="in">{}</span></div>').format(
            escape(upcoming.prayer.capitalize()), upcoming.at.strftime("%H:%M"), upcoming.at.isoformat(),
            describe_day(upcoming.at.date(), now.date()).capitalize(), format_duration(upcoming.at - now))
    else:
        hero = '<p class="label">Next azaan</p><div class="hero-row">None in the next two days</div>'

    if muted:
        hero += '<div class="notice">{}.</div>'.format(escape(describe_mute(config, muted, now)))
    elif upcoming and upcoming.silenced == "skipped":
        hero += '<div class="notice">This azaan will be skipped.</div>'

    buttons = []
    if playing and not playing.stopped:
        hero = '<div class="notice" data-playing>Playing {}</div>'.format(escape(playing.describe())) + hero
        buttons.append(("/stop", "primary", "wide", "Stop the " + ("reminder" if playing.reminder else "azaan")))
    if next_to_play:
        when = "{} {}".format(next_to_play.at.strftime("%H:%M"), describe_day(next_to_play.at.date(), now.date()))
        buttons.append(('/skip', "" if buttons else "primary", "wide", 'Skip {}<span class="sub">{}</span>'.format(
            escape(next_to_play.prayer.capitalize()), escape(when))))
    if not muted:
        buttons.append(("/mute", "", "secondary", "Mute today"))
    if muted or state.skip:
        buttons.append(("/resume", "", "secondary", "Resume"))
    if last and last.problem:
        hero += '<div class="notice">{}.</div>'.format(escape(describe_last(last, now)))
    if synchronised is False:
        hero += '<div class="notice">{}</div>'.format(escape(clock.NOT_SYNCHRONISED))

    hero += '<div class="actions">{}</div>'.format("".join(
        '<form method="post" action="{}" class="{}"><button class="{}">{}</button></form>'.format(
            action, form_class, button_class, label)
        for action, button_class, form_class, label in buttons))

    return PAGE.format(style=STATUS_STYLE, hero=hero,
                       times=times_list(schedule(config, now.date(), state), upcoming, now),
                       date="{:%A} {} {:%B}".format(now, now.day, now),
                       details=escape(timezone_name(config, now.date())) + "<br>" +
                       escape("{} method · {} Asr".format(config.method, config.asr_time)) +
                       ("<br>" + escape("Last azaan: " + describe_last(last, now)) if last else ""),
                       updated=now.strftime("%H:%M"))


def times_list(entries, upcoming, now):
    """Today's prayers, with the azaan and volume for each, and which is next or won't play."""
    if not entries:
        return '<p class="countdown">No prayer times today</p>'

    rows = []
    for entry in entries:
        pill = ""
        if entry.at <= now:
            cls = "is-past"
        elif upcoming and entry.at == upcoming.at:
            cls = "is-next"
            pill = '<span class="pill">{}</span>'.format(entry.silenced) if entry.silenced else \
                '<span class="pill next">next</span>'
        else:
            cls = ""
            if entry.silenced:
                pill = '<span class="pill">{}</span>'.format(entry.silenced)
        detail = '<span class="detail">Azaan {} · {}%</span>'.format(entry.azaan, entry.volume)
        if entry.at.date() != now.date():
            detail += '<span class="detail">After midnight</span>'
        rows.append('<li class="{}"><span class="name">{}{}</span>'
                    '<span class="right">{}<time datetime="{}">{}</time></span></li>'.format(
                        cls, escape(entry.prayer.capitalize()), detail, pill,
                        entry.at.isoformat(), entry.at.strftime("%H:%M")))
    return '<ol class="times">{}</ol>'.format("".join(rows))


# Keeps the page current: the countdown ticks, and the page reloads once the azaan is due, every ten minutes, and
# when reopened from the home screen, as phones keep a page open in the background without reloading it.
SCRIPT = """
(function () {
  var loaded = Date.now();
  var countdown = document.querySelector("[data-at]");
  function duration(ms) {
    var minutes = Math.max(1, Math.ceil(ms / 60000)), hours = Math.floor(minutes / 60);
    minutes = minutes % 60;
    return hours && minutes ? hours + " h " + minutes + " min" : hours ? hours + " h" : minutes + " min";
  }
  function tick() {
    if (!countdown) return;
    var left = new Date(countdown.getAttribute("data-at")) - Date.now();
    if (left <= 0) { location.reload(); return; }
    countdown.querySelector(".in").textContent = duration(left);
  }
  setInterval(tick, 30000);
  // while playing, reload soon, so the stop button goes once it has finished
  setTimeout(function () { location.reload(); }, document.querySelector("[data-playing]") ? 15000 : 600000);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && Date.now() - loaded > 60000) location.reload();
  });
})();
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f6f5f1" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#151614" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="PiPrayer">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-180.png">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<noscript><meta http-equiv="refresh" content="60"></noscript>
<title>PiPrayer</title>
<style>{style}</style>
</head>
<body>
<main>
<header class="top"><span class="brand">PiPrayer</span><span class="date">{date}</span></header>
<section class="card">{hero}</section>
<section class="card">
<h2 class="label">Today</h2>
{times}
</section>
<footer>{details} · updated {updated}</footer>
</main>
<script>{script}</script>
</body>
</html>
""".replace("{script}", SCRIPT.replace("{", "{{").replace("}", "}}"))
