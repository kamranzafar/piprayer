# PiPrayer
Azaan/Adhan player for Raspberry Pi.

## Overview
PiPrayer project is for setting up and playing Azaan on Raspberry Pi (any version), using a bluetooth speaker.
The Azaan automatically plays at the calculated times depending on the coordinates provided. 
PiPrayer runs as a background service, works out each day's Azaan times and adjusts automatically for daylight saving.
Tested on **Raspberry Pi 3** using **Amazon Echo Pop** as a bluetooth speaker.

## Setup
Install the prerequisites and clone the project source. Raspberry Pi OS Bookworm and later use
[PipeWire](https://pipewire.org) for audio. The desktop image already includes it, these commands
install it on Raspberry Pi OS Lite.

```shell
sudo apt install python3 bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils

git clone https://github.com/kamranzafar/piprayer.git
```

On older releases (Bullseye and earlier) that use PulseAudio, install these instead.

```shell
sudo apt install python3 bluez pulseaudio pulseaudio-module-bluetooth
```

You could also download the latest piprayer release instead of cloning the repository.

```shell
wget -qO- https://github.com/kamranzafar/piprayer/archive/v0.6.tar.gz | tar -xzf -
```

### Configure Default Bluetooth Device
Scan and pair Raspberry Pi with the bluetooth speaker.

```shell
bluetoothctl scan on
```

All available bluetooth devices will be listed with their MAC addresses (_format is XX:XX:XX:XX:XX:XX_). Copy the MAC address of the preferred audio device from the list. 
Once copied, run the following command to pair and trust the bluetooth speaker.

```shell
bluetoothctl pair XX:XX:XX:XX:XX:XX
bluetoothctl trust XX:XX:XX:XX:XX:XX
```

If more than one speaker is paired, PiPrayer uses one that is already connected, otherwise the first paired speaker that connects.

## PiPrayer Configuration and Installation
Create a `.piprayer` configuration file in the `piprayer` project directory. Below is a sample configuration. 
The description of each configuration option is given at the end.

```editorconfig
[Default]
lat = -37.909499
lng = 144.751876
timezone = Australia/Melbourne
method = MWL
asr-time = Hanafi
prayers = Fajr:-11, Dhuhr:1, Asr:0, Maghrib:-23, Isha:19
```
You can get the coordinates **(latitude and longitude)** of the place from Google Maps, see the following link for more information.

https://support.google.com/maps/answer/18539?co=GENIE.Platform%3DDesktop&hl=en

To check the configuration, print today's Azaan times:

```shell
python3 piprayer.py times
```

### Installation
Once the `.piprayer` configuration file is created, simply run the following script to setup and install PiPrayer.

```shell
./setup-piprayer.sh
```

This installs PiPrayer as a systemd user service that starts at boot, and removes any cron jobs
left by older versions of PiPrayer. It may ask for your sudo password once, to let the service start at boot without logging in.

This script also takes an optional integer argument to play a different Azaan. 
There are 15 different Azaans available in this project, so the script argument can be any number 
between 1 and 15.  By default `azaan-1` is played, which can be changed by running the script as below.

```shell
./setup-piprayer.sh 5
```

All Azaan files can be found in the `media` folder.

Changes to `.piprayer` are picked up automatically. To change the Azaan or to update after pulling a new version
of PiPrayer, run `./setup-piprayer.sh` again. To remove PiPrayer, run `./setup-piprayer.sh --uninstall`.

### Managing the service

```shell
systemctl --user status piprayer      # is it running, and when is the next Azaan
journalctl --user-unit piprayer -f    # follow the logs
python3 piprayer.py play              # play the Azaan now, to test the speaker
```

### Troubleshooting: no sound on a headless Raspberry Pi
WirePlumber only enables bluetooth audio for a logged in user by default. If the Azaan plays fine while you
are logged in but not otherwise, turn that check off and restart the audio services.

For WirePlumber 0.5 and later (check with `wireplumber --version`), create `~/.config/wireplumber/wireplumber.conf.d/80-disable-seat-monitoring.conf`:

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

Then restart the audio services with `systemctl --user restart wireplumber pipewire pipewire-pulse`.

### Configuration options
Below are the available configuration options.
```text
lat         # Latitude of the place

lng         # Longitude of the place

timezone    # IANA timezone name of the place, e.g. Australia/Melbourne, Europe/London
            # Daylight saving is applied automatically (requires Python 3.9+)
            # Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

dst         # (Legacy, only used when timezone is not set) Daylight Saving
            # Acceptable values (only one):
            #   0
            #   1

gmt-offset  # (Legacy, only used when timezone is not set) Timezone, offset from GMT

method      # Calculation method 
            # Acceptable values (only one):
            #   MWL
            #   ISNA
            #   Egypt
            #   Makkah
            #   Karachi
            #   Tehran
            #   Jafari

asr-time    # Asr prayer calculation
            # Acceptable values (only one):
            #   Standard
            #   Hanafi

prayers     # Prayers to setup Azaan for
            # For each prayer, the time offset (in minutes) can be added after the colon (:)
            # By default all time offsets are set to 0 minutes
            # Acceptable values (comma separated) are below, with offsets separate by colon
            #   Fajr:0, Dhuhr:0, Asr:0, Maghrib:0, Isha:0
```
