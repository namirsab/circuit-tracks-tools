// Circuit Tracks SysEx file-management protocol (command group 0x03).
// Ported byte-for-byte from src/circuit_tracks/ncs_transfer.py — see
// docs/sysex-file-protocol.md for the full reverse-engineered spec.
// Pure module: no browser APIs, runs in Node for the golden-vector tests.

export const SYSEX_START = 0xf0;
export const SYSEX_END = 0xf7;

export const MANUFACTURER_ID = [0x00, 0x20, 0x29]; // Novation
export const PRODUCT_TYPE = 0x01; // Synth
export const PRODUCT_NUMBER = 0x64; // Circuit Tracks (100)
const CMD_GROUP = 0x03; // File management protocol

// Sub-commands (Host → Device)
export const SUBCMD_WRITE_INIT = 0x01;
export const SUBCMD_WRITE_DATA = 0x02;
export const SUBCMD_WRITE_FINISH = 0x03;
export const SUBCMD_SET_FILENAME = 0x07;
export const SUBCMD_QUERY_INFO = 0x09;
export const SUBCMD_DIR_CONTROL = 0x0b;
export const SUBCMD_OPEN_SESSION = 0x40;
export const SUBCMD_CLOSE_SESSION = 0x41;

// Sub-commands (Device → Host)
export const SUBCMD_ACK = 0x04;
export const SUBCMD_FILE_ENTRY = 0x0c;

export const FILE_TYPE_PROJECT = 0x03;
export const FILE_TYPE_PATCH = 0x04;
export const FILE_TYPE_SAMPLE = 0x05;

// Raw bytes per WRITE_DATA message
export const BLOCK_SIZE = 8192;

// WRITE_INIT carries the file size as 5 hex nibbles, so a single file is
// capped at 0xFFFFF bytes. The device's total sample memory is 60 seconds
// of 48 kHz / 16-bit / mono audio (Novation spec).
export const MAX_SAMPLE_BYTES = 0xfffff; // 1,048,575 ≈ 10.9 s
export const TOTAL_SAMPLE_MEMORY_BYTES = 60 * 48000 * 2; // 5,760,000

export const SYSEX_HEADER = [...MANUFACTURER_ID, PRODUCT_TYPE, PRODUCT_NUMBER, CMD_GROUP];

/**
 * Encode 8-bit data into 7-bit MIDI-safe bytes using MSB interleave.
 * For every 7 data bytes, emits 1 MSB-header byte (bit j = MSB of byte j)
 * followed by the 7 bytes with MSBs cleared. Last group may be partial.
 */
export function encodeMsbInterleave(data) {
  const result = [];
  for (let i = 0; i < data.length; i += 7) {
    const end = Math.min(i + 7, data.length);
    let msbHeader = 0;
    for (let j = i; j < end; j++) {
      if (data[j] & 0x80) msbHeader |= 1 << (j - i);
    }
    result.push(msbHeader);
    for (let j = i; j < end; j++) result.push(data[j] & 0x7f);
  }
  return result;
}

/** Decode MSB-interleaved 7-bit MIDI data back to 8-bit bytes. */
export function decodeMsbInterleave(encoded) {
  const result = [];
  let i = 0;
  while (i < encoded.length) {
    const msbHeader = encoded[i];
    i += 1;
    for (let j = 0; j < 7 && i < encoded.length; j++, i++) {
      const msb = (msbHeader >> j) & 1;
      result.push(encoded[i] | (msb << 7));
    }
  }
  return new Uint8Array(result);
}

/** Encode an integer as hex nibbles, most significant first. */
export function intToNibbles(value, count) {
  const nibbles = [];
  for (let i = count - 1; i >= 0; i--) {
    nibbles.push(Number((BigInt(value) >> BigInt(4 * i)) & 0x0fn));
  }
  return nibbles;
}

/** Decode a sequence of hex nibbles back to an integer. */
export function nibblesToInt(nibbles) {
  let value = 0;
  for (const n of nibbles) value = value * 16 + (n & 0x0f);
  return value;
}

/**
 * Convert a sequential block number to the 8-byte address field:
 * 16 offsets per page, only the last two bytes are used.
 */
export function blockAddress(blockNum) {
  return [0, 0, 0, 0, 0, 0, blockNum >> 4, blockNum & 0x0f];
}

/** 3-byte file ID: type + 14-bit slot split across two 7-bit bytes. */
export function fileId(fileType, slot) {
  return [fileType, (slot >> 7) & 0x7f, slot & 0x7f];
}

let crcTable = null;
function buildCrcTable() {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
}

