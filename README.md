# PiPrayer
Azaan player for Raspberry Pi.<p>
**Note: This project is still in progress. Will be released soon.**

## Overview
PiPrayer project is for setting up and playing Azaan on Raspberry Pi (any version), using a bluetooth/usb Speaker.
The Azaan automatically plays at the calculated times depending on the coordinates provided. And also constantly updates the Azaan times as they change.
Tested on **Raspberry Pi Zero** (Raspbian LITE) using **Amazon Echo Dot** as a bluetooth speaker.

## Setup
Run the following commands to install prerequisites and clone project source.

```shell
sudo apt install bluealsa
sudo apt install python python-pip
pip install configparser

git clone https://github.com/kamranzafar/piprayer.git
```

The above setup assumes that the Raspberry PI device can already connect to the bluetooth speakers.

## Configuration and Installation
Create a `.piprayer` configuration file in the `piprayer` project directory. Below is a sample configuration.

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

### Installation
Once the configuration file is created, simply run the following script to setup and install PiPrayer.

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

## Configuring Default Bluetooth Device
Create `.asoundrc` file in the home directory, with the following device configuration

```shell
defaults.bluealsa.interface "hci0"
defaults.bluealsa.device "00:00:00:00:00:00" # MAC address of the bluetooth device
defaults.bluealsa.profile "a2dp"
defaults.bluealsa.delay 10000
```