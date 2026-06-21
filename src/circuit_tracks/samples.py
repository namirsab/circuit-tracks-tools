"""Sample transfer via SysEx for Novation Circuit Tracks

Implements the sample management SysEx protocol used by Novation Components
to send .wav (or any audio) files to the Circuit Tracks over MIDI.

Protocol reverse-engineered from captured WebMIDI output.
See plans/sample-transfer-protocol.md for full specification.
"""
from __future__ import annotations

import math
import time
import zlib
import io
import struct
from collections.abc import Callable

from pydub import AudioSegment

# Assuming these are imported from your existing project layout
from circuit_tracks.constants import (
    _SUBCMD_WRITE_INIT,
    _SUBCMD_WRITE_DATA,
    _SUBCMD_WRITE_FINISH,
    _SUBCMD_ACK,
    _SUBCMD_SET_FILENAME,
    _SUBCMD_OPEN_SESSION,
    _SUBCMD_CLOSE_SESSION,
    _SYSEX_HEADER,
)
from circuit_tracks import midi_protocol
from circuit_tracks.midi import MidiConnection


# File type for drum samples
_FILE_TYPE_SAMPLE = 0x05

# Sub-slot/target byte constant observed for drum samples
_SUB_SLOT_SAMPLE = 0x08

# Data block size for sample streaming (matches your structure, safe for MIDI buffers)
_BLOCK_SIZE = 8192


def sample_file_id(pack: int, slot: int) -> list[int]:
    """Build a 3-byte target routing ID for a specific pack and sample slot.

    Yields: [file_type, pack, slot]
    """
    return [_FILE_TYPE_SAMPLE, pack & 0x7F, slot & 0x7F]


def _make_msg(subcmd: int, payload: list[int] | None = None) -> list[int]:
    """Build a complete SysEx data payload (without outer F0/F7 borders)."""
    msg = _SYSEX_HEADER + [subcmd]
    if payload:
        msg += payload
    return msg


def _wait_for_ack(
    midi: MidiConnection,
    expected_addr: list[int],
    expected_file_id: list[int],
    timeout_s: float = 5.0,
) -> bool:
    """Wait for a device ACK matching the given address and file ID."""
    if not midi.has_input:
        time.sleep(0.05)
        return True

    deadline = time.monotonic() + timeout_s
    header_len = len(_SYSEX_HEADER)

    while time.monotonic() < deadline:
        msg = midi._input_port.poll()
        if msg is None:
            time.sleep(0.005)
            continue
        if msg.type == "sysex":
            data = list(msg.data)
            # Structure attendue : Header + ACK (0x04) + Adresse (8) + FileID (3)
            if (
                len(data) >= header_len + 1 + 8 + 3
                and data[:header_len] == _SYSEX_HEADER
                and data[header_len] == _SUBCMD_ACK
                and data[header_len + 1 : header_len + 9] == expected_addr
                and data[header_len + 9 : header_len + 12] == expected_file_id
            ):
                return True
    return False


