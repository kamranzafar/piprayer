# PiPrayer - Azaan for Raspberry Pi
# Copyright (C) 2021-2026 Kamran Zafar
# SPDX-License-Identifier: GPL-3.0-or-later
"""How long a recording is, read from its file: Ogg (Opus or Vorbis), WAV or FLAC.

Enough to tell whether Surah Al-Kahf will finish before it has to stop, without ffmpeg on the Raspberry Pi.
"""
import struct

OPUS_RATE = 48000  # Opus always counts samples at 48 kHz
# The last Ogg page is in the last 64 KB of the file
TAIL_BYTES = 65536


def duration(path):
    """The length of the recording in seconds, or None when it can't be told, e.g. for an MP3."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if head[:4] == b"OggS":
                return ogg_duration(f, head)
            if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                return wav_duration(head)
            if head[:4] == b"fLaC":
                return flac_duration(head)
    except (OSError, struct.error, ValueError, ZeroDivisionError):
        pass
    return None


def ogg_duration(f, head):
    """From the position of the last sample, in the last page, and the rate in the first packet."""
    if b"OpusHead" in head:
        start = head.index(b"OpusHead")
        rate, skip = OPUS_RATE, struct.unpack_from("<H", head, start + 10)[0]
    elif b"\x01vorbis" in head:
        start = head.index(b"\x01vorbis")
        rate, skip = struct.unpack_from("<I", head, start + 12)[0], 0
    else:
        return None
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - TAIL_BYTES))
    tail = f.read()
    last = tail.rfind(b"OggS")
    if last < 0 or last + 14 > len(tail):
        return None
    granule = struct.unpack_from("<q", tail, last + 6)[0]
    return max(0, granule - skip) / rate


def wav_duration(head):
    """The size of the data over the bytes a second, from the fmt chunk."""
    position, byte_rate = 12, None
    while position + 8 <= len(head):
        chunk, size = head[position:position + 4], struct.unpack_from("<I", head, position + 4)[0]
        if chunk == b"fmt ":
            byte_rate = struct.unpack_from("<I", head, position + 16)[0]
        elif chunk == b"data":
            return size / byte_rate if byte_rate else None
        position += 8 + size + size % 2
    return None


def flac_duration(head):
    """The samples over the sample rate, from the STREAMINFO block, which comes first."""
    info = head[8:8 + 34]
    rate = (info[10] << 12) | (info[11] << 4) | (info[12] >> 4)
    samples = ((info[13] & 0x0F) << 32) | struct.unpack_from(">I", info, 14)[0]
    return samples / rate if rate and samples else None
