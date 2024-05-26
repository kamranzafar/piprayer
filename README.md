# PiPrayer
Azaan player for Raspberry Pi.

## Overview
PiPrayer project is for setting up and playing Azaan on Raspberry Pi (any version), using a bluetooth speaker.
The Azaan automatically plays at the calculated times depending on the coordinates provided. 
PiPrayer also constantly updates the Azaan times as they change.
Tested on **Raspberry Pi 3** using **Amazon Echo Pop** as a bluetooth speaker.

## Setup
Run the following commands to install prerequisites and clone project source.

```shell
sudo apt install pulseaudio-module-bluetooth 
sudo apt install python python-pip
pip install configparser

pulseaudio --start

git clone https://github.com/kamranzafar/piprayer.git
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

## PiPrayer Configuration and Installation
Create a `.piprayer` configuration file in the `piprayer` project directory. Below is a sample configuration. 
The description of each configuration option is given at the end.

```editorconfig
[Default]
lat = -37.909499
lng = 144.751876
dst = 1
gmt-offset = 10
method = MWL
asr-time = Hanafi
prayers = Fajr:-11, Dhuhr:1, Asr:0, Maghrib:-23, Isha:19
```
You can get the coordinates **(latitude and longitude)** of the place from Google Maps, see the following link for more information.

https://support.google.com/maps/answer/18539?co=GENIE.Platform%3DDesktop&hl=en

### Installation
Once the `.piprayer` configuration file is created, simply run the following script to setup and install PiPrayer.

```shell
./setup-piprayer.sh
```

This script also takes an optional integer argument to play a different Azaan. 
There are 15 different Azaans available in this project, so the script argument can be any number 
between 1 and 15.  By default `azaan-1` is played, which can be changed by running the script as below.

```shell
./setup-piprayer.sh 5
```

All Azaan files can be found in the `media` folder.

### Configuration options
Below are the available configuration options.
```text
lat         # Latitude of the place

lng         # Longitude of the place

dst         # Daylight Saving
            # Acceptable values (only one):
            #   0
            #   1

gmt-offset  # Timezone, offset from GMT

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
