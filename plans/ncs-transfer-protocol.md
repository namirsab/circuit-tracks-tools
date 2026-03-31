# NCS Project Transfer Protocol — Reverse Engineering & Implementation Plan

## Overview

This document describes the SysEx protocol used by Novation Components to send `.ncs` project files (160,780 bytes) to the Circuit Tracks over MIDI. The protocol was fully reverse-engineered by capturing WebMIDI output from the Components browser app using a JavaScript SysEx interceptor.

## Protocol Specification

### SysEx Message Format

All messages share a common header:
```
F0 00 20 29 01 64 03 <subcmd> <payload...> F7
```

| Field | Bytes | Value | Meaning |
|-------|-------|-------|---------|
| SysEx start | 1 | `F0` | Standard MIDI SysEx start |
| Manufacturer | 3 | `00 20 29` | Novation |
| Product type | 1 | `01` | Synth |
| Product number | 1 | `64` | Circuit Tracks (100) |
| Command group | 1 | `03` | File management protocol |
| Sub-command | 1 | varies | See message types below |
| Payload | N | varies | Message-specific data |
| SysEx end | 1 | `F7` | Standard MIDI SysEx end |

### Sub-Commands

| SubCmd | Name | Direction | Purpose |
|--------|------|-----------|---------|
| `0x40` | OPEN_SESSION | Host→Device | Start file management session |
| `0x41` | CLOSE_SESSION | Host→Device | End session |
| `0x0b` | DIR_CONTROL | Host→Device | Directory listing control |
| `0x09` | QUERY_INFO | Host→Device | Request device/version info |
| `0x01` | WRITE_INIT | Host→Device | Begin file write (metadata) |
| `0x02` | WRITE_DATA | Host→Device | File data chunk |
| `0x03` | WRITE_FINISH | Host→Device | End file write (checksum) |
| `0x07` | SET_FILENAME | Host→Device | Set filename for slot |
| `0x04` | ACK | Device→Host | Acknowledge (matches address) |
| `0x0c` | FILE_ENTRY | Device→Host | File listing entry |

### Complete Transfer Sequence

Captured from a real Components project send (29 SysEx messages):

```
Step  SubCmd  Size    Description
───── ─────── ─────── ────────────────────────────────────
  1   0x40       9    OPEN SESSION
  2   0x0b      10    DIR_CONTROL: 0x0b 01
  3   0x09      11    QUERY_INFO:  0x09 01 00
  4   0x0b      10    DIR_CONTROL: 0x0b 02
  5   0x0b      11    DIR_CONTROL: 0x0b 03 00
  6   0x01      29    WRITE_INIT (address 0, slot 1, size=160780)
  7   0x02    9383    WRITE_DATA block 1  (8192 bytes → 9363 encoded)
  8   0x02    9383    WRITE_DATA block 2
  ...
 25   0x02    9383    WRITE_DATA block 19
 26   0x02    5886    WRITE_DATA block 20  (partial, 5132 bytes → 5866 encoded)
 27   0x03      28    WRITE_FINISH (CRC32 = 0x2D9CB759)
 28   0x07      26    SET_FILENAME ("01_FXFinal.ncs")
 29   0x41       9    CLOSE SESSION
```

The device responds to each write message with an ACK (subcmd `0x04`) containing the matching address and file ID.

### Block Addressing

Each WRITE message contains an 8-byte address field. Only the last 2 bytes are used:
```
00 00 00 00 00 00 <page> <offset>
```

- Offsets go `0x00` to `0x0F` (16 per page), then page increments
- WRITE_INIT uses address (0,0) = block 0
- WRITE_DATA starts at block 1: (0,1), (0,2), ..., (0,15), (1,0), (1,1), ...
- WRITE_FINISH uses the next block number after the last data block

For a 160,780-byte NCS file with 8192-byte blocks:
- 20 data blocks (19 full + 1 partial)
- Blocks 1-15 → page 0, offsets 1-15
- Blocks 16-20 → page 1, offsets 0-4
- FINISH → page 1, offset 5

### File ID

3 bytes identifying the target file:
```
<type> <slot_hi> <slot_lo>
```

- Type `0x03` = project file
- Slot: 0-63 for the 64 project slots
- Example: `03 00 01` = project slot 1

### WRITE_INIT Payload (subcmd 0x01)

```
<8-byte address=0> <3-byte file_id> 01 00 00 00 <5 size nibbles>
```

Size nibbles encode the file size as hex nibbles, MSN first:
- 160,780 = 0x2740C → nibbles: `02 07 04 00 0c`

Full example for slot 1:
```
00 00 00 00 00 00 00 00  03 00 01  01 00 00 00  02 07 04 00 0c
└──── address ────────┘  └─file─┘  └─ flags ─┘  └─ size ─────┘
```

### WRITE_DATA Payload (subcmd 0x02)

```
<8-byte address> <3-byte file_id> <MSB-interleave-encoded data>
```

Each block carries 8192 bytes of raw data, encoded as ~9363 bytes (last block is smaller).

### Data Encoding: MSB Interleave

