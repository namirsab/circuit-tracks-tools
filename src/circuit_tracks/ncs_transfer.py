"""NCS project file transfer via SysEx for Novation Circuit Tracks.

Implements the file management SysEx protocol used by Novation Components
to send .ncs project files to the Circuit Tracks over MIDI.

Protocol reverse-engineered from captured WebMIDI output.
See plans/ncs-transfer-protocol.md for full specification.
"""

from __future__ import annotations

import math
import time
import zlib
from collections.abc import Callable

from circuit_tracks.constants import (
    SYSEX_MANUFACTURER_ID,
    SYSEX_PRODUCT_NUMBER,
    SYSEX_PRODUCT_TYPE,
)
from circuit_tracks.midi import MidiConnection

# File management protocol command group
_CMD_GROUP = 0x03

# Sub-commands (Host → Device)
_SUBCMD_WRITE_INIT = 0x01
_SUBCMD_WRITE_DATA = 0x02
_SUBCMD_WRITE_FINISH = 0x03
_SUBCMD_SET_FILENAME = 0x07
_SUBCMD_QUERY_INFO = 0x09
_SUBCMD_DIR_CONTROL = 0x0B
_SUBCMD_OPEN_SESSION = 0x40
_SUBCMD_CLOSE_SESSION = 0x41

# Sub-commands (Device → Host)
_SUBCMD_ACK = 0x04
_SUBCMD_FILE_ENTRY = 0x0C

# File type for projects
_FILE_TYPE_PROJECT = 0x03

# Patch size in bytes
_PATCH_SIZE = 340

# Data block size (raw bytes per WRITE_DATA message)
_BLOCK_SIZE = 8192

# Common SysEx header for file management protocol
_SYSEX_HEADER = SYSEX_MANUFACTURER_ID + [SYSEX_PRODUCT_TYPE, SYSEX_PRODUCT_NUMBER, _CMD_GROUP]

# NCS file size
NCS_FILE_SIZE = 160780


def encode_msb_interleave(data: bytes) -> list[int]:
    """Encode 8-bit data into 7-bit MIDI-safe bytes using MSB interleave.

    For every 7 data bytes, produces 8 output bytes: one MSB header byte
    followed by the 7 data bytes with their MSBs cleared. The MSB header
    stores the MSBs: bit 0 = MSB of byte 0, bit 1 = MSB of byte 1, etc.
    """
    result: list[int] = []
    i = 0
    while i < len(data):
        group = data[i : i + 7]
        msb_header = 0
        for j, byte in enumerate(group):
            if byte & 0x80:
                msb_header |= 1 << j
        result.append(msb_header)
        for byte in group:
            result.append(byte & 0x7F)
        i += 7
    return result


def decode_msb_interleave(encoded: list[int]) -> bytes:
    """Decode MSB-interleaved 7-bit MIDI data back to 8-bit bytes."""
    result = bytearray()
    i = 0
    while i < len(encoded):
        msb_header = encoded[i]
        i += 1
        for j in range(7):
            if i >= len(encoded):
                break
            msb = (msb_header >> j) & 1
            result.append(encoded[i] | (msb << 7))
            i += 1
    return bytes(result)


def int_to_nibbles(value: int, count: int) -> list[int]:
    """Encode an integer as a sequence of hex nibbles, MSN first."""
    nibbles = []
    for i in range(count - 1, -1, -1):
        nibbles.append((value >> (4 * i)) & 0x0F)
    return nibbles


def nibbles_to_int(nibbles: list[int]) -> int:
    """Decode a sequence of hex nibbles back to an integer."""
    value = 0
    for n in nibbles:
        value = (value << 4) | (n & 0x0F)
    return value


def block_address(block_num: int) -> list[int]:
    """Convert a sequential block number to an 8-byte address.

    Uses (page, offset) encoding with 16 offsets per page:
    block 0 → (0, 0), block 15 → (0, 15), block 16 → (1, 0), etc.
    """
    page = block_num >> 4  # block_num // 16
    offset = block_num & 0x0F  # block_num % 16
    return [0, 0, 0, 0, 0, 0, page, offset]


