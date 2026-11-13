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

- 🕌 **Accurate prayer times** from your coordinates, with 22 calculation methods and Standard or Hanafi Asr
- ⏱️ **Per-prayer offsets** in minutes, to match your local mosque's timetable
- 🔔 **Optional reminder chime** a few minutes before each Azaan
- 🌍 **Daylight saving handled automatically** from your timezone
- 🔊 **17 Azaan recordings** to choose from, including 4 Fajr azaans to play at Fajr, and per-prayer volume
- 🔁 **Runs as a background service** that starts at boot and picks up configuration changes on its own
- 📶 **Connects to your Bluetooth speaker** just before the Azaan and disconnects afterwards
- ⏸️ **Skip the next Azaan or mute for a few days**, from the command line, an optional status page or Home Assistant

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
./setup-piprayer.sh        # or ./setup-piprayer.sh 5 to play azaan 5 (choose 1-17)
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
isn't installed yet.

**Updating**

```shell
git pull
```

The running service notices the new version within a minute and restarts itself. If the
[changelog](CHANGELOG.md) says an update changes how the service is installed, also run `./setup-piprayer.sh`.
Options in `.piprayer` that
PiPrayer doesn't recognise, e.g. a typo like `status_port`, are logged and shown by `piprayer.py times`, with the
option you probably meant.

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
| `azaan` | Azaan recording to play, from 1 to 17 | 1, or the number given to `setup-piprayer.sh` | `5` |
| `fajr-azaan` | A different recording for Fajr, e.g. one of the [Fajr azaans](#azaan-recordings) | same as `azaan` | `14` |
| `volume` | Volume in percent. Add `Prayer:percent` to set a prayer's volume | `100` | `80, Fajr:40` |
| `reminder` | Play a short chime this many minutes before each Azaan, up to 60. `0` turns it off | `0` | `10` |
| `speaker` | Bluetooth address of the speaker to play on. Other paired devices are not used | any paired speaker | `AA:BB:CC:DD:EE:FF` |
| `status-port` | Serve the [status page](#status-page) on this port | off | `8080` |
| `mqtt-host` | MQTT broker for [Home Assistant](#home-assistant), e.g. the Mosquitto add-on | off | `homeassistant.local` |
| `mqtt-port` | Port of the MQTT broker | `1883` | `1883` |
| `mqtt-username`, `mqtt-password` | Login for the MQTT broker | none | `piprayer` |
| `mqtt-topic` | Start of PiPrayer's MQTT topics | `piprayer` | `home/azaan` |
| `high-latitude` | How to set Fajr and Isha when summer nights are too short for their angles, see [below](#short-summer-nights) | `NightMiddle` | `AngleBased` |

For example, to play azaan 5, a quieter azaan 14 for Fajr, and a chime 10 minutes before each prayer:

```ini
azaan = 5
fajr-azaan = 14
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
| `Gulf` | Gulf Region | 19.5° | 90 min after Maghrib |
| `Kuwait` | Kuwait | 18° | 17.5° |
| `Qatar` | Qatar | 18° | 90 min after Maghrib |
| `Dubai` | Dubai | 18.2° | 18.2° |
| `Singapore` | Majlis Ugama Islam Singapura | 20° | 18° |
| `Malaysia` | Jabatan Kemajuan Islam Malaysia (JAKIM) | 20° | 18° |
| `Indonesia` | Kementerian Agama Republik Indonesia (Kemenag) | 20° | 18° |
| `Turkey` | Diyanet İşleri Başkanlığı | 18° | 17° |
| `France` | Union des Organisations Islamiques de France | 12° | 12° |
| `Russia` | Spiritual Administration of Muslims of Russia | 16° | 15° |
| `Morocco` | Morocco | 19° | 17° |
| `Algeria` | Algeria | 18° | 17° |
| `Tunisia` | Tunisia | 18° | 18° |
| `Jordan` | Ministry of Awqaf, Jordan | 18° | 18° |
| `Portugal` | Comunidade Islâmica de Lisboa | 18° | 77 min after Maghrib |

The methods match those of [AlAdhan](https://aladhan.com/calculation-methods), including the minutes some of them
add as a precaution: Turkey adds 5 to Dhuhr, 4 to Asr and 7 to Maghrib, Dubai 3 to Dhuhr and Maghrib, Morocco 5 to
Dhuhr and Maghrib, Portugal 5 to Dhuhr and 3 to Maghrib, and Jordan 5 to Maghrib. Offsets in `prayers` add to these.
The configuration wizard lists the first seven and suggests the usual method for your timezone. Enter `m`
to list them all, or a method's name or number.

### Short summer nights

From about 46° north or south, summer nights can be too short for the sun to reach the Fajr and Isha angles,
e.g. 18° for MWL. `high-latitude` sets them another way on those nights:

| `high-latitude` | Fajr and Isha |
|---|---|
| `NightMiddle` | At the middle of the night, between sunset and sunrise. Both can be at the same minute |
| `AngleBased` | A part of the night set by the angle, e.g. 18/60 of the night before sunrise for 18° |
| `OneSeventh` | One seventh of the night before sunrise and after sunset |
| `None` | Not played on nights the sun doesn't reach the angle |

In London on 21 June, MWL gives Isha and Fajr at 01:02 with `NightMiddle`, Isha at 23:27 and Fajr at 02:31 with
`AngleBased`, and Isha at 22:25 and Fajr at 03:40 with `OneSeventh`. Check which your mosque uses. The configuration
wizard asks from 45° north or south. An Isha after midnight plays then, and the times show it as after midnight.

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
| `python3 piprayer.py skip` | Don't play the next Azaan or its reminder. Run it again to skip the one after too |
| `python3 piprayer.py mute` | Don't play any Azaan until the end of today. `-d 3` mutes today and the next 2 days, `-u 2026-12-25` until the end of a date, up to 30 days |
| `python3 piprayer.py resume` | Clear skip and mute, so every Azaan plays again |
| `systemctl --user status piprayer` | Check the service is running, and when the next Azaan is |
| `journalctl --user-unit piprayer -f` | Follow the logs |
| `./setup-piprayer.sh [1-17]` | Set the default Azaan, and show the status page's address. The `azaan` setting takes precedence |
| `./setup-piprayer.sh --configure [1-17]` | Ask for each setting, save `.piprayer`, then install |
| `./setup-piprayer.sh --uninstall` | Remove PiPrayer |

### Azaan recordings

The Azaan recordings and the reminder chime are in the [`media`](media) folder. The recordings are Opus files,
normalised to the same loudness (-16 LUFS), so they play at a similar level at the same volume.

Azaans **14 to 17 are Fajr azaans**: they include *as-salatu khayrun min an-nawm* ("prayer is better than
sleep"), so use them as the `fajr-azaan` only. Azaans 16 and 17 are recorded at the Malmö Mosque and in the medina
of Marrakesh, and are used under Creative Commons licences, see [media/CREDITS.md](media/CREDITS.md).

## Status page

Turn on the status page to see the times and skip or mute from a phone or computer, without logging in to the
Raspberry Pi. `python3 piprayer.py config` asks for a port, or add one to `.piprayer`:

```ini
status-port = 8080
```

Within a minute, the page is at `http://raspberrypi.local:8080` (use your Raspberry Pi's hostname or address). It
shows the next Azaan with a countdown and today's times, with buttons to skip the next Azaan, mute today, or resume.

**On your phone**, add it to the home screen to use it like an app, full screen with its own icon:
- iPhone: open the page in Safari, tap **Share**, then **Add to Home Screen**
- Android: open the page in Chrome, tap **⋮**, then **Add to Home screen** or **Install app**

It stays up to date by itself, and reloads when you open it again.

The page has no password: anyone on your network can see the times, and skip or mute the Azaan. Don't make the
port reachable from the internet.

It also answers JSON requests, e.g. for Home Assistant:

| Request | Result |
|---|---|
| `GET /api/next` | The next Azaan, the same as `piprayer.py times next -f json` |
| `GET /api/times` | Today's times, the same as `piprayer.py times -f json` |
| `POST /api/skip` | Skip the next Azaan that would play, and return it |
| `POST /api/mute?days=1` | Mute until the end of today, or for up to 30 days counting today |
| `POST /api/resume` | Clear skip and mute |

The JSON for each Azaan has `skipped` and `muted` set to `true` when it won't play, and `muted_until` gives the
time the mute ends.

## Home Assistant

**With MQTT**, PiPrayer appears in Home Assistant as a device, with no YAML to write:

| Entity | Shows or does |
|---|---|
| Next azaan | When the next Azaan is, e.g. "in 2 hours", with the prayer and whether it is skipped |
| Next prayer | e.g. Asr |
| Muted | On while muted, with the time the mute ends |
| Skip next azaan, Mute today, Resume | Buttons |

To set it up with the Mosquitto broker add-on:

1. On the Raspberry Pi, install the MQTT library: `sudo apt install python3-paho-mqtt`
2. In Home Assistant, create a user for PiPrayer under **Settings → People → Users**, and set up the
   **MQTT** integration if it isn't already.
3. Run `python3 piprayer.py config` and enter the broker (e.g. `homeassistant.local`), port `1883`, and that user's
   name and password. Or add them to `.piprayer`:

   ```ini
   mqtt-host = homeassistant.local
   mqtt-username = piprayer
   mqtt-password = your password
   ```

Within a minute, **PiPrayer** appears under **Settings → Devices & services → MQTT**. It shows as unavailable
while the Raspberry Pi or the service is off. The password is saved in `.piprayer`, which the configuration wizard
makes readable only by you. Skip and mute from Home Assistant, the status page and the command line all apply
to the same Azaans.

Automations can also publish `skip`, `mute`, `mute 3` (days, up to 30) or `resume` to `piprayer/command`. The state
is published to `piprayer/state` as JSON. MQTT is off unless `mqtt-host` is set, so PiPrayer doesn't need
`paho-mqtt` otherwise.

**With the status page**, add a sensor for the next Azaan and commands to skip, mute and resume to
`configuration.yaml`:

```yaml
rest:
  - resource: http://raspberrypi.local:8080/api/next
    scan_interval: 60
    sensor:
      - name: Next Azaan
        device_class: timestamp
        value_template: "{{ value_json.time }}"
        json_attributes: [prayer, azaan, volume, reminder, skipped, muted, muted_until]

rest_command:
  azaan_skip:
    url: http://raspberrypi.local:8080/api/skip
    method: post
  azaan_mute_today:
    url: http://raspberrypi.local:8080/api/mute?days=1
    method: post
  azaan_resume:
    url: http://raspberrypi.local:8080/api/resume
    method: post
```

Call `rest_command.azaan_skip` from a button or an automation, e.g. to skip the Azaan when nobody is home.

<details>
<summary><b>Without the status page</b>, over SSH</summary>

Home Assistant can run PiPrayer's commands over SSH instead. Create a key without a passphrase in the Home Assistant
terminal, and allow it to log in to the Raspberry Pi (replace `pi@raspberrypi.local` with your user and hostname):

```shell
ssh-keygen -t ed25519 -N "" -f /config/.ssh/id_ed25519
ssh-copy-id -i /config/.ssh/id_ed25519 pi@raspberrypi.local
```

Then add to `configuration.yaml`, with the path to `piprayer.py` on the Raspberry Pi:

```yaml
command_line:
  - sensor:
      name: Next Azaan
      command: >-
        ssh -i /config/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new pi@raspberrypi.local
        python3 piprayer/piprayer.py times next -f json
      scan_interval: 300
      device_class: timestamp
      value_template: "{{ value_json.time }}"
      json_attributes: [prayer, azaan, volume, reminder, skipped, muted, muted_until]

shell_command:
  azaan_skip: ssh -i /config/.ssh/id_ed25519 pi@raspberrypi.local python3 piprayer/piprayer.py skip
  azaan_mute_today: ssh -i /config/.ssh/id_ed25519 pi@raspberrypi.local python3 piprayer/piprayer.py mute
  azaan_resume: ssh -i /config/.ssh/id_ed25519 pi@raspberrypi.local python3 piprayer/piprayer.py resume
```
</details>

## How it works

```text
 .piprayer ──► piprayer.py run ──► sleeps until the next prayer time
                     │
                     ▼
              play-azaan.sh ──► connects the speaker ──► plays media/azaan-N.opus ──► disconnects
```

`setup-piprayer.sh` installs `piprayer.py run` as a systemd user service and turns on lingering, so it starts at boot.
While waiting for the next prayer, the service checks `.piprayer` at least once a minute and reloads it when it
changes, so new settings apply without a restart. Skip and mute are saved in `.piprayer-state`, which the service
reads just before each Azaan, so they apply straight away and survive a restart.
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

The repository history was rewritten to remove old audio files, which brought a clone down from about 430 MB to
about 30 MB. `git pull` can't update a clone made before that, so clone again and copy your settings across.
Run this in the folder that contains `piprayer`:

```shell
mv piprayer piprayer.old
git clone https://github.com/kamranzafar/piprayer.git
cp piprayer.old/.piprayer piprayer/
cd piprayer && ./setup-piprayer.sh
```

Once the Azaan plays as before, remove the old copy with `rm -rf ../piprayer.old`.

The recordings are renumbered so that the Fajr azaans come last. If `.piprayer` sets `azaan` or `fajr-azaan`,
change the numbers to keep the same recordings, or run `python3 piprayer.py config`:

| Before | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Now | 1 | 2 | **14** | 3 | 4 | 5 | 6 | 7 | 8 | **15** | 9 | 10 | 11 | 12 | 13 |

Also run `./setup-piprayer.sh` with the new number if you installed with one, e.g. `./setup-piprayer.sh 4`
instead of `./setup-piprayer.sh 5`.

When upgrading from a cron based version (before 0.7), the setup also removes the old cron jobs and installs the
service.
</details>

## Development

The code is in the [`pipray`](pipray) package, one module per concern: `config`, `schedule`, `state`, `output`,
`player`, `web`, `mqtt`, `service`, `bluetooth`, `wizard` and `cli`. Each module depends only on the ones before
it, and `piprayer.py` just runs `pipray.cli`. Everything reads the time through `pipray.clock`, so the tests can
replace it.

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
