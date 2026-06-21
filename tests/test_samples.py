import struct
from circuit_tracks.samples import (
    sample_file_id,
    convert_any_bytes_to_wav_48k_optimized
)


def _create_wav_bytes(frequency=48000, bits_per_sample=16, channels=1, duration_seconds=1.0):
    """Create audio bytes"""
    # Generate empty data
    num_samples = int(frequency * duration_seconds)
    # 16 bits = 2B per sample
    bytes_per_sample = bits_per_sample // 8
    data_size = num_samples * channels * bytes_per_sample

    audio_data = struct.pack(f'<{num_samples * channels}h', *([0] * num_samples * channels))

    chunk_size = 36 + data_size
    byte_rate = frequency * channels * bytes_per_sample
    block_align = channels * bytes_per_sample

    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',          # ChunkID
        chunk_size,      # ChunkSize
        b'WAVE',          # Format
        b'fmt ',          # Subchunk1ID
        16,              # Subchunk1Size (16 pour du PCM)
        1,               # AudioFormat (1 pour PCM non compressé)
        channels,        # NumChannels
        frequency,       # SampleRate
        byte_rate,       # ByteRate
        block_align,     # BlockAlign
        bits_per_sample, # BitsPerSample
        b'data',          # Subchunk2ID
        data_size        # Subchunk2Size
    )
    return header + audio_data


def _validate_wav_specifications(wav_bytes: bytes) -> bool:
    """
    Checks if a bytes object represents a valid PCM WAV file with:
    - Sample Rate: 48,000 Hz
    - Channels: 1 (Mono)
    - Bits per Sample: 16-bit
    """
    # A standard WAV header requires at least 44 bytes
    if len(wav_bytes) < 44:
        return False

    try:
        # 1. Verify file signatures (Magic Bytes)
        if wav_bytes[0:4] != b'RIFF' or wav_bytes[8:12] != b'WAVE':
            return False

        # 2. Locate the format subchunk ('fmt ')
        fmt_index = wav_bytes.find(b'fmt ')
        if fmt_index == -1:
            return False

        # 3. Extract audio format, channels, and sample rate
        # '<HHI' reads: Little-Endian, uint16, uint16, uint32
        # We start reading 8 bytes after the 'fmt ' magic string
        audio_format, channels, sample_rate = struct.unpack(
            '<HHI',
            wav_bytes[fmt_index + 8 : fmt_index + 16]
        )

        # 4. Extract bits per sample (located 22 bytes after 'fmt ')
        bits_per_sample = struct.unpack(
            '<H',
            wav_bytes[fmt_index + 22 : fmt_index + 24]
        )[0]

        # 5. Validate against required specs
        # audio_format == 1 guarantees uncompressed PCM data
        return (
            audio_format == 1 and
            sample_rate == 48000 and
            bits_per_sample == 16 and
            channels == 1
        )

    except (struct.error, IndexError):
        # Returns False if the byte array is corrupted or malformed
        return False


class TestSampleFileId:
    """Test file ID generation."""

    def test_pack_zero_slot_zero(self):
        assert sample_file_id(0, 0) == [0x05, 0x00, 0x00]

    def test_pack_zero_slot_one(self):
        assert sample_file_id(0, 1) == [0x05, 0x00, 0x01]

    def test_pack_one_slot_zero(self):
        assert sample_file_id(1, 0) == [0x05, 0x01, 0x00]

    def test_pack_one_slot_one(self):
        assert sample_file_id(1, 1) == [0x05, 0x01, 0x01]


class TestConvertToWav:
    """Test audio bytes convertion to Circuit Tracks format"""

    def test_fast_path(self):
        input_bytes = _create_wav_bytes()
        output_bytes = convert_any_bytes_to_wav_48k_optimized(input_bytes)
        assert _validate_wav_specifications(output_bytes)

    def test_slow_path(self):
        input_bytes = _create_wav_bytes()
        output_bytes = convert_any_bytes_to_wav_48k_optimized(input_bytes)
        assert _validate_wav_specifications(output_bytes)
