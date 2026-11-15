# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""pipray.audio, which connects the speaker and plays, run with fake bluetoothctl, paplay and pactl on the PATH."""
import os
import shutil
import signal
import stat
import subprocess
import sys
import textwrap
import time

import pytest

from conftest import ROOT
from pipray import audio

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash for the fake commands")

SPEAKER_1 = "AA:AA:AA:AA:AA:01"
SPEAKER_2 = "BB:BB:BB:BB:BB:02"


def make_command(directory, name, script):
    path = directory / name
    path.write_text("#!/bin/bash\n" + textwrap.dedent(script))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class Result:
    def __init__(self, returncode, stdout):
        self.returncode, self.stdout = returncode, stdout


@pytest.fixture
def bluetooth(tmp_path, monkeypatch, capsys):
    """Fake bluetoothctl with two paired speakers. Records commands in a log.

    The player runs in this process, with waits cut short, except when it is started to be stopped.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()

    make_command(bin_dir, "bluetoothctl", """\
        STATE={state}
        if [ "$1" = devices ]; then
          echo "Device {s1} Kitchen Speaker"
          echo "Device {s2} Echo Pop"
          exit
        fi
        if [ "$1" = info ]; then
          echo "Device $2"
          echo "	Paired: yes"
          grep -qx "$2" $STATE/connected 2>/dev/null && echo "	Connected: yes" || echo "	Connected: no"
          exit
        fi
        read -r cmd mac
        echo "$cmd $mac" >> $STATE/log
        if [ "$cmd" = connect ] && ! grep -qx "$mac" $STATE/refuse 2>/dev/null; then
          echo "$mac" >> $STATE/connected
        fi
        """.format(state=state, s1=SPEAKER_1, s2=SPEAKER_2))
    def paplay(script):
        """Fake paplay. The silence that wakes the output first is recorded apart from the sounds played."""
        make_command(bin_dir, "paplay", """\
            if [ "$1" = --raw ]; then
              cat > /dev/null
              echo "wake $*" >> {state}/wakes
              echo "wake" >> {state}/order
              exit 0
            fi
            echo "play" >> {state}/order
            """.format(state=state) + textwrap.dedent(script))

    paplay('echo "play $*" >> {}/log\n'.format(state))
    monkeypatch.setenv("PATH", "{}:{}".format(bin_dir, os.environ["PATH"]))
    monkeypatch.setattr(audio, "sleep", lambda seconds: time.sleep(min(seconds, 0.01)))
    monkeypatch.setattr(audio, "CHECK_SECONDS", 0.05)

    class Bluetooth:
        bin_dir = None
        state = None

        def connected(self, *macs):
            (state / "connected").write_text("".join(m + "\n" for m in macs))

        def refuse(self, *macs):
            (state / "refuse").write_text("".join(m + "\n" for m in macs))

        def sinks(self, *names):
            """Audio outputs pactl lists. A speaker's output is only listed while it is connected."""
            make_command(bin_dir, "pactl", """\
                for name in {names}; do
                  mac=$(echo "$name" | sed -n 's/^bluez_output\\.\\([0-9A-F_]*\\)\\..*/\\1/p' | tr _ :)
                  if [ -z "$mac" ] || grep -qx "$mac" {state}/connected 2>/dev/null; then
                    printf '1\\t%s\\tPipeWire\\ts16le 2ch 48000Hz\\tSUSPENDED\\n' "$name"
                  fi
                done
                """.format(names=" ".join(names), state=state))

        def paplay_fails(self):
            paplay('echo "play $*" >> {}/log\nexit 1\n'.format(state))

        def run(self, *args):
            capsys.readouterr()
            code = audio.main(list(args))
            return Result(code, capsys.readouterr().out)

        def fading(self, steps, stream_found=True):
            """paplay plays until the volume has been raised the given number of times, and pactl lists its stream
            as number 7, and records volume changes."""
            (state / "steps").write_text(str(steps))
            paplay("""\
                echo "play $*" >> {state}/log
                echo $$ > {state}/paplay
                for ((i = 0; i < 100; i++)); do
                  [ -f {state}/faded ] && break
                  /bin/sleep 0.03
                done
                """.format(state=state))
            make_command(bin_dir, "pactl", """\
                if [ "$1 $2" = "list sink-inputs" ]; then
                  {found} && [ -f {state}/paplay ] || exit 0
                  printf 'Sink Input #7\\n\\tDriver: PipeWire\\n\\tProperties:\\n'
                  printf '\\t\\tapplication.name = "paplay"\\n'
                  printf '\\t\\tapplication.process.id = "%s"\\n' "$(cat {state}/paplay)"
                  exit 0
                fi
                if [ "$1" = set-sink-input-volume ]; then
                  echo "volume $2 $3" >> {state}/log
                  [ "$(grep -c ^volume {state}/log)" -ge "$(cat {state}/steps)" ] && touch {state}/faded
                fi
                exit 0
                """.format(state=state, found="true" if stream_found else "false"))

        def paplay_blocks(self):
            """paplay plays until it is stopped."""
            paplay("""\
                echo "play $*" >> {state}/log
                touch {state}/playing
                while true; do /bin/sleep 0.05; done
                """.format(state=state))

        def start(self, *args):
            """Start the player in a process of its own, as PiPrayer does."""
            env = dict(os.environ, PYTHONPATH=ROOT)
            return subprocess.Popen([sys.executable, "-m", "pipray.audio"] + list(args), env=env,
                                    stdout=subprocess.PIPE, text=True, start_new_session=True)

        def wait_until_playing(self, timeout=10):
            deadline = time.monotonic() + timeout
            while not (state / "playing").exists():
                assert time.monotonic() < deadline, "paplay didn't start"
                time.sleep(0.05)

        def play(self, *args):
            return self.run(*(args or ["3"]))

        def log(self):
            path = state / "log"
            return path.read_text().splitlines() if path.exists() else []

        def wakes(self):
            """How the output was woken with silence, one line each time."""
            path = state / "wakes"
            return path.read_text().splitlines() if path.exists() else []

        def order(self):
            """Waking the output and playing sounds, in the order they happened."""
            path = state / "order"
            return path.read_text().splitlines() if path.exists() else []

    result = Bluetooth()
    result.bin_dir, result.state = bin_dir, state
    return result