def receive_sample(
    midi: MidiConnection,
    pack: int,
    slot: int,
    progress_callback: Callable[[int], None] | None = None,
    timeout_s: float = 10.0,
) -> bytes:
    """Download sample from a given pack/slot.

    Args:
        midi: Connected MidiConnection with input port.
        pack: Pack number (0-31).
        slot: Project slot number (0-63).
        progress_callback: Called with (bytes_sent, total_bytes) after each block.

    Returns:
        Raw sample as WAV file
    """
    midi._ensure_connected()
    if not midi.has_input:
        raise RuntimeError("Input port required to receive project data")

    header_len = len(_SYSEX_HEADER)
    midi_protocol._drain_and_wait(midi, 0.01)

    # 1. Open Session
    midi.send_sysex(_make_msg(_SUBCMD_OPEN_SESSION))
    midi_protocol._drain_and_wait(midi, 0.1)

    # 2. WRITE_INIT with 0x02
    addr_zero = [0, 0, 0, 0, 0, 0, 0, 0]
    fid = [_FILE_TYPE_SAMPLE, pack & 0x7F, slot & 0x7F]
    read_flag = [0x02]

    init_payload = addr_zero + fid + read_flag
    midi.send_sysex(_make_msg(_SUBCMD_WRITE_INIT, init_payload))

    # 3. Loop for catch all dataframes
    raw_sample_data = bytearray()
    deadline = time.monotonic() + timeout_s
    stream_ended = False

    while time.monotonic() < deadline:
        msg = midi._input_port.poll()
        if msg is None:
            time.sleep(0.001)
            continue

        if msg.type != "sysex":
            continue

        data = list(msg.data)
        if len(data) < header_len + 1 or data[:header_len] != _SYSEX_HEADER:
            continue

        subcmd = data[header_len]

        if subcmd == _SUBCMD_WRITE_DATA:
            # Received format
            # Header(6 bytes) + subcmd(1) + address(8) + file_id(3) + encoded_data
            # So encoded data start index is header_len + 1 + 8 + 3
            encoded_start = header_len + 1 + 8 + 3
            encoded_chunk = data[encoded_start:]

            decoded_chunk = midi_protocol.decode_msb_interleave(encoded_chunk)
            raw_sample_data.extend(decoded_chunk)

            if progress_callback:
                progress_callback(len(raw_sample_data))

            # Refresh timeout for each block
            deadline = time.monotonic() + 3.0

        elif subcmd == _SUBCMD_WRITE_FINISH or subcmd == 0x03:
            stream_ended = True
            break

    # 6. Close session
    midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
    midi_protocol._drain_and_wait(midi, 0.05)

    if not stream_ended and len(raw_sample_data) == 0:
        raise RuntimeError("No data received from Tracks")

    return bytes(raw_sample_data)


def convert_any_bytes_to_wav_48k_optimized(audio_bytes):
    """
    Ensure audio bytes are in valid format for the Circuit Track:
    48000Hz, 1 channel (mono), 16 bits
    """
    # 1. Fast Path - Check if .wav
    if len(audio_bytes) >= 44 and audio_bytes[0:4] == b'RIFF' and audio_bytes[8:12] == b'WAVE':
        try:
            # WAV metadata extraction (norme RIFF)
            # - channels (offset 22, 2 bytes)
            # - sample_rate (offset 24, 4 bytes)
            # - bits_per_sample (offset 34, 2 bytes)
            channels = struct.unpack('<H', audio_bytes[22:24])[0]
            sample_rate = struct.unpack('<I', audio_bytes[24:28])[0]
            bits_per_sample = struct.unpack('<H', audio_bytes[34:36])[0]

            if channels == 1 and sample_rate == 48000 and bits_per_sample == 16:
                # Good format
                return audio_bytes
        except Exception:
            # Let pydub do the job
            pass

    # 2. Slow Path - convert
    audio_fp = io.BytesIO(audio_bytes)
    audio = AudioSegment.from_file(audio_fp)

    # Apply filters : Mono (channels=1), 48kHz, 16-bit (sample_width=2)
    audio_optimized = audio.set_frame_rate(48000).set_channels(1).set_sample_width(2)

    output_fp = io.BytesIO()
    audio_optimized.export(output_fp, format="wav")

    return output_fp.getvalue()


