import time


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


def block_address(block_num: int) -> list[int]:
    """Convert a sequential block number to an 8-byte address.

    Uses (page, offset) encoding with 16 offsets per page:
    block 0 → (0, 0), block 15 → (0, 15), block 16 → (1, 0), etc.
    """
    page = block_num >> 4  # block_num // 16
    offset = block_num & 0x0F  # block_num % 16
    return [0, 0, 0, 0, 0, 0, page, offset]


def _drain_input(midi) -> None:
    """Drain pending input messages from the MIDI port."""
    if midi.has_input:
        while midi._input_port.poll() is not None:
            pass


def _drain_and_wait(midi, sleep_time) -> None:
    time.sleep(sleep_time)
    _drain_input(midi)