def azaan_file(azaan, volume=65536):
    return "play --volume={} {}".format(volume, os.path.join(ROOT, "media", "azaan-{}.opus".format(azaan)))


def test_connects_plays_and_disconnects(bluetooth):
    result = bluetooth.play("3")
    assert result.returncode == 0
    assert bluetooth.log() == ["connect " + SPEAKER_1, azaan_file(3), "disconnect " + SPEAKER_1]


def test_uses_speaker_that_is_already_connected(bluetooth):
    bluetooth.connected(SPEAKER_2)
    assert bluetooth.play().returncode == 0
    assert bluetooth.log() == [azaan_file(3), "disconnect " + SPEAKER_2]


def test_tries_next_speaker_when_one_refuses(bluetooth):
    bluetooth.refuse(SPEAKER_1)
    assert bluetooth.play().returncode == 0
    assert bluetooth.log() == [
        "connect " + SPEAKER_1, "connect " + SPEAKER_2, azaan_file(3), "disconnect " + SPEAKER_2]


def test_fails_when_no_speaker_connects(bluetooth):
    bluetooth.refuse(SPEAKER_1, SPEAKER_2)
    result = bluetooth.play()
    assert result.returncode == 2
    assert "Can't connect to bluetooth" in result.stdout
    assert not any(line.startswith("play") for line in bluetooth.log())


def test_plays_at_requested_volume(bluetooth):
    assert bluetooth.play("3", "50").returncode == 0
    assert azaan_file(3, volume=32768) in bluetooth.log()


def test_plays_a_sound_file(bluetooth):
    sound = os.path.join(ROOT, "media", "reminder.wav")
    assert bluetooth.play(sound, "100").returncode == 0
    assert "play --volume=65536 " + sound in bluetooth.log()


WAKE = "wake --raw --rate=48000 --channels=2 --format=s16le"


def test_wakes_the_output_with_silence_before_playing(bluetooth):
    """A Bluetooth speaker, or an output that was idle, cuts off or fades in the start of a sound played straight
    away, which loses most of the reminder chime."""
    assert bluetooth.play(os.path.join(ROOT, "media", "reminder.wav"), "100").returncode == 0
    assert bluetooth.wakes() == [WAKE]
    assert bluetooth.order() == ["wake", "play"]


def test_wakes_the_speakers_audio_output(bluetooth):
    bluetooth.sinks(LOCAL_SINK, SINK_1)
    assert bluetooth.play("3").returncode == 0
    assert bluetooth.wakes() == [WAKE + " --device=" + SINK_1]


