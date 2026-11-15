# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""Connecting a Bluetooth speaker and playing a sound on it, with bluetoothctl, paplay and pactl.

The service runs this in a process of its own, python -m pipray.audio, so that stopping it (piprayer.py stop, the
status page or Home Assistant) stops paplay with it, and a stuck bluetoothctl can't hold up the service:

    python -m pipray.audio [--fallback] [--fade-in seconds] [--then sound file] <azaan number | sound file>
                           [volume percent] [speaker]
    python -m pipray.audio --connect [speaker address]
    python -m pipray.audio --disconnect <speaker address>

Without a speaker address, any paired speaker is used. With --fallback, the sound plays on the Raspberry Pi's own
audio output (headphone jack or HDMI) when no speaker connects. --connect connects a speaker ahead of time, so the
azaan starts on time, and prints its address. A second of silence plays before the sound, so the speaker is awake and
the start isn't cut off. --fade-in raises the volume from silent over the first seconds. --then plays another sound
straight after, e.g. the dua after the azaan, at the same volume and without disconnecting in between. If only that
one doesn't play, it still exits with 0.

While it plays on a speaker, it checks every few seconds that the speaker is still connected and playing the sound.
If the connection drops, the sound would carry on to another output or to nothing, so it stops, and exits with 3 so
PiPrayer tries again.

Stop it with SIGTERM: it stops playing and disconnects the speaker. The last line it prints says how it went.
"""
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from .config import MAC_ADDRESS, MAX_FADE_IN, MEDIA_DIR

# Exit codes
PLAYED = 0
INVALID = 1
NOT_CONNECTED = 2
NOT_PLAYED = 3
STOPPED = 4

# What it says when it played on the Raspberry Pi's own audio output
PLAYED_LOCALLY = "Played on the local audio output"

# How long to wait for a speaker to connect, and then for its audio output to appear
CONNECT_SECONDS = 10

# Silence played before the sound, so a Bluetooth speaker or an output that was idle is awake when the sound starts,
# and its start isn't cut off or faded in
WAKE_SECONDS = 1
WAKE_FORMAT = ["--raw", "--rate=48000", "--channels=2", "--format=s16le"]
WAKE_BYTES_PER_SECOND = 48000 * 2 * 2

# How often to check that the speaker is still connected while it plays
CHECK_SECONDS = 2

# How often the volume is raised while fading in
FADE_STEPS_PER_SECOND = 4

# 65536 is 100%, on the same scale as the system volume control
FULL_VOLUME = 65536

COMMAND_TIMEOUT = 10


def sleep(seconds):
    """time.sleep, replaced in tests."""
    time.sleep(seconds)


class Stopped(Exception):
    """Raised by SIGTERM or SIGINT."""


def say(message):
    print(message, flush=True)


def run(args, input=None):
    """Run a command and return its output, or "" if it can't run, fails or takes too long."""
    try:
        return subprocess.run(args, input=input, capture_output=True, text=True, timeout=COMMAND_TIMEOUT).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def succeeds(args):
    try:
        return subprocess.run(args, capture_output=True, timeout=COMMAND_TIMEOUT).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def has_pactl():
    return shutil.which("pactl") is not None


# ---------------------------------------------------------------- Bluetooth

def device_info(mac):
    return run(["bluetoothctl", "info", mac])


def is_connected(mac):
    return "Connected: yes" in device_info(mac)


def send(command, mac):
    """Give bluetoothctl a command, e.g. connect, without waiting for it to finish."""
    run(["bluetoothctl"], input="{} {}\n".format(command, mac))


def disconnect(mac):
    send("disconnect", mac)


def paired_devices():
    return [line.split()[1] for line in run(["bluetoothctl", "devices"]).splitlines()
            if line.startswith("Device") and len(line.split()) > 1]


def connect_speaker(speaker=None):
    """The address of a connected speaker: the given one, or one that is already connected, otherwise the first
    paired one that accepts the connection. None if none connects."""
    devices = [speaker] if speaker else paired_devices()
    for mac in devices:
        if is_connected(mac):
            return mac
    for mac in devices:
        if "Paired: yes" not in device_info(mac):
            continue
        send("connect", mac)
        for _ in range(CONNECT_SECONDS):
            if is_connected(mac):
                return mac
            sleep(1)
    return None


# ---------------------------------------------------------------- audio outputs and streams

def sinks():
    """The audio outputs as [(number, name)]."""
    result = []
    for line in run(["pactl", "list", "short", "sinks"]).splitlines():
        fields = line.split()
        if len(fields) >= 2:
            result.append((fields[0], fields[1]))
    return result


def speaker_sink(mac):
    """The audio output of a connected speaker, waiting for it to appear, e.g. bluez_output.AA_BB_CC_DD_EE_FF.1 with
    PipeWire. None when it can't be found, and the sound then plays on the default output."""
    if not has_pactl():
        return None
    id_ = mac.replace(":", "_")
    for _ in range(CONNECT_SECONDS):
        for _, name in sinks():
            if id_ in name:
                return name
        sleep(1)
    return None


