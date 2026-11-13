"""play-azaan.sh and setup-piprayer.sh, run with fake system commands on the PATH."""
import os
import shutil
import stat
import subprocess
import textwrap

import pytest

from conftest import MELBOURNE_CONFIG, ROOT

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

SPEAKER_1 = "AA:AA:AA:AA:AA:01"
SPEAKER_2 = "BB:BB:BB:BB:BB:02"


def make_command(directory, name, script):
    path = directory / name
    path.write_text("#!/bin/bash\n" + textwrap.dedent(script))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run(cmd, bin_dir, home=None, cwd=None, stdin=None):
    env = dict(os.environ, PATH="{}:{}".format(bin_dir, os.environ["PATH"]))
    if home:
        env["HOME"] = str(home)
        env.pop("XDG_CONFIG_HOME", None)
    return subprocess.run(cmd, env=env, cwd=cwd, input=stdin or "", capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------- play-azaan.sh

@pytest.fixture
def bluetooth(tmp_path):
    """Fake bluetoothctl with two paired speakers. Records commands in a log."""
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
    make_command(bin_dir, "paplay", 'echo "play $*" >> {}/log\n'.format(state))
    make_command(bin_dir, "sleep", "")

    class Bluetooth:
        def connected(self, *macs):
            (state / "connected").write_text("".join(m + "\n" for m in macs))

        def refuse(self, *macs):
            (state / "refuse").write_text("".join(m + "\n" for m in macs))

        def play(self, *args):
            return run([os.path.join(ROOT, "play-azaan.sh")] + list(args or ["3"]), bin_dir)

        def log(self):
            path = state / "log"
            return path.read_text().splitlines() if path.exists() else []

    return Bluetooth()


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
    assert result.returncode == 1
    assert "Can't connect to bluetooth" in result.stdout
    assert not any(line.startswith("play") for line in bluetooth.log())


def test_plays_at_requested_volume(bluetooth):
    assert bluetooth.play("3", "50").returncode == 0
    assert azaan_file(3, volume=32768) in bluetooth.log()


def test_plays_a_sound_file(bluetooth):
    sound = os.path.join(ROOT, "media", "reminder.wav")
    assert bluetooth.play(sound, "100").returncode == 0
    assert "play --volume=65536 " + sound in bluetooth.log()


def test_plays_on_chosen_speaker_only(bluetooth):
    bluetooth.connected(SPEAKER_1)
    assert bluetooth.play("3", "100", SPEAKER_2).returncode == 0
    assert bluetooth.log() == ["connect " + SPEAKER_2, azaan_file(3), "disconnect " + SPEAKER_2]


def test_chosen_speaker_that_is_connected_is_used(bluetooth):
    bluetooth.connected(SPEAKER_2)
    assert bluetooth.play("3", "100", SPEAKER_2.lower()).returncode == 0
    assert bluetooth.log() == [azaan_file(3), "disconnect " + SPEAKER_2]


def test_fails_when_chosen_speaker_refuses(bluetooth):
    bluetooth.refuse(SPEAKER_2)
    result = bluetooth.play("3", "100", SPEAKER_2)
    assert result.returncode == 1
    assert "Can't connect to bluetooth speaker " + SPEAKER_2 in result.stdout
    assert bluetooth.log() == ["connect " + SPEAKER_2]


@pytest.mark.parametrize("args, message", [
    (["3", "100", "kitchen"], "Invalid speaker: kitchen"),
    (["99"], "Sound file not found"),
    (["/no/such/file.wav"], "Sound file not found"),
    (["3", "150"], "Invalid volume: 150"),
    (["3", "loud"], "Invalid volume: loud"),
])
def test_rejects_bad_arguments_before_connecting(bluetooth, args, message):
    result = bluetooth.play(*args)
    assert result.returncode == 1
    assert message in result.stdout
    assert bluetooth.log() == []


# ------------------------------------------------------------- setup-piprayer.sh

OLD_CRONTAB = """\
0 3 * * * /usr/bin/backup.sh
## PI PRAYER ##
23 4 * * * export XDG_RUNTIME_DIR=/run/user/1000 && /home/pi/piprayer/play-azaan.sh 1 #fajr
0 1 * * * export XDG_RUNTIME_DIR=/run/user/1000 && /home/pi/piprayer/setup-piprayer.sh 1
## PI PRAYER ##

30 6 * * * /usr/bin/other.sh
"""


@pytest.fixture
def setup(tmp_path):
    """A copy of the project with fake systemctl, crontab, loginctl and sudo."""
    project = tmp_path / "piprayer"
    project.mkdir()
    for name in ("piprayer.py", "praytimes.py", "play-azaan.sh", "setup-piprayer.sh"):
        shutil.copy2(os.path.join(ROOT, name), project / name)
    shutil.copytree(os.path.join(ROOT, "pipray"), project / "pipray",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (project / "media").mkdir()
    for n in range(1, 18):
        (project / "media" / "azaan-{}.opus".format(n)).touch()
    (project / ".piprayer").write_text(MELBOURNE_CONFIG)

    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    crontab = tmp_path / "crontab"

    make_command(bin_dir, "systemctl", 'echo "systemctl $*" >> {}\n'.format(log))
    make_command(bin_dir, "sudo", 'echo "sudo $*" >> {}\n'.format(log))
    make_command(bin_dir, "loginctl", '[ "$1" = show-user ] && echo no\n')
    make_command(bin_dir, "bluetoothctl", "")
    make_command(bin_dir, "crontab", """\
        if [ "$1" = -l ]; then
          [ -f {crontab} ] || {{ echo "no crontab for user" >&2; exit 1; }}
          cat {crontab}
        else
          cat > {crontab}.new && mv {crontab}.new {crontab}
        fi
        """.format(crontab=crontab))

    class Setup:
        service_file = home / ".config" / "systemd" / "user" / "piprayer.service"

        def run(self, *args, answers=None):
            return run([str(project / "setup-piprayer.sh")] + list(args), bin_dir, home=home, stdin=answers)

        def log(self):
            return log.read_text().splitlines() if log.exists() else []

        def set_crontab(self, text):
            crontab.write_text(text)

        def crontab(self):
            return crontab.read_text() if crontab.exists() else None

    Setup.project = project
    Setup.bin_dir = bin_dir
    return Setup()


def test_setup_installs_and_starts_service(setup):
    result = setup.run("5")
    assert result.returncode == 0, result.stdout + result.stderr

    unit = setup.service_file.read_text()
    assert '"{}/piprayer.py" -c "{}/.piprayer" run --azaan 5'.format(setup.project, setup.project) in unit
    assert "WantedBy=default.target" in unit

    assert setup.log() == [
        "systemctl --user daemon-reload",
        "systemctl --user enable piprayer.service",
        "systemctl --user restart piprayer.service",
        "sudo loginctl enable-linger " + os.environ.get("USER", ""),
    ]
    assert "Fajr     04:" in result.stdout
    assert "Azaan" in result.stdout


def test_setup_removes_only_old_piprayer_cron_jobs(setup):
    setup.set_crontab(OLD_CRONTAB)
    result = setup.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Removed old PiPrayer cron jobs" in result.stdout
    assert setup.crontab() == "0 3 * * * /usr/bin/backup.sh\n\n30 6 * * * /usr/bin/other.sh\n"


def test_setup_leaves_crontab_without_piprayer_alone(setup):
    setup.set_crontab("0 3 * * * /usr/bin/backup.sh\n")
    assert setup.run().returncode == 0
    assert setup.crontab() == "0 3 * * * /usr/bin/backup.sh\n"


def test_setup_reports_service_failure(setup):
    setup.set_crontab(OLD_CRONTAB)
    make_command(setup.bin_dir, "systemctl", "exit 1\n")

    result = setup.run()
    assert result.returncode == 1
    assert "Failed to start piprayer.service" in result.stdout
    # the old cron jobs are kept, so the azaan still plays
    assert setup.crontab() == OLD_CRONTAB


def test_setup_works_without_a_crontab(setup):
    assert setup.run().returncode == 0
    assert setup.crontab() is None


@pytest.mark.parametrize("azaan", ["0", "18", "abc"])
def test_setup_rejects_unknown_azaan(setup, azaan):
    result = setup.run(azaan)
    assert result.returncode == 1
    assert "choose a number between 1 and 17" in result.stdout
    assert not setup.service_file.exists()


def test_setup_rejects_invalid_config(setup):
    (setup.project / ".piprayer").write_text(MELBOURNE_CONFIG.replace("Fajr:-11", "Fajir:-11"))
    result = setup.run()
    assert result.returncode == 1
    assert "Unknown prayer: Fajir" in result.stderr
    assert not setup.service_file.exists()


def test_setup_configure_asks_for_settings_then_installs(setup):
    answers = "Europe/London\n51.5\n-0.12\nISNA\n\n\n7\n\n\n\ny\n"
    result = setup.run("--configure", "5", answers=answers)
    assert result.returncode == 0, result.stdout + result.stderr

    config = (setup.project / ".piprayer").read_text()
    assert "timezone = Europe/London" in config
    assert "method = ISNA" in config
    assert "azaan = 7" in config
    assert "run --azaan 5" in setup.service_file.read_text()
    assert "PiPrayer installed." in result.stdout
    assert "Run ./setup-piprayer.sh" not in result.stdout


def test_setup_configure_stops_when_not_saved(setup):
    result = setup.run("--configure", answers="\n" * 10 + "n\n")
    assert result.returncode == 1
    assert "Not saved" in result.stdout
    assert (setup.project / ".piprayer").read_text() == MELBOURNE_CONFIG
    assert not setup.service_file.exists()


def test_setup_without_config_explains_how_to_create_it(setup):
    (setup.project / ".piprayer").unlink()
    result = setup.run()
    assert result.returncode == 1
    assert "create it with: ./setup-piprayer.sh --configure" in result.stdout
    assert not setup.service_file.exists()


def test_uninstall(setup):
    assert setup.run().returncode == 0
    setup.set_crontab(OLD_CRONTAB)

    result = setup.run("--uninstall")
    assert result.returncode == 0
    assert not setup.service_file.exists()
    assert "## PI PRAYER ##" not in setup.crontab()
    assert "systemctl --user disable --now piprayer.service" in setup.log()