def test_wakes_the_output_once_before_the_azaan_and_the_dua(bluetooth):
    assert bluetooth.play("--then", os.path.join(ROOT, "media", "reminder.wav"), "3").returncode == 0
    assert bluetooth.order() == ["wake", "play", "play"]


def test_doesnt_wake_the_output_when_no_speaker_connects(bluetooth):
    bluetooth.refuse(SPEAKER_1, SPEAKER_2)
    assert bluetooth.play().returncode == 2
    assert bluetooth.wakes() == []


def test_plays_on_chosen_speaker_only(bluetooth):
    bluetooth.connected(SPEAKER_1)
    assert bluetooth.play("3", "100", SPEAKER_2).returncode == 0
    assert bluetooth.log() == ["connect " + SPEAKER_2, azaan_file(3), "disconnect " + SPEAKER_2]


def test_chosen_speaker_that_is_connected_is_used(bluetooth):
    bluetooth.connected(SPEAKER_2)
    assert bluetooth.play("3", "100", SPEAKER_2.lower()).returncode == 0
    assert bluetooth.log() == [azaan_file(3), "disconnect " + SPEAKER_2]


def unpair(bluetooth, mac):
    """bluetoothctl no longer knows the speaker, as when it was reset or paired with another device."""
    make_command(bluetooth.bin_dir, "bluetoothctl", """\
        if [ "$1" = info ] && [ "$2" = {mac} ]; then echo "Device $2 not available" >&2; exit 1; fi
        if [ "$1" = info ]; then printf '\\tPaired: yes\\n\\tConnected: no\\n'; exit; fi
        if [ "$1" = devices ]; then exit; fi
        read -r cmd mac
        echo "$cmd $mac" >> {state}/log
        """.format(mac=mac, state=bluetooth.state))


def test_says_when_the_chosen_speaker_isnt_paired(bluetooth):
    unpair(bluetooth, SPEAKER_2)
    result = bluetooth.play("3", "100", SPEAKER_2)
    assert result.returncode == 2
    assert result.stdout.strip() == "Can't connect to bluetooth speaker {}, which isn't paired (pair it again with " \
                                    "python3 piprayer.py config)".format(SPEAKER_2)
    assert bluetooth.log() == []  # not asked to connect


def test_says_when_the_speaker_to_connect_ahead_isnt_paired(bluetooth):
    unpair(bluetooth, SPEAKER_2)
    result = bluetooth.run("--connect", SPEAKER_2)
    assert result.returncode == 2
    assert "which isn't paired" in result.stdout


def test_fallback_says_the_chosen_speaker_isnt_paired(bluetooth):
    unpair(bluetooth, SPEAKER_2)
    result = bluetooth.play("--fallback", "3", "100", SPEAKER_2)
    assert result.returncode == 0
    assert "which isn't paired (pair it again with python3 piprayer.py config), playing on the local audio output " \
           "instead" in result.stdout


def test_a_paired_speaker_that_refuses_isnt_called_unpaired(bluetooth):
    bluetooth.refuse(SPEAKER_2)
    assert "isn't paired" not in bluetooth.play("3", "100", SPEAKER_2).stdout


def test_fails_when_chosen_speaker_refuses(bluetooth):
    bluetooth.refuse(SPEAKER_2)
    result = bluetooth.play("3", "100", SPEAKER_2)
    assert result.returncode == 2
    assert "Can't connect to bluetooth speaker " + SPEAKER_2 in result.stdout
    assert bluetooth.log() == ["connect " + SPEAKER_2]


LOCAL_SINK = "alsa_output.platform-bcm2835_audio.stereo-fallback"
SINK_1 = "bluez_output.AA_AA_AA_AA_AA_01.1"


def test_plays_on_the_speakers_audio_output(bluetooth):
    bluetooth.sinks(LOCAL_SINK, SINK_1)
    result = bluetooth.play("3")
    assert result.returncode == 0, result.stdout
    assert bluetooth.log() == [
        "connect " + SPEAKER_1,
        "play --volume=65536 --device={} {}".format(SINK_1, os.path.join(ROOT, "media", "azaan-3.opus")),
        "disconnect " + SPEAKER_1]
    assert result.stdout.strip().endswith("Played on speaker " + SPEAKER_1)