MIDI SysEx requires all data bytes < 0x80. The encoding handles this by grouping every 7 data bytes with a preceding MSB header byte:

```
[MSB_header] [d0 & 0x7F] [d1 & 0x7F] [d2 & 0x7F] [d3 & 0x7F] [d4 & 0x7F] [d5 & 0x7F] [d6 & 0x7F]
```

MSB header bit layout:
- **bit 0** = MSB of d0
- **bit 1** = MSB of d1
- **bit 2** = MSB of d2
- **bit 3** = MSB of d3
- **bit 4** = MSB of d4
- **bit 5** = MSB of d5
- **bit 6** = MSB of d6

To decode: `original_byte = (data_byte & 0x7F) | ((msb_header >> bit_position) & 1) << 7`

Expansion ratio: 8 encoded bytes per 7 raw bytes. For 8192 raw bytes: ceil(8192 / 7) * 8 = 9368 encoded bytes (actual captured: 9363, so the last group may be partial).

### WRITE_FINISH Payload (subcmd 0x03)

```
<8-byte address> <3-byte file_id> <8 CRC32 nibbles>
```

CRC32 nibbles encode a **standard reflected CRC32** (`zlib.crc32()`) of the raw NCS file data:
- 0x2D9CB759 → nibbles: `02 0d 09 0c 0b 07 05 09`

**This is NOT the non-reflected CRC32 from the firmware update gist.** It is standard `zlib.crc32()`.

### SET_FILENAME Payload (subcmd 0x07)

```
<3-byte file_id> <ASCII filename>
```

Example: `03 00 01` + `30 31 5f 46 58 46 69 6e 61 6c 2e 6e 63 73` = "01_FXFinal.ncs"

### Device ACK Messages (subcmd 0x04)

The device sends an ACK for each received message:
```
<8-byte address> <3-byte file_id>
```

Matching the address and file_id of the message being acknowledged. Implementation should wait for each ACK before sending the next block.

### Session Open/Close

- OPEN: `F0 00 20 29 01 64 03 40 F7` (no payload)
- CLOSE: `F0 00 20 29 01 64 03 41 F7` (no payload)

### Directory Listing Handshake

Before writing, Components sends:
1. `0x0b 01` — device responds with `0x0b 01 01` (ACK)
2. `0x09 01 00` — device responds with `0x09 01 <version info>`
3. `0x0b 02` — device responds with `0x0c 02 00 <project name>` (current project name)
4. `0x0b <file_type> 00` — device responds with `0x0b <file_type> 00 3f 00` then 64x `0x0c <file_type> <slot> <filename>` entries

The `<file_type>` byte in step 4 selects which directory to list:

| File Type | Contents | Entries |
|-----------|----------|---------|
| `0x03` | Project files (.ncs) | Up to 64 |
| `0x04` | Synth patches | Up to 64 |
| `0x05` | Drum samples (.wav) | 64 |

This handshake may be optional for write operations. If the device rejects writes without it, include it.

## Verification

The protocol was verified by:
1. Capturing 29 outgoing SysEx messages from Components via browser DevTools JavaScript injection
2. Capturing 93 incoming device responses via MIDI Monitor
3. Decoding all 20 data blocks from the captured transfer
4. Reconstructing the complete 160,780-byte NCS file
5. Confirming it starts with "USER" magic and has correct internal file size
6. Computing `zlib.crc32()` → `0x2D9CB759`, matching the FINISH message checksum exactly

## Implementation Plan

### New file: `src/circuit_mcp/ncs_transfer.py`

Core functions:
- `encode_msb_interleave(data: bytes) -> list[int]` — 8-bit to 7-bit MSB interleave
- `decode_msb_interleave(data: list[int]) -> bytes` — reverse
- `int_to_nibbles(value: int, count: int) -> list[int]` — int to hex nibbles
- `block_address(block_num: int) -> list[int]` — sequential block to (page, offset) 8-byte address
- `send_ncs_project(midi, ncs_data, slot, filename, progress_callback) -> dict` — full transfer

### Modify: `src/circuit_mcp/server.py`

Add MCP tool:
```python
@mcp.tool()
async def send_project_file(file_path: str, slot: int = 0) -> dict:
    """Send an .ncs project file to the Circuit Tracks via SysEx."""
```

### New file: `tests/test_ncs_transfer.py`

- MSB interleave encode/decode round-trip
- Known encoding vectors
- `int_to_nibbles` for file size
- Block address generation
- CRC32 verification against captured data
- Full message sequence structure

### Existing code to reuse
- `MidiConnection.send_sysex()` and `sysex_request()` from `src/circuit_mcp/midi.py`
- `SYSEX_MANUFACTURER_ID`, `SYSEX_PRODUCT_TYPE`, `SYSEX_PRODUCT_NUMBER` from `src/circuit_mcp/constants.py`
- `serialize_ncs()` from `src/circuit_mcp/ncs_parser.py`

### Testing
1. `pytest tests/test_ncs_transfer.py` — unit tests
2. Hardware test: send a known .ncs file, verify it appears on device
3. Capture file at `midi-monitor/sysex_capture.json` serves as ground truth
