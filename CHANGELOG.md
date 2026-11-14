# Changelog

All notable changes to PiPrayer are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Today's Hijri date, calculated with the Umm al-Qura calendar, in `piprayer.py times`, its JSON as `hijri`, on
  the status page, and in Home Assistant as a "Hijri date" sensor. From Maghrib it shows the next date, e.g.
  "Evening of 1 Ramadan 1448". `hijri-adjust = -1` (from -2 to 2) moves it by whole days to match a local calendar
  or moon sighting. It can be changed with the − and + buttons on the status page, `POST /api/hijri-adjust`, a
  "Hijri date adjustment" number in Home Assistant, the `hijri-adjust <days>` MQTT command, and
  `piprayer.py config`. These change only that line of `.piprayer`, keeping its comments
  ([#10](https://github.com/kamranzafar/piprayer/issues/10)).
- Optional `fade-in` option to start the Azaan quietly and raise it to its volume over a number of seconds, per
  prayer like `volume`, e.g. `fade-in = 5, Fajr:15`. Off unless set. `piprayer.py times` shows a Fade column when it
  is set, and the JSON has `fade_in` for each prayer. `play-azaan.sh` takes `--fade-in`
  ([#9](https://github.com/kamranzafar/piprayer/issues/9)).
- `piprayer.py stop` stops the Azaan or reminder that is playing and disconnects the speaker, and it isn't tried
  again. The status page has a Stop button while one plays, and `POST /api/stop`; Home Assistant has a Stop button,
  a `stop` command, and a "Playing" binary sensor. `/api/times` says what is `playing`
  ([#8](https://github.com/kamranzafar/piprayer/issues/8)).
- The speaker is connected 30 seconds before each Azaan and reminder, so they start on time, and play on the
  speaker's own audio output once it is ready, instead of waiting a fixed 5 seconds after connecting
  ([#6](https://github.com/kamranzafar/piprayer/issues/6)).
- When the speaker doesn't connect or the sound doesn't play, PiPrayer tries again every 30 seconds for up to 5
  minutes after the Azaan's time ([#5](https://github.com/kamranzafar/piprayer/issues/5)).
- `fallback-output = local` plays on the Raspberry Pi's own audio output, e.g. the headphone jack, when no speaker
  connects.
- How the last Azaan went (played, played locally, failed, missed, skipped or muted) is saved in `.piprayer-last`
  and shown by `piprayer.py times`, on the status page, in `/api/times` as `last`, and in Home Assistant as a
  "Last azaan" sensor. The status page warns when it didn't play.
- PiPrayer warns when the clock isn't set from the network, in the log, `piprayer.py times`, the status page,
  `/api/times` as `clock_synchronised`, and Home Assistant
  ([#7](https://github.com/kamranzafar/piprayer/issues/7)).
- A "Problem" binary sensor in Home Assistant, on when the last Azaan didn't play or the clock isn't set, for
  automations that send a notification.

### Changed
- The status page updates in place, with htmx, instead of reloading: its buttons, the refresh every 10 minutes
  (15 seconds while playing), and when the countdown reaches the Azaan. The scroll position is kept, a button can't
  be pressed twice while it is working, and the page says so when PiPrayer can't be reached or answers with an
  error. htmx is served from the Raspberry Pi, and the page still works without JavaScript
  ([#14](https://github.com/kamranzafar/piprayer/issues/14)).
- Every setting in `.piprayer` is checked, with a message saying what is wrong and what to use, e.g. for a latitude
  out of range, an unknown `method` or `asr-time` (which used to fall back to MWL and Standard without a word), a
  prayer listed twice, an offset of more than 180 minutes, or no prayers. `method` and `asr-time` can be in any
  case. Only Fajr, Dhuhr, Asr, Maghrib and Isha are accepted as prayers.
- A mistake that makes `.piprayer` unreadable, e.g. an option set twice or a line without `=`, is reported with its
  line instead of a Python error, and a `%` in a value, e.g. `volume = 80%`, is read as written.
- When `.piprayer` is changed with a mistake while the service runs, it logs the error and keeps using the previous
  settings until the file is fixed, instead of stopping, so the Azaan still plays. If it starts with a mistake, it
  waits for the file to be fixed instead of restarting every 30 seconds.
- `play-azaan.sh` exits with 2 when no speaker connects and 3 when the sound doesn't play, instead of 1, and takes
  `--fallback`, `--connect` and `--disconnect`. Stopped with SIGTERM, it stops playing, disconnects the speaker and
  exits with 4.
- Every source file now carries a copyright notice (2021-2026) and an `SPDX-License-Identifier: GPL-3.0-or-later` line;
  the bundled `praytimes.py` is marked `LGPL-3.0-only`.

## [0.9] - 2026-11-13

### Upgrading from 0.8
- The repository history was rewritten to remove old audio files, so `git pull` can't update an existing clone.
  Clone again and copy your settings across, from the folder that contains `piprayer`:

  ```shell
  mv piprayer piprayer.old
  git clone https://github.com/kamranzafar/piprayer.git
  cp piprayer.old/.piprayer piprayer/
  cd piprayer && ./setup-piprayer.sh
  ```

  Then remove `piprayer.old` once the Azaan plays as before.
- The Azaan recordings are renumbered so that the Fajr azaans come last, as 14 to 17. If `.piprayer` sets `azaan`
  or `fajr-azaan`, or you installed with `./setup-piprayer.sh <number>`, change the number to keep the same
  recording, or run `python3 piprayer.py config`:

  | Before | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | Now | 1 | 2 | **14** | 3 | 4 | 5 | 6 | 7 | 8 | **15** | 9 | 10 | 11 | 12 | 13 |
- From now on, `git pull` is enough to update: `.piprayer` is no longer part of the repository, so your settings
  aren't touched, and the service restarts itself within a minute to run the new version.

### Added
- `LICENSE` with the full GNU GPL v3 text, and `COPYING.LESSER` with the GNU LGPL v3 text for `praytimes.py`.
- `piprayer.py config` creates or changes `.piprayer` by asking for each setting. It suggests the system
  timezone, that timezone's main city as the location, and the usual calculation method for the region, checks
  each answer, and shows the Azaan times before saving. Run it again to change the settings: the running service
  picks them up within a minute, and the wizard says so, or that the service still needs installing.
- `./setup-piprayer.sh --configure` runs the configuration wizard before installing. The setup also runs it when
  there is no `.piprayer`.
- `speaker` option to play on one Bluetooth speaker, by its address, instead of any paired speaker. The
  configuration wizard lists the paired speakers to choose from, and can find and pair a new speaker.
- Azaans 16 and 17, Fajr azaans recorded at the Malmö Mosque and in the medina of Marrakesh, under CC BY 3.0.
  `media/CREDITS.md` credits the recordings.
- `piprayer.py skip` skips the next Azaan and its reminder, `piprayer.py mute` mutes until the end of today, or for
  up to 30 days with `--days` or `--until`, and `piprayer.py resume` clears both. They are saved in
  `.piprayer-state`, so they survive a restart, and `times` shows which Azaans won't play. The JSON output has
  `skipped`, `muted` and `muted_until` fields.
- Optional status page, turned on with `status-port = 8080`. It shows the next Azaan and today's times, with
  buttons to skip, mute and resume, and serves the times as JSON at `/api/next` and `/api/times`, with
  `POST /api/skip`, `/api/mute` and `/api/resume`. It uses only the Python standard library. The configuration
  wizard asks for the port and shows the page's address.
- 15 more calculation methods, matching AlAdhan's: `Gulf`, `Kuwait`, `Qatar`, `Dubai`, `Singapore`, `Malaysia`,
  `Indonesia`, `Turkey`, `France`, `Russia`, `Morocco`, `Algeria`, `Tunisia`, `Jordan` and `Portugal`, with the
  minutes some of them add to Dhuhr, Asr and Maghrib. The configuration wizard suggests them for their timezones,
  and lists the original seven and the suggested one, with the rest on request.
- `high-latitude` option to choose how Fajr and Isha are set when summer nights are too short for their angles:
  `NightMiddle` (the default, as before), `AngleBased`, `OneSeventh` or `None`. The configuration wizard asks for
  it from 45° north or south.
- Optional MQTT for Home Assistant, turned on with `mqtt-host`. PiPrayer appears as a device through MQTT
  discovery, with the next Azaan, the next prayer, whether it is muted, and buttons to skip, mute today and resume.
  It needs `paho-mqtt` (`sudo apt install python3-paho-mqtt`), version 1 or 2, only when MQTT is on. The
  configuration wizard asks for the broker and login, and keeps `.piprayer` readable only by its owner when it
  holds the password. When the broker can't be reached, the log says why: the name isn't found, the port is
  refused, or there is no answer.
- The service restarts itself within a minute of `git pull` updating PiPrayer, so updates apply without running
  `./setup-piprayer.sh`.
- Unknown options in `.piprayer`, e.g. `status_port` for `status-port`, are logged by the service and shown by
  `piprayer.py times` and the setup, with the option they were probably meant to be.
- `./setup-piprayer.sh` shows the status page's address when it is turned on.
- The status page is designed for phones and can be added to the home screen as an app, with its own icon. The
  countdown to the next Azaan ticks, and the page reloads when it is opened again, so it stays up to date.
- The README explains how to show the next Azaan in Home Assistant and skip or mute from it, with the status page
  or over SSH.

### Changed
- The Azaan recordings are renumbered so that the Fajr azaans are 14 to 17: the old 3 and 10 are now 14 and 15.
  The README and the configuration wizard say which azaans are Fajr azaans.
- The Azaan recordings are restored from the original MP3s instead of 8 kHz WAV files, so they sound fuller and
  take 27 MB instead of 79 MB. They are Opus files (`media/azaan-N.opus`), normalised to the same loudness
  (-16 LUFS) so they play at a similar level. Their numbers are unchanged, so `azaan` and `fajr-azaan` settings
  keep working. Recordings 1, 2, 9, 12 and 13 only had low quality sources and still need better ones.
  Playing Opus needs libsndfile 1.0.29 or later: Raspberry Pi OS Bullseye or later.
- Azaans 3, 4, 7, 12, 14 and 15 no longer end with about 7 seconds of spoken credits.
- `play-azaan.sh` takes an optional speaker address after the volume.
- The repository history no longer contains the old WAV and MP3 recordings, so a clone downloads about 30 MB
  instead of about 430 MB. Every commit and tag has a new hash. The original MP3s are kept as
  [`original-mp3s.zip`](https://github.com/kamranzafar/piprayer/releases/download/v0.1/original-mp3s.zip) on the
  v0.1 release.
- The code is split from one large `piprayer.py` into modules in the `pipray` package. `piprayer.py` is still the
  command to run, and nothing changes in how PiPrayer behaves.
- `.piprayer` is ignored by git instead of shipping with example settings for Melbourne. The first
  `./setup-piprayer.sh` creates it by asking for each setting.

### Fixed
- An Isha after midnight, e.g. in the UK in summer, was put on the morning of the same day, nearly a day early and
  before Fajr. It now plays after midnight, and the times say so.
- An Azaan due while another was playing was missed, e.g. Fajr at the same minute as Isha on short summer nights.
  It now plays straight afterwards, if that is within 5 minutes of its time.
- Times on the day daylight saving starts or ends use the UTC offset in force at each time, and places whose
  clocks are a day off from the sun, such as the Chatham Islands, get the right day's times.

## [0.8] - 2026-11-12

### Upgrading from 0.7
- Run `git pull` and then `./setup-piprayer.sh` to restart the service with the new version.
- The new settings are optional, so existing configurations work unchanged.

### Added
- `azaan` option to choose the Azaan recording in `.piprayer`. It takes precedence over the number given to
  `setup-piprayer.sh`, and changes are picked up without running the setup again.
- `fajr-azaan` option to play a different recording for Fajr.
- `volume` option, in percent, with optional per-prayer volumes, e.g. `volume = 80, Fajr:40`.
- `reminder` option to play a short chime (`media/reminder.wav`) a number of minutes before each Azaan.
- `piprayer.py times` prints a table with each prayer's azaan, volume and reminder, and marks the next azaan.
  `-d YYYY-MM-DD` shows another day.
- `piprayer.py times next` shows the next azaan and how long until it.
- `-f json` output for `times` and `times next`, with ISO 8601 times. `-f plain` prints the previous
  `fajr-04:21` format for scripts.
- `piprayer.py play` uses the configured azaan and volume. `-p fajr` plays what is set for a prayer,
  and `-r` plays the reminder chime.
- Automated tests, run by GitHub Actions on every push for Python 3.9, 3.11, 3.13 and 3.14. They cover:
  - prayer times, compared with the AlAdhan API for 7 methods, 3 locations and both Asr methods
  - configuration parsing
  - the service loop, using a simulated clock that includes daylight saving changes
  - `play-azaan.sh` and `setup-piprayer.sh`
- ShellCheck linting of the shell scripts in CI.

### Changed
- `play-azaan.sh` takes a sound file as well as an Azaan number, and an optional volume. It checks its
  arguments before connecting to the speaker.
- The README header is now a centered image (`assets/header.svg`).

### Fixed
- The `Makkah` method put Isha 90 minutes before Maghrib instead of after it. Maghrib set in minutes
  after sunset had the same problem.

## [0.7] - 2026-11-12

### Upgrading from 0.6
- Run `git pull` and then `./setup-piprayer.sh`. This replaces the old cron jobs with a systemd service.
- **Check the `method` in `.piprayer`.** Because of a bug (see Fixed), every version before 0.7 calculated
  prayer times with the `Jafari` method whatever the configuration said. If your offsets were tuned against
  those times, set `method = Jafari` to keep your Azaan times exactly as they were.
- Replace `gmt-offset` and `dst` with `timezone`, e.g. `timezone = Australia/Melbourne`, so daylight saving
  is handled automatically.

### Added
- `timezone` option that takes an IANA timezone name and applies daylight saving automatically.
- PiPrayer runs as a systemd user service that starts at boot, instead of rescheduling cron jobs every night.
- `piprayer.py` commands: `times` prints today's Azaan times, `play` plays the Azaan now to test the speaker,
  `run` runs the service. `-c` sets the configuration file.
- Configuration changes are picked up automatically, without restarting the service.
- An Azaan noticed more than 5 minutes late, e.g. after the clock jumps forward at boot, is skipped rather than played late.
- `./setup-piprayer.sh --uninstall` removes PiPrayer.
- Clear error messages for unknown prayer names, invalid offsets and missing configuration options.
- Support for more than one paired Bluetooth speaker: PiPrayer uses a connected one, otherwise the first that connects.
- `CHANGELOG.md`.

### Changed
- `setup-piprayer.sh` installs the systemd service and removes cron jobs left by older versions.
  `XDG_RUNTIME_DIR` no longer needs to be set.
- Prayer offsets in `prayers` are optional and default to 0 minutes, e.g. `prayers = Fajr, Dhuhr:5, Asr`.
- Requires Python 3 (3.9 or later for `timezone`).
- `play-azaan.sh` uses only `bluetoothctl`. The deprecated `hcitool` is no longer used.
- README rewritten, with PipeWire setup for Raspberry Pi OS Bookworm and later.

### Deprecated
- `gmt-offset` and `dst` options. They still work when `timezone` is not set.

### Fixed
- The `method` option was ignored and every configuration was calculated as `Jafari`.
- `PrayTimes.setMethod()` crashed, and `PrayTimes` instances shared settings and offsets.
- A missing configuration file raised a Python error instead of a clear message.
- `play-azaan.sh` was missing its shebang line.

## [0.6] - 2024-05-26

### Added
- Per-prayer time offsets in minutes, e.g. `prayers = Fajr:-11, Dhuhr:1`.

## [0.5] - 2024-03-06

### Changed
- Bluetooth audio uses PulseAudio instead of BlueALSA.

## [0.4] - 2023-04-25

### Changed
- The Bluetooth speaker is disconnected after the Azaan finishes.

## [0.3] - 2021-05-06

### Changed
- Updated when the setup reruns to refresh the daily schedule.

### Fixed
- Setup script path when run from another directory.

## [0.2] - 2021-02-24

### Fixed
- Error handling in the setup and configuration.

## [0.1] - 2021-02-20

### Added
- First release: plays the Azaan on a Bluetooth speaker at calculated prayer times, scheduled with cron.
- 15 Azaan recordings, selectable with an argument to the setup script.

[Unreleased]: https://github.com/kamranzafar/piprayer/compare/v0.9...HEAD
[0.9]: https://github.com/kamranzafar/piprayer/compare/v0.8...v0.9
[0.8]: https://github.com/kamranzafar/piprayer/compare/v0.7...v0.8
[0.7]: https://github.com/kamranzafar/piprayer/compare/v0.6...v0.7
[0.6]: https://github.com/kamranzafar/piprayer/compare/v0.5...v0.6
[0.5]: https://github.com/kamranzafar/piprayer/compare/v0.4...v0.5
[0.4]: https://github.com/kamranzafar/piprayer/compare/v0.3...v0.4
[0.3]: https://github.com/kamranzafar/piprayer/compare/v0.2...v0.3
[0.2]: https://github.com/kamranzafar/piprayer/compare/v0.1...v0.2
[0.1]: https://github.com/kamranzafar/piprayer/releases/tag/v0.1
