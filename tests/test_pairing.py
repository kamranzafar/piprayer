# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding, pairing and forgetting speakers from the settings page, with a fake bluetoothctl."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pipray.pairing import Pairing
from pipray.settings import SettingsRoutes
from pipray.state import Playing, playing_file
from test_settings import settings  # noqa: F401, the settings fixture
from test_wizard import ECHO, KITCHEN, PHONE, SOUNDBAR, bluetooth  # noqa: F401, the bluetooth fixture

MELBOURNE = ZoneInfo("Australia/Melbourne")


class Later:
    """Runs the background work when asked, so a test can look at the page while it runs."""

    def __init__(self):
        self.work = []

    def __call__(self, work):
        self.work.append(work)

    def finish(self):
        while self.work:
            self.work.pop(0)()


@pytest.fixture
def speakers(settings, bluetooth):  # noqa: F811
    """The settings page with the fake bluetoothctl, logged in. Background work runs straight away."""
    bluetooth.nearby = {SOUNDBAR: "Soundbar", "EE-EE-EE-EE-EE-05".replace("-", ":"): "EE-EE-EE-EE-EE-05"}
    original = bluetooth.__call__

    def bluetoothctl(*args, timeout=10, input=None, errors=False):
        if args and args[0] == "remove":
            bluetooth.commands.append(" ".join(args))
            if args[1] in bluetooth.devices:
                del bluetooth.devices[args[1]]
                return "Device has been removed\n"
            return "Device {} not available\n".format(args[1])
        return original(*args, timeout=timeout)

    from pipray import bluetooth as bluetooth_module
    bluetooth_module.bluetoothctl = bluetoothctl  # undone by the bluetooth fixture's monkeypatch

    s = settings()
    s.routes = SettingsRoutes(s.path, 1, s.routes.throttle)
    s.routes.pairing = Pairing(playing=s.routes.playing, run=lambda work: work())
    s.login()
    s.bluetooth = bluetooth

    def section():
        return s.get("/settings/speaker").body

    def act(action, **form):
        return s.post("speaker/" + action, **form)

    s.section = section
    s.act = act
    return s


def test_section_offers_to_find_speakers_with_pairing_hints(speakers):
    body = speakers.section()
    assert 'action="/settings/speaker/scan"' in body
    assert "Find speakers" in body
    assert "Alexa, pair Bluetooth" in body
    assert "Enable pairing mode" in body
    # the paired speakers, each of which can be forgotten
    assert "Kitchen Speaker (AA:AA:AA:AA:AA:01)" in body
    assert body.count('action="/settings/speaker/forget"') == 2


def test_find_then_pair_then_use_a_speaker(speakers):
    response = speakers.act("scan")
    assert response.status == 303
    assert response.location == "/settings?speakers=1#speaker"

    body = speakers.section()
    # named devices that aren't paired, also those bluetoothctl knew, not those without a name, nor the paired ones
    assert "Soundbar ({})".format(SOUNDBAR) in body
    assert "Phone ({})".format(PHONE) in body
    assert "EE-EE-EE-EE-EE-05" not in body
    assert body.count('action="/settings/speaker/pair"') == 2

    speakers.act("pair", mac=SOUNDBAR.lower(), name="Soundbar")
    assert "pair " + SOUNDBAR in speakers.bluetooth.commands
    assert "trust " + SOUNDBAR in speakers.bluetooth.commands
    body = speakers.section()
    assert "Paired Soundbar." in body
    assert "Use Soundbar for the Azaan" in body
    assert '<option value="{}">Soundbar ({})</option>'.format(SOUNDBAR, SOUNDBAR) in body

    response = speakers.act("use", mac=SOUNDBAR)
    assert response.location == "/settings?speakers=1&saved=speaker#speaker"
    assert "speaker = " + SOUNDBAR in speakers.text()
    assert "Use Soundbar" not in speakers.section()


def test_pairing_failure_is_shown(speakers):
    speakers.bluetooth.refuse.add(SOUNDBAR)
    speakers.act("scan")
    speakers.act("pair", mac=SOUNDBAR, name="Soundbar")
    body = speakers.section()
    assert "Couldn&#x27;t pair Soundbar: Failed to pair: org.bluez.Error.AuthenticationFailed" in body
    assert "Use Soundbar" not in body
    assert "speaker =" not in speakers.text()


