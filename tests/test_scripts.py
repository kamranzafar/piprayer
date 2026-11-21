# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""setup-piprayer.sh, run with fake system commands on the PATH."""
import os
import shutil
import subprocess

import pytest

from conftest import MELBOURNE_CONFIG, ROOT, make_command

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")



def run(cmd, bin_dir, home=None, cwd=None, stdin=None):
    env = dict(os.environ, PATH="{}:{}".format(bin_dir, os.environ["PATH"]))
    if home:
        env["HOME"] = str(home)
        env.pop("XDG_CONFIG_HOME", None)
    return subprocess.run(cmd, env=env, cwd=cwd, input=stdin or "", capture_output=True, text=True, timeout=30)


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
    for name in ("piprayer.py", "praytimes.py", "setup-piprayer.sh"):
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
    make_command(bin_dir, "hostname", "echo pi-prayer\n")
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
    assert "Type=notify" in unit
    assert "WatchdogSec=180" in unit
    assert "NotifyAccess=main" in unit

    assert setup.log() == [
        "systemctl --user daemon-reload",
        "systemctl --user enable piprayer.service",
        "systemctl --user restart piprayer.service",
        "sudo loginctl enable-linger " + os.environ.get("USER", ""),
    ]
    assert "Fajr     04:" in result.stdout
    assert "Azaan" in result.stdout


def test_setup_shows_the_status_page_address(setup):
    with open(setup.project / ".piprayer", "a") as f:
        f.write("status-port = 8080\n")
    result = setup.run()
    assert "Page:   http://pi-prayer.local:8080" in result.stdout


def test_setup_without_status_page_shows_no_address(setup):
    assert "Page:" not in setup.run().stdout


def test_setup_warns_about_unknown_options(setup):
    with open(setup.project / ".piprayer", "a") as f:
        f.write("status_port = 8080\n")
    result = setup.run()
    assert result.returncode == 0
    assert "Unknown option status_port in" in result.stderr
    assert "Did you mean status-port?" in result.stderr


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
    # the blank after ISNA keeps Standard Asr, the next one the rule for short summer nights
    answers = "Europe/London\n51.5\n-0.12\nISNA\n\n\n\n7\n\n\n\n\n\n\n\ny\n"
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
    result = setup.run("--configure", answers="\n" * 14 + "n\n")
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


# ------------------------------------------------------------- WirePlumber

WIREPLUMBER_05 = "wireplumber/wireplumber.conf.d/80-disable-seat-monitoring.conf"
WIREPLUMBER_04 = "wireplumber/bluetooth.lua.d/80-disable-logind.lua"
RESTART_AUDIO = "systemctl --user restart wireplumber pipewire pipewire-pulse"


def fake_wireplumber(setup, version):
    make_command(setup.bin_dir, "wireplumber", """\
        echo wireplumber
        echo "Compiled with libwireplumber {0}"
        echo "Linked with libwireplumber {0}"
        """.format(version))


def config_dir(setup):
    return setup.service_file.parents[2]


def test_setup_turns_off_wireplumber_seat_monitoring_for_05(setup):
    fake_wireplumber(setup, "0.5.8")
    result = setup.run()
    assert result.returncode == 0, result.stdout + result.stderr

    conf = (config_dir(setup) / WIREPLUMBER_05).read_text()
    assert "monitor.bluez.seat-monitoring = disabled" in conf
    assert conf.startswith("# Written by PiPrayer")
    assert not (config_dir(setup) / WIREPLUMBER_04).exists()
    assert RESTART_AUDIO in setup.log()
    assert "Bluetooth audio works without logging in" in result.stdout
    # before the service starts, so it finds the speaker
    assert setup.log().index(RESTART_AUDIO) < setup.log().index("systemctl --user restart piprayer.service")


def test_setup_turns_off_wireplumber_logind_for_04(setup):
    fake_wireplumber(setup, "0.4.13")
    assert setup.run().returncode == 0

    conf = (config_dir(setup) / WIREPLUMBER_04).read_text()
    assert 'bluez_monitor.properties["with-logind"] = false' in conf
    assert conf.startswith("-- Written by PiPrayer")
    assert not (config_dir(setup) / WIREPLUMBER_05).exists()
    assert RESTART_AUDIO in setup.log()


