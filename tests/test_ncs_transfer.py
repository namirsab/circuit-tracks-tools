"""Tests for NCS project transfer protocol."""

import json
import os
import zlib
from pathlib import Path

import pytest

from circuit_mcp.ncs_transfer import (
    NCS_FILE_SIZE,
    _BLOCK_SIZE,
    block_address,
    decode_msb_interleave,
    encode_msb_interleave,
    file_id,
    int_to_nibbles,
    nibbles_to_int,
)


class TestMsbInterleave:
    """Test MSB interleave encoding/decoding."""

    def test_all_low_bytes(self):
        """Bytes < 0x80 should pass through with MSB header = 0."""
        data = bytes([0x55, 0x53, 0x45, 0x52, 0x0C, 0x74, 0x02])  # "USER..."
        encoded = encode_msb_interleave(data)
        assert encoded[0] == 0x00  # MSB header = 0 (no high bits)
        assert encoded[1:] == list(data)

    def test_high_bit_bytes(self):
        """Bytes >= 0x80 should have MSB stored in header."""
        data = bytes([0xFF, 0x80, 0x00, 0x7F, 0x81, 0x00, 0x00])
        encoded = encode_msb_interleave(data)
        # MSB header: bit0=1 (0xFF), bit1=1 (0x80), bit4=1 (0x81)
        assert encoded[0] == 0b00010011  # bits 0,1,4
        assert encoded[1] == 0x7F  # 0xFF & 0x7F
        assert encoded[2] == 0x00  # 0x80 & 0x7F
        assert encoded[3] == 0x00
        assert encoded[4] == 0x7F
        assert encoded[5] == 0x01  # 0x81 & 0x7F

    def test_round_trip_exact_group(self):
        """7 bytes should encode to 8 bytes and decode back."""
        data = bytes(range(7))
        encoded = encode_msb_interleave(data)
        assert len(encoded) == 8
        decoded = decode_msb_interleave(encoded)
        assert decoded == data

    def test_round_trip_multiple_groups(self):
        """Multiple groups should round-trip correctly."""
        data = bytes(range(14))  # exactly 2 groups
        encoded = encode_msb_interleave(data)
        assert len(encoded) == 16
        decoded = decode_msb_interleave(encoded)
        assert decoded == data

    def test_round_trip_partial_group(self):
        """Non-multiple-of-7 length should round-trip correctly."""
        data = bytes(range(10))  # 1 full group + 3 extra
        encoded = encode_msb_interleave(data)
        # 8 bytes for first group + 1 MSB header + 3 data = 12
        assert len(encoded) == 12
        decoded = decode_msb_interleave(encoded)
        assert decoded == data

    def test_round_trip_all_ff(self):
        """All 0xFF bytes should round-trip."""
        data = bytes([0xFF] * 21)  # 3 groups
        decoded = decode_msb_interleave(encode_msb_interleave(data))
        assert decoded == data

    def test_round_trip_random(self):
        """Random-ish data should round-trip."""
        import hashlib
        # Deterministic pseudo-random data
        data = hashlib.sha256(b"test").digest() * 10  # 320 bytes
        decoded = decode_msb_interleave(encode_msb_interleave(data))
        assert decoded == data

    def test_round_trip_large(self):
        """Block-sized data should round-trip."""
        data = bytes(range(256)) * 32  # 8192 bytes = 1 block
        decoded = decode_msb_interleave(encode_msb_interleave(data))
        assert decoded == data

    def test_all_encoded_bytes_under_0x80(self):
        """All encoded bytes must be valid MIDI data bytes (< 0x80)."""
        data = bytes(range(256)) * 4
        encoded = encode_msb_interleave(data)
        for i, b in enumerate(encoded):
            assert b < 0x80, f"Byte {i} = 0x{b:02X} >= 0x80"

    def test_empty(self):
        """Empty input should produce empty output."""
        assert encode_msb_interleave(b"") == []
        assert decode_msb_interleave([]) == b""