def test_fallback_plays_on_the_local_output(bluetooth):
    bluetooth.sinks(LOCAL_SINK, SINK_1)
    bluetooth.refuse(SPEAKER_1, SPEAKER_2)
    result = bluetooth.play("--fallback", "3")
    assert result.returncode == 0
    assert "Can't connect to bluetooth, playing on the local audio output instead" in result.stdout
    assert result.stdout.strip().endswith("Played on the local audio output")
    assert "play --volume=65536 --device={} {}".format(LOCAL_SINK, os.path.join(ROOT, "media", "azaan-3.opus")) \
        in bluetooth.log()
    assert not any(line.startswith("disconnect") for line in bluetooth.log())


def test_fallback_isnt_used_when_a_speaker_connects(bluetooth):
    bluetooth.sinks(LOCAL_SINK, SINK_1)
    result = bluetooth.play("--fallback", "3")
    assert result.stdout.strip().endswith("Played on speaker " + SPEAKER_1)


def test_reports_when_the_sound_doesnt_play(bluetooth):
    bluetooth.paplay_fails()
    result = bluetooth.play("3")
    assert result.returncode == 3
    assert "paplay failed with exit code 1" in result.stdout
    assert bluetooth.log()[-1] == "disconnect " + SPEAKER_1  # still disconnected


def test_connect_ahead_prints_the_speaker(bluetooth):
    result = bluetooth.run("--connect")
    assert (result.returncode, result.stdout.strip()) == (0, SPEAKER_1)
    assert bluetooth.log() == ["connect " + SPEAKER_1]


def test_connect_ahead_to_chosen_speaker(bluetooth):
    result = bluetooth.run("--connect", SPEAKER_2.lower())
    assert (result.returncode, result.stdout.strip()) == (0, SPEAKER_2)


def test_connect_ahead_fails_when_no_speaker_connects(bluetooth):
    bluetooth.refuse(SPEAKER_1, SPEAKER_2)
    result = bluetooth.run("--connect")
    assert result.returncode == 2
    assert "Can't connect to bluetooth" in result.stdout


def test_connected_speaker_plays_without_connecting_again(bluetooth):
    bluetooth.run("--connect")
    bluetooth.play("3")
    assert bluetooth.log() == ["connect " + SPEAKER_1, azaan_file(3), "disconnect " + SPEAKER_1]


def test_stopping_stops_playing_and_disconnects(bluetooth):
    bluetooth.paplay_blocks()
    process = bluetooth.start("3")
    bluetooth.wait_until_playing()
    os.killpg(process.pid, signal.SIGTERM)  # as piprayer.py stop does
    output, _ = process.communicate(timeout=10)

    assert process.returncode == 4
    assert output.strip().endswith("Stopped")
    assert bluetooth.log() == ["connect " + SPEAKER_1, azaan_file(3), "disconnect " + SPEAKER_1]


def speaker_drops_while_playing(bluetooth, sound="azaan"):
    """paplay carries on after the speaker disconnects partway through the given sound, as it does when PipeWire
    moves its stream elsewhere."""
    make_command(bluetooth.bin_dir, "paplay", """\
        if [ "$1" = --raw ]; then cat > /dev/null; exit 0; fi
        echo "play $*" >> {state}/log
        case "$*" in *{sound}*) rm -f {state}/connected ;; esac
        for ((i = 0; i < 100; i++)); do /bin/sleep 0.05; done
        """.format(state=bluetooth.state, sound=sound))


def test_stops_and_fails_when_the_speaker_disconnects_while_playing(bluetooth):
    speaker_drops_while_playing(bluetooth)
    result = bluetooth.play("3")

    assert result.returncode == 3  # PiPrayer tries again
    assert result.stdout.strip().endswith("Stopped playing, as speaker {} disconnected".format(SPEAKER_1))


def test_doesnt_play_then_when_the_speaker_disconnects_during_the_azaan(bluetooth):
    speaker_drops_while_playing(bluetooth)
    dua = os.path.join(ROOT, "media", "reminder.wav")
    result = bluetooth.play("--then", dua, "3")

    assert result.returncode == 3
    assert not any(dua in line for line in bluetooth.log())


def test_speaker_disconnecting_during_then_still_counts_as_played(bluetooth):
    speaker_drops_while_playing(bluetooth, sound="reminder.wav")
    result = bluetooth.play("--then", os.path.join(ROOT, "media", "reminder.wav"), "3")

    assert result.returncode == 0
    assert "reminder.wav didn't play to the end: speaker {} disconnected".format(SPEAKER_1) in result.stdout