def file_id(slot: int) -> list[int]:
    """Build a 3-byte file ID for a project slot (0-63)."""
    return [_FILE_TYPE_PROJECT, (slot >> 7) & 0x7F, slot & 0x7F]


def _make_msg(subcmd: int, payload: list[int] | None = None) -> list[int]:
    """Build a complete SysEx data payload (without F0/F7)."""
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
        # No input port — just add a delay and hope for the best
        time.sleep(0.05)
        return True

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = midi._input_port.poll()
        if msg is None:
            time.sleep(0.005)
            continue
        if msg.type == "sysex":
            data = list(msg.data)
            # ACK format: header + 0x04 + address(8) + file_id(3)
            if (
                len(data) >= len(_SYSEX_HEADER) + 1 + 8 + 3
                and data[: len(_SYSEX_HEADER)] == _SYSEX_HEADER
                and data[len(_SYSEX_HEADER)] == _SUBCMD_ACK
            ):
                return True
    return False


def _drain_input(midi: MidiConnection) -> None:
    """Drain any pending input messages."""
    if midi.has_input:
        while midi._input_port.poll() is not None:
            pass


def list_directory(
    midi: MidiConnection,
    file_type: int = _FILE_TYPE_PROJECT,
    timeout_s: float = 3.0,
) -> list[dict]:
    """List files on the device by capturing FILE_ENTRY responses.

    Opens a file management session, sends the directory handshake for
    the given file type, and captures FILE_ENTRY (0x0C) responses instead
    of draining them.

    Known file types: 0x03=projects, 0x04=patches, 0x05=drum samples.

    Args:
        midi: Connected MidiConnection with input port.
        file_type: File type byte to query.
        timeout_s: How long to wait for entries after the listing request.

    Returns:
        List of dicts with 'slot' and 'filename' keys.
    """
    midi._ensure_connected()
    if not midi.has_input:
        return []

    _drain_input(midi)

    # 1. Open session
    midi.send_sysex(_make_msg(_SUBCMD_OPEN_SESSION))
    time.sleep(0.3)
    _drain_input(midi)

    # 2. Directory handshake
    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x01]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_QUERY_INFO, [0x01, 0x00]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x02]))
    time.sleep(0.1)
    _drain_input(midi)

    # 3. Request file listing — capture FILE_ENTRY responses
    # Protocol: device responds with DIR_CONTROL ack first, then 64x FILE_ENTRY
    # The second byte of DIR_CONTROL 0x03 payload selects the file type to list
    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [file_type, 0x00]))

    entries: list[dict] = []
    header_len = len(_SYSEX_HEADER)
    deadline = time.monotonic() + timeout_s
    last_msg_time = time.monotonic()

    while time.monotonic() < deadline:
        msg = midi._input_port.poll()
        if msg is None:
            # If we have entries and haven't seen a message in 0.5s, we're done
            if entries and (time.monotonic() - last_msg_time) > 0.5:
                break
            time.sleep(0.01)
            continue

        last_msg_time = time.monotonic()
        if msg.type != "sysex":
            continue
        data = list(msg.data)
        if len(data) > header_len + 3 and data[:header_len] == _SYSEX_HEADER and data[header_len] == _SUBCMD_FILE_ENTRY:
            # FILE_ENTRY format: header + 0x0C + subtype + slot_hi + slot_lo + filename bytes
            subtype = data[header_len + 1]
            slot = (data[header_len + 2] << 7) | data[header_len + 3]
            name_bytes = data[header_len + 4 :]
            filename = "".join(chr(b) for b in name_bytes if 32 <= b <= 126)
            entries.append(
                {
                    "slot": slot,
                    "filename": filename,
                    "file_type": file_type,
                    "subtype": subtype,
                }
            )

    # 4. Close session
    midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
    time.sleep(0.1)
    _drain_input(midi)

    return entries


