# Circuit Tracks SysEx File Management Protocol

Reverse-engineered from Novation Components browser app via WebMIDI capture.

## Message Format

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
| Sub-command | 1 | varies | See below |
| Payload | N | varies | Message-specific data |
| SysEx end | 1 | `F7` | Standard MIDI SysEx end |

## Sub-Commands

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

## File Types

| Type | Contents | Slots | Notes |
|------|----------|-------|-------|
| `0x03` | Project files (.ncs) | 0-63 | 160,780 bytes each |
| `0x04` | Synth patches | 0-63 | 340 bytes each |
| `0x05` | Drum samples (.wav) | 0-63 | |

## File ID

3 bytes identifying a target file:
```
<type> <slot_hi> <slot_lo>
```
Slot is encoded as a 14-bit value split across two 7-bit bytes.
Example: `03 00 01` = project slot 1.

## Directory Listing

To list files on the device, open a session and send the directory handshake:

1. `OPEN_SESSION` (0x40)
2. `DIR_CONTROL` [0x01] — device ACKs
3. `QUERY_INFO` [0x01, 0x00] — device responds with version info
4. `DIR_CONTROL` [0x02] — device responds with current file name (`FILE_ENTRY` subcmd 0x0C)
5. `DIR_CONTROL` [file_type, 0x00] — device responds with:
   - DIR_CONTROL ACK: `0x0b <file_type> 00 3f 00`
   - Then 64x FILE_ENTRY: `0x0c <file_type> <slot> <filename bytes>`
6. `CLOSE_SESSION` (0x41)

Implementation: `list_directory()` in `ncs_transfer.py`.

## Project Transfer (Write)

Full sequence for sending an NCS project file (29 SysEx messages):

```
Step  SubCmd  Description
───── ─────── ────────────────────────────────────
  1   0x40    OPEN SESSION
  2   0x0b    DIR_CONTROL: 0x0b 01
  3   0x09    QUERY_INFO:  0x09 01 00
  4   0x0b    DIR_CONTROL: 0x0b 02
  5   0x0b    DIR_CONTROL: 0x0b 03 00  (list projects)
  6   0x01    WRITE_INIT (address 0, file_id, size)
  7   0x02    WRITE_DATA block 1  (8192 bytes → ~9363 encoded)
  ...
 25   0x02    WRITE_DATA block 19
 26   0x02    WRITE_DATA block 20 (partial)
 27   0x03    WRITE_FINISH (CRC32)
 28   0x07    SET_FILENAME
 29   0x41    CLOSE SESSION
```

The device ACKs each WRITE message. Wait for ACK before sending the next block.

Implementation: `send_ncs_project()` in `ncs_transfer.py`.

### Block Addressing

Each WRITE message has an 8-byte address field (only last 2 bytes used):
```
00 00 00 00 00 00 <page> <offset>
```
- 16 offsets per page (0x00-0x0F), then page increments
- WRITE_INIT = block 0, WRITE_DATA starts at block 1
- WRITE_FINISH = next block after last data block

### WRITE_INIT Payload
```
<8-byte address=0> <3-byte file_id> 01 00 00 00 <5 size nibbles>
```
Size nibbles = file size as hex nibbles, MSN first.
160,780 = 0x2740C → `02 07 04 00 0c`

### WRITE_DATA Payload
```
<8-byte address> <3-byte file_id> <MSB-interleave-encoded data>
```

### WRITE_FINISH Payload
```
<8-byte address> <3-byte file_id> <8 CRC32 nibbles>
```
CRC32 = standard `zlib.crc32()` of the raw file data.

### SET_FILENAME Payload
```
<3-byte file_id> <ASCII filename>
```

## Data Encoding: MSB Interleave

MIDI SysEx requires all bytes < 0x80. For every 7 data bytes, output 8 bytes:

```
[MSB_header] [d0 & 0x7F] [d1 & 0x7F] ... [d6 & 0x7F]
```

MSB header: bit N = MSB of byte N (bits 0-6).
To decode: `original = (data & 0x7F) | ((header >> bit) & 1) << 7`

Expansion ratio: 8/7. Last group may be partial.

Implementation: `encode_msb_interleave()` / `decode_msb_interleave()` in `ncs_transfer.py`.

## Patch Save

Patches use a two-phase approach (the documented Replace Patch SysEx 0x01 is silently ignored without this):

**Phase 1: File management session**
1. Open session
2. Directory handshake (same as above)
3. Query patch directory entries — send WRITE_INIT with file type 0x04 for each entry in the page
4. Close session

**Phase 2: Replace Patch SysEx** (command group 0x00, not 0x03)
```
F0 00 20 29 01 64 01 00 00 <slot> 00 <340 patch bytes> F7
```
- Command 0x01 = Replace Patch
- Location byte: 0x00 = synth 1, 0x01 = synth 2
- The Phase 1 session "unlocks" flash writes

Implementation: `send_patch_to_slot()` in `ncs_transfer.py`.

## Project Transfer (Read)

Reverse-engineered from Novation Components "Get Project from Circuit Tracks" flow.

Reading a project reuses the same sub-commands as writing but in reverse:
the host sends a single READ request, and the device streams the entire
project back as data blocks.

### Prerequisites

A file management session must be open (OPEN_SESSION + directory handshake).
Components typically lists the project directory first, then issues the read
within the same session.

### Sequence

```
Step  SubCmd  Dir           Description
───── ─────── ───────────── ────────────────────────────────────
  1   0x01    Host→Device   READ_REQUEST (WRITE_INIT with 0x02 flag)
  2   0x01    Device→Host   READ_INIT response (file size)
  3   0x02    Device→Host   READ_DATA block 1  (9383 bytes encoded)
  ...
 22   0x02    Device→Host   READ_DATA block 20 (partial, 5886 bytes)
 23   0x03    Device→Host   READ_FINISH (CRC32)
 24   0x41    Host→Device   CLOSE SESSION
```

No per-block ACKs from host — the device streams all blocks continuously.

### READ_REQUEST Payload (WRITE_INIT with read flag)
```
<8-byte address=0> <3-byte file_id> 02
```
The `02` byte is the **read flag** (vs `01` for write in WRITE_INIT).

Example: read project slot 36 → `00 00 00 00 00 00 00 00 03 00 24 02`

### READ_INIT Response
```
<8-byte address=0> <3-byte file_id> 01 00 00 00 <5 size nibbles>
```
Same format as WRITE_INIT but sent Device→Host. Size confirms 160,780 bytes.

### READ_DATA Blocks
```
<8-byte address> <3-byte file_id> <MSB-interleave-encoded data>
```
Same format as WRITE_DATA but Device→Host. 20 full blocks (8192 raw bytes
each = 9363 encoded + 11 header = 9383 total) plus one partial final block.

### READ_FINISH
```
<8-byte address> <3-byte file_id> <8 CRC32 nibbles>
```
Device sends CRC32 for verification. Host should validate against
`zlib.crc32()` of the reassembled raw data.

Implementation: `receive_ncs_project()` in `ncs_transfer.py`.
