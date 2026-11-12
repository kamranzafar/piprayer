<p align="center">
  <img src="assets/header.svg" alt="PiPrayer: Azaan at every prayer time, on your Raspberry Pi" width="608">
</p>

<p align="center">
  <a href="https://github.com/kamranzafar/piprayer/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/kamranzafar/piprayer/actions/workflows/tests.yml/badge.svg?branch=main"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white">
  <img alt="Raspberry Pi" src="https://img.shields.io/badge/Raspberry%20Pi-any-C51A4A?logo=raspberrypi&logoColor=white">
  <img alt="systemd service" src="https://img.shields.io/badge/runs%20as-systemd%20service-30D475">
  <a href="LICENSE"><img alt="License GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-blue"></a>
</p>

PiPrayer turns a Raspberry Pi and a Bluetooth speaker into an Azaan (Adhan) player. Tell it where you
are, and it calculates each day's prayer times and plays the Azaan when they arrive.

- 🕌 **Accurate prayer times** from your coordinates, with 7 calculation methods and Standard or Hanafi Asr
- ⏱️ **Per-prayer offsets** in minutes, to match your local mosque's timetable
- 🔔 **Optional reminder chime** a few minutes before each Azaan
- 🌍 **Daylight saving handled automatically** from your timezone
- 🔊 **15 Azaan recordings** to choose from, with a separate one for Fajr if you like, and per-prayer volume
- 🔁 **Runs as a background service** that starts at boot and picks up configuration changes on its own
- 📶 **Connects to your Bluetooth speaker** just before the Azaan and disconnects afterwards

Tested on a **Raspberry Pi 3** with an **Amazon Echo Pop** speaker.

## Quick start

**1. Install the prerequisites and get PiPrayer**

```shell
sudo apt install python3 bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils
git clone https://github.com/kamranzafar/piprayer.git
cd piprayer
```

