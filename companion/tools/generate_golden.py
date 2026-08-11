#!/usr/bin/env python3
"""Generate golden test vectors from the Python reference implementation.

Uses src/circuit_tracks/ncs_transfer.py as the source of truth so the JS
protocol port in companion/js/midi/protocol.js can be verified byte-for-byte.

Run from the repo root with the project venv:
    venv/bin/python companion/tools/generate_golden.py
"""

import json
import random
import zlib
from pathlib import Path

from circuit_tracks import ncs_transfer as ref

OUT = Path(__file__).resolve().parent.parent / "tests" / "golden" / "vectors.json"

FILE_TYPE_SAMPLE = 0x05


def sample_file_id(slot: int) -> list[int]:
    # Mirrors ref.file_id() but for drum samples (type 0x05).
    return [FILE_TYPE_SAMPLE, (slot >> 7) & 0x7F, slot & 0x7F]


def main() -> None:
    rng = random.Random(0xC1BC)

    vectors: dict = {}

    # MSB interleave: various lengths incl. empty, partial and full groups
    msb_cases = []
    for length in [0, 1, 6, 7, 8, 13, 14, 20, 100, 8192]:
        data = bytes(rng.randrange(256) for _ in range(length))
        encoded = ref.encode_msb_interleave(data)
        assert ref.decode_msb_interleave(encoded) == data
        msb_cases.append({"raw": list(data), "encoded": encoded})
    vectors["msb_interleave"] = msb_cases

    # Nibble encoding
    vectors["nibbles"] = [
        {"value": v, "count": c, "nibbles": ref.int_to_nibbles(v, c)}
        for v, c in [
            (0, 5),
            (1, 5),
            (160780, 5),
            (0xFFFFF, 5),
            (126066, 5),
            (0, 8),
            (0xDEADBEEF, 8),
            (0xFFFFFFFF, 8),
            (305419896, 8),
        ]
    ]

    # Block addressing
    vectors["block_address"] = [
        {"block": b, "address": ref.block_address(b)} for b in [0, 1, 15, 16, 17, 20, 21, 31, 32, 255]
    ]

    # File IDs for drum samples
    vectors["file_id_sample"] = [{"slot": s, "file_id": sample_file_id(s)} for s in [0, 1, 42, 63]]

    # CRC32 over a few payloads
    crc_cases = []
    for length in [0, 1, 100, 10000]:
        data = bytes(rng.randrange(256) for _ in range(length))
        crc_cases.append({"raw": list(data), "crc32": zlib.crc32(data) & 0xFFFFFFFF})
    vectors["crc32"] = crc_cases

    # Full write-sequence messages for a fake drum sample, built exactly the
    # way send_ncs_project() builds them (generalised to file type 0x05).
    sample_data = bytes(rng.randrange(256) for _ in range(20000))  # 3 blocks
    slot = 7
    filename = "07_TestKick.wav"
    fid = sample_file_id(slot)
    crc = zlib.crc32(sample_data) & 0xFFFFFFFF
    block_size = 8192
    num_blocks = -(-len(sample_data) // block_size)

    def make(subcmd, payload=None):
        return ref._make_msg(subcmd, payload)

    messages = {
        "open_session": make(ref._SUBCMD_OPEN_SESSION),
        "dir_handshake": [
            make(ref._SUBCMD_DIR_CONTROL, [0x01]),
            make(ref._SUBCMD_QUERY_INFO, [0x01, 0x00]),
            make(ref._SUBCMD_DIR_CONTROL, [0x02]),
        ],
        "dir_list_samples": make(ref._SUBCMD_DIR_CONTROL, [FILE_TYPE_SAMPLE, 0x00]),
        "write_init": make(
            ref._SUBCMD_WRITE_INIT,
            ref.block_address(0) + fid + [0x01, 0x00, 0x00, 0x00] + ref.int_to_nibbles(len(sample_data), 5),
        ),
        "write_data_blocks": [
            make(
                ref._SUBCMD_WRITE_DATA,
                ref.block_address(block)
                + fid
                + ref.encode_msb_interleave(sample_data[(block - 1) * block_size : block * block_size]),
            )
            for block in range(1, num_blocks + 1)
        ],
        "write_finish": make(
            ref._SUBCMD_WRITE_FINISH,
            ref.block_address(num_blocks + 1) + fid + ref.int_to_nibbles(crc, 8),
        ),
        "set_filename": make(ref._SUBCMD_SET_FILENAME, fid + [ord(c) for c in filename]),
        "close_session": make(ref._SUBCMD_CLOSE_SESSION),
    }
    vectors["write_sequence"] = {
        "sample_data": list(sample_data),
        "slot": slot,
        "filename": filename,
        "crc32": crc,
        "num_blocks": num_blocks,
        "messages": messages,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vectors))
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
