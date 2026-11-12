# Changelog

All notable changes to PiPrayer are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Upgrading from 0.8
- `.piprayer` is no longer part of the repository, so `git pull` doesn't touch your settings. To upgrade, move it
  aside while pulling, otherwise git refuses to pull, or deletes the file if you never changed it:

  ```shell
  mv .piprayer .piprayer.bak && git pull && mv .piprayer.bak .piprayer && ./setup-piprayer.sh
  ```

### Added
- `piprayer.py config` creates or changes `.piprayer` by asking for each setting. It suggests the system
  timezone, that timezone's main city as the location, and the usual calculation method for the region, checks
  each answer, and shows the Azaan times before saving. Run it again to change the settings: the running service
  picks them up within a minute, and the wizard says so, or that the service still needs installing.
- `./setup-piprayer.sh --configure` runs the configuration wizard before installing. The setup also runs it when
  there is no `.piprayer`.
- `speaker` option to play on one Bluetooth speaker, by its address, instead of any paired speaker. The
  configuration wizard lists the paired speakers to choose from, and can find and pair a new speaker.

### Changed
- `play-azaan.sh` takes an optional speaker address after the volume.
- `.piprayer` is ignored by git instead of shipping with example settings for Melbourne. The first
  `./setup-piprayer.sh` creates it by asking for each setting.

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

[Unreleased]: https://github.com/kamranzafar/piprayer/compare/v0.8...HEAD
[0.8]: https://github.com/kamranzafar/piprayer/compare/v0.7...v0.8
[0.7]: https://github.com/kamranzafar/piprayer/compare/v0.6...v0.7
[0.6]: https://github.com/kamranzafar/piprayer/compare/v0.5...v0.6
[0.5]: https://github.com/kamranzafar/piprayer/compare/v0.4...v0.5
[0.4]: https://github.com/kamranzafar/piprayer/compare/v0.3...v0.4
[0.3]: https://github.com/kamranzafar/piprayer/compare/v0.2...v0.3
[0.2]: https://github.com/kamranzafar/piprayer/compare/v0.1...v0.2
[0.1]: https://github.com/kamranzafar/piprayer/releases/tag/v0.1
