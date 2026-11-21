#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

#    PiPrayer - Azaan for Raspberry Pi
#    Copyright (C) 2021-2026  Kamran Zafar
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

#    Installs PiPrayer with one command:
#
#      curl -fsSL https://raw.githubusercontent.com/kamranzafar/piprayer/main/install.sh | bash
#
#    It installs the packages PiPrayer needs, gets PiPrayer into ~/piprayer, or updates it if it is there, and runs
#    ./setup-piprayer.sh, which asks for the settings the first time. It is safe to run again.
#
#      PIPRAYER_DIR=~/azaan      install into another folder
#      PIPRAYER_VERSION=v1.1     install a release instead of the latest code
#
#    Everything is in main, called on the last line, so a partly downloaded script does nothing.

main() {
  local repo="https://github.com/kamranzafar/piprayer.git"
  local dir="${PIPRAYER_DIR:-$HOME/piprayer}"
  local version="${PIPRAYER_VERSION:-}"
  local os_release="${PIPRAYER_OS_RELEASE:-/etc/os-release}"
  local id="" id_like="" version_id="" codename="" packages

  if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as the user PiPrayer will run as, not as root or with sudo. It asks for sudo when it needs it."
    return 1
  fi

  # Which Debian release, from /etc/os-release
  if [ -r "$os_release" ]; then
    id=$(read_os_release "$os_release" ID)
    id_like=$(read_os_release "$os_release" ID_LIKE)
    version_id=$(read_os_release "$os_release" VERSION_ID)
    codename=$(read_os_release "$os_release" VERSION_CODENAME)
  fi
  case " $id $id_like " in
  *" debian "* | *" raspbian "*) ;;
  *)
    echo "PiPrayer installs on Raspberry Pi OS or Debian, this looks like ${id:-an unknown system}."
    echo "See https://github.com/kamranzafar/piprayer#quick-start to install it step by step."
    return 1
    ;;
  esac
  if [ "$id" != debian ] && [ "$id" != raspbian ]; then
    version_id=""  # e.g. Ubuntu's version numbers aren't Debian's
  fi

  case "$codename" in
  bullseye) version_id=11 ;;
  buster | stretch | jessie) version_id=10 ;;
  esac
  if [ -n "$version_id" ] && [ "${version_id%%.*}" -le 10 ] 2>/dev/null; then
    echo "PiPrayer needs Raspberry Pi OS Bullseye or later: ${codename:-this release} can't play its Opus recordings."
    return 1
  elif [ "$version_id" = 11 ]; then
    echo "Raspberry Pi OS Bullseye: using PulseAudio for audio"
    packages="git python3 bluez pulseaudio pulseaudio-module-bluetooth"
  else
    packages="git python3 bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils"
  fi

  echo "Installing the packages PiPrayer needs, with sudo (it may ask for your password):"
  echo "  $packages"
  # shellcheck disable=SC2086 # the packages are separate words
  if ! { sudo apt-get update -qq && sudo apt-get install -y -qq $packages; }; then
    echo "Couldn't install the packages. Fix the error above, then run this again."
    return 1
  fi

  if [ -d "$dir/.git" ]; then
    echo "Updating PiPrayer in $dir"
    if ! git -C "$dir" fetch --quiet --tags origin; then
      echo "Couldn't get the latest PiPrayer. Check the Raspberry Pi is online, then run this again."
      return 1
    fi
    if [ -n "$version" ]; then
      git -C "$dir" checkout --quiet "$version" || return 1
    elif ! { git -C "$dir" checkout --quiet main && git -C "$dir" merge --quiet --ff-only origin/main; }; then
      echo "Couldn't update $dir, it has changes of its own. See git -C $dir status."
      return 1
    fi
  elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    echo "$dir is already there and isn't PiPrayer. Move it, or choose another folder with PIPRAYER_DIR."
    return 1
  else
    echo "Getting PiPrayer into $dir"
    if ! git clone --quiet "$repo" "$dir"; then
      echo "Couldn't get PiPrayer. Check the Raspberry Pi is online, then run this again."
      return 1
    fi
    if [ -n "$version" ]; then
      git -C "$dir" checkout --quiet "$version" || return 1
    fi
  fi

  echo
  # Through a pipe, the script is the standard input, so the setup's questions read the terminal instead
  if { : </dev/tty; } 2>/dev/null; then
    "$dir/setup-piprayer.sh" </dev/tty
  else
    "$dir/setup-piprayer.sh"
  fi
}

# The value of a KEY=value line in os-release, without quotes
read_os_release() {
  sed -n "s/^$2=[\"']\{0,1\}\([^\"']*\)[\"']\{0,1\}\$/\1/p" "$1" | head -n 1
}

main "$@"