/** Standard CRC32 (same polynomial/init as zlib.crc32), unsigned. */
export function crc32(data) {
  if (!crcTable) crcTable = buildCrcTable();
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = crcTable[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/** Build a complete SysEx message data array (without F0/F7). */
export function makeMsg(subcmd, payload = null) {
  const msg = [...SYSEX_HEADER, subcmd];
  if (payload) msg.push(...payload);
  return msg;
}

/** Wrap message data in F0..F7 for Web MIDI's send(). */
export function wrapSysex(msgData) {
  return new Uint8Array([SYSEX_START, ...msgData, SYSEX_END]);
}

// --- Message builders (data arrays without F0/F7, like _make_msg) ---

export const buildOpenSession = () => makeMsg(SUBCMD_OPEN_SESSION);
export const buildCloseSession = () => makeMsg(SUBCMD_CLOSE_SESSION);

/** The three-message directory handshake sent after OPEN_SESSION. */
export function buildDirHandshake() {
  return [
    makeMsg(SUBCMD_DIR_CONTROL, [0x01]),
    makeMsg(SUBCMD_QUERY_INFO, [0x01, 0x00]),
    makeMsg(SUBCMD_DIR_CONTROL, [0x02]),
  ];
}

/** Request the 64-entry file listing for a file type. */
export function buildDirListRequest(fileType) {
  return makeMsg(SUBCMD_DIR_CONTROL, [fileType, 0x00]);
}

/** WRITE_INIT: address 0 + file_id + 01 00 00 00 + 5 size nibbles. */
export function buildWriteInit(fileType, slot, size) {
  const payload = [
    ...blockAddress(0),
    ...fileId(fileType, slot),
    0x01, 0x00, 0x00, 0x00,
    ...intToNibbles(size, 5),
  ];
  return makeMsg(SUBCMD_WRITE_INIT, payload);
}

/** WRITE_DATA block N (1-based): address + file_id + encoded chunk. */
export function buildWriteData(fileType, slot, blockNum, chunk) {
  const payload = [
    ...blockAddress(blockNum),
    ...fileId(fileType, slot),
    ...encodeMsbInterleave(chunk),
  ];
  return makeMsg(SUBCMD_WRITE_DATA, payload);
}

/** WRITE_FINISH: address + file_id + 8 CRC32 nibbles. */
export function buildWriteFinish(fileType, slot, blockNum, crc) {
  const payload = [
    ...blockAddress(blockNum),
    ...fileId(fileType, slot),
    ...intToNibbles(crc, 8),
  ];
  return makeMsg(SUBCMD_WRITE_FINISH, payload);
}

/** SET_FILENAME: file_id + ASCII filename bytes. */
export function buildSetFilename(fileType, slot, filename) {
  const nameBytes = [...filename].map((c) => c.charCodeAt(0) & 0x7f);
  return makeMsg(SUBCMD_SET_FILENAME, [...fileId(fileType, slot), ...nameBytes]);
}

/**
 * Build the full write sequence for one file as an ordered plan.
 * Each entry: { msg, expectAck } — the sender must wait for a device ACK
 * after every expectAck message before sending the next.
 */
export function buildWriteSequence(fileType, slot, data, filename) {
  const numBlocks = Math.ceil(data.length / BLOCK_SIZE);
  const steps = [];
  steps.push({ msg: buildOpenSession(), expectAck: false, label: 'open session' });
  for (const m of buildDirHandshake()) steps.push({ msg: m, expectAck: false, label: 'handshake' });
  steps.push({ msg: buildDirListRequest(fileType), expectAck: false, label: 'list directory' });
  steps.push({ msg: buildWriteInit(fileType, slot, data.length), expectAck: true, label: 'write init' });
  for (let block = 1; block <= numBlocks; block++) {
    const chunk = data.subarray((block - 1) * BLOCK_SIZE, block * BLOCK_SIZE);
    steps.push({
      msg: buildWriteData(fileType, slot, block, chunk),
      expectAck: true,
      label: `block ${block}/${numBlocks}`,
      rawBytes: chunk.length,
    });
  }
  steps.push({
    msg: buildWriteFinish(fileType, slot, numBlocks + 1, crc32(data)),
    expectAck: true,
    label: 'write finish',
  });
  steps.push({ msg: buildSetFilename(fileType, slot, filename), expectAck: false, label: 'set filename' });
  steps.push({ msg: buildCloseSession(), expectAck: false, label: 'close session' });
  return steps;
}

// --- Incoming message parsing ---

/** Strip F0/F7 if present; returns the message data array or null. */
export function unwrapSysex(bytes) {
  if (!bytes || bytes.length < 2) return null;
  let data = Array.from(bytes);
  if (data[0] === SYSEX_START) data = data.slice(1);
  if (data[data.length - 1] === SYSEX_END) data = data.slice(0, -1);
  return data;
}

function hasHeader(data) {
  return (
    data.length > SYSEX_HEADER.length &&
    SYSEX_HEADER.every((b, i) => data[i] === b)
  );
}

/** True if the message data is a device ACK (0x04). */
export function isAck(data) {
  return (
    hasHeader(data) &&
    data[SYSEX_HEADER.length] === SUBCMD_ACK &&
    data.length >= SYSEX_HEADER.length + 1 + 8 + 3
  );
}

/**
 * True if the message is an ACK for the given block address and file ID.
 * ACK payload: <8-byte address> <3-byte file_id>. Matching prevents a
 * stale ACK (e.g. from the previous file in a batch) from being taken as
 * the acknowledgement of the message just sent.
 */
export function ackMatches(data, addr, fid) {
  if (!isAck(data)) return false;
  const base = SYSEX_HEADER.length + 1;
  for (let i = 0; i < 8; i++) if (data[base + i] !== addr[i]) return false;
  for (let i = 0; i < 3; i++) if (data[base + 8 + i] !== fid[i]) return false;
  return true;
}

/**
 * Parse a FILE_ENTRY (0x0C) message.
 * Returns { fileType, slot, filename } or null if not a file entry.
 */
export function parseFileEntry(data) {
  const h = SYSEX_HEADER.length;
  if (!hasHeader(data) || data[h] !== SUBCMD_FILE_ENTRY || data.length < h + 4) return null;
  const fileType = data[h + 1];
  const slot = (data[h + 2] << 7) | data[h + 3];
  let filename = '';
  for (const b of data.slice(h + 4)) {
    if (b >= 32 && b <= 126) filename += String.fromCharCode(b);
  }
  return { fileType, slot, filename };
}