class TestNibbles:
    """Test nibble encoding/decoding."""

    def test_file_size(self):
        """160780 = 0x2740C encoded as 5 nibbles."""
        nibbles = int_to_nibbles(160780, 5)
        assert nibbles == [0x02, 0x07, 0x04, 0x00, 0x0C]

    def test_round_trip(self):
        assert nibbles_to_int(int_to_nibbles(160780, 5)) == 160780

    def test_crc32(self):
        """CRC32 value encoded as 8 nibbles."""
        nibbles = int_to_nibbles(0x2D9CB759, 8)
        assert nibbles == [0x02, 0x0D, 0x09, 0x0C, 0x0B, 0x07, 0x05, 0x09]

    def test_zero(self):
        assert int_to_nibbles(0, 4) == [0, 0, 0, 0]


class TestBlockAddress:
    """Test block address generation."""

    def test_block_zero(self):
        assert block_address(0) == [0, 0, 0, 0, 0, 0, 0, 0]

    def test_block_one(self):
        assert block_address(1) == [0, 0, 0, 0, 0, 0, 0, 1]

    def test_block_fifteen(self):
        assert block_address(15) == [0, 0, 0, 0, 0, 0, 0, 15]

    def test_block_sixteen(self):
        """Block 16 wraps to page 1, offset 0."""
        assert block_address(16) == [0, 0, 0, 0, 0, 0, 1, 0]

    def test_block_twenty(self):
        assert block_address(20) == [0, 0, 0, 0, 0, 0, 1, 4]

    def test_block_twenty_one(self):
        """FINISH address for a 160780-byte file."""
        assert block_address(21) == [0, 0, 0, 0, 0, 0, 1, 5]


class TestFileId:
    """Test file ID generation."""

    def test_slot_zero(self):
        assert file_id(0) == [0x03, 0x00, 0x00]

    def test_slot_one(self):
        assert file_id(1) == [0x03, 0x00, 0x01]

    def test_slot_sixty_three(self):
        assert file_id(63) == [0x03, 0x00, 0x3F]


class TestCapturedTransfer:
    """Verify against the real captured Components transfer."""

    @pytest.fixture
    def capture_path(self):
        path = Path(__file__).parent.parent / "midi-monitor" / "sysex_capture.json"
        if not path.exists():
            pytest.skip("sysex_capture.json not found")
        return path

    def test_decode_captured_data(self, capture_path):
        """Decode captured transfer and verify CRC32 matches."""
        with open(capture_path) as f:
            msgs = json.load(f)

        # Extract and decode all WRITE_DATA messages
        decoded_data = bytearray()
        for msg in msgs:
            inner = msg[1:-1]  # strip F0/F7
            if len(inner) >= 7 and inner[6] == 0x02:  # WRITE_DATA
                payload = inner[7:]
                encoded = payload[11:]  # skip 8-byte addr + 3-byte file_id
                decoded = decode_msb_interleave(encoded)
                decoded_data.extend(decoded)

        ncs_data = bytes(decoded_data[:NCS_FILE_SIZE])

        # Verify NCS magic
        assert ncs_data[:4] == b"USER"

        # Verify CRC32 matches the FINISH message
        crc = zlib.crc32(ncs_data) & 0xFFFFFFFF
        assert crc == 0x2D9CB759

    def test_encoding_matches_capture(self, capture_path):
        """Re-encode decoded data and verify it matches the captured bytes."""
        with open(capture_path) as f:
            msgs = json.load(f)

        # First decode to get the raw NCS data
        decoded_data = bytearray()
        captured_encoded_blocks = []
        for msg in msgs:
            inner = msg[1:-1]
            if len(inner) >= 7 and inner[6] == 0x02:
                payload = inner[7:]
                encoded = payload[11:]
                captured_encoded_blocks.append(encoded)
                decoded = decode_msb_interleave(encoded)
                decoded_data.extend(decoded)

        ncs_data = bytes(decoded_data[:NCS_FILE_SIZE])

        # Re-encode each block and compare
        import math
        num_blocks = math.ceil(NCS_FILE_SIZE / _BLOCK_SIZE)
        for block_num in range(num_blocks):
            offset = block_num * _BLOCK_SIZE
            chunk = ncs_data[offset:offset + _BLOCK_SIZE]
            re_encoded = encode_msb_interleave(chunk)
            captured = captured_encoded_blocks[block_num]
            assert re_encoded == captured, f"Block {block_num} encoding mismatch"
