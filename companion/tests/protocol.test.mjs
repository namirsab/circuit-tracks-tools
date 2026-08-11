// Byte-identical verification of the JS protocol port against golden
// vectors generated from the Python reference (tools/generate_golden.py).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  encodeMsbInterleave, decodeMsbInterleave,
  intToNibbles, nibblesToInt,
  blockAddress, fileId, crc32,
  buildWriteInit, buildWriteData, buildWriteFinish, buildSetFilename,
  buildOpenSession, buildCloseSession, buildDirHandshake, buildDirListRequest,
  buildWriteSequence, wrapSysex, unwrapSysex, isAck, parseFileEntry,
  FILE_TYPE_SAMPLE, SYSEX_HEADER, SUBCMD_ACK, SUBCMD_FILE_ENTRY, BLOCK_SIZE,
} from '../js/midi/protocol.js';

const vectors = JSON.parse(
  readFileSync(fileURLToPath(new URL('./golden/vectors.json', import.meta.url)), 'utf8'),
);

test('MSB interleave encode matches Python reference', () => {
  for (const { raw, encoded } of vectors.msb_interleave) {
    assert.deepEqual(encodeMsbInterleave(new Uint8Array(raw)), encoded);
  }
});

test('MSB interleave decode round-trips', () => {
  for (const { raw, encoded } of vectors.msb_interleave) {
    assert.deepEqual(Array.from(decodeMsbInterleave(encoded)), raw);
  }
});

test('nibble encoding matches Python reference', () => {
  for (const { value, count, nibbles } of vectors.nibbles) {
    assert.deepEqual(intToNibbles(value, count), nibbles);
    assert.equal(nibblesToInt(nibbles), value);
  }
});

test('block addressing matches Python reference', () => {
  for (const { block, address } of vectors.block_address) {
    assert.deepEqual(blockAddress(block), address);
  }
});

test('sample file IDs match Python reference', () => {
  for (const { slot, file_id } of vectors.file_id_sample) {
    assert.deepEqual(fileId(FILE_TYPE_SAMPLE, slot), file_id);
  }
});

test('CRC32 matches zlib.crc32', () => {
  for (const { raw, crc32: expected } of vectors.crc32) {
    assert.equal(crc32(new Uint8Array(raw)), expected);
  }
});

test('write-sequence messages are byte-identical to Python reference', () => {
  const ws = vectors.write_sequence;
  const data = new Uint8Array(ws.sample_data);
  const m = ws.messages;

  assert.deepEqual(buildOpenSession(), m.open_session);
  assert.deepEqual(buildDirHandshake(), m.dir_handshake);
  assert.deepEqual(buildDirListRequest(FILE_TYPE_SAMPLE), m.dir_list_samples);
  assert.deepEqual(buildWriteInit(FILE_TYPE_SAMPLE, ws.slot, data.length), m.write_init);
  for (let block = 1; block <= ws.num_blocks; block++) {
    const chunk = data.subarray((block - 1) * BLOCK_SIZE, block * BLOCK_SIZE);
    assert.deepEqual(
      buildWriteData(FILE_TYPE_SAMPLE, ws.slot, block, chunk),
      m.write_data_blocks[block - 1],
      `block ${block}`,
    );
  }
  assert.equal(crc32(data), ws.crc32);
  assert.deepEqual(
    buildWriteFinish(FILE_TYPE_SAMPLE, ws.slot, ws.num_blocks + 1, ws.crc32),
    m.write_finish,
  );
  assert.deepEqual(buildSetFilename(FILE_TYPE_SAMPLE, ws.slot, ws.filename), m.set_filename);
  assert.deepEqual(buildCloseSession(), m.close_session);
});

test('buildWriteSequence emits the documented message order', () => {
  const ws = vectors.write_sequence;
  const data = new Uint8Array(ws.sample_data);
  const steps = buildWriteSequence(FILE_TYPE_SAMPLE, ws.slot, data, ws.filename);
  const m = ws.messages;
  const expected = [
    m.open_session, ...m.dir_handshake, m.dir_list_samples,
    m.write_init, ...m.write_data_blocks, m.write_finish,
    m.set_filename, m.close_session,
  ];
  assert.equal(steps.length, expected.length);
  steps.forEach((step, i) => assert.deepEqual(step.msg, expected[i], `step ${i}`));
  // ACKs are expected for WRITE_INIT, each data block and WRITE_FINISH only.
  const ackFlags = steps.map((s) => s.expectAck);
  assert.deepEqual(
    ackFlags,
    [false, false, false, false, false, true, true, true, true, true, false, false],
  );
});

test('sysex wrap/unwrap round-trips', () => {
  const msg = buildOpenSession();
  const wrapped = wrapSysex(msg);
  assert.equal(wrapped[0], 0xf0);
  assert.equal(wrapped[wrapped.length - 1], 0xf7);
  assert.deepEqual(unwrapSysex(wrapped), msg);
});

test('isAck recognises device ACKs', () => {
  const ack = [...SYSEX_HEADER, SUBCMD_ACK, ...blockAddress(3), ...fileId(FILE_TYPE_SAMPLE, 7)];
  assert.equal(isAck(ack), true);
  assert.equal(isAck(buildOpenSession()), false);
  assert.equal(isAck([...SYSEX_HEADER, SUBCMD_ACK]), false); // too short
});

test('parseFileEntry decodes slot and filename', () => {
  const name = '07_TestKick.wav';
  const entry = [
    ...SYSEX_HEADER, SUBCMD_FILE_ENTRY, FILE_TYPE_SAMPLE, 0x00, 0x07,
    ...[...name].map((c) => c.charCodeAt(0)),
  ];
  assert.deepEqual(parseFileEntry(entry), {
    fileType: FILE_TYPE_SAMPLE, slot: 7, filename: name,
  });
  assert.equal(parseFileEntry(buildOpenSession()), null);
});