def receive_ncs_project(
    midi: MidiConnection,
    slot: int = 0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> bytes:
    """Read an NCS project file from the Circuit Tracks via SysEx.

    Opens a file management session, sends a read request for the given
    project slot, and captures the data blocks streamed back by the device.

    Args:
        midi: Connected MidiConnection with input port.
        slot: Project slot number (0-63).
        progress_callback: Called with (bytes_received, total_bytes) after each block.

    Returns:
        Raw NCS file bytes (160,780 bytes).

    Raises:
        ValueError: If slot is out of range.
        RuntimeError: If the device doesn't respond or CRC check fails.
    """
    if not 0 <= slot <= 63:
        raise ValueError(f"Slot must be 0-63, got {slot}")

    midi._ensure_connected()
    if not midi.has_input:
        raise RuntimeError("Input port required to receive project data")

    fid = file_id(slot)
    header_len = len(_SYSEX_HEADER)

    _drain_input(midi)

    # 1. Open session
    midi.send_sysex(_make_msg(_SUBCMD_OPEN_SESSION))
    time.sleep(0.3)
    _drain_input(midi)

    # 2. Directory handshake
    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x01]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_QUERY_INFO, [0x01, 0x00]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x02]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x03, 0x00]))
    time.sleep(0.5)
    _drain_input(midi)

    # 3. Send READ request: WRITE_INIT with 0x02 flag
    read_payload = block_address(0) + fid + [0x02]
    midi.send_sysex(_make_msg(_SUBCMD_WRITE_INIT, read_payload))

    # 4. Receive READ_INIT response (contains file size)
    file_size = None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        msg = midi._input_port.poll()
        if msg is None:
            time.sleep(0.005)
            continue
        if msg.type != "sysex":
            continue
        data = list(msg.data)
        if (
            len(data) >= header_len + 1 + 8 + 3 + 5
            and data[:header_len] == _SYSEX_HEADER
            and data[header_len] == _SUBCMD_WRITE_INIT
        ):
            # Extract size nibbles: after header(6) + subcmd(1) + addr(8) + fid(3) + flags(4)
            size_offset = header_len + 1 + 8 + 3 + 4
            size_nibbles = data[size_offset : size_offset + 5]
            file_size = nibbles_to_int(size_nibbles)
            break

    if file_size is None:
        midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
        raise RuntimeError("No READ_INIT response from device")

    if file_size != NCS_FILE_SIZE:
        midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
        raise RuntimeError(f"Unexpected file size: {file_size} (expected {NCS_FILE_SIZE})")

    # 5. Receive data blocks and WRITE_FINISH
    raw_data = bytearray()
    crc_received = None
    deadline = time.monotonic() + 60.0  # generous timeout for full transfer

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
            # Data block: header(6) + subcmd(1) + addr(8) + fid(3) + encoded_data
            encoded_start = header_len + 1 + 8 + 3
            encoded = data[encoded_start:]
            decoded = decode_msb_interleave(encoded)
            raw_data.extend(decoded)

            if progress_callback:
                progress_callback(len(raw_data), file_size)

        elif subcmd == _SUBCMD_WRITE_FINISH:
            # Finish: header(6) + subcmd(1) + addr(8) + fid(3) + 8 CRC nibbles
            crc_offset = header_len + 1 + 8 + 3
            crc_nibbles = data[crc_offset : crc_offset + 8]
            crc_received = nibbles_to_int(crc_nibbles)
            break

    # 6. Close session
    midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
    time.sleep(0.1)
    _drain_input(midi)

    if crc_received is None:
        raise RuntimeError("Transfer incomplete: no WRITE_FINISH received")

    # Trim to exact file size (last block may have padding)
    raw_bytes = bytes(raw_data[:file_size])

    # Verify CRC32
    crc_computed = zlib.crc32(raw_bytes) & 0xFFFFFFFF
    if crc_computed != crc_received:
        raise RuntimeError(f"CRC32 mismatch: computed 0x{crc_computed:08X}, received 0x{crc_received:08X}")

    return raw_bytes