Or download the [latest release](https://github.com/kamranzafar/piprayer/releases/latest). See the [changelog](CHANGELOG.md) for what's new.

Raspberry Pi OS Bookworm and later use [PipeWire](https://pipewire.org) for audio. The desktop image already
includes it, the command above installs it on Raspberry Pi OS Lite.

<details>
<summary>Bullseye, which uses PulseAudio</summary>

```shell
sudo apt install python3 bluez pulseaudio pulseaudio-module-bluetooth
```

Buster and earlier can't play the Opus recordings, which need libsndfile 1.0.29 or later.
</details>

**2. Pair your Bluetooth speaker**

The setup in the next step can find and pair the speaker for you. To pair it yourself:

```shell
bluetoothctl scan on                     # find your speaker's MAC address (XX:XX:XX:XX:XX:XX)
bluetoothctl pair XX:XX:XX:XX:XX:XX
bluetoothctl trust XX:XX:XX:XX:XX:XX
```

If more than one speaker is paired, PiPrayer uses one that is already connected, otherwise the first one that connects.
To always use one speaker, choose it during the setup or set the `speaker` option.

**3. Install PiPrayer**

```shell
./setup-piprayer.sh        # or ./setup-piprayer.sh 5 to play azaan 5 (choose 1-15)
```

The first time, the setup asks for your settings and saves them to `.piprayer`. It lists the paired speakers to
choose from, and can look for a new one and pair it. It suggests a default for each:
the timezone comes from the system, the location from that timezone's main city, and the calculation method from
the region. Enter your own coordinates for accurate times, e.g. from
[Google Maps](https://support.google.com/maps/answer/18539). Before saving, it shows the Azaan times:

```console
 Thursday 12 November 2026
 Australia/Melbourne (AEDT) · MWL method · Hanafi Asr

 Prayer   Time   Azaan  Volume
 ─────────────────────────────
 Fajr     04:21  1      100%
 Dhuhr    13:05  1      100%
 Asr      18:03  1      100%    ◀ next
 Maghrib  20:07  1      100%
 Isha     21:44  1      100%
```

That's it. PiPrayer now runs in the background and starts at boot. The setup may ask for your sudo password
once, so the service can start without anyone logging in.

**Changing the settings**

Run the wizard again. It starts from your current settings, so press Enter to keep each one:

```shell
python3 piprayer.py config
```

Or edit `.piprayer` directly. Either way, the running service picks up the new settings within a minute, with no
restart or setup needed. The wizard says so when it saves, or tells you to run `./setup-piprayer.sh` if the service
isn't installed yet. Run the setup again only to apply an update after `git pull`.

<details>
<summary>Writing <code>.piprayer</code> by hand</summary>

Create a `.piprayer` file in the `piprayer` folder before running the setup:

```ini
[Default]
lat = -37.909499
lng = 144.751876
timezone = Australia/Melbourne
method = MWL
asr-time = Hanafi
prayers = Fajr, Dhuhr, Asr, Maghrib, Isha
```

Get your timezone from the [tz database list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones),
and check the result with `python3 piprayer.py times`. All the options are described below.
</details>

## Configuration

All options go in the `[Default]` section of `.piprayer`. `python3 piprayer.py config` sets them for you, or edit
the file directly. The running service picks up changes within a minute, no restart needed.

| Option | Description | Example |
|---|---|---|
| `lat` | Latitude of the place | `-37.909499` |
| `lng` | Longitude of the place | `144.751876` |
| `timezone` | [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) of the place. Daylight saving is applied automatically | `Australia/Melbourne` |
| `method` | Calculation method, see below | `MWL` |
| `asr-time` | Asr calculation: `Standard` or `Hanafi` | `Hanafi` |
| `prayers` | Comma separated prayers to play the Azaan for. Add `:minutes` to shift a time earlier (negative) or later | `Fajr:-10, Dhuhr:5, Asr, Maghrib, Isha` |

### Optional settings

| Option | Description | Default | Example |
|---|---|---|---|
| `azaan` | Azaan recording to play, from 1 to 15 | 1, or the number given to `setup-piprayer.sh` | `5` |
| `fajr-azaan` | A different recording for Fajr | same as `azaan` | `3` |
| `volume` | Volume in percent. Add `Prayer:percent` to set a prayer's volume | `100` | `80, Fajr:40` |
| `reminder` | Play a short chime this many minutes before each Azaan, up to 60. `0` turns it off | `0` | `10` |
| `speaker` | Bluetooth address of the speaker to play on. Other paired devices are not used | any paired speaker | `AA:BB:CC:DD:EE:FF` |

For example, to play azaan 5, a quieter azaan 3 for Fajr, and a chime 10 minutes before each prayer:

```ini
azaan = 5
fajr-azaan = 3
volume = 80, Fajr:40
reminder = 10
```

The volume is that of the Raspberry Pi's audio output, so the speaker's own volume still applies.

### Calculation methods

| `method` | Organisation | Fajr | Isha |
|---|---|---|---|
| `MWL` | Muslim World League | 18° | 17° |
| `ISNA` | Islamic Society of North America | 15° | 15° |
| `Egypt` | Egyptian General Authority of Survey | 19.5° | 17.5° |
| `Makkah` | Umm Al-Qura University, Makkah | 18.5° | 90 min after Maghrib |
| `Karachi` | University of Islamic Sciences, Karachi | 18° | 18° |
| `Tehran` | Institute of Geophysics, University of Tehran | 17.7° | 14° |
| `Jafari` | Shia Ithna-Ashari, Leva Institute, Qum | 16° | 14° |

<details>
<summary>Legacy options: <code>gmt-offset</code> and <code>dst</code></summary>

Older configurations set a fixed offset from GMT and a manual daylight saving flag instead of `timezone`.
These still work, but `dst` has to be changed by hand twice a year. They are ignored when `timezone` is set.

```ini
gmt-offset = 10    # offset from GMT, in hours
dst = 1            # 1 during daylight saving, otherwise 0
```
</details>

## Usage

| Command | What it does |
|---|---|
| `python3 piprayer.py times` | Print today's Azaan times, with the azaan, volume and reminder for each |
| `python3 piprayer.py times next` | Show the next Azaan and how long until it |
| `python3 piprayer.py times -d 2026-12-25` | Print the times for another day |
| `python3 piprayer.py times -f json` | Print the times as JSON, e.g. for a dashboard or Home Assistant. Also works with `next`. `-f plain` prints `fajr-04:21` lines |
| `python3 piprayer.py config` | Create or change `.piprayer`, asking for each setting. The running service uses the new settings within a minute |
| `python3 piprayer.py play` | Play the Azaan now, to test the speaker |
| `python3 piprayer.py play -p fajr` | Play the Azaan and volume set for a prayer |
| `python3 piprayer.py play -r` | Play the reminder chime |
| `systemctl --user status piprayer` | Check the service is running, and when the next Azaan is |
| `journalctl --user-unit piprayer -f` | Follow the logs |
| `./setup-piprayer.sh [1-15]` | Set the default Azaan, or apply an update after `git pull`. The `azaan` setting takes precedence |
| `./setup-piprayer.sh --configure [1-15]` | Ask for each setting, save `.piprayer`, then install |
| `./setup-piprayer.sh --uninstall` | Remove PiPrayer |

The Azaan recordings and the reminder chime are in the [`media`](media) folder. The recordings are Opus files,
normalised to the same loudness (-16 LUFS), so they play at a similar level at the same volume.

## How it works

```text
 .piprayer ──► piprayer.py run ──► sleeps until the next prayer time
                     │
                     ▼
              play-azaan.sh ──► connects the speaker ──► plays media/azaan-N.opus ──► disconnects
```

`setup-piprayer.sh` installs `piprayer.py run` as a systemd user service and turns on lingering, so it starts at boot.
While waiting for the next prayer, the service checks `.piprayer` at least once a minute and reloads it when it
changes, so new settings apply without a restart.
Prayer times are calculated with the [PrayTimes.org](http://praytimes.org) library, included as `praytimes.py`.

If the clock jumps forward after boot (the Raspberry Pi has no hardware clock), any Azaan missed by more than
5 minutes is skipped rather than played late.

## Troubleshooting

<details>
<summary><b>No sound unless someone is logged in</b></summary>

WirePlumber only enables Bluetooth audio for a logged in user by default. On a headless Raspberry Pi, turn that check off.

For WirePlumber 0.5 and later (check with `wireplumber --version`), create
`~/.config/wireplumber/wireplumber.conf.d/80-disable-seat-monitoring.conf`:

```text
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
```

For WirePlumber 0.4, create `~/.config/wireplumber/bluetooth.lua.d/80-disable-logind.lua`:

```text
bluez_monitor.properties["with-logind"] = false
```

Then restart the audio services:

```shell
systemctl --user restart wireplumber pipewire pipewire-pulse
```
</details>

<details>
<summary><b>"Can't connect to bluetooth" in the logs</b></summary>

Make sure the speaker is switched on, paired and trusted (`bluetoothctl info XX:XX:XX:XX:XX:XX` should show
`Paired: yes` and `Trusted: yes`), then test with `python3 piprayer.py play`.
</details>

<details>
<summary><b>Upgrading from version 0.8 or earlier</b></summary>

`.piprayer` used to be part of the repository, so move it aside while pulling. Otherwise git refuses to pull, or
deletes the file if you never changed it:

```shell
mv .piprayer .piprayer.bak && git pull && mv .piprayer.bak .piprayer && ./setup-piprayer.sh
```

When upgrading from a cron based version (before 0.7), the setup also removes the old cron jobs and installs the
service.
</details>

## Development

The tests cover:
- prayer times, compared with reference times from the [AlAdhan API](https://aladhan.com/prayer-times-api)
- the service loop, run against a simulated clock
- both shell scripts, with fake `bluetoothctl` and `systemctl` commands

```shell
python3 -m pip install pytest
python3 -m pytest
```

They run on GitHub Actions for every push, along with [ShellCheck](https://www.shellcheck.net) for the shell scripts.
Notable changes are recorded in the [changelog](CHANGELOG.md).

## License

PiPrayer is released under the [GNU GPL v3](LICENSE) or any later version.
`praytimes.py` is from [PrayTimes.org](http://praytimes.org) and is licensed under the [GNU LGPL v3](COPYING.LESSER).
