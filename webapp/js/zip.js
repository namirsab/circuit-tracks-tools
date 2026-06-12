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