def send_ncs_project(
    midi: MidiConnection,
    ncs_data: bytes,
    slot: int = 0,
    filename: str | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """Send an NCS project file to the Circuit Tracks via SysEx.

    Args:
        midi: Connected MidiConnection with input port.
        ncs_data: Raw NCS file bytes (must be 160,780 bytes).
        slot: Project slot number (0-63).
        filename: Filename to set on the device (e.g. "01_MyProject.ncs").
                  If None, generates from slot number.
        progress_callback: Called with (bytes_sent, total_bytes) after each block.

    Returns:
        Dict with transfer result info.
    """
    if len(ncs_data) != NCS_FILE_SIZE:
        raise ValueError(f"NCS data must be {NCS_FILE_SIZE} bytes, got {len(ncs_data)}")
    if not 0 <= slot <= 63:
        raise ValueError(f"Slot must be 0-63, got {slot}")

    midi._ensure_connected()

    if filename is None:
        filename = f"{slot:02d}_SESSION.ncs"

    fid = file_id(slot)
    crc = zlib.crc32(ncs_data) & 0xFFFFFFFF
    num_blocks = math.ceil(len(ncs_data) / _BLOCK_SIZE)
    size_nibbles = int_to_nibbles(len(ncs_data), 5)

    _drain_input(midi)

    # 1. Open session
    midi.send_sysex(_make_msg(_SUBCMD_OPEN_SESSION))
    _wait_for_ack(midi, block_address(0), fid, timeout_s=3.0)

    # 2. Directory handshake (replicate what Components sends)
    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x01]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_QUERY_INFO, [0x01, 0x00]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x02]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x03, 0x00]))
    time.sleep(0.5)  # Wait for device to send full file listing (64 entries)
    _drain_input(midi)

    # 3. WRITE_INIT
    init_payload = block_address(0) + fid + [0x01, 0x00, 0x00, 0x00] + size_nibbles
    midi.send_sysex(_make_msg(_SUBCMD_WRITE_INIT, init_payload))
    if not _wait_for_ack(midi, block_address(0), fid):
        return {"error": "No ACK for WRITE_INIT. Is the device ready?"}

    # 4. WRITE_DATA blocks
    bytes_sent = 0
    for block_num in range(1, num_blocks + 1):
        offset = (block_num - 1) * _BLOCK_SIZE
        chunk = ncs_data[offset : offset + _BLOCK_SIZE]
        encoded = encode_msb_interleave(chunk)

        addr = block_address(block_num)
        data_payload = addr + fid + encoded
        midi.send_sysex(_make_msg(_SUBCMD_WRITE_DATA, data_payload))

        if not _wait_for_ack(midi, addr, fid):
            return {
                "error": f"No ACK for block {block_num}/{num_blocks}",
                "bytes_sent": bytes_sent,
            }

        bytes_sent += len(chunk)
        if progress_callback:
            progress_callback(bytes_sent, len(ncs_data))

    # 5. WRITE_FINISH with CRC32
    finish_addr = block_address(num_blocks + 1)
    crc_nibbles = int_to_nibbles(crc, 8)
    finish_payload = finish_addr + fid + crc_nibbles
    midi.send_sysex(_make_msg(_SUBCMD_WRITE_FINISH, finish_payload))
    if not _wait_for_ack(midi, finish_addr, fid):
        return {"error": "No ACK for WRITE_FINISH", "bytes_sent": bytes_sent}

    # 6. SET_FILENAME
    filename_bytes = [ord(c) for c in filename]
    midi.send_sysex(_make_msg(_SUBCMD_SET_FILENAME, fid + filename_bytes))
    time.sleep(0.1)
    _drain_input(midi)

    # 7. Close session
    midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
    time.sleep(0.1)
    _drain_input(midi)

    return {
        "status": "ok",
        "slot": slot,
        "filename": filename,
        "bytes_sent": bytes_sent,
        "blocks": num_blocks,
        "crc32": f"0x{crc:08X}",
    }


