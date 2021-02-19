# PiPrayer
Prayer times for Raspberry Pi.<p>
**Note: This project is still in progress. Will be released soon.**

## Overview
PiPrayer project is for setting up and playing Azaan on Raspberry Pi (any version), using a bluetooth/usb Speaker.
The Azaan automatically plays at the calculated times depending on the coordinates provided. And also constantly updates the Azaan times as they change.
Tested on **Raspberry Pi Zero** (Raspbian LITE) using **Echo Dot** as a bluetooth speaker.

## Installation
Run the following commands to install prerequisites and clone project source.

```shell
sudo apt install sox
pip install configparser

git clone https://github.com/kamranzafar/piprayer.git
```

## Configuration
Create a `.piprayer` configuration file in the `piprayer` project directory. Below is a sample configuration file.

```editorconfig
[Default]
lat = -37.909499
lng = 144.751876
dst = 1
gmt-offset = 10
method = MWL
asr-time = Hanafi
prayers = Fajr, Dhuhr, Asr, Maghrib, Isha
```

### Configuration options

```
lat         # Latitude of the place

lng         # Longitude of the place

dst         # Daylight Saving
            # Acceptable values (only one):
            #   0
            #   1

gmt-offset  # Timezone, offet from GMT

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
            # Acceptable values (comma separated): 
            #   Fajr, Dhuhr, Asr, Maghrib, Isha
```