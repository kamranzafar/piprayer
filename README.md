```text
     .-'''-.    *      ____  _ ____
    /   .-'           / __ \(_) __ \_________ ___  _____  _____
   |   (       .     / /_/ / / /_/ / ___/ __ `/ / / / _ \/ ___/
   |   (            / ____/ / ____/ /  / /_/ / /_/ /  __/ /
    \   '-.    *   /_/   /_/_/   /_/   \__,_/\__, /\___/_/
     '-...-'                                /____/

          Azaan at every prayer time, on your Raspberry Pi
```

<p>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white">
  <img alt="Raspberry Pi" src="https://img.shields.io/badge/Raspberry%20Pi-any-C51A4A?logo=raspberrypi&logoColor=white">
  <img alt="systemd service" src="https://img.shields.io/badge/runs%20as-systemd%20service-30D475">
  <img alt="License GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-blue">
</p>

PiPrayer turns a Raspberry Pi and a Bluetooth speaker into an Azaan (Adhan) player. Tell it where you
are, and it calculates each day's prayer times and plays the Azaan when they arrive.

- 🕌 **Accurate prayer times** from your coordinates, with 7 calculation methods and Standard or Hanafi Asr
- ⏱️ **Per-prayer offsets** in minutes, to match your local mosque's timetable
- 🌍 **Daylight saving handled automatically** from your timezone
- 🔊 **15 Azaan recordings** to choose from
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

Raspberry Pi OS Bookworm and later use [PipeWire](https://pipewire.org) for audio. The desktop image already
includes it, the command above installs it on Raspberry Pi OS Lite.

<details>
<summary>Older releases (Bullseye and earlier) that use PulseAudio</summary>

```shell
sudo apt install python3 bluez pulseaudio pulseaudio-module-bluetooth
```
</details>

**2. Pair your Bluetooth speaker**

```shell
bluetoothctl scan on                     # find your speaker's MAC address (XX:XX:XX:XX:XX:XX)
bluetoothctl pair XX:XX:XX:XX:XX:XX
bluetoothctl trust XX:XX:XX:XX:XX:XX
```

If more than one speaker is paired, PiPrayer uses one that is already connected, otherwise the first one that connects.

**3. Create the configuration file**

Create a `.piprayer` file in the `piprayer` folder:

```ini
[Default]
lat = -37.909499
lng = 144.751876
timezone = Australia/Melbourne
method = MWL
asr-time = Hanafi
prayers = Fajr, Dhuhr, Asr, Maghrib, Isha
```

Get your coordinates from [Google Maps](https://support.google.com/maps/answer/18539) and your timezone from
the [tz database list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). Check the result with:

```console
$ python3 piprayer.py times
fajr-04:21
dhuhr-13:05
asr-18:03
maghrib-20:07
isha-21:44
```

**4. Install the service**

```shell
./setup-piprayer.sh        # or ./setup-piprayer.sh 5 to play azaan 5 (choose 1-15)
```

That's it. PiPrayer now runs in the background and starts at boot. The setup may ask for your sudo password
once, so the service can start without anyone logging in.

## Configuration

All options go in the `[Default]` section of `.piprayer`. Changes are picked up automatically, no restart needed.

| Option | Description | Example |
|---|---|---|
| `lat` | Latitude of the place | `-37.909499` |
| `lng` | Longitude of the place | `144.751876` |
| `timezone` | [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) of the place. Daylight saving is applied automatically | `Australia/Melbourne` |
| `method` | Calculation method, see below | `MWL` |
| `asr-time` | Asr calculation: `Standard` or `Hanafi` | `Hanafi` |
| `prayers` | Comma separated prayers to play the Azaan for. Add `:minutes` to shift a time earlier (negative) or later | `Fajr:-10, Dhuhr:5, Asr, Maghrib, Isha` |

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
| `python3 piprayer.py times` | Print today's Azaan times |
| `python3 piprayer.py play` | Play the Azaan now, to test the speaker |
| `systemctl --user status piprayer` | Check the service is running, and when the next Azaan is |
| `journalctl --user-unit piprayer -f` | Follow the logs |
| `./setup-piprayer.sh [1-15]` | Change the Azaan, or apply an update after `git pull` |
| `./setup-piprayer.sh --uninstall` | Remove PiPrayer |

The Azaan recordings are in the [`media`](media) folder.

## How it works

```text
 .piprayer ──► piprayer.py run ──► sleeps until the next prayer time
                     │
                     ▼
              play-azaan.sh ──► connects the speaker ──► plays media/azaan-N.wav ──► disconnects
```

`setup-piprayer.sh` installs `piprayer.py run` as a systemd user service and turns on lingering, so it starts at boot.
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
<summary><b>Upgrading from an older, cron based version</b></summary>

Run `git pull` and then `./setup-piprayer.sh`. The setup removes the old cron jobs and installs the service.
</details>

## License

PiPrayer is released under the [GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.html).
`praytimes.py` is from [PrayTimes.org](http://praytimes.org) and is licensed under the GNU LGPL v3.