def local_sink():
    """The Raspberry Pi's own audio output: the first one that isn't a Bluetooth device."""
    if not has_pactl():
        return None
    return next((name for _, name in sinks() if not name.startswith("bluez")), None)


def stream_of(pid):
    """The number of the stream a process plays, as pactl lists it, waiting up to 3 seconds for it to appear."""
    wanted = 'application.process.id = "{}"'.format(pid)
    for _ in range(30):
        number = None
        for line in run(["pactl", "list", "sink-inputs"]).splitlines():
            line = line.strip()
            if line.startswith("Sink Input #"):
                number = line[len("Sink Input #"):]
            elif line == wanted and number:
                return number
        sleep(0.1)
    return None


def speaker_playing(mac, device, stream):
    """Whether the speaker is still connected and its audio output is playing the stream (if known).

    When the connection drops, the output goes and PipeWire moves the stream to another output, or leaves it playing
    to nothing, and paplay carries on as if it were heard.
    """
    if not is_connected(mac):
        return False
    if not device or not has_pactl():
        return True
    sink = next((number for number, name in sinks() if name == device), None)
    if sink is None:
        return False
    if not stream:
        return True
    for line in run(["pactl", "list", "short", "sink-inputs"]).splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == stream:
            return fields[1] == sink
    return True  # a stream that isn't listed has just finished