def test_nothing_found(speakers):
    speakers.bluetooth.nearby = {}
    del speakers.bluetooth.devices[PHONE]
    speakers.act("scan")
    assert "No speakers found. Is the speaker in pairing mode, and close by?" in speakers.section()


def test_forget_a_speaker(speakers):
    speakers.act("forget", mac=KITCHEN, name="Kitchen Speaker")
    assert "remove " + KITCHEN in speakers.bluetooth.commands
    body = speakers.section()
    assert "Forgot Kitchen Speaker." in body
    assert "Kitchen Speaker (AA" not in body


def test_the_chosen_speaker_cant_be_forgotten(speakers):
    with open(speakers.path, "a") as f:
        f.write("speaker = {}\n".format(ECHO))
    body = speakers.section()
    assert body.count('action="/settings/speaker/forget"') == 1
    assert '<input type="hidden" name="mac" value="{}"><button class="secondary">Forget'.format(ECHO) not in body

    speakers.act("forget", mac=ECHO)
    assert "remove " + ECHO not in speakers.bluetooth.commands


def test_speaker_that_isnt_paired_any_more_can_be_paired_again(speakers):
    with open(speakers.path, "a") as f:
        f.write("speaker = {}\n".format(SOUNDBAR))
    body = speakers.section()
    assert "{} isn&#x27;t paired any more".format(SOUNDBAR) in body
    assert "Pair again" in body

    speakers.act("pair", mac=SOUNDBAR, again="1")
    commands = speakers.bluetooth.commands
    assert commands.index("remove " + SOUNDBAR) < commands.index("--timeout 15 scan on") < \
        commands.index("pair " + SOUNDBAR)
    body = speakers.section()
    assert "Paired {}.".format(SOUNDBAR) in body
    assert "isn&#x27;t paired any more" not in body
    assert "Use " not in body  # it is the chosen speaker already


def test_page_polls_while_scanning_and_one_runs_at_a_time(speakers):
    later = Later()
    speakers.routes.pairing.run = later
    speakers.act("scan")

    body = speakers.section()
    assert "Looking for speakers, for about 15 seconds..." in body
    assert 'hx-trigger="every 2s"' in body
    assert "Find speakers" not in body

    speakers.act("pair", mac=SOUNDBAR)  # refused while scanning
    assert len(later.work) == 1
    speakers.act("forget", mac=KITCHEN)
    assert "remove " + KITCHEN not in speakers.bluetooth.commands

    later.finish()
    body = speakers.section()
    assert "every 2s" not in body
    assert "Soundbar ({})".format(SOUNDBAR) in body


def test_page_says_which_speaker_is_pairing(speakers):
    speakers.act("scan")
    later = Later()
    speakers.routes.pairing.run = later
    speakers.act("pair", mac=SOUNDBAR, name="Soundbar")
    assert "Pairing Soundbar..." in speakers.section()
    later.finish()
    assert "Paired Soundbar." in speakers.section()


def test_doesnt_scan_while_an_azaan_plays(speakers):
    Playing("fajr", False, datetime(2026, 11, 12, 4, 23, tzinfo=MELBOURNE)).save(playing_file(speakers.path))
    speakers.act("scan")
    assert "--timeout 15 scan on" not in speakers.bluetooth.commands
    assert "Wait until the Azaan has finished" in speakers.section()


def test_failure_in_the_background_is_shown_not_left_waiting(speakers, monkeypatch):
    from pipray import bluetooth as bluetooth_module

    def broken(seconds=15):
        raise RuntimeError("adapter gone")

    monkeypatch.setattr(bluetooth_module, "scan_speakers", broken)
    speakers.act("scan")
    body = speakers.section()
    assert "Something went wrong: adapter gone" in body
    assert "every 2s" not in body


def test_speaker_actions_need_the_pin(speakers):
    speakers.cookies = {}
    response = speakers.act("scan")
    assert response.status == 303
    assert response.location == "/settings"
    assert "--timeout 15 scan on" not in speakers.bluetooth.commands


@pytest.mark.parametrize("action, form", [("pair", {"mac": "nonsense"}), ("forget", {}), ("dance", {"mac": ECHO})])
def test_unknown_speaker_actions_are_not_found(speakers, action, form):
    assert speakers.act(action, **form) is None


def test_device_known_but_not_paired_isnt_listed_before_a_scan(speakers):
    # bluetoothctl knows the phone, but it isn't paired, so it isn't offered to forget
    assert "Phone ({})".format(PHONE) not in speakers.section()
