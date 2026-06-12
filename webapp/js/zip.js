// Minimal dependency-free ZIP reader for Components packs
// (.circuittrackspack / .zip). Supports stored (method 0) and deflate
// (method 8, via the browser's DecompressionStream) entries.

const EOCD_SIG = 0x06054b50;
const CENTRAL_SIG = 0x02014b50;
const LOCAL_SIG = 0x04034b50;

// Parse a zip ArrayBuffer into Map<path, () => Promise<ArrayBuffer>>.
export function readZip(buf) {
  const data = new DataView(buf);
  const bytes = new Uint8Array(buf);

  // End-of-central-directory record: scan backwards (comment may follow it).
  let eocd = -1;
  for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 22 - 65535); i--) {
    if (data.getUint32(i, true) === EOCD_SIG) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('Not a zip file');
  const count = data.getUint16(eocd + 10, true);
  let off = data.getUint32(eocd + 16, true);

  const entries = new Map();
  for (let n = 0; n < count; n++) {
    if (data.getUint32(off, true) !== CENTRAL_SIG) throw new Error('Bad zip central directory');
    const method = data.getUint16(off + 10, true);
    const compSize = data.getUint32(off + 20, true);
    const nameLen = data.getUint16(off + 28, true);
    const extraLen = data.getUint16(off + 30, true);
    const commentLen = data.getUint16(off + 32, true);
    const localOff = data.getUint32(off + 42, true);
    const name = new TextDecoder().decode(bytes.subarray(off + 46, off + 46 + nameLen));
    if (!name.endsWith('/')) {
      entries.set(name, () => extract(data, bytes, localOff, method, compSize, name));
    }
    off += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}

// Build a zip Blob from [{name, data: Uint8Array}] entries (stored, no
// compression — pack content is mostly WAV/NCS that barely compresses).
export function writeZip(entries) {
  const enc = new TextEncoder();
  const parts = [];
  const central = [];
  let offset = 0;

  for (const { name, data } of entries) {
    const nameBytes = enc.encode(name);
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, LOCAL_SIG, true);
    local.setUint16(4, 20, true); // version needed
    local.setUint16(8, 0, true); // method: store
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, nameBytes.length, true);
    parts.push(local.buffer, nameBytes, data);

    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, CENTRAL_SIG, true);
    cd.setUint16(4, 20, true);
    cd.setUint16(6, 20, true);
    cd.setUint16(10, 0, true);
    cd.setUint32(16, crc, true);
    cd.setUint32(20, data.length, true);
    cd.setUint32(24, data.length, true);
    cd.setUint16(28, nameBytes.length, true);
    cd.setUint32(42, offset, true);
    central.push(cd.buffer, nameBytes);
    offset += 30 + nameBytes.length + data.length;
  }

  const cdSize = central.reduce((n, p) => n + (p.byteLength ?? p.length), 0);
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, EOCD_SIG, true);
  eocd.setUint16(8, entries.length, true);
  eocd.setUint16(10, entries.length, true);
  eocd.setUint32(12, cdSize, true);
  eocd.setUint32(16, offset, true);
  return new Blob([...parts, ...central, eocd.buffer], { type: 'application/zip' });
}

let CRC_TABLE = null;

function crc32(data) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) crc = CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

async function extract(data, bytes, localOff, method, compSize, name) {
  if (data.getUint32(localOff, true) !== LOCAL_SIG) throw new Error(`Bad zip entry: ${name}`);
  // Local header name/extra lengths can differ from the central directory's.
  const nameLen = data.getUint16(localOff + 26, true);
  const extraLen = data.getUint16(localOff + 28, true);
  const start = localOff + 30 + nameLen + extraLen;
  const raw = bytes.slice(start, start + compSize);
  if (method === 0) return raw.buffer;
  if (method === 8) {
    const ds = new DecompressionStream('deflate-raw');
    return new Response(new Blob([raw]).stream().pipeThrough(ds)).arrayBuffer();
  }
  throw new Error(`Unsupported zip compression method ${method} in ${name}`);
}
