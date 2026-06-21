"""Tests for NCS project transfer protocol."""

import json
import zlib
from pathlib import Path

import pytest

from circuit_tracks.ncs_transfer import (
    _BLOCK_SIZE,
    NCS_FILE_SIZE,
    decode_msb_interleave,
    encode_msb_interleave,
    file_id,
    int_to_nibbles,
    nibbles_to_int,
)


class TestFileId:
    """Test file ID generation."""

    def test_slot_zero(self):
        assert file_id(0) == [0x03, 0x00, 0x00]

    def test_slot_one(self):
        assert file_id(1) == [0x03, 0x00, 0x01]

    def test_slot_sixty_three(self):
        assert file_id(63) == [0x03, 0x00, 0x3F]


class TestNibblesToInt:
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
            chunk = ncs_data[offset : offset + _BLOCK_SIZE]
            re_encoded = encode_msb_interleave(chunk)
            captured = captured_encoded_blocks[block_num]
            assert re_encoded == captured, f"Block {block_num} encoding mismatch"
