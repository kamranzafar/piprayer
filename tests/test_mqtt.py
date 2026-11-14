# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""MQTT for Home Assistant, with a fake paho-mqtt so the tests don't need it or a broker.

It was also checked against a real broker with paho-mqtt 1.6.1 and 2.2.0.
"""
import json
import logging
import sys
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import MELBOURNE_CONFIG
from pipray import mqtt as mqtt_module
from pipray.config import Config, ConfigError, MqttSettings
from pipray.mqtt import MqttBridge, Topics, discovery_messages, handle_command, state_payload
from pipray.state import State, state_file

MELBOURNE = ZoneInfo("Australia/Melbourne")
MQTT_CONFIG = MELBOURNE_CONFIG + "mqtt-host = homeassistant.local\nmqtt-username = piprayer\nmqtt-password = secret\n"
NODE = "raspberrypi"

# At 14:00 on Thursday 12 November 2026 the next azaan is Asr at 18:03


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch, at_time):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(mqtt_module.socket, "gethostname", lambda: "RaspberryPi.local")
    at_time(datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))


class Message:
    def __init__(self, topic, payload, retain=False):
        self.topic, self.payload, self.retain = topic, payload.encode(), retain


class Published:
    def wait_for_publish(self, timeout=None):
        pass


def fake_paho(monkeypatch, version):
    """Install a fake paho.mqtt.client of the given major version, returning the list of clients made."""
    clients = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs
            self.published, self.subscribed, self.calls = [], [], []
            clients.append(self)

        def username_pw_set(self, username, password):
            self.login = (username, password)

        def will_set(self, topic, payload, retain=False):
            self.will = (topic, payload, retain)

        def reconnect_delay_set(self, low, high):
            pass

        def connect_async(self, host, port, keepalive):
            self.connected_to = (host, port)

        def loop_start(self):
            self.calls.append("loop_start")

        def loop_stop(self):
            self.calls.append("loop_stop")

        def disconnect(self):
            self.calls.append("disconnect")

        def subscribe(self, topics):
            self.subscribed.extend(topic for topic, _ in topics)

        def publish(self, topic, payload, retain=False):
            self.published.append((topic, payload, retain))
            return Published()

    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = Client
    if version == 2:
        client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2="v2")
    mqtt_package = types.ModuleType("paho.mqtt")
    mqtt_package.client = client_module
    paho_package = types.ModuleType("paho")
    paho_package.mqtt = mqtt_package
    for name, module in [("paho", paho_package), ("paho.mqtt", mqtt_package), ("paho.mqtt.client", client_module)]:
        monkeypatch.setitem(sys.modules, name, module)
    return clients


def published(client, topic):
    return [payload for t, payload, _ in client.published if t == topic]


# ------------------------------------------------------------------ settings

def test_mqtt_is_off_by_default(write_config):
    assert Config(write_config()).mqtt is None


def test_mqtt_settings(write_config):
    settings = Config(write_config(MQTT_CONFIG + "mqtt-port = 1884\nmqtt-topic = home/azaan\n")).mqtt
    assert vars(settings) == {"host": "homeassistant.local", "port": 1884, "username": "piprayer",
                              "password": "secret", "topic": "home/azaan"}


def test_mqtt_defaults(write_config):
    settings = Config(write_config(MELBOURNE_CONFIG + "mqtt-host = 192.168.1.10\n")).mqtt
    assert (settings.port, settings.username, settings.password, settings.topic) == (1883, None, None, "piprayer")


@pytest.mark.parametrize("line, message", [
    ("mqtt-topic = home/#", "Invalid mqtt-topic: home/#"),
    ("mqtt-topic = /piprayer", "Invalid mqtt-topic: /piprayer"),
    ("mqtt-port = 0", "Invalid mqtt-port: 0"),
])
def test_invalid_mqtt_settings(write_config, line, message):
    with pytest.raises(ConfigError, match=message):
        Config(write_config(MQTT_CONFIG + line + "\n"))


# ------------------------------------------------------------------ messages

def test_discovery_creates_sensors_and_buttons():
    messages = dict(discovery_messages(Topics("piprayer"), NODE))

    assert sorted(messages) == [
        "homeassistant/binary_sensor/piprayer_raspberrypi/muted/config",
        "homeassistant/binary_sensor/piprayer_raspberrypi/playing/config",
        "homeassistant/binary_sensor/piprayer_raspberrypi/problem/config",
        "homeassistant/button/piprayer_raspberrypi/mute_today/config",
        "homeassistant/button/piprayer_raspberrypi/resume/config",
        "homeassistant/button/piprayer_raspberrypi/skip/config",
        "homeassistant/button/piprayer_raspberrypi/stop/config",
        "homeassistant/number/piprayer_raspberrypi/hijri_adjust/config",
        "homeassistant/sensor/piprayer_raspberrypi/hijri_date/config",
        "homeassistant/sensor/piprayer_raspberrypi/last_azaan/config",
        "homeassistant/sensor/piprayer_raspberrypi/next_azaan/config",
        "homeassistant/sensor/piprayer_raspberrypi/next_prayer/config",
    ]
    configs = {topic.split("/")[3]: json.loads(payload) for topic, payload in messages.items()}
    assert configs["next_azaan"]["device_class"] == "timestamp"
    assert configs["next_azaan"]["state_topic"] == "piprayer/state"
    assert configs["skip"]["command_topic"] == "piprayer/command"
    assert [configs[key]["payload_press"] for key in ("skip", "mute_today", "resume")] == ["skip", "mute", "resume"]
    assert all(config["availability_topic"] == "piprayer/availability" for config in configs.values())
    assert configs["problem"]["device_class"] == "problem"
    assert configs["stop"]["payload_press"] == "stop"
    assert (configs["hijri_adjust"]["min"], configs["hijri_adjust"]["max"], configs["hijri_adjust"]["step"]) == \
        (-2, 2, 1)
    assert configs["hijri_adjust"]["command_topic"] == "piprayer/command"
    assert configs["hijri_adjust"]["command_template"] == "hijri-adjust {{ value | int }}"
    assert configs["hijri_date"]["value_template"] == "{{ value_json.hijri_date }}"
    assert len({config["unique_id"] for config in configs.values()}) == 12
    assert all(config["device"]["identifiers"] == ["piprayer_raspberrypi"] for config in configs.values())


def test_state_payload(write_config):
    path = write_config()
    config = Config(path)
    now = datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE)

    assert json.loads(state_payload(config, now, State())) == {
        "next_azaan": "2026-11-12T18:03:00+11:00", "next_prayer": "Asr", "next_skipped": False, "muted_until": None,
        "last_result": None, "last_prayer": None, "last_time": None, "last_error": None,
        "clock_synchronised": None, "problem": None, "playing": None, "playing_reminder": False,
        "hijri_date": "2 Jumada al-Thani 1448", "hijri_adjust": 0,
        "hijri": {"day": 2, "month": 6, "month_name": "Jumada al-Thani", "year": 1448, "evening": False, "adjust": 0,
                  "text": "2 Jumada al-Thani 1448"},
    }
    state = State([datetime(2026, 11, 12, 18, 3, tzinfo=MELBOURNE)], datetime(2026, 11, 14, tzinfo=MELBOURNE))
    data = json.loads(state_payload(config, now, state))
    assert (data["next_skipped"], data["muted_until"]) == (False, "2026-11-14T00:00:00+11:00")  # muted wins


@pytest.mark.parametrize("command, skip, mute_until, log", [
    ("skip", ["2026-11-12T18:03:00+11:00"], None, "MQTT: skipping asr at 18:03"),
    ("mute", [], "2026-11-13T00:00:00+11:00", "MQTT: muted until 2026-11-13 00:00"),
    (" Mute 3 ", [], "2026-11-15T00:00:00+11:00", "MQTT: muted until 2026-11-15 00:00"),
    ("mute 99", [], None, "days must be a whole number from 1 to 30"),
    ("dance", [], None, "Ignoring unknown MQTT command 'dance'"),
    ("", [], None, "Ignoring an empty MQTT command"),
])
def test_commands(write_config, command, skip, mute_until, log):
    path = write_config()
    assert log in handle_command(command, path, 1)
    state = State.load(state_file(path))
    assert [at.isoformat() for at in state.skip] == skip
    assert (state.mute_until.isoformat() if state.mute_until else None) == mute_until


def test_resume_command(write_config):
    path = write_config()
    handle_command("skip", path, 1)
    handle_command("mute", path, 1)
    assert handle_command("resume", path, 1) == "MQTT: skip and mute cleared"
    state = State.load(state_file(path))
    assert (state.skip, state.mute_until) == ([], None)


# ------------------------------------------------------------------ connection

@pytest.fixture(params=[1, 2], ids=["paho-mqtt-1", "paho-mqtt-2"])
def connected(request, monkeypatch, write_config):
    """A bridge connected through a fake paho-mqtt, as (bridge, client, config path)."""
    clients = fake_paho(monkeypatch, request.param)
    path = write_config(MQTT_CONFIG)
    bridge = MqttBridge(path, 1)
    bridge.configure(Config(path).mqtt)
    client = clients[0]
    # paho-mqtt 2 passes properties too
    args = (client, None, {}, 0) + ((None,) if request.param == 2 else ())
    bridge.on_connect(*args)
    yield bridge, client, path
    bridge.configure(None)


def test_connects_with_the_configured_settings(connected):
    bridge, client, path = connected
    assert client.connected_to == ("homeassistant.local", 1883)
    assert client.login == ("piprayer", "secret")
    assert client.kwargs["client_id"] == "piprayer-raspberrypi"
    assert client.will == ("piprayer/availability", "offline", True)
    assert "loop_start" in client.calls


def test_paho_2_uses_its_new_callbacks(monkeypatch, write_config):
    clients = fake_paho(monkeypatch, 2)
    path = write_config(MQTT_CONFIG)
    bridge = MqttBridge(path, 1)
    bridge.configure(Config(path).mqtt)
    assert clients[0].args == ("v2",)
    bridge.configure(None)


@pytest.mark.parametrize("command, saved, log", [
    ("hijri-adjust 1", "1", "MQTT: hijri-adjust set to 1, the Hijri date is 3 Jumada al-Thani 1448"),
    ("hijri-adjust -2", "-2", "MQTT: hijri-adjust set to -2, the Hijri date is 30 Jumada al-Awwal 1448"),
    ("hijri-adjust 3", None, "Ignoring MQTT command 'hijri-adjust 3': Invalid hijri-adjust: 3"),
    ("hijri-adjust soon", None, "Ignoring MQTT command 'hijri-adjust soon': Invalid hijri-adjust: soon"),
])
def test_hijri_adjust_command(write_config, at_time, command, saved, log):
    at_time(datetime(2026, 11, 12, 14, 0, tzinfo=MELBOURNE))
    path = write_config()
    assert log in handle_command(command, path, 1)
    assert open(path).read() == MELBOURNE_CONFIG + ("hijri-adjust = {}\n".format(saved) if saved else "")


def test_hijri_adjust_from_home_assistant_is_published(connected):
    bridge, client, path = connected
    bridge.on_message(client, None, Message("piprayer/command", "hijri-adjust 1"))
    state = json.loads(published(client, "piprayer/state")[-1])
    assert (state["hijri_adjust"], state["hijri_date"]) == (1, "3 Jumada al-Thani 1448")


def test_announces_itself_on_connecting(connected):
    bridge, client, path = connected
    assert client.subscribed == ["piprayer/command", "homeassistant/status"]
    discovery = [(topic, retain) for topic, _, retain in client.published if topic.startswith("homeassistant/")]
    assert len(discovery) == 12 and all(retain for _, retain in discovery)
    assert published(client, "piprayer/availability") == ["online"]
    assert json.loads(published(client, "piprayer/state")[0])["next_prayer"] == "Asr"


def test_command_message_changes_state_and_publishes_it(connected):
    bridge, client, path = connected
    bridge.on_message(client, None, Message("piprayer/command", "skip"))

    assert State.load(state_file(path)).skip == [datetime(2026, 11, 12, 18, 3, tzinfo=MELBOURNE)]
    assert json.loads(published(client, "piprayer/state")[-1])["next_skipped"] is True


def test_ignores_retained_commands_and_other_topics(connected):
    bridge, client, path = connected
    bridge.on_message(client, None, Message("piprayer/command", "skip", retain=True))
    bridge.on_message(client, None, Message("piprayer/state", "skip"))
    assert State.load(state_file(path)).skip == []


def test_announces_again_when_home_assistant_restarts(connected):
    bridge, client, path = connected
    client.published.clear()
    bridge.on_message(client, None, Message("homeassistant/status", "online"))
    assert len([t for t, _, _ in client.published if t.startswith("homeassistant/")]) == 12
    assert published(client, "piprayer/state")  # sent again, although unchanged


def test_state_is_only_sent_when_it_changes(connected):
    bridge, client, path = connected
    bridge.refresh()
    assert len(published(client, "piprayer/state")) == 1

    handle_command("mute", path, 1)  # e.g. from the command line or the status page
    bridge.refresh()
    states = published(client, "piprayer/state")
    assert len(states) == 2 and json.loads(states[1])["muted_until"] == "2026-11-13T00:00:00+11:00"


def test_stopping_says_offline_and_disconnects(connected):
    bridge, client, path = connected
    bridge.configure(None)
    assert published(client, "piprayer/availability")[-1] == "offline"
    assert client.calls[-2:] == ["disconnect", "loop_stop"]


def test_reconnects_when_settings_change(monkeypatch, write_config):
    clients = fake_paho(monkeypatch, 2)
    path = write_config(MQTT_CONFIG)
    bridge = MqttBridge(path, 1)
    bridge.configure(Config(path).mqtt)
    bridge.configure(Config(path).mqtt)  # unchanged
    assert len(clients) == 1
    bridge.configure(MqttSettings("broker.lan", 1883, None, None, "piprayer"))
    assert len(clients) == 2 and clients[1].connected_to == ("broker.lan", 1883)
    bridge.configure(None)


def test_refused_connection_is_logged(connected, caplog):
    bridge, client, path = connected
    client.subscribed.clear()
    bridge.on_connect(client, None, {}, 5)  # not authorised
    assert "MQTT broker refused the connection: 5" in caplog.text
    assert client.subscribed == []


def test_without_paho_mqtt_explains_how_to_install_it(monkeypatch, write_config, caplog):
    monkeypatch.setitem(sys.modules, "paho", None)  # makes the import fail
    path = write_config(MQTT_CONFIG)
    bridge = MqttBridge(path, 1)
    with caplog.at_level(logging.ERROR):
        bridge.configure(Config(path).mqtt)
        bridge.configure(Config(path).mqtt)
    assert caplog.text.count("MQTT needs paho-mqtt, install it with: sudo apt install python3-paho-mqtt") == 1
    assert bridge.client is None


def test_service_connects_and_disconnects(monkeypatch, write_config, fake_clock):
    clients = fake_paho(monkeypatch, 2)
    clock = fake_clock(datetime(2026, 11, 12, 15, 0), MELBOURNE)
    clock.run_service(write_config(MQTT_CONFIG), hours=4)

    assert clients[0].connected_to == ("homeassistant.local", 1883)
    assert clients[0].calls[-2:] == ["disconnect", "loop_stop"]
    assert [sound for _, sound, _ in clock.played] == ["1"]  # the azaan still plays


# ------------------------------------------------------------------ connection failures

@pytest.mark.parametrize("failure, message", [
    ("resolve", "can't find homeassistant.local on the network. Check the name, or use its IP address"),
    (ConnectionRefusedError(), "homeassistant.local refused port 1883. Check the broker is running, and the port"),
    (mqtt_module.socket.timeout(), "no answer from homeassistant.local on port 1883. Check the address"),
    (OSError(113, "No route to host"), "homeassistant.local on port 1883: No route to host"),
    (None, "the connection to homeassistant.local:1883 was closed. Check the broker's log"),
])
def test_connection_problem(monkeypatch, failure, message):
    def getaddrinfo(*args, **kwargs):
        if failure == "resolve":
            raise mqtt_module.socket.gaierror("Name or service not known")
        return []

    class Connection:
        def close(self):
            pass

    def create_connection(address, timeout):
        if isinstance(failure, Exception):
            raise failure
        return Connection()

    monkeypatch.setattr(mqtt_module.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(mqtt_module.socket, "create_connection", create_connection)
    assert message in mqtt_module.connection_problem("homeassistant.local", 1883)


def test_failures_are_logged_once_an_hour(monkeypatch, write_config, caplog):
    caplog.set_level(logging.INFO)
    fake_paho(monkeypatch, 2)
    monkeypatch.setattr(mqtt_module, "connection_problem", lambda host, port: "no answer")
    seconds = [0]
    monkeypatch.setattr(mqtt_module.clock, "monotonic", lambda: seconds[0])
    path = write_config(MQTT_CONFIG)
    bridge = MqttBridge(path, 1)
    bridge.configure(Config(path).mqtt)

    for seconds[0] in (0, 30, 90, 3599, 3600, 3700):
        bridge.on_connect_fail(bridge.client, None)
    logged = [r.message for r in caplog.records if "can't connect" in r.message]
    assert logged == ["MQTT: can't connect to the broker (attempt 1), retrying: no answer",
                      "MQTT: can't connect to the broker (attempt 5), retrying: no answer"]

    bridge.on_connect(bridge.client, None, {}, 0, None)
    assert "MQTT: connected, after 6 failed attempts" in caplog.text
    bridge.configure(None)
