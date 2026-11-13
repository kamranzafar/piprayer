# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line: piprayer.py times, run, play, skip, mute, resume and config."""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta

from . import clock
from .config import DEFAULT_AZAAN, DEFAULT_CONFIG, DEFAULT_VOLUME, Config, ConfigError, azaan_file
from .output import (RESUME_HINT, describe_azaan, describe_mute, format_next, format_plain, format_times,
                     next_json, times_json)
from .player import REMINDER_SOUND, play
from .schedule import next_azaan
from .service import Updated, installed_azaan, run
from .state import MAX_MUTE_DAYS, State, mute, resume, skip_next, state_file
from .wizard import configure

PRAYERS = ["fajr", "dhuhr", "asr", "maghrib", "isha"]


# ------------------------------------------------------------------ commands

def times_command(args):
    config = Config(args.config, args.azaan or installed_azaan())
    for warning in config.warnings:
        print(warning, file=sys.stderr)
    now = clock.now(config.tz)
    state = State.load(state_file(args.config))

    if args.which == "next":
        if args.date:
            raise ConfigError("--date can't be used with next")
        entry = next_azaan(config, now, state)
        if args.format == "json":
            print(json.dumps(next_json(config, entry, now, state), indent=2))
        elif args.format == "plain":
            if entry:
                print(entry.prayer + "-" + entry.at.strftime("%H:%M"))
        else:
            print(format_next(config, entry, now, state))
        return 0

    day = args.date or now.date()
    if args.format == "json":
        print(json.dumps(times_json(config, day, now, state), indent=2))
    elif args.format == "plain":
        print(format_plain(config, day))
    else:
        print(format_times(config, day, now, state))
    return 0


def run_command(args):
    try:
        run(args.config, args.azaan)
    except Updated:
        # The status page and MQTT are stopped by now. Start again in this process, with the same arguments, so the
        # new code runs straight away without waiting for systemd
        logging.getLogger("piprayer").info("PiPrayer was updated, restarting")
        restart()
    return 0


def restart():
    os.execv(sys.executable, [sys.executable] + sys.argv)


def play_command(args):
    """Play what the service would play, using the configuration if there is one."""
    config = Config(args.config) if os.path.exists(args.config) else None
    volume = config.volume_for(args.prayer) if config else DEFAULT_VOLUME
    speaker = config.speaker if config else None
    if args.reminder:
        return play(REMINDER_SOUND, volume, "reminder", speaker)

    azaan = args.azaan or (config.azaan_for(args.prayer) if config else DEFAULT_AZAAN)
    return play(azaan, volume, speaker=speaker)


def skip_command(args):
    config = Config(args.config, installed_azaan())
    now = clock.now(config.tz)
    entry = skip_next(config, state_file(args.config), now)
    if entry is None:
        print("No azaan to skip")
        return 1
    print("Skipping the azaan for {}{}. Run piprayer.py resume to play it after all."
          .format(describe_azaan(entry, now), " and its reminder" if entry.reminder else ""))
    return 0


def mute_command(args):
    config = Config(args.config)
    now = clock.now(config.tz)
    today = now.date()
    if args.until:
        last_day = args.until
        if not today <= last_day <= today + timedelta(days=MAX_MUTE_DAYS - 1):
            raise ConfigError("--until must be from today to {} days ahead".format(MAX_MUTE_DAYS - 1))
    else:
        last_day = today + timedelta(days=args.days - 1)
    mute_until = mute(config, state_file(args.config), now, last_day)
    print(describe_mute(config, mute_until, now) + ". " + RESUME_HINT)
    return 0


def resume_command(args):
    config = Config(args.config, installed_azaan())
    now = clock.now(config.tz)
    resume(state_file(args.config))
    entry = next_azaan(config, now)
    print("Skip and mute cleared." + (" Next azaan: " + describe_azaan(entry, now) + "." if entry else ""))
    return 0


def config_command(args):
    return configure(args.config, next_step=args.next_step)