class Fade(threading.Thread):
    """Raises a stream's volume from silent to the given level over a number of seconds."""

    def __init__(self, stream, level, seconds):
        super().__init__(daemon=True)
        self.stream, self.level, self.steps = stream, level, seconds * FADE_STEPS_PER_SECOND
        self.cancelled = False

    def run(self):
        for i in range(1, self.steps + 1):
            sleep(1 / FADE_STEPS_PER_SECOND)
            if self.cancelled or not succeeds(
                    ["pactl", "set-sink-input-volume", self.stream, str(self.level * i // self.steps)]):
                return

    def cancel(self):
        self.cancelled = True


# ---------------------------------------------------------------- playing

class Player:
    """Plays sounds on one output, keeping what is playing so a stop can end it."""

    def __init__(self, mac, device):
        self.mac = mac  # the speaker, None on the local output
        self.device = device  # the audio output, None for the default one
        self.paplay = None
        self.fade = None

    def device_args(self):
        return ["--device=" + self.device] if self.device else []

    def wake(self):
        """Play silence first, so the output is awake when the sound starts."""
        try:
            self.paplay = subprocess.Popen(["paplay"] + WAKE_FORMAT + self.device_args(), stdin=subprocess.PIPE)
        except OSError:
            return
        self.paplay.communicate(bytes(WAKE_SECONDS * WAKE_BYTES_PER_SECOND))
        self.paplay = None

    def start(self, level, sound):
        self.paplay = subprocess.Popen(["paplay", "--volume={}".format(level)] + self.device_args() + [sound])

    def play(self, sound, level, fade_in=0):
        """Play a sound to the end. Returns (paplay's exit code, whether the speaker stopped playing it)."""
        try:
            stream = None
            if fade_in and has_pactl():
                self.start(0, sound)
                stream = stream_of(self.paplay.pid)
                if stream:
                    self.fade = Fade(stream, level, fade_in)
                    self.fade.start()
                else:
                    # Never leave the azaan silent: start again at full volume
                    say("Can't find the stream to fade in, playing without fading in")
                    self.kill()
                    self.start(level, sound)
            else:
                self.start(level, sound)
        except OSError as e:
            say("Can't run paplay: {}".format(e))
            return 127, False
        # Not cleared if stopped meanwhile, so stop() can end them
        result = self.wait(stream)
        self.paplay = None
        if self.fade:
            self.fade.cancel()
            self.fade = None
        return result

    def wait(self, stream):
        """Wait for paplay to finish, checking the speaker while it plays. Stops paplay if the speaker stops playing
        it."""
        if self.mac and not stream and self.device and has_pactl():
            stream = stream_of(self.paplay.pid)
        while True:
            try:
                return exit_code(self.paplay.wait(timeout=CHECK_SECONDS if self.mac else None)), False
            except subprocess.TimeoutExpired:
                if self.paplay.poll() is None and not speaker_playing(self.mac, self.device, stream):
                    self.kill()
                    return exit_code(self.paplay.returncode), True

    def kill(self):
        if self.paplay and self.paplay.poll() is None:
            self.paplay.terminate()
            try:
                self.paplay.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.paplay.kill()
                self.paplay.wait()

    def stop(self):
        if self.fade:
            self.fade.cancel()
        self.kill()
        if self.mac:
            disconnect(self.mac)


def exit_code(code):
    """As the shell gives it: 128 plus the signal for one that killed it."""
    return 128 - code if code < 0 else code


def not_paired(mac):
    """Whether bluetoothctl says the speaker isn't paired, e.g. after it was reset or paired with another device.

    False when it can't tell, e.g. when bluetoothctl doesn't run.
    """
    try:
        info = subprocess.run(["bluetoothctl", "info", mac], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=COMMAND_TIMEOUT).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "Paired: no" in info or "{} not available".format(mac) in info


def not_connected(speaker):
    message = "Can't connect to bluetooth" + (" speaker " + speaker if speaker else "")
    if speaker and not_paired(speaker):
        message += ", which isn't paired (pair it again with python3 piprayer.py config)"
    return message


def play(sound, volume=100, speaker=None, fallback=False, fade_in=0, then=None):
    """Connect the speaker and play the sound, then the one after it. Returns the exit code."""
    player = None
    try:
        mac = connect_speaker(speaker)
        if mac:
            player = Player(mac, speaker_sink(mac))
            where = "speaker " + mac
        elif fallback:
            say(not_connected(speaker) + ", playing on the local audio output instead")
            player = Player(None, local_sink())
            where = "the local audio output"  # PiPrayer looks for PLAYED_LOCALLY
        else:
            say(not_connected(speaker))
            return NOT_CONNECTED

        level = volume * FULL_VOLUME // 100
        player.wake()
        status, lost = player.play(sound, level, fade_in)

        # The sound to play after it, while still connected. Stopping it stops this one too
        if status == 0 and not lost and then:
            then_status, then_lost = player.play(then, level)
            if then_lost:
                say("{} didn't play to the end: speaker {} disconnected".format(os.path.basename(then), mac))
            elif then_status != 0:
                say("{} didn't play: paplay failed with exit code {}".format(os.path.basename(then), then_status))
    except Stopped:
        if player:
            player.stop()
        say("Stopped")
        return STOPPED

    if mac:
        disconnect(mac)
    if lost:
        say("Speaker {} disconnected while playing".format(mac))
        return NOT_PLAYED
    if status != 0:
        say("paplay failed with exit code {}".format(status))
        return NOT_PLAYED
    say("Played on " + where)
    return PLAYED


# ---------------------------------------------------------------- command line

class Invalid(Exception):
    pass


def is_number(text):
    return text.isascii() and text.isdigit()


def sound_file(sound):
    """An azaan number's file, or the sound file itself."""
    return os.path.join(MEDIA_DIR, "azaan-{}.opus".format(sound)) if is_number(sound) else sound


def check_speaker(arg):
    speaker = arg.upper()
    if speaker and not MAC_ADDRESS.match(speaker):
        raise Invalid("Invalid speaker: {}, use the Bluetooth address, e.g. AA:BB:CC:DD:EE:FF".format(arg))
    return speaker or None


def main(args):
    """Run as the command line says. Returns the exit code."""
    mode, fallback, fade_in, then = "play", False, "0", None
    args = list(args)
    try:
        while args and args[0].startswith("--"):
            option = args.pop(0)
            if option == "--connect":
                mode = "connect"
            elif option == "--disconnect":
                mode = "disconnect"
            elif option == "--fallback":
                fallback = True
            elif option in ("--fade-in", "--then"):
                value = args.pop(0) if args else ""
                if option == "--fade-in":
                    fade_in = value
                else:
                    then = value or None
            else:
                raise Invalid("Unknown option: " + option)

        if not is_number(fade_in) or int(fade_in) > MAX_FADE_IN:
            raise Invalid("Invalid fade-in: {}, use a number of seconds from 0 to {}".format(fade_in, MAX_FADE_IN))

        if mode != "play":
            speaker = check_speaker(args[0] if args else "")
            if mode == "connect":
                mac = connect_speaker(speaker)
                if not mac:
                    say(not_connected(speaker))
                    return NOT_CONNECTED
                speaker_sink(mac)
                say(mac)
                return PLAYED
            if not speaker:
                raise Invalid("Missing speaker address")
            disconnect(speaker)
            return PLAYED

        sound = sound_file(args[0] if args else "")
        volume = args[1] if len(args) > 1 else "100"
        speaker = check_speaker(args[2] if len(args) > 2 else "")
        for path in [sound] + ([then] if then else []):
            if not os.path.isfile(path):
                raise Invalid("Sound file not found: " + path)
        if not is_number(volume) or int(volume) > 100:
            raise Invalid("Invalid volume: {}, use a number from 0 to 100".format(volume))
    except Invalid as e:
        say(str(e))
        return INVALID

    return play(sound, int(volume), speaker, fallback, int(fade_in), then)


def raise_stopped(signum, frame):
    raise Stopped


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, raise_stopped)
    signal.signal(signal.SIGINT, raise_stopped)
    sys.exit(main(sys.argv[1:]))