def test_stops_when_the_sound_is_moved_off_the_speaker(bluetooth):
    """The speaker reconnected quickly, but PipeWire had already moved the stream to another output."""
    sink = "bluez_output.{}.1".format(SPEAKER_1.replace(":", "_"))
    make_command(bluetooth.bin_dir, "paplay", """\
        if [ "$1" = --raw ]; then cat > /dev/null; exit 0; fi
        echo "play $*" >> {state}/log
        echo $$ > {state}/paplay
        for ((i = 0; i < 100; i++)); do /bin/sleep 0.05; done
        """.format(state=bluetooth.state))
    make_command(bluetooth.bin_dir, "pactl", """\
        case "$*" in
          "list short sinks")
            printf '1\\talsa_output.platform-hdmi\\tPipeWire\\n'
            printf '2\\t{sink}\\tPipeWire\\n' ;;
          "list sink-inputs")
            [ -f {state}/paplay ] || exit 0
            printf 'Sink Input #7\\n\\t\\tapplication.process.id = "%s"\\n' "$(cat {state}/paplay)" ;;
          "list short sink-inputs")
            printf '7\\t1\\t3\\tPipeWire\\n' ;;  # on the HDMI output
        esac
        """.format(sink=sink, state=bluetooth.state))
    result = bluetooth.play("3")

    assert result.returncode == 3
    assert result.stdout.strip().endswith(
        "Stopped playing, as the sound was moved off speaker {} to another output".format(SPEAKER_1))


def test_stops_when_the_speakers_audio_output_goes(bluetooth):
    """Bluetooth still says connected, but the speaker's audio output is gone."""
    bluetooth.sinks(SINK_1)
    make_command(bluetooth.bin_dir, "paplay", """\
        if [ "$1" = --raw ]; then cat > /dev/null; exit 0; fi
        echo "play $*" >> {state}/log
        touch {state}/playing
        for ((i = 0; i < 100; i++)); do /bin/sleep 0.05; done
        """.format(state=bluetooth.state))
    make_command(bluetooth.bin_dir, "pactl", """\
        [ "$*" = "list short sinks" ] || exit 0
        [ -f {state}/playing ] || printf '1\\t{sink}\\tPipeWire\\n'
        """.format(sink=SINK_1, state=bluetooth.state))
    result = bluetooth.play("3")

    assert result.returncode == 3
    assert result.stdout.strip().endswith(
        "Stopped playing, as the audio output of speaker {} went away".format(SPEAKER_1))


def test_one_failed_check_doesnt_stop_it(bluetooth):
    """bluetoothctl says not connected once, e.g. as it was slow to answer, then connected again."""
    make_command(bluetooth.bin_dir, "paplay", """\
        if [ "$1" = --raw ]; then cat > /dev/null; exit 0; fi
        echo "play $*" >> {state}/log
        touch {state}/playing
        /bin/sleep 0.5
        """.format(state=bluetooth.state))
    make_command(bluetooth.bin_dir, "bluetoothctl", """\
        if [ "$1" = devices ]; then echo "Device {mac} Kitchen Speaker"; exit; fi
        if [ "$1" = info ]; then
          echo "	Paired: yes"
          # not connected on the first check while playing, connected otherwise
          if [ -f {state}/playing ] && [ ! -f {state}/blipped ]; then
            touch {state}/blipped; echo "	Connected: no"
          else
            echo "	Connected: yes"
          fi
          exit
        fi
        read -r cmd mac
        echo "$cmd $mac" >> {state}/log
        """.format(mac=SPEAKER_1, state=bluetooth.state))
    result = bluetooth.play("3")

    assert (bluetooth.state / "blipped").exists()
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip().endswith("Played on speaker " + SPEAKER_1)


def test_keeps_playing_while_the_speaker_stays_connected(bluetooth):
    bluetooth.sinks("bluez_output.{}.1".format(SPEAKER_1.replace(":", "_")))
    make_command(bluetooth.bin_dir, "paplay", """\
        if [ "$1" = --raw ]; then cat > /dev/null; exit 0; fi
        echo "play $*" >> {state}/log
        /bin/sleep 0.3
        """.format(state=bluetooth.state))
    result = bluetooth.play("3")

    assert result.returncode == 0
    assert result.stdout.strip().endswith("Played on speaker " + SPEAKER_1)