# ------------------------------------------------------------------ arguments

def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("use the format YYYY-MM-DD, e.g. 2026-11-12")


def mute_days(value):
    try:
        days = int(value)
    except ValueError:
        days = 0
    if not 1 <= days <= MAX_MUTE_DAYS:
        raise argparse.ArgumentTypeError("use a whole number from 1 to {}".format(MAX_MUTE_DAYS))
    return days


def azaan_number(value):
    azaan = int(value)
    if not os.path.isfile(azaan_file(azaan)):
        raise argparse.ArgumentTypeError("no azaan {}, choose from 1 to 17".format(value))
    return azaan


def add_times_arguments(command):
    """The times command's arguments."""
    command.add_argument("which", nargs="?", choices=["next"], help="show only the next azaan")
    command.add_argument("-d", "--date", type=parse_date, help="show another day, as YYYY-MM-DD")
    command.add_argument("-f", "--format", choices=["table", "json", "plain"], default="table",
                         help="output format: table (default), json, or plain prayer-HH:MM lines")
    command.add_argument("-a", "--azaan", type=azaan_number, metavar="1-17",
                         help="azaan to show when the configuration doesn't set one "
                              "(default: the one the service was set up with)")


def parser():
    result = argparse.ArgumentParser(description="PiPrayer - Azaan for Raspberry Pi")
    result.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                        help="configuration file (default: %(default)s)")
    commands = result.add_subparsers(dest="command")

    times = commands.add_parser("times", help="print today's azaan times (default)")
    add_times_arguments(times)
    times.set_defaults(func=times_command)

    run_parser = commands.add_parser("run", help="run as a service, playing the azaan at each prayer time")
    run_parser.add_argument("-a", "--azaan", type=azaan_number, default=DEFAULT_AZAAN, metavar="1-17",
                            help="azaan to play when the configuration doesn't set one (default: %(default)s)")
    run_parser.set_defaults(func=run_command)

    play_parser = commands.add_parser("play", help="play the azaan now, to test the speaker")
    play_parser.add_argument("-a", "--azaan", type=azaan_number, metavar="1-17",
                             help="azaan to play (default: the one in the configuration)")
    play_parser.add_argument("-p", "--prayer", choices=PRAYERS, type=str.lower,
                             help="play the azaan and volume set for this prayer")
    play_parser.add_argument("-r", "--reminder", action="store_true", help="play the reminder sound")
    play_parser.set_defaults(func=play_command)

    commands.add_parser("skip", help="don't play the next azaan or its reminder").set_defaults(func=skip_command)

    mute_parser = commands.add_parser("mute", help="don't play any azaan until the end of today, "
                                                   "or for a number of days")
    mute_for = mute_parser.add_mutually_exclusive_group()
    mute_for.add_argument("-d", "--days", type=mute_days, default=1, metavar="DAYS",
                          help="days to mute, counting today (default: 1, up to {})".format(MAX_MUTE_DAYS))
    mute_for.add_argument("-u", "--until", type=parse_date, metavar="YYYY-MM-DD", help="last day to mute")
    mute_parser.set_defaults(func=mute_command)

    commands.add_parser("resume", help="clear skip and mute, so every azaan plays again") \
        .set_defaults(func=resume_command)

    config_parser = commands.add_parser("config",
                                        help="create or change the configuration, asking for each setting")
    # setup-piprayer.sh installs the service next, so it doesn't need saying
    config_parser.add_argument("--no-next-step", dest="next_step", action="store_false", help=argparse.SUPPRESS)
    config_parser.set_defaults(func=config_command)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(argv)
    if args.command is None:
        # "piprayer.py" on its own is the same as "piprayer.py times"
        args = parser().parse_args(argv + ["times"])
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        return args.func(args)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if args.command == "config":
            print("\nNot saved", file=sys.stderr)
            return 1
        return 0
    except EOFError:
        print("\nNot saved, no answer to read", file=sys.stderr)
        return 1