def test_setup_without_wireplumber_writes_nothing(setup):
    assert setup.run().returncode == 0
    assert not (config_dir(setup) / "wireplumber").exists()
    assert RESTART_AUDIO not in setup.log()


def test_setup_leaves_an_existing_wireplumber_file_alone(setup):
    fake_wireplumber(setup, "0.5.2")
    conf = config_dir(setup) / WIREPLUMBER_05
    conf.parent.mkdir(parents=True)
    conf.write_text("# mine\n")

    result = setup.run()
    assert result.returncode == 0
    assert conf.read_text() == "# mine\n"
    assert "Leaving {} as it is".format(conf) in result.stdout
    assert RESTART_AUDIO not in setup.log()

    # nor removes it on uninstall
    setup.run("--uninstall")
    assert conf.read_text() == "# mine\n"


def test_setup_writes_wireplumber_file_once(setup):
    fake_wireplumber(setup, "0.5.2")
    assert setup.run().returncode == 0
    assert setup.run().returncode == 0
    assert setup.log().count(RESTART_AUDIO) == 1


def test_uninstall_removes_the_wireplumber_file_it_wrote(setup):
    fake_wireplumber(setup, "0.5.2")
    assert setup.run().returncode == 0
    conf = config_dir(setup) / WIREPLUMBER_05
    assert conf.exists()

    result = setup.run("--uninstall")
    assert result.returncode == 0
    assert not conf.exists()
    assert "Removed {}".format(conf) in result.stdout


# ------------------------------------------------------------- install.sh

BOOKWORM = 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n'
BULLSEYE = 'PRETTY_NAME="Raspbian GNU/Linux 11 (bullseye)"\nID=raspbian\nID_LIKE=debian\nVERSION_ID="11"\n' \
           'VERSION_CODENAME=bullseye\n'
BUSTER = 'ID=raspbian\nID_LIKE=debian\nVERSION_ID="10"\nVERSION_CODENAME=buster\n'
TRIXIE_TESTING = 'PRETTY_NAME="Debian GNU/Linux trixie/sid"\nID=debian\nVERSION_CODENAME=trixie\n'
FEDORA = 'ID=fedora\nVERSION_ID=40\n'


