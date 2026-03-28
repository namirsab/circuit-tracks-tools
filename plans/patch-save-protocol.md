# Patch Save via Components Protocol

## Context

The Programmer's Reference "Replace Patch" SysEx command (0x01) is silently ignored by Circuit Tracks firmware. Novation Components uses the undocumented file management protocol (command group 0x03) to save patches to flash — the same protocol used for NCS project transfers (see `plans/ncs-transfer-protocol.md`).

This plan assumes `src/circuit_mcp/ncs_transfer.py` is already implemented with the core protocol functions from the NCS transfer plan.

## What needs to happen

A patch save is a miniature version of a project transfer:
- Same session open/close (subcmd 0x40/0x41)
- Same WRITE_INIT / WRITE_DATA / WRITE_FINISH sequence
- Same MSB interleave encoding and CRC32 checksum
- Same ACK handshake

The differences:
- **File ID**: patches use a different type byte (TBD — capture from Components)
- **Data size**: 340 bytes → fits in a single WRITE_DATA block
- **Filename**: patch name (16 chars) instead of project filename

## Steps

### 1. Capture the patch file ID

Use the MIDI monitor to capture Components saving a single patch. Look at the WRITE_INIT message's file ID field (3 bytes after the 8-byte address):
```
<8-byte address> <type> <slot_hi> <slot_lo> ...
```

For projects this is `03 00 XX`. For patches it's likely `01 00 XX` or `02 00 XX` for synth 1/2.

### 2. Add `send_patch_to_slot()` in `ncs_transfer.py`

Reuse existing functions from the NCS transfer implementation:

```python
def send_patch_to_slot(midi: MidiConnection, patch_bytes: bytes, slot: int,
                       patch_name: str = "") -> dict:
    """Save a 340-byte patch to a flash slot using the Components protocol."""
    # 1. open_session()
    # 2. send_write_init(file_id=(PATCH_TYPE, 0, slot), size=340)
    # 3. wait_for_ack()
    # 4. send_write_data(block=1, file_id, encode_msb_interleave(patch_bytes))
    # 5. wait_for_ack()
    # 6. send_write_finish(file_id, crc32=zlib.crc32(patch_bytes))
    # 7. wait_for_ack()
    # 8. send_set_filename(file_id, patch_name)
    # 9. close_session()
```

### 3. Update `save_synth_patch` MCP tool in `server.py`

Replace the non-working Replace Patch SysEx (0x01) with `send_patch_to_slot()`.

### 4. Test

- Save a patch to slot 60
- Select a different patch
- Select slot 60 → verify the saved patch loads

## Functions to reuse from `ncs_transfer.py`

- `encode_msb_interleave(data)` — 7-bit encoding
- `int_to_nibbles(value, count)` — size/CRC encoding
- `block_address(block_num)` — address field generation
- `open_session(midi)` / `close_session(midi)`
- `wait_for_ack(midi)` — ACK handshake
- `send_write_init()` / `send_write_data()` / `send_write_finish()`
- `send_set_filename()`

## Resolved (from Components capture)

- Patch directory file type byte is **0x04** (not 0x01/0x02 as hypothesized)
- Components does NOT use WRITE_INIT/WRITE_DATA/WRITE_FINISH for patches
- Instead: open file mgmt session → browse patch directory (16 entries per page) → close session → send Replace Patch SysEx
- Replace Patch SysEx format: `header + 0x01 + location + 0x00 + slot + 0x00 + 340 bytes` (location: 0x00=synth1, 0x01=synth2)
- The old Replace Patch format was missing the location byte and had wrong byte count