def send_sample(
    midi: MidiConnection,
    sample_data: bytes,
    pack: int,
    slot: int,
    filename: str | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """Send an sample to the Circuit Tracks via SysEx.

    Args:
        midi: Connected MidiConnection with input port.
        sample_data: Raw WAV file bytes
        pack: Pack number (0-31).
        slot: Project slot number (0-63).
        filename: Filename to set on the device (e.g. "kick.wav").
                  If None, generates from slot number.
        progress_callback: Called with (bytes_sent, total_bytes) after each block.

    Returns:
        Dict with transfer result info.
    """
    midi._ensure_connected()

    fid = sample_file_id(pack, slot)
    crc = zlib.crc32(sample_data) & 0xFFFFFFFF
    num_blocks = math.ceil(len(sample_data) / _BLOCK_SIZE)

    size_bytes = [
        (len(sample_data) >> 24) & 0x7F,
        (len(sample_data) >> 16) & 0x7F,
        (len(sample_data) >> 8) & 0x7F,
        len(sample_data) & 0x7F,
    ]

    midi_protocol._drain_input(midi)

    # 1. Open session
    midi.send_sysex(_make_msg(_SUBCMD_OPEN_SESSION))
    time.sleep(0.1)  # Laisse l'appareil respirer et vider ses ACK spontanés
    midi_protocol._drain_input(midi)

    # 2. WRITE_INIT for sample
    init_addr = midi_protocol.block_address(0)
    init_payload = init_addr + fid + [0x01, 0x01, 0x00, 0x00, 0x00, 0x00] + size_bytes

    midi.send_sysex(_make_msg(_SUBCMD_WRITE_INIT, init_payload))
    if not _wait_for_ack(midi, init_addr, fid):
        return {"error": "No ACK for WRITE_INIT. Is the device ready?"}

    # 3. Loop of WRITE_DATA
    bytes_sent = 0
    for block_num in range(1, num_blocks + 1):
        offset = (block_num - 1) * _BLOCK_SIZE
        chunk = sample_data[offset : offset + _BLOCK_SIZE]
        encoded = midi_protocol.encode_msb_interleave(chunk)

        addr = midi_protocol.block_address(block_num)
        data_payload = addr + fid + encoded

        midi.send_sysex(_make_msg(_SUBCMD_WRITE_DATA, data_payload))

        if not _wait_for_ack(midi, addr, fid):
            return {
                "error": f"Sending failed : No ACK for {block_num}/{num_blocks}",
                "bytes_sent": bytes_sent,
            }

        bytes_sent += len(chunk)
        if progress_callback:
            progress_callback(bytes_sent, len(sample_data))

    # 4. WRITE_FINISH with CRC32
    finish_addr = midi_protocol.block_address(num_blocks + 1)
    crc_nibbles = midi_protocol.int_to_nibbles(crc, 8)
    finish_payload = finish_addr + fid + crc_nibbles

    midi.send_sysex(_make_msg(_SUBCMD_WRITE_FINISH, finish_payload))
    if not _wait_for_ack(midi, finish_addr, fid):
        return {"error": "Failed to finalize: No ACK for WRITE_FINISH."}

    # TODO: Doesn't work
    # 5. SET_FILENAME
    # if filename is None:
    #     filename = f"sample_{slot:02d}.wav"
    # filename_bytes = [ord(c) for c in filename if 34 <= ord(c) <= 126]
    # midi.send_sysex(_make_msg(_SUBCMD_SET_FILENAME, fid + filename_bytes))
    # if not _wait_for_ack(midi, midi_protocol.block_address(0), fid):
    #     pass
    # midi_protocol._drain_input(midi)

    # 6. Close session
    midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
    time.sleep(0.1)
    midi_protocol._drain_input(midi)

    return {
        "status": "ok",
        "pack": pack,
        "slot": slot,
        # "filename": filename,
        "bytes_sent": bytes_sent,
        "blocks": num_blocks,
        "crc32": f"0x{crc:08X}",
    }


def clear_sample_slot(midi: MidiConnection, pack: int, slot: int):
    """Empty a sample slot.

    Args:
        midi: Connected MidiConnection with input port.
        pack: Pack number (0-31).
        slot: Project slot number (0-63).
    """
    fid = sample_file_id(pack, slot)
    addr_init = midi_protocol.block_address(0)
    addr_finish = midi_protocol.block_address(1)

    # 1. Open session
    midi.send_sysex(_make_msg(_SUBCMD_OPEN_SESSION))
    time.sleep(0.1)
    midi_protocol._drain_input(midi)

    # 2. WRITE_INIT with metadata for empty file
    # [0x03, pack, slot]
    init_payload = addr_init + fid + [0x01, 0x01, 0x00, 0x00, 0x00, 0x00] + [0x00, 0x00, 0x00, 0x00]
    midi.send_sysex(_make_msg(_SUBCMD_WRITE_INIT, init_payload))
    if not _wait_for_ack(midi, addr_init, fid):
        return {"error": "No ACK for WRITE_INIT. Is the device ready?"}

    # 3. Instant WRITE_FINISH (without sending WRITE_DATA)
    crc_nibbles = midi_protocol.int_to_nibbles(0, 8)
    finish_payload = addr_finish + fid + crc_nibbles

    midi.send_sysex(_make_msg(_SUBCMD_WRITE_FINISH, finish_payload))
    if not _wait_for_ack(midi, addr_finish, fid):
        return {"error": "Failed to finalize: No ACK for WRITE_FINISH."}

    # 4. Closing session
    midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
    time.sleep(0.1)
    midi_protocol._drain_input(midi)

    return True