@pytest.fixture
def install(tmp_path):
    """install.sh with fake sudo, apt-get and git. The fake git clone makes a folder with a fake setup."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    os_release = tmp_path / "os-release"
    os_release.write_text(BOOKWORM)

    make_command(bin_dir, "sudo", 'echo "sudo $*" >> {}\n"$@"\n'.format(log))
    make_command(bin_dir, "apt-get", 'echo "apt-get $*" >> {}\n'.format(log))
    make_command(bin_dir, "git", """\
        echo "git $*" >> {log}
        if [ "$1" = clone ]; then
          mkdir -p "$4"/.git
          printf '#!/bin/bash\\necho "setup ran in $(dirname "$0")"\\n' > "$4"/setup-piprayer.sh
          chmod +x "$4"/setup-piprayer.sh
        fi
        [ -z "$GIT_FAILS" ] || [ "$GIT_FAILS" != "$3" ]
        """.format(log=log))

    class Install:
        def run(self, **env):
            command_env = dict(os.environ, PATH="{}:{}".format(bin_dir, os.environ["PATH"]), HOME=str(home),
                               PIPRAYER_OS_RELEASE=str(os_release))
            for name in ("PIPRAYER_DIR", "PIPRAYER_VERSION", "GIT_FAILS"):
                command_env.pop(name, None)
            command_env.update(env)
            # as through curl | bash: the script is the standard input
            with open(os.path.join(ROOT, "install.sh")) as script:
                return subprocess.run(["bash"], stdin=script, env=command_env, capture_output=True, text=True,
                                      timeout=30)

        def log(self):
            return log.read_text().splitlines() if log.exists() else []

        def set_os(self, text):
            os_release.write_text(text)

    Install.home = home
    return Install()


def test_install_on_bookworm_installs_pipewire_clones_and_runs_the_setup(install):
    result = install.run()
    assert result.returncode == 0, result.stdout + result.stderr

    log = install.log()
    assert "sudo apt-get update -qq" in log
    assert ("sudo apt-get install -y -qq git python3 bluez pipewire pipewire-pulse wireplumber "
            "libspa-0.2-bluetooth pulseaudio-utils") in log
    assert "git clone --quiet https://github.com/kamranzafar/piprayer.git {}/piprayer".format(install.home) in log
    assert "with sudo" in result.stdout
    assert "setup ran in {}/piprayer".format(install.home) in result.stdout


def test_install_on_bullseye_uses_pulseaudio(install):
    install.set_os(BULLSEYE)
    result = install.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sudo apt-get install -y -qq git python3 bluez pulseaudio pulseaudio-module-bluetooth" in install.log()
    assert "PulseAudio" in result.stdout


def test_install_on_debian_testing_without_version_uses_pipewire(install):
    install.set_os(TRIXIE_TESTING)
    assert install.run().returncode == 0
    assert any("pipewire" in line for line in install.log())


@pytest.mark.parametrize("os_release, message", [
    (BUSTER, "needs Raspberry Pi OS Bullseye or later"),
    (FEDORA, "installs on Raspberry Pi OS or Debian"),
])
def test_install_refuses_unsupported_systems(install, os_release, message):
    install.set_os(os_release)
    result = install.run()
    assert result.returncode == 1
    assert message in result.stdout
    assert install.log() == []


def test_install_into_another_folder_at_a_release(install, tmp_path):
    folder = tmp_path / "azaan"
    result = install.run(PIPRAYER_DIR=str(folder), PIPRAYER_VERSION="v1.1")
    assert result.returncode == 0, result.stdout + result.stderr
    log = install.log()
    assert "git clone --quiet https://github.com/kamranzafar/piprayer.git {}".format(folder) in log
    assert "git -C {} checkout --quiet v1.1".format(folder) in log
    assert "setup ran in {}".format(folder) in result.stdout


def test_install_again_updates_instead_of_cloning(install):
    assert install.run().returncode == 0
    first = len(install.log())

    result = install.run()
    assert result.returncode == 0, result.stdout + result.stderr
    again = install.log()[first:]
    folder = install.home / "piprayer"
    assert not any(line.startswith("git clone") for line in again)
    assert "git -C {} fetch --quiet --tags origin".format(folder) in again
    assert "git -C {} merge --quiet --ff-only origin/main".format(folder) in again
    assert "Updating PiPrayer in {}".format(folder) in result.stdout
    assert "setup ran" in result.stdout


def test_install_stops_when_the_update_fails(install):
    assert install.run().returncode == 0
    result = install.run(GIT_FAILS="checkout")  # git -C <dir> checkout fails
    assert result.returncode == 1
    assert "has changes of its own" in result.stdout
    assert "setup ran" not in result.stdout


def test_install_leaves_another_folder_alone(install):
    folder = install.home / "piprayer"
    folder.mkdir()
    (folder / "notes.txt").write_text("mine")
    result = install.run()
    assert result.returncode == 1
    assert "isn't PiPrayer" in result.stdout
    assert not any(line.startswith("git") for line in install.log())


def test_install_stops_when_packages_fail(install):
    make_command(install.home.parent / "bin", "apt-get", "exit 100\n")
    result = install.run()
    assert result.returncode == 1
    assert "Couldn't install the packages" in result.stdout
    assert "setup ran" not in result.stdout


def test_partly_downloaded_install_does_nothing(install, tmp_path):
    with open(os.path.join(ROOT, "install.sh")) as f:
        script = f.read()
    partial = script[:script.rindex("main \"$@\"")]
    env = dict(os.environ, PATH="{}:{}".format(tmp_path / "bin", os.environ["PATH"]), HOME=str(install.home))
    result = subprocess.run(["bash"], input=partial, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert result.stdout == ""
    assert install.log() == []