def test_then_plays_another_sound_in_the_same_connection(bluetooth):
    dua = os.path.join(ROOT, "media", "reminder.wav")
    result = bluetooth.play("--then", dua, "3", "50")
    assert result.returncode == 0, result.stdout
    assert bluetooth.log() == ["connect " + SPEAKER_1, azaan_file(3, 32768), "play --volume=32768 " + dua,
                               "disconnect " + SPEAKER_1]
    assert result.stdout.strip().endswith("Played on speaker " + SPEAKER_1)


def test_then_isnt_played_when_the_azaan_doesnt_play(bluetooth):
    bluetooth.paplay_fails()
    result = bluetooth.play("--then", os.path.join(ROOT, "media", "reminder.wav"), "3")
    assert result.returncode == 3
    assert bluetooth.log() == ["connect " + SPEAKER_1, azaan_file(3), "disconnect " + SPEAKER_1]


def test_then_failing_still_counts_as_played(bluetooth):
    make_command(bluetooth.bin_dir, "paplay", """\
        echo "play $*" >> {}/log
        case "$*" in *reminder.wav) exit 1 ;; esac
        """.format(bluetooth.state))
    result = bluetooth.play("--then", os.path.join(ROOT, "media", "reminder.wav"), "3")
    assert result.returncode == 0, result.stdout
    assert "reminder.wav didn't play: paplay failed with exit code 1" in result.stdout
    assert bluetooth.log()[-1] == "disconnect " + SPEAKER_1


def test_stopping_the_azaan_doesnt_play_then(bluetooth):
    bluetooth.paplay_blocks()
    process = bluetooth.start("--then", os.path.join(ROOT, "media", "reminder.wav"), "3")
    bluetooth.wait_until_playing()
    os.killpg(process.pid, signal.SIGTERM)
    process.communicate(timeout=10)
    assert process.returncode == 4
    assert bluetooth.log() == ["connect " + SPEAKER_1, azaan_file(3), "disconnect " + SPEAKER_1]


def test_fades_in_to_the_volume(bluetooth):
    bluetooth.fading(steps=8)
    result = bluetooth.play("--fade-in", "2", "3", "50")
    assert result.returncode == 0, result.stdout
    sound = os.path.join(ROOT, "media", "azaan-3.opus")
    # starts silent, then 4 steps a second up to 50% (32768)
    assert bluetooth.log() == ["connect " + SPEAKER_1, "play --volume=0 " + sound] + \
        ["volume 7 {}".format(32768 * i // 8) for i in range(1, 9)] + ["disconnect " + SPEAKER_1]


def test_fade_in_with_fallback_in_any_order(bluetooth):
    bluetooth.fading(steps=4)
    bluetooth.refuse(SPEAKER_1, SPEAKER_2)
    result = bluetooth.play("--fade-in", "1", "--fallback", "3")
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip().endswith("Played on the local audio output")
    assert bluetooth.log()[-1] == "volume 7 65536"


def test_plays_at_full_volume_when_the_stream_cant_be_found(bluetooth):
    bluetooth.fading(steps=0, stream_found=False)
    result = bluetooth.play("--fade-in", "5", "3")
    assert result.returncode == 0
    assert "Can't find the stream to fade in, playing without fading in" in result.stdout
    sound = os.path.join(ROOT, "media", "azaan-3.opus")
    assert bluetooth.log() == ["connect " + SPEAKER_1, "play --volume=0 " + sound, "play --volume=65536 " + sound,
                               "disconnect " + SPEAKER_1]


def test_disconnect(bluetooth):
    assert bluetooth.run("--disconnect", SPEAKER_2).returncode == 0
    assert bluetooth.log() == ["disconnect " + SPEAKER_2]
    assert bluetooth.run("--disconnect").returncode == 1


@pytest.mark.parametrize("args, message", [
    (["3", "100", "kitchen"], "Invalid speaker: kitchen"),
    (["99"], "Sound file not found"),
    (["/no/such/file.wav"], "Sound file not found"),
    (["3", "150"], "Invalid volume: 150"),
    (["3", "loud"], "Invalid volume: loud"),
    (["--fade-in", "31", "3"], "Invalid fade-in: 31"),
    (["--fade-in", "soft", "3"], "Invalid fade-in: soft"),
    (["--louder", "3"], "Unknown option: --louder"),
    (["--then", "/no/such/dua.opus", "3"], "Sound file not found: /no/such/dua.opus"),
])
def test_rejects_bad_arguments_before_connecting(bluetooth, args, message):
    result = bluetooth.play(*args)
    assert result.returncode == 1
    assert message in result.stdout
    assert bluetooth.log() == []