def send_patch_to_slot(
    midi: MidiConnection,
    patch_bytes: bytes | list[int],
    synth: int,
    slot: int,
) -> dict:
    """Save a 340-byte synth patch to a flash slot.

    Replicates the Novation Components patch save protocol:
    1. Open a file management session and browse the patch directory
    2. Close the session
    3. Send the patch via Replace Patch SysEx (command group 0x01)

    The file management session appears to "unlock" flash writes.

    Args:
        midi: Connected MidiConnection (must have input port).
        patch_bytes: The 340-byte patch binary.
        synth: Synth number (1 or 2).
        slot: Patch slot number (0-63).

    Returns:
        Dict with transfer result info.
    """
    if synth not in (1, 2):
        raise ValueError(f"Synth must be 1 or 2, got {synth}")
    if not 0 <= slot <= 63:
        raise ValueError(f"Slot must be 0-63, got {slot}")

    if isinstance(patch_bytes, list):
        patch_bytes = bytes(patch_bytes)
    if len(patch_bytes) != _PATCH_SIZE:
        raise ValueError(f"Patch data must be {_PATCH_SIZE} bytes, got {len(patch_bytes)}")

    midi._ensure_connected()
    _drain_input(midi)

    # --- Phase 1: File management session (directory browse) ---
    # This appears to prepare the device for flash writes.

    # Patch directory file ID: type 0x04, slot encoded as 7-bit pair
    _FILE_TYPE_PATCH = 0x04

    # 1. Open session
    midi.send_sysex(_make_msg(_SUBCMD_OPEN_SESSION))
    time.sleep(0.3)
    _drain_input(midi)

    # 2. Directory handshake
    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x01]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_QUERY_INFO, [0x01, 0x00]))
    time.sleep(0.1)
    _drain_input(midi)

    midi.send_sysex(_make_msg(_SUBCMD_DIR_CONTROL, [0x02]))
    time.sleep(0.1)
    _drain_input(midi)

    # 3. Query patch directory entries (Components sends 16 per page)
    # Each query: WRITE_INIT with addr(0) + [0x04, 0x00, entry, 0x02]
    page_start = (slot // 16) * 16
    for i in range(16):
        entry = page_start + i
        query_payload = block_address(0) + [_FILE_TYPE_PATCH, 0x00, entry, 0x02]
        midi.send_sysex(_make_msg(_SUBCMD_WRITE_INIT, query_payload))
        time.sleep(0.02)
    time.sleep(0.3)
    _drain_input(midi)

    # 4. Close session
    midi.send_sysex(_make_msg(_SUBCMD_CLOSE_SESSION))
    time.sleep(0.1)
    _drain_input(midi)

    # --- Phase 2: Replace Patch SysEx ---
    # Components format: header + cmd(0x01) + location + 0x00 + slot + 0x00 + 340 bytes
    # Header: manufacturer(3) + product_type(1) + product_number(1)
    from circuit_tracks.constants import (
        SYSEX_MANUFACTURER_ID,
        SYSEX_PRODUCT_NUMBER,
        SYSEX_PRODUCT_TYPE,
    )

    patch_header = SYSEX_MANUFACTURER_ID + [SYSEX_PRODUCT_TYPE, SYSEX_PRODUCT_NUMBER]
    sysex_data = patch_header + [0x01, 0x00, 0x00, slot, 0x00] + list(patch_bytes)
    midi.send_sysex(sysex_data)

    patch_name = "".join(chr(b) for b in patch_bytes[0:16] if 32 <= b <= 126).strip()

    return {
        "status": "ok",
        "synth": synth,
        "slot": slot,
        "patch_name": patch_name,
        "bytes_sent": _PATCH_SIZE,
    }
