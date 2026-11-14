# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional MQTT for Home Assistant, turned on with mqtt-host in .piprayer.

Publishes the next azaan and whether PiPrayer is muted, with Home Assistant MQTT discovery so the entities appear
on their own, and takes skip, mute and resume commands. Needs paho-mqtt, which is only imported when MQTT is on:
sudo apt install python3-paho-mqtt
"""
import json
import logging
import re
import socket
import threading

from . import clock
from .config import Config, ConfigError
from .schedule import next_azaan
from .output import describe_last
from .state import LastAzaan, State, last_file, mute_days, resume, skip_next, state_file

log = logging.getLogger("piprayer")

DISCOVERY_PREFIX = "homeassistant"
HOME_ASSISTANT_STATUS = DISCOVERY_PREFIX + "/status"

# How often the state is checked for changes, e.g. the next azaan once one has played, or a skip from the
# command line. A message is only sent when something changed.
REFRESH_SECONDS = 30

INSTALL_HINT = "sudo apt install python3-paho-mqtt"

# While the broker can't be reached, paho-mqtt keeps retrying. The first failure is logged, then one an hour
FAILURE_LOG_SECONDS = 3600


# ------------------------------------------------------------------ messages

def node_id():
    """This Raspberry Pi in topic names and Home Assistant ids, e.g. raspberrypi."""
    return re.sub(r"[^a-z0-9_]", "_", socket.gethostname().split(".")[0].lower()) or "piprayer"


class Topics:
    def __init__(self, base):
        self.availability = base + "/availability"
        self.state = base + "/state"
        self.command = base + "/command"


def discovery_messages(topics, node):
    """(topic, payload) pairs that make Home Assistant create PiPrayer's entities."""
    device = {"identifiers": ["piprayer_" + node], "name": "PiPrayer",
              "manufacturer": "PiPrayer", "model": "Azaan player"}
    common = {"availability_topic": topics.availability, "device": device}

    def entity(component, key, name, **settings):
        # Home Assistant names the entities after the device and these names, e.g. sensor.piprayer_next_azaan
        config = dict(common, name=name, unique_id="piprayer_{}_{}".format(node, key), **settings)
        return "{}/{}/piprayer_{}/{}/config".format(DISCOVERY_PREFIX, component, node, key), json.dumps(config)

    state = {"state_topic": topics.state}
    return [
        entity("sensor", "next_azaan", "Next azaan", device_class="timestamp", icon="mdi:mosque",
               value_template="{{ value_json.next_azaan }}", json_attributes_topic=topics.state,
               json_attributes_template="{{ {'prayer': value_json.next_prayer, "
                                        "'skipped': value_json.next_skipped} | tojson }}", **state),
        entity("sensor", "next_prayer", "Next prayer", icon="mdi:clock-outline",
               value_template="{{ value_json.next_prayer }}", **state),
        entity("binary_sensor", "muted", "Muted", icon="mdi:volume-off",
               value_template="{{ 'ON' if value_json.muted_until else 'OFF' }}",
               json_attributes_topic=topics.state,
               json_attributes_template="{{ {'muted_until': value_json.muted_until} | tojson }}", **state),
        entity("sensor", "last_azaan", "Last azaan", icon="mdi:history",
               value_template="{{ value_json.last_result }}", json_attributes_topic=topics.state,
               json_attributes_template="{{ {'prayer': value_json.last_prayer, 'time': value_json.last_time, "
                                        "'error': value_json.last_error} | tojson }}", **state),
        entity("binary_sensor", "problem", "Problem", device_class="problem", entity_category="diagnostic",
               value_template="{{ 'ON' if value_json.problem else 'OFF' }}", json_attributes_topic=topics.state,
               json_attributes_template="{{ {'problem': value_json.problem} | tojson }}", **state),
        entity("button", "skip", "Skip next azaan", icon="mdi:skip-next",
               command_topic=topics.command, payload_press="skip"),
        entity("button", "mute_today", "Mute today", icon="mdi:volume-off",
               command_topic=topics.command, payload_press="mute"),
        entity("button", "resume", "Resume", icon="mdi:play",
               command_topic=topics.command, payload_press="resume"),
    ]


