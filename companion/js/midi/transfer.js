// Web MIDI transport for the Circuit Tracks file-management protocol.
// Orchestrates sessions, directory listings and the sample write sequence,
// mirroring the timings/ACK handling of the Python reference
// (src/circuit_tracks/ncs_transfer.py).

import {
  wrapSysex, unwrapSysex, isAck, parseFileEntry,
  buildOpenSession, buildCloseSession, buildDirHandshake, buildDirListRequest,
  buildWriteInit, buildWriteData, buildWriteFinish, buildSetFilename,
  crc32, fileId, blockAddress,
  FILE_TYPE_SAMPLE, BLOCK_SIZE, MAX_SAMPLE_BYTES,
} from './protocol.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function midiSupported() {
  return typeof navigator !== 'undefined' && typeof navigator.requestMIDIAccess === 'function';
}

export class CircuitConnection {
  constructor() {
    this.access = null;
    this.input = null;
    this.output = null;
    this._waiters = [];
    this._collectors = [];
    this.onStateChange = null;
  }

  get connected() {
    return !!(this.output && this.output.state === 'connected');
  }

  get portName() {
    return this.output ? this.output.name : null;
  }

  async connect() {
    if (!midiSupported()) {
      throw new Error('Web MIDI is not supported in this browser');
    }
    this.access = await navigator.requestMIDIAccess({ sysex: true });
    this._refreshPorts();
    this.access.onstatechange = () => {
      this._refreshPorts();
      if (this.onStateChange) this.onStateChange();
    };
    if (!this.output) {
      throw new Error(
        'Circuit Tracks not found. Connect it via USB, power it on, then retry.',
      );
    }
    return this;
  }

  _refreshPorts() {
    const byName = (ports) => {
      const list = [...ports.values()];
      return (
        list.find((p) => /circuit tracks/i.test(p.name)) ||
        list.find((p) => /circuit/i.test(p.name)) ||
        null
      );
    };
    this.output = byName(this.access.outputs);
    const input = byName(this.access.inputs);
    if (input !== this.input) {
      if (this.input) this.input.onmidimessage = null;
      this.input = input;
      if (this.input) {
        this.input.onmidimessage = (e) => this._handleMessage(e.data);
      }
    }
  }

  _handleMessage(bytes) {
    if (!bytes || bytes[0] !== 0xf0) return;
    const data = unwrapSysex(bytes);
    if (!data) return;
    for (const collector of this._collectors) collector(data);
    this._waiters = this._waiters.filter((w) => {
      if (w.predicate(data)) {
        w.resolve(data);
        return false;
      }
      return true;
    });
  }

  _send(msgData) {
    this.output.send(wrapSysex(msgData));
  }

  _waitFor(predicate, timeoutMs, what) {
    return new Promise((resolve, reject) => {
      const waiter = { predicate, resolve: null };
      const timer = setTimeout(() => {
        this._waiters = this._waiters.filter((w) => w !== waiter);
        reject(new Error(`Timed out waiting for ${what}`));
      }, timeoutMs);
      waiter.resolve = (data) => {
        clearTimeout(timer);
        resolve(data);
      };
      this._waiters.push(waiter);
    });
  }

  async _openSessionWithHandshake() {
    this._send(buildOpenSession());
    await sleep(300);
    for (const msg of buildDirHandshake()) {
      this._send(msg);
      await sleep(100);
    }
  }

  async _closeSession() {
    this._send(buildCloseSession());
    await sleep(100);
  }

  /**
   * List the device's drum sample slots (file type 0x05).
   * Returns an array of { slot, filename } for occupied slots.
   */
  async listSampleSlots({ timeoutMs = 4000 } = {}) {
    if (!this.connected || !this.input) {
      throw new Error('Not connected to Circuit Tracks');
    }
    const entries = [];
    let lastEntryAt = performance.now();
    const collector = (data) => {
      const entry = parseFileEntry(data);
      if (entry && entry.fileType === FILE_TYPE_SAMPLE) {
        entries.push(entry);
        lastEntryAt = performance.now();
      }
    };
    this._collectors.push(collector);
    try {
      await this._openSessionWithHandshake();
      this._send(buildDirListRequest(FILE_TYPE_SAMPLE));
      const deadline = performance.now() + timeoutMs;
      // Done when all 64 arrived, or entries stopped coming for 500 ms
      while (performance.now() < deadline && entries.length < 64) {
        if (entries.length && performance.now() - lastEntryAt > 500) break;
        await sleep(50);
      }
    } finally {
      this._collectors = this._collectors.filter((c) => c !== collector);
      await this._closeSession();
    }
    return entries;
  }

  /**
   * Send a sample (raw WAV bytes) to a drum sample slot (0-63).
   * Follows the documented write sequence, waiting for a device ACK after
   * WRITE_INIT, every WRITE_DATA block, and WRITE_FINISH.
   */
  async sendSample(slot, wavBytes, filename, onProgress = null) {
    if (slot < 0 || slot > 63) throw new Error(`Slot must be 0-63, got ${slot}`);
    if (wavBytes.length > MAX_SAMPLE_BYTES) {
      throw new Error(
        `Sample is ${wavBytes.length} bytes; the protocol caps a single file at ${MAX_SAMPLE_BYTES}`,
      );
    }
    if (!this.connected || !this.input) {
      throw new Error('Not connected to Circuit Tracks');
    }

    const numBlocks = Math.ceil(wavBytes.length / BLOCK_SIZE);
    const crc = crc32(wavBytes);
    const ackTimeout = 5000;

    await this._openSessionWithHandshake();
    try {
      // Components lists the directory inside the session before writing
      this._send(buildDirListRequest(FILE_TYPE_SAMPLE));
      await sleep(500);

      const sendAcked = async (msg, what) => {
        const ackPromise = this._waitFor(isAck, ackTimeout, `ACK for ${what}`);
        this._send(msg);
        await ackPromise;
      };

      await sendAcked(buildWriteInit(FILE_TYPE_SAMPLE, slot, wavBytes.length), 'WRITE_INIT');

      let bytesSent = 0;
      for (let block = 1; block <= numBlocks; block++) {
        const chunk = wavBytes.subarray((block - 1) * BLOCK_SIZE, block * BLOCK_SIZE);
        await sendAcked(
          buildWriteData(FILE_TYPE_SAMPLE, slot, block, chunk),
          `block ${block}/${numBlocks}`,
        );
        bytesSent += chunk.length;
        if (onProgress) onProgress(bytesSent, wavBytes.length);
      }

      await sendAcked(
        buildWriteFinish(FILE_TYPE_SAMPLE, slot, numBlocks + 1, crc),
        'WRITE_FINISH',
      );

      this._send(buildSetFilename(FILE_TYPE_SAMPLE, slot, filename));
      await sleep(100);
    } finally {
      await this._closeSession();
    }

    return {
      slot,
      filename,
      bytesSent: wavBytes.length,
      blocks: numBlocks,
      crc32: `0x${crc.toString(16).padStart(8, '0').toUpperCase()}`,
    };
  }
}

// Re-export bits the UI needs so it doesn't import protocol.js directly
export { MAX_SAMPLE_BYTES, blockAddress, fileId };
