"""Tests for project utils."""

from pathlib import Path

import pytest

from circuit_tracks.midi_protocol import (
    block_address,
    decode_msb_interleave,
    encode_msb_interleave,
    int_to_nibbles,
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