def state_payload(config, now, state, last=None, synchronised=None):
    """What Home Assistant shows: the next azaan and prayer, the end of a mute, how the last azaan went, and any
    problem: the last azaan didn't play as it should have, or the clock isn't set from the network."""
    entry = next_azaan(config, now, state)
    muted = state.muted_at(now)
    problems = ([describe_last(last, now)] if last and last.problem else []) + \
        ([clock.NOT_SYNCHRONISED] if synchronised is False else [])
    return json.dumps({
        "next_azaan": entry.at.isoformat() if entry else None,
        "next_prayer": entry.prayer.capitalize() if entry else None,
        "next_skipped": bool(entry and entry.silenced == "skipped"),
        "muted_until": muted.isoformat() if muted else None,
        "last_result": last.result if last else None,
        "last_prayer": last.prayer.capitalize() if last else None,
        "last_time": last.at.isoformat() if last else None,
        "last_error": last.error if last else None,
        "clock_synchronised": synchronised,
        "problem": ". ".join(problems) or None,
    }, sort_keys=True)


def handle_command(text, config_path, default_azaan):
    """Do what a command message asks: skip, mute, mute <days> or resume. Returns a log message."""
    words = text.strip().lower().split()
    if not words:
        return "Ignoring an empty MQTT command"
    config = Config(config_path, default_azaan)
    now = clock.now(config.tz)
    path = state_file(config_path)

    if words == ["skip"]:
        entry = skip_next(config, path, now)
        return "MQTT: skipping {}".format(entry.prayer + " at " + entry.at.strftime("%H:%M") if entry else "nothing")
    if words[0] == "mute" and len(words) <= 2:
        try:
            mute_until = mute_days(config, path, now, words[1] if len(words) == 2 else "1")
        except ValueError as e:
            return "Ignoring MQTT command {!r}: {}".format(text, e)
        return "MQTT: muted until {}".format(mute_until.strftime("%Y-%m-%d %H:%M"))
    if words == ["resume"]:
        resume(path)
        return "MQTT: skip and mute cleared"
    return "Ignoring unknown MQTT command {!r}, use skip, mute, mute <days> or resume".format(text)


# ------------------------------------------------------------------ connection

def connection_problem(host, port, timeout=5):
    """Why connecting to the broker fails, worked out by trying the steps paho-mqtt doesn't report."""
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return "can't find {} on the network. Check the name, or use its IP address".format(host)
    try:
        socket.create_connection((host, port), timeout=timeout).close()
    except ConnectionRefusedError:
        return "{} refused port {}. Check the broker is running, and the port".format(host, port)
    except socket.timeout:
        return "no answer from {} on port {}. Check the address, and that no firewall is in the way".format(host, port)
    except OSError as e:
        return "{} on port {}: {}".format(host, port, e.strerror or e)
    return "the connection to {}:{} was closed. Check the broker's log".format(host, port)


def load_paho():
    """The paho-mqtt client module, or None when it isn't installed."""
    try:
        import paho.mqtt.client as paho
    except ImportError:
        return None
    return paho


class MqttBridge:
    """Keeps a connection to the broker while the service runs, from paho's own thread and a refresh thread."""

    def __init__(self, config_path, default_azaan):
        self.config_path = config_path
        self.default_azaan = default_azaan
        self.settings = None
        self.client = None
        self.topics = None
        self.node = node_id()
        self.stopping = threading.Event()
        self.refresher = None
        self.lock = threading.Lock()
        self.last_state = None
        self.failures = 0
        self.last_failure_log = None

    def configure(self, settings):
        """Connect, reconnect or disconnect to match the configuration. None disconnects."""
        if settings == self.settings:
            return
        self.stop()
        # Remembered even if starting fails, so the error is logged once rather than every minute
        self.settings = settings
        if settings:
            self.start(settings)

    def start(self, settings):
        paho = load_paho()
        if paho is None:
            log.error("MQTT needs paho-mqtt, install it with: %s", INSTALL_HINT)
            return

        self.topics = Topics(settings.topic)
        client_id = "piprayer-" + self.node
        if hasattr(paho, "CallbackAPIVersion"):  # paho-mqtt 2
            client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=client_id)
        else:  # paho-mqtt 1, e.g. on Raspberry Pi OS Bookworm
            client = paho.Client(client_id=client_id)
        if settings.username:
            client.username_pw_set(settings.username, settings.password)
        client.will_set(self.topics.availability, "offline", retain=True)
        client.reconnect_delay_set(1, 60)
        client.on_connect = self.on_connect
        client.on_connect_fail = self.on_connect_fail
        client.on_message = self.on_message
        client.on_disconnect = self.on_disconnect

        try:
            client.connect_async(settings.host, settings.port, keepalive=60)
        except (OSError, ValueError) as e:
            log.error("Can't connect to MQTT broker %s:%d: %s", settings.host, settings.port, e)
            return
        client.loop_start()
        self.client = client
        self.stopping.clear()
        self.refresher = threading.Thread(target=self.refresh_loop, name="mqtt-refresh", daemon=True)
        self.refresher.start()
        log.info("MQTT: connecting to %s:%d", settings.host, settings.port)

    def stop(self):
        if self.client is None:
            return
        self.stopping.set()
        try:
            self.client.publish(self.topics.availability, "offline", retain=True).wait_for_publish(2)
        except (RuntimeError, ValueError, OSError):
            pass  # not connected
        self.client.disconnect()
        self.client.loop_stop()
        self.client = None
        self.last_state = None
        log.info("MQTT: disconnected")

    # paho calls these from its network thread. The arguments differ between paho-mqtt 1 and 2.

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", reason_code != 0):
            log.error("MQTT broker refused the connection: %s", reason_code)
            return
        if self.failures:
            log.info("MQTT: connected, after %d failed attempts", self.failures)
        else:
            log.info("MQTT: connected")
        self.failures = 0
        self.last_failure_log = None
        client.subscribe([(self.topics.command, 0), (HOME_ASSISTANT_STATUS, 0)])
        self.announce()

    def on_connect_fail(self, client, userdata):
        self.failures += 1
        now = clock.monotonic()
        if self.last_failure_log is not None and now - self.last_failure_log < FAILURE_LOG_SECONDS:
            return
        self.last_failure_log = now
        settings = self.settings
        log.error("MQTT: can't connect to the broker (attempt %d), retrying: %s", self.failures,
                  connection_problem(settings.host, settings.port))

    def on_disconnect(self, client, userdata, *args):
        if not self.stopping.is_set():
            log.warning("MQTT: connection lost, reconnecting")

    def on_message(self, client, userdata, message):
        text = message.payload.decode("utf-8", "replace")
        if message.topic == HOME_ASSISTANT_STATUS:
            if text == "online":  # Home Assistant restarted and needs the entities again
                self.announce()
            return
        if message.topic != self.topics.command:
            return
        if message.retain:
            # A command kept by the broker would be done again at every reconnect. Buttons don't send retained ones
            log.warning("Ignoring retained MQTT command %r, publish commands without retain", text)
            return
        try:
            log.info(handle_command(text, self.config_path, self.default_azaan))
        except ConfigError as e:
            log.error("Can't handle MQTT command, configuration error: %s", e)
            return
        self.refresh()

    def announce(self):
        """Send the entities, availability and state, e.g. after connecting."""
        for topic, payload in discovery_messages(self.topics, self.node):
            self.client.publish(topic, payload, retain=True)
        self.client.publish(self.topics.availability, "online", retain=True)
        with self.lock:
            self.last_state = None
        self.refresh()

    def refresh(self):
        """Send the state if it changed since it was last sent."""
        client = self.client
        if client is None:
            return
        try:
            config = Config(self.config_path, self.default_azaan)
            now = clock.now(config.tz)
            payload = state_payload(config, now, State.load(state_file(self.config_path)),
                                    LastAzaan.load(last_file(self.config_path)), clock.synchronised())
        except ConfigError as e:
            log.error("Can't update MQTT state, configuration error: %s", e)
            return
        with self.lock:
            if payload == self.last_state:
                return
            self.last_state = payload
        client.publish(self.topics.state, payload, retain=True)

    def refresh_loop(self):
        while not self.stopping.wait(REFRESH_SECONDS):
            self.refresh()
